from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import Field
from redis.exceptions import RedisError
from sqlalchemy import select

from ..auth import DB, Admin
from ..models import EvaluationDataset, EvaluationResult, EvaluationRun, Feedback
from ..queueing import enqueue
from ..schemas import Strict
from .runner import RunConfig, create_run, execute_run, sandbox_engine

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


def summary(run, dataset):
    return {
        "id": run.id,
        "dataset": dataset,
        "status": run.status,
        "configuration": run.configuration,
        "metrics": run.metrics,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
    }


@router.post("/runs", status_code=202)
def start(config: RunConfig, admin: Admin, db: DB):
    try:
        sandbox_engine()
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from None
    run = create_run(db, config)
    try:
        job = enqueue(execute_run, str(run.id))
    except RedisError:
        run.status = "failed"
        run.metrics = {"error": "queue_unavailable"}
        db.commit()
        raise HTTPException(503, "Evaluation queue unavailable") from None
    return {"run_id": run.id, "job_id": job}


@router.get("/runs")
def runs(admin: Admin, db: DB):
    rows = db.execute(
        select(EvaluationRun, EvaluationDataset.name)
        .join(EvaluationDataset)
        .order_by(EvaluationRun.created_at.desc())
        .limit(100)
    )
    return [summary(*row) for row in rows]


@router.get("/runs/{run_id}")
def run_detail(run_id: UUID, admin: Admin, db: DB):
    row = db.execute(
        select(EvaluationRun, EvaluationDataset.name)
        .join(EvaluationDataset)
        .where(EvaluationRun.id == run_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "Evaluation run not found")
    results = db.scalars(
        select(EvaluationResult).where(EvaluationResult.run_id == run_id).order_by(EvaluationResult.case_id)
    )
    return {**summary(*row), "results": [{"case_id": item.case_id, **item.outcome} for item in results]}


@router.get("/metrics")
@router.get("/experiments")
def experiments(admin: Admin, db: DB):
    return [row for row in runs(admin, db) if row["status"] == "completed"]


@router.get("/feedback")
def review_feedback(admin: Admin, db: DB):
    rows = db.scalars(
        select(Feedback)
        .where(Feedback.kind.in_(("unhelpful", "incorrect", "unsupported")))
        .order_by(Feedback.created_at.desc())
        .limit(100)
    )
    return [
        {
            "id": row.id,
            "job_id": row.job_id,
            "kind": row.kind,
            "trace_id": row.trace_id,
            "ranking_version": row.ranking_version,
            "created_at": row.created_at,
        }
        for row in rows
    ]


class CaseReview(Strict):
    verdict: Literal["supported", "unsupported", "abstain"]
    rationale: str = Field(min_length=4, max_length=1000)


@router.post("/runs/{run_id}/cases/{case_id}/review", status_code=201)
def annotate(run_id: UUID, case_id: str, data: CaseReview, admin: Admin, db: DB):
    row = db.scalar(
        select(EvaluationResult)
        .join(EvaluationRun)
        .where(
            EvaluationResult.run_id == run_id,
            EvaluationResult.case_id == case_id,
            EvaluationRun.status == "completed",
        )
        .with_for_update(of=EvaluationResult)
    )
    if row is None:
        raise HTTPException(404, "Completed evaluation case not found")
    history = row.outcome.get("human_reviews", [])
    review = {**data.model_dump(), "reviewer_id": str(admin.id), "at": datetime.now(UTC).isoformat()}
    if len(history) >= 100:
        raise HTTPException(409, "Review history limit reached")
    row.outcome = {**row.outcome, "human_reviews": [*history, review]}
    db.commit()
    return review
