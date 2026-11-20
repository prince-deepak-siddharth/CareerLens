"""Import published listings from employers' public Greenhouse Job Board API."""

import argparse
import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.error import URLError
from urllib.request import urlopen

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .db import engine
from .ingestion import ingest
from .models import Job, JobSource
from .schemas import JobImport

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"
MAX_FEED_BYTES = 32 * 1024 * 1024
EMPLOYMENT = {
    "full time": "full-time",
    "part time": "part-time",
    "intern": "internship",
    "internship": "internship",
    "contract": "contract",
}


class PostingText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden.append(tag)
        if tag in {"p", "div", "li", "br", "h1", "h2", "h3", "tr"} and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()
        if tag in {"p", "div", "li", "tr"} and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def normalize_posting(item):
    html = item.get("content") or ""
    parser = PostingText()
    parser.feed(unescape(html) if html.lstrip().startswith(("&lt;", "&#")) else html)
    description = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
    metadata = {entry["name"].casefold(): entry.get("value") for entry in item.get("metadata") or []}
    time_type = next(
        (
            metadata[key]
            for key in ("employment type", "time type", "req type")
            if isinstance(metadata.get(key), str)
        ),
        "",
    )
    title = item["title"]
    employment = EMPLOYMENT.get(re.sub(r"[-_]", " ", time_type.strip().casefold()))
    if employment is None and re.search(r"\b(?:intern|internship)\b", title, flags=re.IGNORECASE):
        employment = "internship"
    location = (item.get("location") or {}).get("name")
    remote = (
        "remote"
        if location
        and (
            location.casefold() == "remote"
            or re.fullmatch(r"Remote\s*[-:(]\s*[^;]+", location, re.IGNORECASE)
        )
        else None
    )
    return JobImport(
        external_id=str(item["id"]),
        title=title,
        company=item["company_name"],
        description=description[:24000],
        required_skills=[],
        employment_type=employment,
        location=location[:160] if location else None,
        remote_policy=remote,
        posted_at=item.get("first_published"),
        updated_at=item["updated_at"],
        expires_at=item.get("application_deadline"),
        source_url=item["absolute_url"],
    )


def sync_board(board, *, timeout=20):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", board):
        raise ValueError("Invalid Greenhouse board token")
    url = f"{BASE_URL}/{board}/jobs?content=true"
    with urlopen(url, timeout=timeout) as response:
        body = response.read(MAX_FEED_BYTES + 1)
    if len(body) > MAX_FEED_BYTES:
        raise ValueError("Greenhouse board response exceeds size limit")
    items = json.loads(body)["jobs"]
    if not isinstance(items, list):
        raise TypeError("Invalid Greenhouse job list")
    rows = []
    for item in items:
        try:
            if item.get("language") not in (None, "en") and not item["language"].startswith("en-"):
                continue
            rows.append(normalize_posting(item).model_dump())
        except (AttributeError, KeyError, TypeError, ValidationError, ValueError):
            continue
    if items and not rows:
        raise ValueError("No valid English postings in board response")
    name = f"Greenhouse · {board}"
    result = ingest(rows, name=name, provenance=url)
    # Preserve records and saved jobs when a posting disappears from the public board.
    external_ids = [
        str(item["id"]) for item in items if isinstance(item, dict) and item.get("id") is not None
    ]
    with Session(engine) as db:
        source_id = db.scalar(select(JobSource.id).where(JobSource.name == name))
        result["deactivated"] = db.execute(
            update(Job)
            .where(
                Job.source_id == source_id,
                Job.active.is_(True),
                Job.external_id.not_in(external_ids),
            )
            .values(active=False)
        ).rowcount
        db.commit()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["import"])
    parser.add_argument("boards", help="Comma-separated public Greenhouse board tokens")
    parser.add_argument("--allow-unavailable", action="store_true")
    args = parser.parse_args()
    for board in args.boards.split(","):
        try:
            board = board.strip()
            print(board, sync_board(board))
        except (URLError, TimeoutError, KeyError, TypeError, ValueError) as exc:
            if not args.allow_unavailable:
                raise
            print(f"{board}: Greenhouse import unavailable ({type(exc).__name__})")


if __name__ == "__main__":
    main()
