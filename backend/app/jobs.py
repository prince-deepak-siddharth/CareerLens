from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from .auth import DB, CurrentUser
from .models import Feedback, Interaction, Job, JobSource, SavedJob
from .observability import identify, span, telemetry
from .retrieval import eligibility, job_details, job_view, ranking_version, retrieve
from .schemas import Filters, JobView, Search, SearchResults, Strict

router = APIRouter(tags=["jobs and recommendations"])


class FeedbackInput(Strict):
    job_id: UUID
    kind: Literal["helpful", "unhelpful", "incorrect", "unsupported", "relevant", "dismiss"]
    comment: str | None = Field(None, max_length=1000)


def find_job(db, job_id):
    row = job_details(db, job_id)
    if row is None:
        raise HTTPException(404, "Job not found")
    return row


def save(db, user_id, job_id):
    find_job(db, job_id)
    db.execute(insert(SavedJob).values(user_id=user_id, job_id=job_id).on_conflict_do_nothing())


def saved(db, user_id):
    rows = db.execute(
        select(Job, JobSource)
        .join(JobSource)
        .join(SavedJob)
        .where(
            SavedJob.user_id == user_id,
        )
        .order_by(SavedJob.created_at.desc())
        .limit(100)
    )
    return [job_view(*row) for row in rows]


def search_for_user(db, user_id, query, trace_id=None):
    result = retrieve(db, query, user_id)
    db.add(
        Interaction(
            user_id=user_id,
            kind="search",
            details={
                "search": query.model_dump(mode="json"),
                "ranking_version": result.ranking_version,
                "job_ids": [str(item.job.id) for item in result.items],
                "trace_id": trace_id,
            },
        )
    )
    return result


@router.get("/jobs")
def list_jobs(user: CurrentUser, db: DB, limit: int = Query(12, ge=1, le=50)) -> SearchResults:
    return retrieve(db, Search(mode="hybrid", limit=limit), user.id)


@router.post("/jobs/search")
def search(data: Search, user: CurrentUser, db: DB) -> SearchResults:
    with span("jobs.search") as observation, identify(observation, user.id, "search") as trace_id:
        result = search_for_user(db, user.id, data, trace_id)
        db.commit()
    return result


class CatalogSource(Strict):
    name: str
    provenance: str
    synthetic: bool
    total: int


class CatalogPage(Strict):
    items: list[JobView]
    sources: list[CatalogSource]
    total: int
    page: int
    page_size: int


@router.get("/jobs/catalog")
def catalog(
    user: CurrentUser,
    db: DB,
    source: Literal["live", "demo"] | None = None,
    page: int = Query(1, ge=1, le=1_000_000),
    page_size: int = Query(24, ge=1, le=50),
) -> CatalogPage:
    now = datetime.now(UTC)
    counts = db.execute(
        select(JobSource.name, JobSource.provenance, JobSource.synthetic, func.count(Job.id))
        .join(Job, Job.source_id == JobSource.id)
        .where(*eligibility(Filters(), now))
        .group_by(JobSource.id)
        .order_by(JobSource.synthetic, JobSource.name)
    )
    sources = [
        CatalogSource(name=name, provenance=provenance, synthetic=synthetic, total=total)
        for name, provenance, synthetic, total in counts
    ]
    total = sum(item.total for item in sources if source is None or item.synthetic == (source == "demo"))
    rows = db.execute(
        select(Job, JobSource)
        .join(JobSource)
        .where(*eligibility(Filters(source=source), now))
        .order_by(JobSource.synthetic, Job.posted_at.desc().nulls_last(), Job.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return CatalogPage(
        items=[job_view(*row) for row in rows], sources=sources, total=total, page=page, page_size=page_size
    )


@router.get("/jobs/saved")
def list_saved(user: CurrentUser, db: DB) -> list[JobView]:
    return saved(db, user.id)


@router.get("/jobs/{job_id}")
def details(job_id: UUID, user: CurrentUser, db: DB) -> JobView:
    return job_view(*find_job(db, job_id))


@router.post("/jobs/{job_id}/save", status_code=204)
def save_job(job_id: UUID, user: CurrentUser, db: DB):
    save(db, user.id, job_id)
    db.commit()


@router.delete("/jobs/{job_id}/save", status_code=204)
def unsave_job(job_id: UUID, user: CurrentUser, db: DB):
    db.execute(delete(SavedJob).where(SavedJob.user_id == user.id, SavedJob.job_id == job_id))
    db.commit()


@router.get("/recommendations")
def recommendations(user: CurrentUser, db: DB) -> SearchResults:
    with (
        span("recommendations.list") as observation,
        identify(observation, user.id, "recommendations") as trace_id,
    ):
        result = search_for_user(db, user.id, Search(), trace_id)
        db.commit()
    return result


@router.post("/recommendations/feedback", status_code=201)
def feedback(data: FeedbackInput, user: CurrentUser, db: DB):
    find_job(db, data.job_id)
    previous = db.scalar(
        select(Interaction)
        .where(
            Interaction.user_id == user.id,
            Interaction.kind == "search",
            Interaction.details["job_ids"].contains([str(data.job_id)]),
        )
        .order_by(Interaction.created_at.desc())
        .limit(1)
    )
    context = previous.details if previous else {}
    version = context.get("ranking_version", ranking_version())
    trace_id = context.get("trace_id")
    db.add(
        Feedback(
            user_id=user.id,
            job_id=data.job_id,
            kind=data.kind,
            comment=data.comment,
            ranking_version=version,
            trace_id=trace_id,
        )
    )
    db.commit()
    if telemetry.client and trace_id and data.kind in {"helpful", "unhelpful", "unsupported", "relevant"}:
        telemetry.call(
            lambda: telemetry.client.create_score(
                trace_id=trace_id,
                name="user_feedback",
                value=float(data.kind in {"helpful", "relevant"}),
            )
        )
    return {"status": "recorded"}


@router.delete("/auth/me", status_code=204)
def erase_account(user: CurrentUser, db: DB):
    db.delete(user)
    db.commit()
