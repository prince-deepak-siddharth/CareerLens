import json
import re
from datetime import UTC, datetime
from typing import Literal, TypedDict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select

from .agent_tools import TOOLS, Execution, ToolContext
from .auth import DB, CurrentUser
from .config import settings
from .live_search import ExternalLink, SourceCheck
from .llm import ModelFailure, ModelUsage, agent_model_config, turn_usage
from .llm import agent_provider as provider
from .models import AgentMemory, Conversation, Message
from .observability import identify, span, telemetry
from .profiles import TERM_LIST, get_profile
from .schemas import Filters, JobView, Match, Strict

router = APIRouter(prefix="/agent", tags=["career agent"])
MAX_TOOLS = 3
ORDINALS = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4}
JOB_TOOLS = {"get_job_details", "explain_job_match", "analyze_skill_gap", "save_job"}
CATALOG_TOOLS = {"search_jobs", "rank_jobs_for_user"}
SEARCH_TOOLS = {*CATALOG_TOOLS, "search_live_jobs"}
INTERNDOOR = r"\bintern[ -]?door(?:\.com)?\b"
INTERNDOOR_ONLY = rf"\bonly (?:on |from |use )?{INTERNDOOR}|{INTERNDOOR}\s+only\b"
WEB_REQUEST = r"\b(?:web|websites?|internet|online|live|published|job boards?)\b"
CATALOG_REQUEST = r"\b(?:catalog|offline|saved catalog)\b"

ToolName = Literal[
    "search_live_jobs",
    "search_jobs",
    "rank_jobs_for_user",
    "get_user_profile",
    "update_user_preferences",
    "get_job_details",
    "explain_job_match",
    "analyze_skill_gap",
    "save_job",
    "get_saved_jobs",
    "get_recent_searches",
]


class AgentRequest(Strict):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None


class Action(BaseModel):
    name: ToolName
    query: str | None = None
    employment_type: str | None = None
    remote_policy: str | None = None
    company_category: str | None = None
    skills: list[str] | None = None
    location: str | None = None
    max_required_years: int | None = None
    job_position: int | None = None


class Plan(BaseModel):
    actions: list[Action]


class CareerAdvice(BaseModel):
    answer: str = Field(min_length=1, max_length=1200)


class AgentReply(Strict):
    conversation_id: UUID
    message: str
    recommendations: list[Match] = Field(default_factory=list)
    jobs: list[JobView] = Field(default_factory=list)
    filters: Filters | None = None
    source_checks: list[SourceCheck] = Field(default_factory=list)
    external_links: list[ExternalLink] = Field(default_factory=list)
    trace_id: str | None = None
    trajectory: list[Execution] = Field(default_factory=list)
    model_usage: dict[str, int | float | None] = Field(default_factory=dict)
    model_name: str | None = None
    prompt_versions: dict[str, str] = Field(default_factory=dict)


class State(TypedDict, total=False):
    message: str
    memory: dict
    decisions: list[tuple[str, dict]]
    results: list[tuple[str, dict]]
    trajectory: list[dict]
    reply: dict
    problem: str
    answer: str


