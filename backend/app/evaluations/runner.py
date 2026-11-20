import argparse
import json
import os
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import Field, field_validator
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from ..agent import AgentRequest, run_turn
from ..config import settings, validate_rank_weights
from ..db import engine
from ..models import EvaluationDataset, EvaluationResult, EvaluationRun, Job, JobSource, PromptVersion, User
from ..observability import telemetry
from ..profiles import extract, get_profile, store_profile
from ..retrieval import eligibility, match, retrieve
from ..schemas import Filters, ProfileData, Search, Strict
from .judges import DeepEvalJudge
from .metrics import latency, ratio, retrieval_scores

Dataset = Literal[
    "agent-single-turn",
    "agent-multiturn",
    "prompt-injection",
    "retrieval",
    "resume-extraction",
    "job-explanations",
]
DATASETS = (
    "agent-single-turn",
    "agent-multiturn",
    "prompt-injection",
    "retrieval",
    "resume-extraction",
    "job-explanations",
)
MODES = ["keyword", "semantic", "hybrid", "reranked", "personalized"]
ROOT = Path(os.environ.get("CAREERLENS_DATASETS_DIR", Path(__file__).parents[3] / "evaluation" / "datasets"))


class RunConfig(Strict):
    dataset: Dataset
    label: str = Field("baseline", min_length=1, max_length=80)
    judge_model: str | None = Field(None, max_length=80)
    rank_weights: dict[str, float] | None = None
    prompt_label: str | None = Field(None, max_length=80)

    check_rank_weights = field_validator("rank_weights")(validate_rank_weights)


def dataset_file(dataset):
    return ROOT / f"careerlens-{dataset}-v1.json"


@lru_cache
def sandbox_engine():
    url = settings().evaluation_database_url
    if not url:
        raise ValueError("Set CAREERLENS_EVALUATION_DATABASE_URL to an isolated, seeded PostgreSQL database")
    if make_url(url).database == engine.url.database:
        raise ValueError("Evaluation database must have a different name from the application database")
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})


@contextmanager
def candidate(db, initial):
    user = User(email=f"{uuid4().hex}@evaluation.invalid", password_hash="disabled")
    db.add(user)
    db.flush()
    user_id = user.id
    if initial:
        store_profile(db, user_id, ProfileData.model_validate(initial))
    db.commit()
    try:
        yield user_id
    finally:
        db.rollback()
        db.delete(db.get(User, user_id))
        db.commit()


