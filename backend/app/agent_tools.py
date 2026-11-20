from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .jobs import find_job, save, saved, search_for_user
from .live_search import LiveSearch, LiveSearchResults, check_public_sources, interndoor_link
from .models import Interaction, User
from .profiles import ProfileView, get_profile, store_preferences
from .retrieval import match, ranking_version, search_intent
from .schemas import Filters, JobView, Match, Search, SearchResults, Strict


class Empty(Strict):
    pass


class JobInput(Strict):
    job_id: UUID


class Reply(Strict):
    message: str


class JobsOutput(Strict):
    jobs: list[JobView]


class SearchesOutput(Strict):
    searches: list[Search]


class SkillGap(Strict):
    job: JobView
    matched: list[str]
    missing: list[str]


class Execution(Strict):
    name: str
    valid: bool
    status: str
    result_count: int = Field(0, ge=0)


@dataclass
class ToolContext:
    db: Session
    user_id: UUID
    trace_id: str | None = None
    injected_failures: frozenset[str] = field(default_factory=frozenset)
    allow_live_search: bool = True

    def authorize(self):
        if self.db.get(User, self.user_id) is None:
            raise PermissionError("Inactive account")


def search_jobs(ctx, args: Search) -> SearchResults:
    return search_for_user(ctx.db, ctx.user_id, args.model_copy(update={"mode": "hybrid"}), ctx.trace_id)


def rank_jobs_for_user(ctx, args: Search) -> SearchResults:
    return search_for_user(
        ctx.db, ctx.user_id, args.model_copy(update={"mode": "personalized"}), ctx.trace_id
    )


def search_live_jobs(ctx, args: LiveSearch) -> LiveSearchResults:
    if not ctx.allow_live_search:
        raise PermissionError("Live search is disabled for this run")
    incoming = Search.model_validate(args.model_dump(exclude={"include_interndoor", "interndoor_only"}))
    filters = search_intent(incoming).model_copy(update={"source": "live"})
    query = incoming.model_copy(update={"filters": filters})
    links = [interndoor_link(query)] if args.include_interndoor or args.interndoor_only else []
    if args.interndoor_only:
        return LiveSearchResults(
            items=[],
            filters=filters,
            mode=query.mode,
            ranking_version=ranking_version(),
            sources=[],
            links=links,
            interndoor_only=True,
        )
    sources = check_public_sources(query)
    results = search_for_user(ctx.db, ctx.user_id, query, ctx.trace_id)
    return LiveSearchResults(**results.model_dump(), sources=sources, links=links)


def get_user_profile(ctx, args: Empty) -> ProfileView:
    return get_profile(ctx.db, ctx.user_id)


def update_user_preferences(ctx, args: Filters) -> Reply:
    ctx.db.scalar(select(User.id).where(User.id == ctx.user_id).with_for_update())
    current = get_profile(ctx.db, ctx.user_id).preferences.model_dump()
    current.update(args.model_dump(exclude_unset=True))
    prefs = Filters.model_validate(current)
    store_preferences(ctx.db, ctx.user_id, prefs)
    return Reply(message="I saved your preferences for future conversations.")


def get_job_details(ctx, args: JobInput) -> JobView:
    from .retrieval import job_view

    return job_view(*find_job(ctx.db, args.job_id))


def explain_job_match(ctx, args: JobInput) -> Match:
    job, source = find_job(ctx.db, args.job_id)
    profile = get_profile(ctx.db, ctx.user_id)
    result = match(job, source, profile.profile, 0, profile.preferences.model_dump(), 0.5, datetime.now(UTC))
    result.score = None
    result.signals = {}
    return result


def analyze_skill_gap(ctx, args: JobInput) -> SkillGap:
    result = explain_job_match(ctx, args)
    return SkillGap(job=result.job, matched=result.matched_skills, missing=result.missing_skills)


def save_job(ctx, args: JobInput) -> Reply:
    save(ctx.db, ctx.user_id, args.job_id)
    return Reply(message="I saved that job.")


def get_saved_jobs(ctx, args: Empty) -> JobsOutput:
    return JobsOutput(jobs=saved(ctx.db, ctx.user_id))


def get_recent_searches(ctx, args: Empty) -> SearchesOutput:
    history = ctx.db.scalars(
        select(Interaction)
        .where(
            Interaction.user_id == ctx.user_id,
            Interaction.kind == "search",
        )
        .order_by(Interaction.created_at.desc())
        .limit(5)
    )
    return SearchesOutput(searches=[Search.model_validate(row.details["search"]) for row in history])


TOOLS = {
    "search_live_jobs": (LiveSearch, LiveSearchResults, search_live_jobs),
    "search_jobs": (Search, SearchResults, search_jobs),
    "rank_jobs_for_user": (Search, SearchResults, rank_jobs_for_user),
    "get_user_profile": (Empty, ProfileView, get_user_profile),
    "update_user_preferences": (Filters, Reply, update_user_preferences),
    "get_job_details": (JobInput, JobView, get_job_details),
    "explain_job_match": (JobInput, Match, explain_job_match),
    "analyze_skill_gap": (JobInput, SkillGap, analyze_skill_gap),
    "save_job": (JobInput, Reply, save_job),
    "get_saved_jobs": (Empty, JobsOutput, get_saved_jobs),
    "get_recent_searches": (Empty, SearchesOutput, get_recent_searches),
}