def route_local(message, memory, preferences, confirmed_years, *, allow_live_search=True):
    text = message.casefold()
    explicit = {
        "employment_type": [
            name
            for pattern, name in [
                (r"\bintern(?:ship)?s?\b", "internship"),
                (r"\bfull[ -]time\b", "full-time"),
                (r"\bpart[ -]time\b", "part-time"),
                (r"\bcontract\b", "contract"),
            ]
            if re.search(pattern, text)
        ],
        "remote_policy": [name for name in ("remote", "hybrid", "onsite") if re.search(rf"\b{name}\b", text)],
        "company_category": [
            name
            for pattern, name in [
                (r"\bstartups?\b", "startup"),
                (r"\benterprises?\b", "enterprise"),
                (r"\bnonprofits?\b", "nonprofit"),
            ]
            if re.search(pattern, text)
        ],
    }
    if any(len(values) > 1 for values in explicit.values()):
        return [], "Which option should I use for those conflicting filters?"
    filters = {key: values[0] for key, values in explicit.items() if values}
    if re.search(r"\b(?:real|live|published) (?:jobs|roles|openings|postings)\b", text):
        filters["source"] = "live"
    elif re.search(r"\b(?:demo|synthetic) (?:jobs|roles|postings)\b", text):
        filters["source"] = "demo"
    if re.search(r"\b(?:requiring|must (?:have|include)|skills?[: ]|prefer)\b", text):
        skills = [
            skill.casefold()
            for skill in TERM_LIST
            if re.search(
                r"(?<![\w+#])" + re.escape(skill.casefold()) + r"(?![\w+#])",
                text,
            )
        ]
        if skills:
            filters["skills"] = skills[:12]
    if re.search(r"\bno experience\b|\bzero years\b", text):
        filters["max_required_years"] = 0
    elif years := re.search(r"\b(?:at most|up to|maximum) (\d{1,2}) years\b", text):
        filters["max_required_years"] = int(years.group(1))
    if location := re.search(r"\bin (bengaluru|london|new york|berlin|mumbai|india)\b", text):
        filters["location"] = location.group(1)
    if re.match(
        r"^(?:please )?(?:remember|save my preference|update my preference|set my preference)\b", text
    ):
        if filters:
            return [("update_user_preferences", filters)], ""
        return [], "Which preference would you like me to remember?"
    if re.search(r"\b(?:recent|past|previous) searches\b", text):
        return [("get_recent_searches", {})], ""
    if re.search(r"\b(?:saved jobs|bookmarks|saved opportunities)\b", text):
        return [("get_saved_jobs", {})], ""
    if re.search(r"\b(?:my profile|my preferences|my skills)\b", text) and not re.search(
        r"\b(?:find|search|recommend|match)\b",
        text,
    ):
        return [("get_user_profile", {})], ""
    tool = None
    if re.match(r"^(?:please )?(?:save|bookmark)\b", text):
        tool = "save_job"
    elif re.search(r"\b(?:skill gaps?|missing skills?|what skills.*need)\b", text):
        tool = "analyze_skill_gap"
    elif re.search(r"\b(?:why|explain)\b", text):
        tool = "explain_job_match"
    elif re.search(r"\b(?:details?|tell me about)\b", text):
        tool = "get_job_details"
    if tool:
        ids = memory.get("job_ids", [])
        index = next((value for key, value in ORDINALS.items() if re.search(rf"\b{key}\b", text)), 0)
        if ordinal := re.search(r"\b(?:job|result|role)\s*#?(\d{1,2})\b", text):
            index = int(ordinal.group(1)) - 1
        if not 0 <= index < len(ids):
            return (
                [],
                "Which job should I use? Find a job first or refer to its position in the latest results.",
            )
        return [(tool, {"job_id": ids[index]})], ""
    follow = bool(memory.get("search")) and bool(
        re.match(
            r"^(?:only|and|also|what about|make (?:them|it)|limit|same|filter)\b",
            text,
        )
    )
    if (
        re.search(
            r"\b(?:find|search|browse|show|recommend|jobs|roles|internships|opportunities|looking for|look for)\b",
            text,
        )
        or follow
        or re.search(INTERNDOOR, text)
    ):
        prior = memory.get("search", {}) if follow else {}
        active = {**preferences, **prior.get("filters", {}), **filters}
        has_years_filter = active.get("max_required_years") is not None
        if confirmed_years is not None:
            active["max_required_years"] = min(
                active.get("max_required_years", confirmed_years), confirmed_years
            )
        search_query = prior.get("query") or re.sub(
            r"\b(?:find|search|show|recommend|only|me|jobs?|roles?|opportunities|remote|hybrid|onsite|requiring)\b",
            " ",
            text,
        )
        search = {"query": " ".join(search_query.split())[:600], "filters": active, "limit": 6}
        chosen = (
            "rank_jobs_for_user"
            if re.search(r"\b(?:recommend|match|rank|best for me)\b", text)
            else "search_jobs"
        )
        wants_interndoor = bool(re.search(INTERNDOOR, text) or prior.get("include_interndoor"))
        interndoor_only = bool(re.search(INTERNDOOR_ONLY, text) or prior.get("interndoor_only"))
        wants_web = bool(re.search(WEB_REQUEST, text)) and filters.get("source") != "demo"
        use_live = wants_web or (
            (settings().agent_live_search or prior.get("live")) and active.get("source") != "demo"
        )
        if allow_live_search and (wants_interndoor or (use_live and not re.search(CATALOG_REQUEST, text))):
            if not has_years_filter:
                # Public boards commonly omit minimum experience. Keep explicitly requested caps.
                active.pop("max_required_years", None)
            clean_query = re.sub(
                rf"{INTERNDOOR}|{WEB_REQUEST}|\b(?:on|from|using|browse)\b", " ", search["query"]
            )
            search.update(
                query=" ".join(clean_query.split()),
                mode="personalized" if chosen == "rank_jobs_for_user" else prior.get("mode", "hybrid"),
                include_interndoor=wants_interndoor,
                interndoor_only=interndoor_only,
            )
            return [("search_live_jobs", search)], ""
        return [(chosen, search)], ""
    return [], "What kind of role are you looking for? You can also ask about your profile or saved jobs."


