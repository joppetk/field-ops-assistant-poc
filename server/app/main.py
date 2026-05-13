import os
import shutil
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, select

from app.database import create_db_and_tables, get_session
from app.models import User, Document, FieldTask
from app.auth import (
    hash_password,
    authenticate_user,
    create_access_token,
    get_current_user,
    require_admin,
)


UPLOAD_DIR = "uploads"

app = FastAPI(
    title="Offline Field Operations Assistant POC",
    description="Day 1 backend for secure offline field operations assistant.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For POC only. Restrict this in production.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/")
def root():
    return {
        "system": "Offline Field Operations Assistant POC",
        "version": "0.1.0",
        "day": "Day 1",
        "status": "running"
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

    return {
        "message": "Document uploaded successfully.",
        "document": {
            "id": doc.id,
            "original_filename": doc.original_filename,
            "stored_filename": doc.filename,
            "file_path": doc.file_path,
            "uploaded_by": doc.uploaded_by,
            "uploaded_at": doc.uploaded_at,
        }
    }


@app.get("/documents")
def list_documents(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    docs = session.exec(select(Document).order_by(Document.uploaded_at.desc())).all()

    return {
        "count": len(docs),
        "documents": [
            {
                "id": doc.id,
                "original_filename": doc.original_filename,
                "stored_filename": doc.filename,
                "file_path": doc.file_path,
                "uploaded_by": doc.uploaded_by,
                "uploaded_at": doc.uploaded_at,
            }
            for doc in docs
        ]
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