def agent_case(db, case, judge_model=None):
    counts = Counter()
    transcript = []
    conversation = None
    previous_jobs = []
    with candidate(db, case["initial"]) as user_id:
        for turn in case["turns"]:
            started = perf_counter()
            request = AgentRequest(
                message=turn["message"],
                conversation_id=None if turn.get("new_conversation") else conversation,
            )
            if turn.get("unauthorized"):
                with candidate(db, {}) as other:
                    try:
                        run_turn(db, other, request, allow_live_search=False)
                        ok = False
                    except HTTPException as exc:
                        ok = exc.status_code == 404
                transcript.append(
                    {
                        "passed": ok,
                        "authorization_check": True,
                        "latency_ms": (perf_counter() - started) * 1000,
                    }
                )
                continue
            failures = frozenset([turn["fail_tool"]]) if turn.get("fail_tool") else frozenset()
            try:
                reply = run_turn(db, user_id, request, failures=failures, allow_live_search=False)
            except Exception as exc:  # noqa: BLE001 - failed case is measured
                db.rollback()
                transcript.append(
                    {
                        "passed": False,
                        "error": type(exc).__name__,
                        "latency_ms": (perf_counter() - started) * 1000,
                    }
                )
                continue
            conversation = reply.conversation_id
            execution = [item.model_dump() for item in reply.trajectory]
            status = turn.get("expected_status", "success")
            required = set(turn["required_tools"])
            called = {item["name"] for item in execution if item["status"] == status}
            forbidden = set(case["forbidden_tools"]) & {item["name"] for item in execution}
            trajectory_ok = (
                required <= called and not forbidden and (not turn.get("no_tools") or not execution)
            )
            counts["tools"] += len(execution)
            counts["valid"] += sum(item["valid"] for item in execution)
            counts["successful"] += sum(item["status"] == "success" for item in execution)
            counts["trajectory"] += trajectory_ok
            counts["tool_selection"] += required <= {item["name"] for item in execution} and not forbidden
            jobs = [item.job for item in reply.recommendations] + reply.jobs
            ids = [job.id for job in jobs]
            stored = dict(db.execute(select(Job.id, Job).where(Job.id.in_(ids))).all()) if ids else {}
            grounded = all(
                job.id in stored
                and all(
                    getattr(job, field) == getattr(stored[job.id], field)
                    for field in ("title", "company", "description", "salary_min", "salary_max")
                )
                for job in jobs
            )
            counts["unsupported"] += not grounded
            filters = reply.filters.model_dump(exclude_none=True) if reply.filters else {}
            prefs = get_profile(db, user_id).preferences.model_dump(exclude_none=True)
            state_ok = all(filters.get(k) == v for k, v in turn.get("filters", {}).items())
            memory_ok = all(prefs.get(k) == v for k, v in turn.get("preferences", {}).items())
            if "filters" in turn:
                counts["state_checks"] += 1
                counts["state_ok"] += state_ok
            if "preferences" in turn:
                counts["memory_checks"] += 1
                counts["memory_ok"] += memory_ok
            criteria = [
                trajectory_ok,
                grounded,
                state_ok,
                memory_ok,
                turn.get("contains", "").casefold() in reply.message.casefold(),
                "has_jobs" not in turn or bool(jobs) == turn["has_jobs"],
                not turn.get("forbidden_jobs") or not jobs,
            ]
            if position := turn.get("previous_position"):
                criteria.append(len(previous_jobs) >= position and ids == [previous_jobs[position - 1]])
            if reply.filters is not None:
                previous_jobs = ids
            transcript.append(
                {
                    "passed": all(criteria),
                    "tools": execution,
                    "message": reply.message,
                    "job_ids": [str(key) for key in ids],
                    "filters": filters,
                    "grounded": grounded,
                    "latency_ms": (perf_counter() - started) * 1000,
                    "trace_id": reply.trace_id,
                    "model_usage": reply.model_usage,
                    "prompt_versions": reply.prompt_versions,
                }
            )
            if judge_model and jobs and grounded:
                transcript[-1]["judge"] = DeepEvalJudge(judge_model).evaluate(
                    turn["message"],
                    reply.message + "\n" + "\n".join(f"{job.title} at {job.company}" for job in jobs),
                    [f"{job.title} at {job.company}: {job.description}" for job in jobs],
                )
            if telemetry.client and reply.trace_id:
                for score, value in {
                    "task_success": all(criteria),
                    "tool_selection": required <= called,
                    "groundedness": grounded,
                    "structured_output_validity": True,
                    "hallucination_violation": not grounded,
                }.items():
                    telemetry.call(
                        lambda score=score, value=value, trace_id=reply.trace_id: (
                            telemetry.client.create_score(
                                trace_id=trace_id,
                                name=score,
                                value=float(value),
                            )
                        )
                    )
    return {"passed": all(item["passed"] for item in transcript), "turns": transcript, "counts": counts}


def retrieval_case(db, case, weights):
    filters = Filters.model_validate(case["filters"])
    if "judgments" in case:
        grades = case["judgments"]
    else:
        eligible = db.scalars(
            select(Job.external_id)
            .join(JobSource)
            .where(
                JobSource.synthetic.is_(True),
                Job.external_id.startswith("demo-"),
                *eligibility(filters, datetime.now(UTC)),
            )
        )
        grades = {
            key: case["fixture_role_grades"][str(int(key[5:]) % 10)]
            for key in eligible
            if str(int(key[5:]) % 10) in case["fixture_role_grades"]
        }
    output = {}
    for mode in MODES:
        if mode == "reranked" and not settings().cross_encoder:
            output[mode] = {"status": "unavailable"}
            continue
        search = Search(query=case["query"], filters=filters, mode=mode, limit=10)
        if mode == "personalized" and case.get("profile"):
            with candidate(db, case["profile"]) as user_id:
                started = perf_counter()
                result = retrieve(db, search, user_id, weights=weights)
                duration = (perf_counter() - started) * 1000
        else:
            started = perf_counter()
            result = retrieve(db, search, weights=weights)
            duration = (perf_counter() - started) * 1000
        ids = [item.job.external_id for item in result.items]
        output[mode] = {"latency_ms": duration, "retrieved_ids": ids, **retrieval_scores(ids, grades)}
    return output


