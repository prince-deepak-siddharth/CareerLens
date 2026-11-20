"""Fetch published postings through supported job-board endpoints."""

import hashlib
import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

from pydantic import HttpUrl, ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from .config import settings
from .greenhouse import EMPLOYMENT, sync_board
from .ingestion import ingest
from .observability import span, telemetry
from .queueing import redis
from .schemas import JobImport, Search, SearchResults, Strict

HIMALAYAS_API = "https://himalayas.app/jobs/api/search"
HIMALAYAS_EMPLOYMENT = {
    "internship": "Intern",
    "full-time": "Full Time",
    "part-time": "Part Time",
    "contract": "Contractor",
}
ROLE_TERMS = {
    "machine learning": ("machine learning", "ml", "pytorch", "tensorflow"),
    "ai engineer": ("ai", "artificial intelligence", "llm"),
    "data science": ("data science", "data scientist", "analytics"),
    "data engineer": ("data engineering", "data engineer", "etl"),
    "software engineer": ("software", "backend", "frontend", "full stack", "developer", "programmer"),
    "devops": ("devops", "cloud engineer", "kubernetes", "sre"),
    "security": ("security", "cybersecurity"),
    "design": ("design", "figma", "ux", "ui"),
    "product": ("product manager", "product management"),
    "marketing": ("marketing",),
}


class LiveSearch(Search):
    include_interndoor: bool = False
    interndoor_only: bool = False


class ExternalLink(Strict):
    label: str
    url: HttpUrl


class SourceCheck(Strict):
    name: str
    status: Literal["checked", "cached", "refreshing", "unavailable"]
    fetched_count: int = 0


class LiveSearchResults(SearchResults):
    sources: list[SourceCheck]
    links: list[ExternalLink]
    interndoor_only: bool = False


def public_role(query):
    return next(
        (
            role
            for role, terms in ROLE_TERMS.items()
            if any(re.search(r"\b" + re.escape(term) + r"\b", query, re.IGNORECASE) for term in terms)
        ),
        "",
    )


def interndoor_link(request):
    terms = [
        public_role(request.query),
        request.filters.location
        if request.filters.location
        in {
            "bengaluru",
            "mumbai",
            "london",
            "india",
            "delhi",
        }
        else None,
        "internship" if request.filters.employment_type == "internship" else None,
    ]
    query = " ".join(term for term in terms if term)
    return ExternalLink(label="Search InternDoor", url="https://interndoor.com/?" + urlencode({"q": query}))


def timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, UTC)
    raise ValueError("Missing timestamp in provider listing")


def normalize_himalayas(item):
    published = timestamp(item["pubDate"])
    expires = timestamp(item["expiryDate"])
    application = item["applicationLink"]
    url = urlsplit(application)
    if url.scheme != "https" or url.netloc.lower() != "himalayas.app":
        raise ValueError("Unexpected Himalayas application URL")
    locations = item.get("locationRestrictions") or []
    employment = EMPLOYMENT.get(item.get("employmentType", "").casefold())
    if item.get("employmentType") == "Contractor":
        employment = "contract"
    return JobImport(
        external_id=hashlib.sha256(item["guid"].encode()).hexdigest(),
        title=item["title"],
        company=item["companyName"],
        description=(item.get("excerpt") or "See the source posting for role details.")[:2000],
        required_skills=[],
        employment_type=employment,
        remote_policy="remote",
        location=", ".join(locations)[:160] if locations else None,
        posted_at=published,
        updated_at=published,
        expires_at=expires,
        source_url=application,
    )


def fetch_himalayas(params):
    url = HIMALAYAS_API + "?" + urlencode(params)
    with urlopen(url, timeout=5) as response:
        payload = json.loads(response.read(2 * 1024 * 1024))
    jobs = payload["jobs"]
    if not isinstance(jobs, list):
        raise TypeError("Invalid Himalayas response")
    rows = []
    for item in jobs[:20]:
        try:
            job = normalize_himalayas(item)
            if job.expires_at > datetime.now(UTC):
                rows.append(job.model_dump())
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError, ValidationError):
            continue
    if rows:
        return ingest(rows, name="Himalayas", provenance="https://himalayas.app")["indexed"]
    return 0


def cached_check(name, key, fetch: Callable[[], int], cache_seconds):
    status_key = "careerlens:job-feed:v1:" + key
    cache = redis()
    with span("jobs.public_feed", source=name) as observation:
        try:
            if (count := cache.get(status_key)) is not None:
                return SourceCheck(name=name, status="cached", fetched_count=int(count))
            lock = cache.lock(status_key + ":lock", timeout=90)
            if not lock.acquire(blocking=False):
                return SourceCheck(name=name, status="refreshing")
            try:
                if (count := cache.get(status_key)) is not None:
                    return SourceCheck(name=name, status="cached", fetched_count=int(count))
                count = fetch()
                cache.setex(status_key, cache_seconds, count)
                return SourceCheck(name=name, status="checked", fetched_count=count)
            finally:
                lock.release()
        except (OSError, KeyError, TypeError, ValueError, RedisError, SQLAlchemyError) as exc:
            if observation:
                error = type(exc).__name__
                telemetry.call(lambda: observation.update(level="WARNING", status_message=error))
            return SourceCheck(name=name, status="unavailable")


def check_public_sources(request):
    config = settings()
    boards = tuple(
        dict.fromkeys(board.strip() for board in config.greenhouse_boards.split(",") if board.strip())
    )
    checks = [
        (
            f"Greenhouse · {board}",
            f"greenhouse:{board}",
            lambda board=board: sync_board(board, timeout=5)["indexed"],
            config.live_feed_cache_seconds,
        )
        for board in boards[:5]
    ]
    if config.himalayas_search_enabled and request.filters.remote_policy in (None, "remote"):
        params = {"sort": "recent"}
        if role := public_role(request.query):
            params["q"] = role
        if employment := HIMALAYAS_EMPLOYMENT.get(request.filters.employment_type):
            params["employment_type"] = employment
        key = hashlib.sha256(urlencode(params).encode()).hexdigest()
        checks.append(
            (
                "Himalayas",
                "himalayas:" + key,
                lambda: fetch_himalayas(params),
                86400,
            )
        )
    if not checks:
        return []
    with ThreadPoolExecutor(max_workers=min(len(checks), 4)) as executor:
        futures = [executor.submit(copy_context().run, cached_check, *entry) for entry in checks]
        return [future.result() for future in futures]
