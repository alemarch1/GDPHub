# SQLModel ORM definitions for all GDPHub database tables.
# Each class maps to a SQLite table and defines its schema, relationships,
# and default values.

from typing import Optional, List
from datetime import datetime
from sqlmodel import SQLModel, Field, Relationship

class ProcessedEmail(SQLModel, table=True):
    """Tracks email message IDs that have already been downloaded."""
    __tablename__ = "processed_email"
    id: str = Field(primary_key=True)
    source: str = Field(default="gmail")  # "gmail" or "outlook"
    processing_date: datetime = Field(default_factory=datetime.utcnow)

class DocumentClassification(SQLModel, table=True):
    """Stores Ollama-generated classifications for a document."""
    __tablename__ = "document_classification"
    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: str = Field(foreign_key="document.id", index=True, ondelete="CASCADE")
    model_used: str
    classification_generic: str
    description_short: str
    time_generic_s: float
    time_short_s: float
    processing_date: datetime = Field(default_factory=datetime.utcnow)

    document: "Document" = Relationship(back_populates="classifications")

class DocumentRopaMapping(SQLModel, table=True):
    """Maps a document to one or more ROPA processing activities."""
    __tablename__ = "document_ropa_mapping"
    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: str = Field(foreign_key="document.id", index=True, ondelete="CASCADE")
    ropa_id: Optional[str] = Field(default=None, index=True) # Soft link to RopaRecord
    model_used: str
    raw_fallback_text: Optional[str] = None
    processing_date: datetime = Field(default_factory=datetime.utcnow)

    document: "Document" = Relationship(back_populates="ropa_mappings")

class Document(SQLModel, table=True):
    """Master record for an extracted and anonymized document."""
    id: str = Field(primary_key=True) # UUID
    type: str # Email, File, Attachment
    parent_id: Optional[str] = None
    file_path: str
    file_name: str
    extracted_text_masked: str
    md5_hash: str = Field(unique=True, index=True)
    names_or_surnames_masked: bool
    creation_date: Optional[datetime] = None
    processing_date: datetime = Field(default_factory=datetime.utcnow)

    classifications: List[DocumentClassification] = Relationship(back_populates="document", cascade_delete=True)
    ropa_mappings: List[DocumentRopaMapping] = Relationship(back_populates="document", cascade_delete=True)

class RopaRecord(SQLModel, table=True):
    """A single row from the ROPA (Register of Processing Activities)."""
    __tablename__ = "ropa_record"
    id: str = Field(primary_key=True) # "0001", "0002"
    activity: str
    lawful_bases: str
    subject_categories: str
    personal_data_categories: str
    recipients_categories: str
    international_transfers: str
    retention_periods: str

class DocumentLifecycle(SQLModel, table=True):
    """Tracks the retention lifecycle and deletion status of a document."""
    __tablename__ = "document_lifecycle"
    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: str = Field(index=True)
    document_type: Optional[str] = None
    creation_date: Optional[datetime] = None
    scheduled_deletion_date: datetime
    actual_deletion_date: Optional[datetime] = None
    status: str = Field(default="PENDING")
    notes: Optional[str] = None

class Configuration(SQLModel, table=True):
    """Key-value store for application configuration (values are JSON strings)."""
    key: str = Field(primary_key=True)
    value: str # Stores JSON string
