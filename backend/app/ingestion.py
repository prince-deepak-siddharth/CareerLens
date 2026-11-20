import argparse
import hashlib
import json
import re
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import engine
from .embeddings import embed
from .models import IngestionRun, Job, JobEmbedding, JobSource
from .observability import span
from .schemas import JobImport

FIXTURES = [
    (
        "Applied AI Intern",
        "Lumen Research Lab",
        ["python", "pytorch"],
        "Build neural models and evaluate language systems.",
    ),
    (
        "Backend Engineering Intern",
        "Harbor Systems",
        ["python", "postgresql"],
        "Build FastAPI services and SQL-backed tools.",
    ),
    (
        "Frontend Engineer",
        "Pixel Workshop",
        ["react", "typescript"],
        "Design accessible interfaces and component systems.",
    ),
    (
        "Data Engineering Intern",
        "Delta Data Lab",
        ["python", "sql"],
        "Develop ETL pipelines and warehouse transformations.",
    ),
    (
        "Platform Engineer",
        "Northstar Platform",
        ["kubernetes", "docker"],
        "Operate container infrastructure and observability.",
    ),
    (
        "Security Analyst",
        "Bastion Labs",
        ["linux", "siem"],
        "Investigate alerts and improve defensive monitoring.",
    ),
    (
        "Mobile Engineer",
        "Orbit Devices",
        ["kotlin", "android"],
        "Build responsive native Android applications.",
    ),
    (
        "Machine Learning Engineer",
        "Tangent Research",
        ["python", "tensorflow"],
        "Train ranking models and build evaluation suites.",
    ),
    (
        "Product Design Intern",
        "Canvas Studio",
        ["figma", "research"],
        "Prototype and evaluate user workflows.",
    ),
    (
        "Database Engineer",
        "Quarry Data",
        ["postgresql", "sql"],
        "Tune queries, improve indexes and test transactional systems.",
    ),
]


RELATED_INTERNSHIPS = (
    (
        "Machine Learning Intern",
        "Vector Grove Demo",
        ["python", "scikit-learn"],
        "Train and evaluate predictive models with supervised learning.",
    ),
    (
        "AI Engineering Intern",
        "Signal Foundry Demo",
        ["python", "pytorch"],
        "Build and evaluate AI applications, model integrations, and machine learning pipelines.",
    ),
    (
        "Data Science Intern",
        "Bayleaf Analytics Demo",
        ["python", "sql"],
        "Analyze datasets, test statistical hypotheses, and prototype predictive models.",
    ),
)


def seed_rows(count, *, include_related=False):
    now = datetime.now(UTC)
    for index in range(count):
        title, company, skills, description = FIXTURES[index % len(FIXTURES)]
        cohort = index // len(FIXTURES)
        is_intern = title.endswith("Intern")
        yield {
            "external_id": f"demo-{index:05}",
            "title": title,
            "company": company if cohort == 0 else f"{company} Demo {cohort:03}",
            "description": f"{description} Synthetic CareerLens demo record.",
            "required_skills": skills,
            "preferred_skills": ["git"],
            "min_years": 0 if is_intern else 1 + cohort % 4,
            "employment_type": "internship" if is_intern else "full-time",
            "location": ["Global", "Bengaluru", "London"][cohort % 3],
            "remote_policy": ["remote", "hybrid", "onsite"][cohort % 3],
            "company_category": ["startup", "enterprise", "nonprofit"][cohort % 3],
            "posted_at": now - timedelta(days=cohort % 60),
            "updated_at": now,
        }

    if include_related:
        for index, (title, company, skills, description) in enumerate(RELATED_INTERNSHIPS):
            yield {
                "external_id": f"demo-related-internship-{index}",
                "title": title,
                "company": company,
                "description": description + " Synthetic CareerLens demo record.",
                "required_skills": skills,
                "min_years": 0,
                "employment_type": "internship",
                "location": "Global",
                "remote_policy": "remote",
                "company_category": "startup",
                "posted_at": now,
                "updated_at": now,
            }


def normalize(raw):
    clean = dict(raw)
    if isinstance(clean.get("description"), str):
        clean["description"] = re.sub(r"[\x00-\x08\x0b-\x1f]", " ", clean["description"])
    posting = JobImport.model_validate(clean)
    values = posting.model_dump()
    values["source_url"] = str(posting.source_url) if posting.source_url else None
    return values


def job_embedding_content(title, skills, description):
    content = " ".join([title, *skills, description])
    return content, hashlib.sha256(content.encode()).hexdigest()


def write_embeddings(db, pending, cache, model):
    uncached = {digest: content for content, digest in pending.values() if digest not in cache}
    if uncached:
        with span("job.embed", count=len(uncached), model=model):
            vectors = embed(list(uncached.values()))
        cache.update(zip(uncached, vectors, strict=True))
    rows = []
    for job_id, (_, digest) in pending.items():
        cache.move_to_end(digest)
        rows.append({"job_id": job_id, "model": model, "content_hash": digest, "value": cache[digest]})
    statement = insert(JobEmbedding).values(rows)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=["job_id"],
            set_={key: getattr(statement.excluded, key) for key in ("model", "content_hash", "value")},
        )
    )
    while len(cache) > 256:
        cache.popitem(last=False)


