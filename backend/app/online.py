"""Sample evidence checks asynchronously without forwarding user text or profile content."""

import hashlib
import random
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from .config import settings
from .db import engine
from .models import Job
from .observability import telemetry
from .queueing import enqueue


def evidence_hash(title, company, evidence):
    return hashlib.sha256(f"{title}\0{company}\0{evidence}".encode()).hexdigest()


def check_trace(trace_id, items):
    with Session(engine) as db:
        supported = True
        for identifier, digest in items:
            job = db.get(Job, UUID(identifier))
            supported = (
                supported
                and job is not None
                and evidence_hash(
                    job.title,
                    job.company,
                    job.description[:420],
                )
                == digest
            )
    if telemetry.client and trace_id:
        telemetry.call(
            lambda: telemetry.client.create_score(
                trace_id=trace_id,
                name="sampled_source_consistency",
                value=float(supported),
            )
        )
    return supported


def sample(reply):
    if not reply.trace_id or not reply.recommendations or random.random() >= settings().online_eval_rate:
        return
    items = [
        (str(item.job.id), evidence_hash(item.job.title, item.job.company, item.evidence))
        for item in reply.recommendations
    ]
    try:
        enqueue(check_trace, reply.trace_id, items)
    except RedisError:
        return
