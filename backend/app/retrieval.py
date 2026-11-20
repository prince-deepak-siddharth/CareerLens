import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select

from .config import settings
from .embeddings import cross_encoder, embed, profile_vector
from .models import Feedback, Job, JobEmbedding, JobSource, Preference, Profile
from .observability import span, telemetry
from .schemas import JobView, Match, ProfileData, Search, SearchResults

RELATED_ROLES = ("machine learning", "AI engineering", "data science")
ROLE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(machine learning|ml)\b",
        r"\b(ai|artificial intelligence)\b",
        r"\b(data science|data scientist)\b",
    )
)
INTERNSHIP_QUERY = re.compile(r"\bintern(?:ship)?s?\b", re.IGNORECASE)
OTHER_EMPLOYMENT = re.compile(r"\b(?:full[ -]time|part[ -]time|contract)\b", re.IGNORECASE)
EXCLUDES_INTERNSHIPS = re.compile(
    r"\b(?:exclude|excluding|except|no|non[ -]|without)\s*intern", re.IGNORECASE
)


def search_intent(request):
    query, filters = request.query, request.filters
    if (
        query
        and request.mode != "keyword"
        and not filters.employment_type
        and INTERNSHIP_QUERY.search(query)
        and not OTHER_EMPLOYMENT.search(query)
        and not EXCLUDES_INTERNSHIPS.search(query)
    ):
        filters = filters.model_copy(update={"employment_type": "internship"})
    return filters


def distinct_titles_first(items):
    first, repeats = {}, []
    for item in items:
        key = item.job.title.strip().casefold()
        if key in first:
            repeats.append(item)
        else:
            first[key] = item
    return [*first.values(), *repeats]


def prioritize_related_roles(items):
    first, related, other = {}, [], []
    for item in items:
        role = next(
            (index for index, pattern in enumerate(ROLE_PATTERNS) if pattern.search(item.job.title)), None
        )
        if role is None:
            other.append(item)
        elif role in first:
            related.append(item)
        else:
            first[role] = item
    return [*first.values(), *related, *other]


def dense_candidates(db, base, model, vectors, limit):
    distances = [JobEmbedding.value.cosine_distance(vector) for vector in vectors]
    # Related titles get their own query vector. Prefer the user's exact intent on ties.
    distance = (
        func.least(distances[0], *(item + 0.08 for item in distances[1:]))
        if len(distances) > 1
        else distances[0]
    )
    scored = (
        base.add_columns(Job.title, distance.label("distance"))
        .join(JobEmbedding)
        .where(JobEmbedding.model == model)
        .cte()
        .prefix_with("MATERIALIZED")
    )
    candidates = select(
        scored.c.id,
        scored.c.distance,
        func.row_number()
        .over(
            partition_by=func.lower(scored.c.title),
            order_by=(scored.c.distance, scored.c.id),
        )
        .label("title_rank"),
    ).subquery()
    return db.execute(
        select(candidates.c.id, candidates.c.distance)
        .order_by(candidates.c.title_rank > 1, candidates.c.distance, candidates.c.id)
        .limit(limit)
    )


def ranking_version(weights=None):
    payload = json.dumps(weights or settings().rank_weights, sort_keys=True)
    return "v2-" + hashlib.sha256(payload.encode()).hexdigest()[:12]


def eligibility(filters, now):
    clauses = [Job.active.is_(True), or_(Job.expires_at.is_(None), Job.expires_at > now)]
    for name in ("employment_type", "remote_policy", "company_category", "currency"):
        if value := getattr(filters, name):
            clauses.append(func.lower(getattr(Job, name)) == value.lower())
    if filters.source:
        clauses.append(
            Job.source_id.in_(select(JobSource.id).where(JobSource.synthetic.is_(filters.source == "demo")))
        )
    if filters.location:
        clauses.append(Job.location.icontains(filters.location, autoescape=True))
    if filters.max_required_years is not None:
        clauses.append(Job.min_years <= filters.max_required_years)
    if filters.min_salary is not None:
        clauses.append(Job.salary_min >= filters.min_salary)
    if filters.skills:
        clauses.append(Job.required_skills.contains(filters.skills))
    if filters.posted_within_days:
        clauses.append(Job.posted_at >= now - timedelta(days=filters.posted_within_days))
    return clauses