def plan_node(state: State, runtime: Runtime[ToolContext]):
    ctx = runtime.context
    message = state["message"]
    memory = state.get("memory", {})
    profile = get_profile(ctx.db, ctx.user_id)
    local, problem = route_local(
        message,
        memory,
        profile.preferences.model_dump(exclude_none=True),
        profile.profile.years_experience,
        allow_live_search=ctx.allow_live_search,
    )
    with span("agent.plan"):
        try:
            model = provider()
            needs_clarification = problem and not problem.startswith("What kind of role")
            if model is None or needs_clarification:
                return {"decisions": local, "problem": problem}
            if (
                not local
                and problem.startswith("What kind of role")
                and re.search(
                    r"\b(?:career|interviews?|resume|portfolio|cover letter|networking|practice)\b|^(?:hi|hello|hey)[!. ]*$",
                    message.casefold(),
                )
            ):
                advice = model.generate("career-advice", message, CareerAdvice)
                return {"decisions": [], "answer": advice.answer.strip()}
            result = model.generate(
                "agent-planning",
                json.dumps(
                    {
                        "message": message,
                        "search": memory.get("search"),
                        "job_positions": len(memory.get("job_ids", [])),
                        "supported_tools": [
                            name for name in TOOLS if ctx.allow_live_search or name != "search_live_jobs"
                        ],
                    }
                ),
                Plan,
            )
            if local:
                return {"decisions": local, "problem": problem}
            actions = []
            for action in result.actions[:MAX_TOOLS]:
                name = action.name
                values = action.model_dump(exclude_none=True, exclude={"name", "query", "job_position"})
                if name in {"update_user_preferences", "save_job"}:
                    continue
                if name in SEARCH_TOOLS:
                    if not action.query and not values:
                        continue
                    filters = {**profile.preferences.model_dump(exclude_none=True), **values}
                    options = {"query": action.query or "", "filters": filters, "limit": 6}
                    if (
                        ctx.allow_live_search
                        and filters.get("source") != "demo"
                        and (name == "search_live_jobs" or settings().agent_live_search)
                    ):
                        options["mode"] = "personalized" if name == "rank_jobs_for_user" else "hybrid"
                        name = "search_live_jobs"
                    elif name == "search_live_jobs":
                        name = "search_jobs"
                    actions.append((name, options))
                elif name in JOB_TOOLS:
                    ids = memory.get("job_ids", [])
                    position = (action.job_position or 0) - 1
                    if 0 <= position < len(ids):
                        actions.append((name, {"job_id": ids[position]}))
                else:
                    actions.append((name, {}))
            return {"decisions": actions, "problem": problem if not actions else ""}
        except (ModelFailure, ValidationError):
            return {
                "decisions": local,
                "problem": problem or "The model is unavailable. I used local routing.",
            }


