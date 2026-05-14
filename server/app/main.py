import os
import shutil
import uuid
from datetime import timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import create_db_and_tables, get_session
from app.models import User, Document, DocumentChunk, FieldTask
from app.auth import (
    hash_password,
    authenticate_user,
    create_access_token,
    get_current_user,
    require_admin,
)
from app.document_processor import process_file_to_chunks
from app.retrieval_agent import search_chunks


UPLOAD_DIR = "uploads"

app = FastAPI(
    title="Offline Field Operations Assistant POC",
    description="Day 2 backend with lightweight RAG retrieval.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For POC only. Restrict this in production.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RagSearchRequest(BaseModel):
    query: str
    limit: Optional[int] = 5


@app.on_event("startup")
def on_startup():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    create_db_and_tables()
    seed_demo_users()


def seed_demo_users():
    """
    Creates demo users for the POC.

    Admin:
      username: admin
      password: admin123

    Engineer:
      username: engineer1
      password: engineer123
    """
    from sqlmodel import Session
    from app.database import engine

    with Session(engine) as session:
        admin = session.exec(select(User).where(User.username == "admin")).first()
        if not admin:
            admin = User(
                username="admin",
                full_name="POC Admin",
                role="admin",
                hashed_password=hash_password("admin123"),
            )
            session.add(admin)

        engineer = session.exec(select(User).where(User.username == "engineer1")).first()
        if not engineer:
            engineer = User(
                username="engineer1",
                full_name="Engineer One",
                role="engineer",
                hashed_password=hash_password("engineer123"),
            )
            session.add(engineer)

        session.commit()


def process_document_internal(document_id: int, session: Session):
    """
    Extract text from a document, split it into chunks,
    and save chunks in the database.
    """
    document = session.get(Document, document_id)

    if not document:
        raise HTTPException(
            status_code=404,
            detail="Document not found."
        )

    if not os.path.exists(document.file_path):
        raise HTTPException(
            status_code=404,
            detail="Document file not found on disk."
        )

    # Remove previous chunks for this document so re-processing is clean.
    existing_chunks = session.exec(
        select(DocumentChunk).where(DocumentChunk.document_id == document_id)
    ).all()

    for chunk in existing_chunks:
        session.delete(chunk)

    try:
        chunk_records = process_file_to_chunks(
            file_path=document.file_path,
            original_filename=document.original_filename,
            chunk_size_words=140,
            overlap_words=30
        )
    except Exception as e:
        session.commit()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process document: {str(e)}"
        )

    for record in chunk_records:
        db_chunk = DocumentChunk(
            document_id=document.id,
            source_filename=record["source_filename"],
            page_number=record["page_number"],
            chunk_index=record["chunk_index"],
            chunk_text=record["chunk_text"],
            keywords=record["keywords"],
        )
        session.add(db_chunk)

    session.commit()

    return {
        "document_id": document.id,
        "original_filename": document.original_filename,
        "chunks_created": len(chunk_records)
    }


@app.get("/")
def root():
    return {
        "system": "Offline Field Operations Assistant POC",
        "version": "0.2.0",
        "day": "Day 2",
        "status": "running",
        "features": [
            "login",
            "document upload",
            "document text extraction",
            "document chunking",
            "keyword-based RAG search",
            "field task creation"
        ]
    }


@app.post("/auth/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session)
):
    user = authenticate_user(session, form_data.username, form_data.password)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password."
        )

    access_token = create_access_token(
        data={
            "sub": user.username,
            "role": user.role,
            "user_id": user.id,
        },
        expires_delta=timedelta(hours=8),
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
        }
    }


@app.get("/auth/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "is_active": current_user.is_active,
    }


@app.post("/documents/upload")
def upload_document(
    file: UploadFile = File(...),
    auto_process: bool = Form(True),
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session)
):
    allowed_extensions = [".pdf", ".txt", ".docx", ".md"]
    original_filename = file.filename

    ext = os.path.splitext(original_filename)[1].lower()

    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {allowed_extensions}"
        )

    safe_filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    doc = Document(
        filename=safe_filename,
        original_filename=original_filename,
        file_path=file_path,
        uploaded_by=current_user.id,
    )

    session.add(doc)
    session.commit()
    session.refresh(doc)

    process_result = None

    if auto_process:
        process_result = process_document_internal(doc.id, session)

    return {
        "message": "Document uploaded successfully.",
        "document": {
            "id": doc.id,
            "original_filename": doc.original_filename,
            "stored_filename": doc.filename,
            "file_path": doc.file_path,
            "uploaded_by": doc.uploaded_by,
            "uploaded_at": doc.uploaded_at,
        },
        "processing": process_result
    }