def reindex():
    model = settings().embedding_model
    cache = OrderedDict()
    updated = 0
    with Session(engine) as db:
        while rows := db.execute(
            select(Job.id, Job.title, Job.required_skills, Job.description)
            .outerjoin(JobEmbedding)
            .where(or_(JobEmbedding.job_id.is_(None), JobEmbedding.model != model))
            .order_by(Job.id)
            .limit(128)
            .with_for_update(of=Job)
        ).all():
            pending = {
                job_id: job_embedding_content(title, skills, description)
                for job_id, title, skills, description in rows
            }
            write_embeddings(db, pending, cache, model)
            db.commit()
            updated += len(rows)
    return {"model": model, "reindexed": updated}


def ingest(rows, *, name, provenance, synthetic=False):
    if not (0 < len(name.strip()) <= 120 and 0 < len(provenance.strip()) <= 500):
        raise ValueError("Supply a source name and provenance within their size limits")
    name, provenance = name.strip(), provenance.strip()
    cfg = settings()
    with Session(engine) as db, span("job.ingestion", synthetic=synthetic):
        source_stmt = insert(JobSource).values(name=name, provenance=provenance, synthetic=synthetic)
        db.execute(source_stmt.on_conflict_do_nothing(index_elements=["name"]))
        source = db.scalar(select(JobSource).where(JobSource.name == name))
        if source.synthetic != synthetic or source.provenance != provenance:
            raise ValueError("Existing source metadata differs")
        run = IngestionRun(source_id=source.id, status="running", stats={})
        db.add(run)
        db.commit()
        stats = {"indexed": 0, "embedded": 0, "duplicates": 0, "invalid": 0}
        remaining = iter(rows)
        cache = OrderedDict()
        try:
            while batch := list(islice(remaining, 128)):
                pending = {}
                for raw in batch:
                    try:
                        values = normalize(raw)
                    except (ValueError, TypeError):
                        stats["invalid"] += 1
                        continue
                    values["source_id"] = source.id
                    statement = insert(Job).values(values)
                    job_id = db.scalar(statement.on_conflict_do_nothing().returning(Job.id))
                    if job_id is None:
                        job = db.scalar(
                            select(Job)
                            .where(
                                Job.source_id == source.id,
                                Job.external_id == values["external_id"],
                            )
                            .with_for_update()
                        )
                        if job is None:
                            stats["duplicates"] += 1
                            continue
                        if values["updated_at"] >= job.updated_at:
                            if job.source_url != values["source_url"]:
                                try:
                                    with db.begin_nested():
                                        for key, value in values.items():
                                            setattr(job, key, value)
                                        db.flush()
                                except IntegrityError:
                                    stats["duplicates"] += 1
                                    continue
                            else:
                                for key, value in values.items():
                                    setattr(job, key, value)
                        else:
                            values = {key: getattr(job, key) for key in values}
                        job_id = job.id
                    content, digest = job_embedding_content(
                        values["title"],
                        values["required_skills"],
                        values["description"],
                    )
                    existing = db.get(JobEmbedding, job_id)
                    if (
                        existing is None
                        or existing.content_hash != digest
                        or existing.model != cfg.embedding_model
                    ):
                        pending[job_id] = (content, digest)
                    else:
                        pending.pop(job_id, None)
                    stats["indexed"] += 1
                if pending:
                    write_embeddings(db, pending, cache, cfg.embedding_model)
                    stats["embedded"] += len(pending)
                db.commit()
            run.status = "completed"
        except Exception:
            db.rollback()
            run.status = "failed"
            raise
        finally:
            run.stats = stats
            db.commit()
        return stats


def ingest_file(path, name, provenance):
    with Path(path).open() as file:

        def rows():
            for line in file:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    yield None

        return ingest(rows(), name=name, provenance=provenance)


def main():
    cli = argparse.ArgumentParser()
    sub = cli.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed")
    seed.add_argument("--count", type=int, default=5000)
    seed.add_argument("--include-related", action="store_true")
    sub.add_parser("reindex")
    imported = sub.add_parser("import")
    imported.add_argument("path")
    imported.add_argument("--source", required=True)
    imported.add_argument("--provenance", required=True)
    imported.add_argument("--background", action="store_true")
    args = cli.parse_args()
    if args.command == "seed":
        if not 1 <= args.count <= 10000:
            cli.error("--count must be between 1 and 10000")
        print(
            ingest(
                seed_rows(args.count, include_related=args.include_related),
                name="CareerLens demo",
                provenance="Built-in synthetic fixture",
                synthetic=True,
            )
        )
    elif args.command == "reindex":
        print(reindex())
    elif args.background:
        from .queueing import enqueue

        print(enqueue(ingest_file, str(Path(args.path).resolve()), args.source, args.provenance))
    else:
        print(ingest_file(args.path, args.source, args.provenance))


if __name__ == "__main__":
    main()