def tools_node(state: State, runtime: Runtime[ToolContext]):
    context = runtime.context
    outcomes, trajectory = [], []
    memory = dict(state.get("memory", {}))
    for name, data in state.get("decisions", [])[:MAX_TOOLS]:
        execution = Execution(name=name, valid=False, status="invalid")
        with span("agent.tool", tool=name, metadata={"tool": name, "argument_fields": sorted(data)}) as obs:
            try:
                schema, output, action = TOOLS[name]
                args = schema.model_validate(data)
                execution.valid = True
                context.authorize()
                if name in context.injected_failures:
                    raise TimeoutError("Evaluation tool timeout")
                with context.db.begin_nested():
                    result = output.model_validate(action(context, args)).model_dump(mode="json")
                outcomes.append((name, result))
                execution.status = "success"
                execution.result_count = len(
                    result.get("items", result.get("jobs", result.get("searches", [])))
                )
                if name in SEARCH_TOOLS:
                    memory["search"] = {
                        **args.model_dump(mode="json", exclude_none=True),
                        "mode": result["mode"],
                        "live": name == "search_live_jobs",
                        "filters": Filters.model_validate(result["filters"]).model_dump(
                            mode="json", exclude_none=True
                        ),
                    }
                    memory["job_ids"] = [item["job"]["id"] for item in result["items"]]
            except (ValidationError, KeyError):
                pass
            except Exception:  # noqa: BLE001 - one tool failure must not end the turn
                execution.status = "error"
            if obs:
                telemetry.call(lambda execution=execution: obs.update(metadata=execution.model_dump()))
        trajectory.append(execution.model_dump())
    return {"results": outcomes, "trajectory": trajectory, "memory": memory}


def render_node(state: State):
    text, jobs, recommended = [], [], []
    source_checks, external_links = [], []
    filters = None
    with span("agent.render"):
        for name, result in state.get("results", []):
            if name in SEARCH_TOOLS:
                recommended = result["items"]
                filters = result["filters"]
                if name == "search_live_jobs":
                    source_checks.extend(result["sources"])
                    external_links.extend(result["links"])
                    if result["interndoor_only"]:
                        text.append(
                            "Open InternDoor to search its listings directly. Its terms restrict automated access."
                        )
                        continue
                    if any(source["status"] in {"checked", "cached"} for source in result["sources"]):
                        text.append(
                            f"I found {len(recommended)} matching published postings."
                            if recommended
                            else "I found no published postings that satisfy those filters."
                        )
                    elif recommended:
                        text.append(
                            "The feeds could not be refreshed. Here are matching employer postings already in CareerLens."
                        )
                    else:
                        text.append(
                            "The feeds could not be refreshed, and I found no matching employer postings in CareerLens."
                        )
                    if result["links"]:
                        text.append("You can also search InternDoor directly using the link below.")
                else:
                    text.append(
                        f"I found {len(recommended)} matching opportunities."
                        if recommended
                        else "I found no opportunities that satisfy those filters."
                    )
            elif name == "get_job_details":
                jobs.append(result)
                text.append(f"{result['title']} at {result['company']}. {result['description'][:420]}")
            elif name == "get_saved_jobs":
                jobs.extend(result["jobs"])
                text.append(f"You have {len(result['jobs'])} saved jobs.")
            elif name == "get_recent_searches":
                queries = [item["query"] or "(filters only)" for item in result["searches"]]
                text.append(
                    "Recent searches include " + ", ".join(queries) + "."
                    if queries
                    else "No recent searches."
                )
            elif name == "get_user_profile":
                skills = result["profile"]["skills"]
                prefs = Filters.model_validate(result["preferences"]).model_dump(exclude_defaults=True)
                summary = ", ".join(
                    ", ".join(value) if isinstance(value, list) else str(value) for value in prefs.values()
                )
                text.append(
                    f"Confirmed skills include {', '.join(skills)}."
                    if skills
                    else "Your profile has no confirmed skills."
                )
                text.append(
                    f"Saved preferences include {summary}." if summary else "You have no saved preferences."
                )
            elif name == "analyze_skill_gap":
                jobs.append(result["job"])
                missing = result["missing"]
                if not result["job"]["required_skills"]:
                    text.append("Check the original posting for its skill requirements.")
                    continue
                text.append(
                    f"Required skills absent from your confirmed profile include {', '.join(missing)}."
                    if missing
                    else "Your confirmed profile covers the listed required skills."
                )
            elif name == "explain_job_match":
                recommended.append(result)
                text.append(result["explanation"])
            else:
                text.append(result["message"])
        if not text:
            text.append(
                state.get("answer")
                or state.get("problem")
                or "I couldn't complete that action. Please try again."
            )
        return {
            "reply": {
                "message": " ".join(text),
                "recommendations": recommended,
                "jobs": jobs,
                "filters": filters,
                "source_checks": source_checks,
                "external_links": external_links,
            }
        }