def quality_case(db, dataset, case, judge_model):
    if dataset == "resume-extraction":
        actual = extract(case["text"])
        expected, predicted = set(case["skills"]), set(actual.skills)
        true_positives = len(expected & predicted)
        precision = true_positives / len(predicted) if predicted else float(not expected)
        recall = true_positives / len(expected) if expected else float(not predicted)
        return {
            "skill_precision": precision,
            "skill_recall": recall,
            "skill_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0,
            "education_correct": actual.education == case["education"],
            "experience_correct": actual.work_experience == case["work_experience"],
            "structured_output_validity": 1.0,
            "unsupported_skill_rate": 1 - precision,
        }
    job, source = db.execute(
        select(Job, JobSource)
        .join(JobSource)
        .where(
            Job.external_id == case["external_id"],
            JobSource.synthetic.is_(True),
        )
    ).one()
    result = match(job, source, ProfileData(skills=case["skills"]), 0, {}, 0.5, datetime.now(UTC))
    supported = result.evidence in job.description and result.matched_skills == case["matched"]
    outcome = {
        "groundedness": float(supported),
        "hallucination_violation": float(not supported),
        "structured_output_validity": 1.0,
        "completeness": float(set(result.matched_skills + result.missing_skills) == set(job.required_skills)),
        "answer": result.explanation,
    }
    if judge_model:
        outcome["judge"] = DeepEvalJudge(judge_model).evaluate(
            "Explain the job fit for this candidate",
            result.explanation,
            [
                job.description,
                "Required skills: " + ", ".join(job.required_skills),
                "Confirmed candidate skills: " + ", ".join(case["skills"]),
            ],
        )
    return outcome


def aggregate(config, records):
    if config.dataset == "retrieval":
        result = {}
        for mode in MODES:
            rows = [value[mode] for value in records]
            if rows and rows[0].get("status") == "unavailable":
                result[mode] = {"status": "configure a cross-encoder"}
                continue
            result[mode] = {
                **numeric_means(rows, omit={"latency_ms"}),
                **latency([row["latency_ms"] for row in rows]),
            }
        return result
    if config.dataset.startswith("agent") or config.dataset == "prompt-injection":
        counts = sum((Counter(row["counts"]) for row in records), Counter())
        turns = [turn for row in records for turn in row["turns"]]
        measured_turns = sum("tools" in turn for turn in turns)
        usage = [turn["model_usage"] for turn in turns if turn.get("model_usage", {}).get("model_calls")]
        costs = [item["estimated_cost_usd"] for item in usage if item["estimated_cost_usd"] is not None]
        return {
            **judge_summary(turns),
            "task_completion_rate": ratio(sum(row["passed"] for row in records), len(records)),
            "tool_selection_accuracy": ratio(counts["tool_selection"], measured_turns),
            "tool_argument_validity": ratio(counts["valid"], counts["tools"]),
            "tool_execution_success_rate": ratio(counts["successful"], counts["valid"]),
            "trajectory_correctness": ratio(counts["trajectory"], measured_turns),
            "memory_accuracy": ratio(counts["memory_ok"], counts["memory_checks"]),
            "state_consistency": ratio(counts["state_ok"], counts["state_checks"]),
            "hallucination_violation_rate": ratio(counts["unsupported"], measured_turns),
            "average_tool_calls_per_task": ratio(counts["tools"], len(records)),
            "failed_turns": sum(not turn["passed"] for turn in turns),
            "cost_per_task": ratio(sum(costs), len(records)) if costs and len(costs) == len(usage) else None,
            "model_calls": sum(item["model_calls"] for item in usage),
            "input_tokens": sum(item["input_tokens"] or 0 for item in usage) if usage else None,
            "output_tokens": sum(item["output_tokens"] or 0 for item in usage) if usage else None,
            **latency([turn["latency_ms"] for turn in turns]),
        }
    return {**numeric_means(records), **judge_summary(records)}


def judge_summary(records):
    summary = {}
    judges = [row["judge"] for row in records if "judge" in row]
    if judges:
        for metric in judges[0]:
            values = [item[metric]["score"] for item in judges if item[metric]["score"] is not None]
            summary[metric] = mean(values) if values else None
        summary["judge_error_count"] = sum(
            result.get("review_required", False) for row in judges for result in row.values()
        )
    return summary


