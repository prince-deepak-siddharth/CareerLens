import io
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from .auth import DB, CurrentUser
from .config import settings
from .embeddings import profile_vector
from .llm import ModelFailure, provider, resume_model_config
from .models import ExtractedSkill, Preference, Profile, Resume, User
from .observability import identify, span
from .schemas import Filters, ProfileData, Strict

router = APIRouter(tags=["profile and resumes"])
PDF_LIMIT = 5 * 1024 * 1024
TERM_LIST = (
    "Python",
    "PostgreSQL",
    "SQL",
    "FastAPI",
    "Java",
    "JavaScript",
    "TypeScript",
    "React",
    "Rust",
    "Go",
    "Kotlin",
    "Android",
    "Docker",
    "Kubernetes",
    "Terraform",
    "PyTorch",
    "TensorFlow",
    "Machine learning",
    "Linux",
    "SIEM",
    "Git",
    "Figma",
    "C++",
    "C#",
    "Redis",
    "AWS",
    "Azure",
)


class ProfileView(Strict):
    profile: ProfileData
    preferences: Filters


class ProfilePatch(Strict):
    profile: ProfileData | None = None
    preferences: Filters | None = None


class ResumeDraft(Strict):
    id: UUID
    draft: ProfileData
    confirmed: bool
    extraction_model: str | None = None


class ResumeExtraction(BaseModel):
    education: list[str]
    skills: list[str]
    projects: list[str]
    work_experience: list[str]
    tools: list[str]
    certifications: list[str]
    domains: list[str]
    experience_level: str | None
    years_experience: int | None


class ResumeExtractor(Strict):
    provider: str
    model: str | None
    configured: bool


class ResumeConfirm(Strict):
    profile: ProfileData = Field(description="User-reviewed profile details")


def read_pdf(raw):
    if len(raw) > PDF_LIMIT:
        raise HTTPException(413, "PDF exceeds the 5 MB limit")
    if not raw.startswith(b"%PDF-"):
        raise HTTPException(415, "Upload a PDF resume")
    try:
        document = PdfReader(io.BytesIO(raw), strict=True)
        if document.is_encrypted or not 1 <= len(document.pages) <= 10:
            raise ValueError
        content = "\n".join(page.extract_text() or "" for page in document.pages)
    except (PyPdfError, ValueError, KeyError, TypeError, OSError, RecursionError):
        raise HTTPException(422, "Upload a readable, unencrypted PDF of up to 10 pages") from None
    content = re.sub(r"[^\S\n]+", " ", content).strip()
    if not content or len(content) > 40_000:
        raise HTTPException(422, "PDF must contain selectable text within the supported size")
    return content


def extract(content):
    try:
        model = provider()
        if model:
            extracted = model.generate("resume-extraction", content, ResumeExtraction).model_dump()
            for field, value in extracted.items():
                if isinstance(value, list):
                    extracted[field] = merge_entries([], value)
            return ProfileData.model_validate(extracted)
    except (ModelFailure, ValueError):
        raise HTTPException(
            503, "Resume extraction failed. Try again or edit your profile directly."
        ) from None
    skills = [
        term
        for term in TERM_LIST
        if re.search(r"(?<![\w+#])" + re.escape(term) + r"(?![\w+#])", content, re.IGNORECASE)
    ]
    sections = {"education": [], "projects": [], "work_experience": [], "certifications": []}
    aliases = {
        "education": "education",
        "projects": "projects",
        "experience": "work_experience",
        "work experience": "work_experience",
        "certifications": "certifications",
    }
    current = None
    for line in content.splitlines():
        value = line.strip(" -•\t")
        key = value.rstrip(":").casefold()
        if key in aliases or key in {"skills", "summary", "contact", "tools"}:
            current = aliases.get(key)
        elif value and current and len(sections[current]) < 20:
            sections[current].append(value[:120])
    return ProfileData(skills=skills, **sections)


def merge_entries(saved, extracted):
    entries = {}
    for value in [*saved, *extracted]:
        if clean := value.strip()[:120].rstrip():
            entries.setdefault(clean.casefold(), clean)
    return list(entries.values())[:80]


def review_profile(current, extracted):
    values = current.model_dump()
    for name, value in extracted.model_dump().items():
        if isinstance(value, list):
            values[name] = merge_entries(values[name], value)
        elif value is not None:
            values[name] = value
    return ProfileData.model_validate(values)