def job_view(job, source):
    return JobView.model_validate(
        {
            **{name: getattr(job, name) for name in JobImportFields},
            "id": job.id,
            "synthetic": source.synthetic,
            "source_name": source.name,
            "source_provenance": source.provenance,
        }
    )


JobImportFields = set(JobView.model_fields) - {"id", "synthetic", "source_name", "source_provenance"}


def match(job, source, profile, relevance, preferences, feedback, now, weights=None):
    known = {skill.casefold() for skill in [*profile.skills, *profile.tools]}
    required = set(job.required_skills)
    matched = sorted(required & known)
    missing = sorted(required - known)
    years = profile.years_experience
    warnings = []
    if job.min_years is None:
        warnings.append("Check the original posting for its experience requirements.")
    elif years is not None and job.min_years > years:
        warnings.append(f"The posting requires {job.min_years} years. Your confirmed profile lists {years}.")
    elif years is None and job.min_years:
        warnings.append(
            f"The posting requires {job.min_years} years. Confirm your experience to check eligibility."
        )
    preferred = [
        float(getattr(job, field).casefold() == preferences[field].casefold())
        for field in ("remote_policy", "employment_type", "company_category", "location")
        if preferences.get(field) and getattr(job, field)
    ]
    if preferences.get("skills"):
        preferred.append(len(required.intersection(preferences["skills"])) / len(preferences["skills"]))
    signals = {
        "relevance": max(0.0, min(1.0, relevance)),
        "skills": len(matched) / len(required) if required else 0.5,
        "preference": sum(preferred) / len(preferred) if preferred else 0.5,
        "experience": 0.5
        if job.min_years is None or (years is None and job.min_years)
        else float(not warnings),
        "freshness": 1 - min(90, max(0, (now - job.posted_at).days)) / 90 if job.posted_at else 0.5,
        "feedback": feedback,
    }
    weights = weights or settings().rank_weights
    score = sum(signals[key] * value for key, value in weights.items()) / sum(weights.values())
    explanation = (
        f"Confirmed skills listed by this job include {', '.join(matched)}."
        if matched
        else (
            "Check the original posting for its requirements."
            if not required
            else "The posting and confirmed profile share no listed required skills."
            if known
            else "Confirm skills in your profile to get a personalized skill comparison."
        )
    )
    return Match(
        job=job_view(job, source),
        score=round(score * 100, 2),
        signals=signals,
        matched_skills=matched,
        missing_skills=missing,
        evidence=job.description[:420],
        explanation=explanation,
        eligibility_warnings=warnings,
    )


def job_details(db, job_id):
    return db.execute(select(Job, JobSource).join(JobSource).where(Job.id == job_id)).one_or_none()