def numeric_means(rows, omit=()):
    return {
        key: mean(values)
        for key in sorted(set().union(*(row.keys() for row in rows)) - set(omit))
        if (values := [row.get(key) for row in rows if isinstance(row.get(key), (int, float))])
    }


def create_run(db, config):
    from ..llm import agent_model_config, resume_model_config

    if config.dataset == "resume-extraction":
        model_provider, model_name, _ = resume_model_config()
    else:
        model_provider, model_name = agent_model_config()
    name = f"careerlens-{config.dataset}-v1"
    statement = insert(EvaluationDataset).values(
        name=name, metadata_json={"provenance": "synthetic fixtures"}
    )
    db.execute(statement.on_conflict_do_nothing(index_elements=["name"]))
    dataset_id = db.scalar(select(EvaluationDataset.id).where(EvaluationDataset.name == name))
    cfg = settings()
    run = EvaluationRun(
        dataset_id=dataset_id,
        status="queued",
        configuration={
            **config.model_dump(),
            "commit": os.getenv("CAREERLENS_CODE_COMMIT", "unrecorded"),
            "provider": model_provider,
            "model": model_name,
            "embedding_model": cfg.embedding_model,
            "cross_encoder": cfg.cross_encoder or None,
            "rank_weights": config.rank_weights or cfg.rank_weights,
            "prompt_label": config.prompt_label or cfg.prompt_label,
            "evaluator_version": "1",
        },
        metrics={},
    )
    db.add(run)
    db.commit()
    return run


def execute_run(run_id):
    from ..llm import experiment_prompt_label

    with Session(engine) as application:
        run = application.get(EvaluationRun, UUID(str(run_id)))
        if run is None:
            raise ValueError("Unknown evaluation run")
        if run.status == "completed":
            return run.metrics
        run.status = "running"
        application.commit()
        config = RunConfig.model_validate(
            {key: value for key, value in run.configuration.items() if key in RunConfig.model_fields}
        )
        reset = experiment_prompt_label.set(config.prompt_label)
        try:
            cases = json.loads(dataset_file(config.dataset).read_text())
            reports = []
            with Session(sandbox_engine()) as sandbox:
                for case in cases:
                    if config.dataset == "retrieval":
                        result = retrieval_case(sandbox, case, config.rank_weights)
                    elif config.dataset.startswith("agent") or config.dataset == "prompt-injection":
                        result = agent_case(sandbox, case, config.judge_model)
                    else:
                        result = quality_case(sandbox, config.dataset, case, config.judge_model)
                    statement = insert(EvaluationResult).values(
                        run_id=run.id, case_id=case["id"], outcome=result
                    )
                    application.execute(
                        statement.on_conflict_do_update(
                            index_elements=["run_id", "case_id"],
                            set_={"outcome": result},
                        )
                    )
                    reports.append(result)
            versions = {
                (name, version)
                for outcome in reports
                for turn in outcome.get("turns", [])
                for name, version in turn.get("prompt_versions", {}).items()
            }
            for name, version in versions:
                application.execute(
                    insert(PromptVersion)
                    .values(name=name, version=version)
                    .on_conflict_do_nothing(index_elements=["name", "version"])
                )
            run.configuration = {
                **run.configuration,
                "observed_prompt_versions": [
                    {"name": name, "version": version} for name, version in sorted(versions)
                ],
            }
            run.metrics = {"case_count": len(reports), **aggregate(config, reports)}
            run.status = "completed"
        except Exception as exc:
            application.rollback()
            run.status = "failed"
            run.metrics = {"error": type(exc).__name__}
            raise
        finally:
            experiment_prompt_label.reset(reset)
            run.finished_at = datetime.now(UTC)
            application.commit()
        return run.metrics


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("dataset", choices=DATASETS)
    cli.add_argument("--label", default="baseline")
    cli.add_argument("--judge-model")
    cli.add_argument("--prompt-label")
    cli.add_argument("--weights", type=json.loads)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    sandbox_engine()
    config = RunConfig(
        dataset=args.dataset,
        label=args.label,
        judge_model=args.judge_model,
        rank_weights=args.weights,
        prompt_label=args.prompt_label,
    )
    with Session(engine) as db:
        run = create_run(db, config)
        identifier = run.id
    metrics = execute_run(identifier)
    report = json.dumps({"run_id": str(identifier), "dataset": config.dataset, "metrics": metrics}, indent=2)
    print(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n")


if __name__ == "__main__":
    main()
