from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Identity:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Owned:
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class User(Identity, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, server_default=false())


class Profile(Owned, Base):
    __tablename__ = "user_profiles"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384))
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ExtractedSkill(Owned, Base):
    __tablename__ = "extracted_skills"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    skill: Mapped[str] = mapped_column(String(120), primary_key=True)


class Preference(Owned, Base):
    __tablename__ = "user_preferences"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    values: Mapped[dict] = mapped_column(JSONB, default=dict)


class Resume(Identity, Owned, Base):
    __tablename__ = "resumes"
    draft: Mapped[dict] = mapped_column(JSONB)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


class JobSource(Identity, Base):
    __tablename__ = "job_sources"
    name: Mapped[str] = mapped_column(String(120), unique=True)
    provenance: Mapped[str] = mapped_column(String(500))
    synthetic: Mapped[bool] = mapped_column(default=False)


class IngestionRun(Identity, Base):
    __tablename__ = "ingestion_runs"
    source_id: Mapped[UUID] = mapped_column(ForeignKey("job_sources.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20))
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)


class Job(Identity, Base):
    __tablename__ = "job_postings"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id"),
        Index("ix_job_search_doc", "search_doc", postgresql_using="gin"),
        Index("ix_job_active_remote", "active", "remote_policy"),
        Index("ix_job_skills", "required_skills", postgresql_using="gin"),
    )
    source_id: Mapped[UUID] = mapped_column(ForeignKey("job_sources.id", ondelete="RESTRICT"))
    external_id: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    company: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    required_skills: Mapped[list[str]] = mapped_column(ARRAY(String(120)))
    preferred_skills: Mapped[list[str]] = mapped_column(ARRAY(String(120)))
    min_years: Mapped[int | None] = mapped_column(Integer)
    employment_type: Mapped[str | None] = mapped_column(String(32))
    location: Mapped[str | None] = mapped_column(String(160))
    remote_policy: Mapped[str | None] = mapped_column(String(32))
    company_category: Mapped[str | None] = mapped_column(String(64))
    salary_min: Mapped[int | None]
    salary_max: Mapped[int | None]
    currency: Mapped[str | None] = mapped_column(String(3))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str | None] = mapped_column(String(2048), unique=True)
    active: Mapped[bool] = mapped_column(default=True)
    search_doc: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', title), 'A') || "
            "setweight(to_tsvector('english', company), 'B') || "
            "setweight(to_tsvector('english', description), 'C')",
            persisted=True,
        ),
    )


class JobEmbedding(Base):
    __tablename__ = "job_embeddings"
    __table_args__ = (
        Index(
            "ix_job_embedding_cosine",
            "value",
            postgresql_using="hnsw",
            postgresql_ops={"value": "vector_cosine_ops"},
        ),
    )
    job_id: Mapped[UUID] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"), primary_key=True)
    model: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))
    value: Mapped[list[float]] = mapped_column(Vector(384))


class SavedJob(Base):
    __tablename__ = "saved_jobs"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Interaction(Identity, Owned, Base):
    __tablename__ = "job_interactions"
    kind: Mapped[str] = mapped_column(String(30))
    details: Mapped[dict] = mapped_column(JSONB)


class Conversation(Identity, Owned, Base):
    __tablename__ = "conversations"
    title: Mapped[str] = mapped_column(String(120))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Message(Identity, Base):
    __tablename__ = "conversation_messages"
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("conversation_id", "sequence"),)
    role: Mapped[str] = mapped_column(String(12))
    content: Mapped[dict] = mapped_column(JSONB)


class AgentMemory(Base):
    __tablename__ = "agent_memory"
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[dict] = mapped_column(JSONB, default=dict)


class Feedback(Identity, Owned, Base):
    __tablename__ = "ranking_feedback"
    job_id: Mapped[UUID] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"))
    trace_id: Mapped[str | None] = mapped_column(String(32))
    ranking_version: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(String(1000))


class EvaluationDataset(Identity, Base):
    __tablename__ = "evaluation_datasets"
    name: Mapped[str] = mapped_column(String(100), unique=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)


class EvaluationRun(Identity, Base):
    __tablename__ = "evaluation_runs"
    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("evaluation_datasets.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(20))
    configuration: Mapped[dict] = mapped_column(JSONB)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationResult(Identity, Base):
    __tablename__ = "evaluation_results"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("evaluation_runs.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str] = mapped_column(String(120))
    outcome: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("run_id", "case_id"),)


class PromptVersion(Identity, Base):
    __tablename__ = "prompt_versions_metadata"
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("name", "version"),)
