from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    full_name: str
    role: str = Field(default="engineer")  # admin, supervisor, engineer
    hashed_password: str
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str
    original_filename: str
    file_path: str
    uploaded_by: int = Field(foreign_key="user.id")
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)


class DocumentChunk(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    document_id: int = Field(foreign_key="document.id", index=True)
    source_filename: str

    chunk_index: int
    chunk_text: str
    keywords: str = Field(default="")

    page_number: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class FieldTask(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    title: str
    description: str

    site_name: str
    equipment_name: str
    task_type: str = Field(default="general")

    assigned_engineer_id: int = Field(foreign_key="user.id")
    created_by: int = Field(foreign_key="user.id")

    priority: str = Field(default="medium")  # low, medium, high
    status: str = Field(default="created")   # created, assigned, downloaded, in_progress, completed

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