@router.get("/resumes/extractor")
def resume_extractor(user: CurrentUser) -> ResumeExtractor:
    name, model, configured = resume_model_config()
    return ResumeExtractor(provider=name, model=model, configured=configured)


def get_profile(db, user_id):
    row, prefs = db.get(Profile, user_id), db.get(Preference, user_id)
    return ProfileView(
        profile=ProfileData.model_validate(row.details if row else {}),
        preferences=Filters.model_validate(prefs.values if prefs else {}),
    )


def store_profile(db, user_id, profile):
    data = profile.model_dump()
    vector = profile_vector(profile)
    values = {"details": data, "embedding": vector, "embedding_model": settings().embedding_model}
    query = insert(Profile).values(user_id=user_id, **values)
    db.execute(
        query.on_conflict_do_update(
            index_elements=["user_id"],
            set_={**values, "updated_at": datetime.now(UTC)},
        )
    )
    db.execute(delete(ExtractedSkill).where(ExtractedSkill.user_id == user_id))
    if skills := sorted({item.casefold() for item in profile.skills}):
        db.execute(insert(ExtractedSkill), [{"user_id": user_id, "skill": skill} for skill in skills])


def store_preferences(db, user_id, preferences):
    values = preferences.model_dump(exclude_none=True)
    query = insert(Preference).values(user_id=user_id, values=values)
    db.execute(query.on_conflict_do_update(index_elements=["user_id"], set_={"values": values}))


@router.get("/profile")
def profile(user: CurrentUser, db: DB) -> ProfileView:
    return get_profile(db, user.id)


@router.patch("/profile")
def update_profile(data: ProfilePatch, user: CurrentUser, db: DB) -> ProfileView:
    if data.profile is not None:
        store_profile(db, user.id, data.profile)
    if data.preferences is not None:
        db.scalar(select(User.id).where(User.id == user.id).with_for_update())
        store_preferences(db, user.id, data.preferences)
    db.commit()
    return get_profile(db, user.id)


@router.get("/resumes")
def drafts(user: CurrentUser, db: DB) -> list[ResumeDraft]:
    rows = db.scalars(
        select(Resume).where(Resume.user_id == user.id).order_by(Resume.created_at.desc()).limit(20)
    )
    return [ResumeDraft(id=row.id, draft=row.draft, confirmed=row.confirmed) for row in rows]


def resume_limit(request: Request):
    request.app.state.rate_limit(request, "resume", 8)


@router.post("/resumes/upload", status_code=201, dependencies=[Depends(resume_limit)])
def upload_resume(user: CurrentUser, db: DB, file: Annotated[UploadFile, File()]) -> ResumeDraft:
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(415, "Upload a PDF resume")
    raw = file.file.read(PDF_LIMIT + 1)
    with span("resume.extract") as observation, identify(observation, user.id, "resume"):
        extracted = extract(read_pdf(raw))
    db.execute(
        delete(Resume).where(
            Resume.user_id == user.id,
            Resume.confirmed.is_(False),
            Resume.created_at < datetime.now(UTC) - timedelta(days=7),
        )
    )
    draft = review_profile(get_profile(db, user.id).profile, extracted)
    resume = Resume(user_id=user.id, draft=draft.model_dump(), confirmed=False)
    db.add(resume)
    db.commit()
    return ResumeDraft(id=resume.id, draft=draft, confirmed=False, extraction_model=resume_model_config()[1])


def own_resume(db, user_id, resume_id):
    resume = db.scalar(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id).with_for_update()
    )
    if resume is None:
        raise HTTPException(404, "Resume not found")
    return resume


@router.post("/resumes/{resume_id}/confirm")
def confirm_resume(resume_id: UUID, data: ResumeConfirm, user: CurrentUser, db: DB) -> ProfileView:
    resume = own_resume(db, user.id, resume_id)
    store_profile(db, user.id, data.profile)
    resume.draft = data.profile.model_dump()
    resume.confirmed = True
    db.commit()
    return get_profile(db, user.id)


@router.delete("/resumes/{resume_id}", status_code=204)
def remove_resume(resume_id: UUID, user: CurrentUser, db: DB):
    db.delete(own_resume(db, user.id, resume_id))
    db.commit()