@app.get("/documents")
def list_documents(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    docs = session.exec(select(Document).order_by(Document.uploaded_at.desc())).all()

    output = []

    for doc in docs:
        chunks = session.exec(
            select(DocumentChunk).where(DocumentChunk.document_id == doc.id)
        ).all()

        output.append(
            {
                "id": doc.id,
                "original_filename": doc.original_filename,
                "stored_filename": doc.filename,
                "file_path": doc.file_path,
                "uploaded_by": doc.uploaded_by,
                "uploaded_at": doc.uploaded_at,
                "chunk_count": len(chunks),
            }
        )

    return {
        "count": len(output),
        "documents": output
    }


@app.post("/documents/{document_id}/process")
def process_document(
    document_id: int,
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session)
):
    result = process_document_internal(document_id, session)

    return {
        "message": "Document processed successfully.",
        "result": result
    }


@app.post("/documents/process-all")
def process_all_documents(
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session)
):
    documents = session.exec(select(Document)).all()

    results = []

    for doc in documents:
        result = process_document_internal(doc.id, session)
        results.append(result)

    return {
        "message": "All documents processed.",
        "count": len(results),
        "results": results
    }


@app.get("/documents/{document_id}/chunks")
def get_document_chunks(
    document_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    document = session.get(Document, document_id)

    if not document:
        raise HTTPException(
            status_code=404,
            detail="Document not found."
        )

    chunks = session.exec(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
    ).all()

    return {
        "document": {
            "id": document.id,
            "original_filename": document.original_filename,
        },
        "chunk_count": len(chunks),
        "chunks": [
            {
                "id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "page_number": chunk.page_number,
                "text": chunk.chunk_text,
                "keywords": chunk.keywords,
            }
            for chunk in chunks
        ]
    }


@app.post("/rag/search")
def rag_search(
    request: RagSearchRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    query = request.query.strip()

    if not query:
        raise HTTPException(
            status_code=400,
            detail="Query cannot be empty."
        )

    limit = request.limit or 5
    limit = max(1, min(limit, 20))

    results = search_chunks(
        session=session,
        query=query,
        limit=limit,
        min_score=1
    )

    return {
        "query": query,
        "limit": limit,
        "result_count": len(results),
        "results": results
    }


@app.post("/tasks")
def create_task(
    title: str = Form(...),
    description: str = Form(...),
    site_name: str = Form(...),
    equipment_name: str = Form(...),
    assigned_engineer_id: int = Form(...),
    task_type: str = Form("troubleshooting"),
    priority: str = Form("medium"),
    current_user: User = Depends(require_admin),
    session: Session = Depends(get_session)
):
    engineer = session.get(User, assigned_engineer_id)

    if not engineer:
        raise HTTPException(
            status_code=404,
            detail="Assigned engineer not found."
        )

    if engineer.role != "engineer":
        raise HTTPException(
            status_code=400,
            detail="Assigned user must have engineer role."
        )

    task = FieldTask(
        title=title,
        description=description,
        site_name=site_name,
        equipment_name=equipment_name,
        assigned_engineer_id=assigned_engineer_id,
        created_by=current_user.id,
        task_type=task_type,
        priority=priority,
        status="assigned",
    )

    session.add(task)
    session.commit()
    session.refresh(task)

    return {
        "message": "Field task created successfully.",
        "task": task
    }


@app.get("/tasks")
def list_tasks(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    if current_user.role in ["admin", "supervisor"]:
        statement = select(FieldTask).order_by(FieldTask.created_at.desc())
    else:
        statement = (
            select(FieldTask)
            .where(FieldTask.assigned_engineer_id == current_user.id)
            .order_by(FieldTask.created_at.desc())
        )

    tasks = session.exec(statement).all()

    return {
        "count": len(tasks),
        "tasks": tasks
    }


@app.get("/tasks/{task_id}")
def get_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    task = session.get(FieldTask, task_id)

    if not task:
        raise HTTPException(
            status_code=404,
            detail="Task not found."
        )

    if current_user.role == "engineer" and task.assigned_engineer_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You are not assigned to this task."
        )

    return {
        "task": task
    }