builder = StateGraph(State, context_schema=ToolContext)
builder.add_node("plan", plan_node)
builder.add_node("tools", tools_node)
builder.add_node("render", render_node)
builder.add_edge(START, "plan")
builder.add_edge("plan", "tools")
builder.add_edge("tools", "render")
builder.add_edge("render", END)
graph = builder.compile()


def run_turn(db, user_id, request, *, failures=frozenset(), allow_live_search=True):
    conversation = (
        db.scalar(
            select(Conversation)
            .where(
                Conversation.id == request.conversation_id,
                Conversation.user_id == user_id,
            )
            .with_for_update()
        )
        if request.conversation_id
        else None
    )
    if request.conversation_id and conversation is None:
        raise HTTPException(404, "Conversation not found")
    if conversation is None:
        conversation = Conversation(user_id=user_id, title=request.message[:100])
        db.add(conversation)
        db.flush()
    memory = db.get(AgentMemory, conversation.id)
    usage = ModelUsage()
    usage_token = turn_usage.set(usage)
    try:
        with span("agent.turn") as observation, identify(observation, user_id, conversation.id) as trace_id:
            state = graph.invoke(
                {"message": request.message, "memory": memory.state if memory else {}},
                context=ToolContext(db, user_id, trace_id, failures, allow_live_search),
                config={"recursion_limit": 8},
            )
            reply = AgentReply(
                conversation_id=conversation.id,
                trace_id=trace_id,
                trajectory=state["trajectory"],
                model_usage=usage.readings(),
                model_name=agent_model_config()[1] if usage.calls > usage.failures else None,
                prompt_versions=usage.prompt_versions or {"agent-planning": "local-rules-v1"},
                **state["reply"],
            )
            if observation:
                telemetry.call(lambda: observation.update(metadata=usage.readings()))
            if memory is None:
                db.add(AgentMemory(conversation_id=conversation.id, state=state["memory"]))
            else:
                memory.state = state["memory"]
            sequence = db.scalar(
                select(func.coalesce(func.max(Message.sequence), 0)).where(
                    Message.conversation_id == conversation.id,
                )
            )
            conversation.updated_at = datetime.now(UTC)
            db.add_all(
                [
                    Message(
                        conversation_id=conversation.id,
                        sequence=sequence + 1,
                        role="user",
                        content={"message": request.message},
                    ),
                    Message(
                        conversation_id=conversation.id,
                        sequence=sequence + 2,
                        role="assistant",
                        content=reply.model_dump(mode="json"),
                    ),
                ]
            )
            db.commit()
            from .online import sample

            sample(reply)
            return reply
    finally:
        turn_usage.reset(usage_token)


def agent_limit(request: Request):
    request.app.state.rate_limit(request, "agent", 25)


@router.post("/chat", dependencies=[Depends(agent_limit)])
def chat(data: AgentRequest, user: CurrentUser, db: DB) -> AgentReply:
    return run_turn(db, user.id, data)


@router.get("/conversations")
def conversations(user: CurrentUser, db: DB):
    rows = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
        .limit(50)
    )
    return [{"id": row.id, "title": row.title, "updated_at": row.updated_at} for row in rows]


@router.get("/conversations/{conversation_id}")
def history(conversation_id: UUID, user: CurrentUser, db: DB):
    row = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(404, "Conversation not found")
    messages = list(
        db.scalars(
            select(Message)
            .where(Message.conversation_id == row.id)
            .order_by(Message.sequence.desc())
            .limit(100)
        )
    )
    return {
        "id": row.id,
        "title": row.title,
        "messages": [{"role": item.role, **item.content} for item in reversed(messages)],
    }