def retrieve(db, request: Search, user_id=None, *, weights=None):
    cfg = settings()
    now = datetime.now(UTC)
    version = ranking_version(weights)
    with span(
        "job.retrieve",
        mode=request.mode,
        embedding_model=cfg.embedding_model,
        ranking_version=version,
        metadata={
            "mode": request.mode,
            "filter_fields": sorted(request.filters.model_fields_set),
            "embedding_model": cfg.embedding_model,
            "rank_version": version,
        },
    ) as observation:
        profile = db.get(Profile, user_id) if user_id else None
        preference = db.get(Preference, user_id) if user_id else None
        preferences = preference.values if preference else {}
        query = request.query.strip()
        filters = search_intent(request)
        if (
            not query
            and request.mode == "personalized"
            and preferences.get("employment_type")
            and not filters.employment_type
        ):
            filters = filters.model_copy(update={"employment_type": preferences["employment_type"]})
        clauses = eligibility(filters, now)
        if request.mode == "personalized" and profile and profile.details.get("years_experience") is not None:
            clauses.append(or_(Job.min_years.is_(None), Job.min_years <= profile.details["years_experience"]))
        if user_id and request.mode == "personalized":
            clauses.append(
                Job.id.not_in(
                    select(Feedback.job_id).where(
                        Feedback.user_id == user_id,
                        Feedback.kind == "dismiss",
                    )
                )
            )
        candidates = max(cfg.retrieval_min_candidates, request.limit * cfg.retrieval_oversample)
        base = select(Job.id).where(*clauses)
        keyword, dense, similarity = [], [], {}
        data = ProfileData.model_validate(profile.details) if profile else ProfileData()
        if query and request.mode != "semantic":
            search_query = func.websearch_to_tsquery("english", query)
            exact_skill = Job.required_skills.contains([query.casefold()])
            keyword = list(
                db.scalars(
                    base.where(or_(Job.search_doc.op("@@")(search_query), exact_skill))
                    .order_by(
                        exact_skill.desc(),
                        func.ts_rank_cd(Job.search_doc, search_query).desc(),
                        Job.id,
                    )
                    .limit(candidates)
                )
            )
        interests = query or " ".join([*data.skills, *data.domains, *data.projects])
        related_intent = filters.employment_type == "internship" and any(
            pattern.search(interests) for pattern in ROLE_PATTERNS
        )
        vectors = []
        if request.mode != "keyword":
            if query:
                queries = [query]
                if related_intent:
                    queries.extend(f"{role} internship" for role in RELATED_ROLES)
                vectors = embed(queries, query=True)
            elif profile:
                vector = (
                    profile.embedding
                    if profile.embedding_model == cfg.embedding_model
                    else profile_vector(data)
                )
                if vector is not None:
                    vectors = [vector]
                    if related_intent:
                        vectors.extend(embed([f"{role} internship" for role in RELATED_ROLES], query=True))
        if vectors:
            for job_id, difference in dense_candidates(db, base, cfg.embedding_model, vectors, candidates):
                if difference is not None:
                    dense.append(job_id)
                    similarity[job_id] = max(0.0, 1 - difference)
        fusion = defaultdict(float)
        for ranking in (keyword, dense):
            for index, job_id in enumerate(ranking, 1):
                fusion[job_id] += 1 / (cfg.retrieval_rrf_k + index)
        ids = sorted(fusion, key=lambda key: (-fusion[key], str(key)))
        if not query and not ids:
            ids = list(db.scalars(base.order_by(Job.posted_at.desc().nulls_last(), Job.id).limit(candidates)))
        rows = (
            {
                job.id: (job, source)
                for job, source in db.execute(select(Job, JobSource).join(JobSource).where(Job.id.in_(ids)))
            }
            if ids
            else {}
        )
        reranked = False
        if request.mode == "reranked" and cfg.cross_encoder and query and ids:
            cap = max(request.limit, cfg.rerank_candidates)
            shortlist = ids[:cap]
            with span("job.rerank", count=len(shortlist)):
                scores = cross_encoder(cfg.cross_encoder).predict(
                    [(query, rows[key][0].description) for key in shortlist]
                )
                ids = [
                    key
                    for key, _ in sorted(
                        zip(shortlist, scores, strict=True), key=lambda pair: -float(pair[1])
                    )
                ] + ids[cap:]
                reranked = True
        with span("job.rank", mode=request.mode, count=len(ids)):
            feedback = {}
            if user_id and request.mode == "personalized":
                history = db.execute(
                    select(Feedback.job_id, Feedback.kind)
                    .where(
                        Feedback.user_id == user_id,
                        Feedback.job_id.in_(ids),
                        Feedback.kind.in_(("helpful", "unhelpful", "dismiss")),
                    )
                    .order_by(Feedback.created_at)
                )
                feedback = {key: float(kind == "helpful") for key, kind in history}
            items = [
                match(
                    *rows[key],
                    data,
                    similarity.get(key, min(1.0, fusion[key] * 30)),
                    preferences,
                    feedback.get(key, 0.5),
                    now,
                    weights,
                )
                for key in ids
            ]
            if request.mode == "personalized":
                items.sort(key=lambda item: -(item.score or 0))
            else:
                for item in items:
                    item.score = None
                    item.signals = {}
        if vectors:
            items = distinct_titles_first(prioritize_related_roles(items) if related_intent else items)
        if observation:
            telemetry.call(
                lambda: observation.update(
                    metadata={
                        "keyword_candidates": len(keyword),
                        "semantic_candidates": len(dense),
                        "job_ids": [str(item.job.id) for item in items[: request.limit]],
                        "reranked": reranked,
                    }
                )
            )
    return SearchResults(
        items=items[: request.limit],
        mode=request.mode,
        filters=filters,
        ranking_version=version,
        reranked=reranked,
        embedding_model=cfg.embedding_model if query and request.mode != "keyword" else None,
    )
