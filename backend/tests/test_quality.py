from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError
from test_product import register

from app import agent, llm
from app.config import settings
from app.evaluations.compare import compare
from app.evaluations.metrics import retrieval_scores
from app.ingestion import normalize, seed_rows
from app.main import app
from app.observability import telemetry
from app.schemas import Filters


def test_retrieval_scoring():
    metrics = retrieval_scores(["irrelevant", "a", "b"], {"a": 3, "b": 1, "c": 1})
    assert metrics["precision_at_5"] == pytest.approx(0.4)
    assert metrics["mrr"] == pytest.approx(0.5)
    assert metrics["recall_at_10"] == pytest.approx(2 / 3)
    assert 0 < metrics["ndcg_at_10"] < 1
    assert retrieval_scores([], {})["empty_correct"] is True


def test_normalization_and_filters():
    row = next(seed_rows(1))
    row["description"] = "Build\x00 things"
    row["required_skills"] = [" PYTHON ", "Python"]
    assert normalize(row)["required_skills"] == ["python"]
    assert normalize(row)["description"] == "Build  things"
    with pytest.raises(ValidationError):
        Filters(min_salary=1500)


def test_regression_gate():
    baseline = {"run_id": "base", "dataset": "retrieval", "metrics": {"hybrid": {"ndcg_at_10": 0.9}}}
    candidate = {"run_id": "new", "dataset": "retrieval", "metrics": {"hybrid": {"ndcg_at_10": 0.85}}}
    assert compare(baseline, candidate, 0.02)["regressions"] == ["hybrid.ndcg_at_10"]


def test_model_failure_uses_local_graph(monkeypatch):
    def unavailable():
        raise llm.ModelFailure("Configured provider timed out")

    monkeypatch.setattr(agent, "provider", unavailable)
    with TestClient(app) as client:
        candidate = register(client)
        reply = client.post(
            "/agent/chat", headers=candidate, json={"message": "Find remote internships"}
        ).json()
        assert reply["recommendations"]
        assert reply["trajectory"][0]["status"] == "success"
        client.delete("/auth/me", headers=candidate)


def test_structured_adapter_accounts_for_tokens(monkeypatch):
    config = settings().model_copy(update={"llm_input_usd_per_million": 1, "llm_output_usd_per_million": 2})
    monkeypatch.setattr(llm, "settings", lambda: config)
    model = object.__new__(llm.OpenAIProvider)
    model.model = config.openai_model
    model.client = Mock()
    model.client.responses.parse.return_value = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
        output_parsed=agent.Plan(actions=[]),
    )
    usage = llm.ModelUsage()
    state = llm.turn_usage.set(usage)
    try:
        assert model.generate("agent-planning", "private example", agent.Plan) == agent.Plan(actions=[])
    finally:
        llm.turn_usage.reset(state)
    assert usage.readings()["input_tokens"] == 1000
    assert usage.readings()["estimated_cost_usd"] == pytest.approx(0.0012)
    assert model.client.responses.parse.call_args.kwargs["store"] is False


def test_admin_restrictions(monkeypatch):
    reserved = f"{uuid4().hex}@example.test"
    monkeypatch.setattr(settings(), "admins", [reserved])
    with TestClient(app) as client:
        result = client.post(
            "/auth/register", json={"email": reserved, "password": "strong testing password"}
        )
        assert result.status_code == 403
        candidate = register(client)
        assert client.get("/evaluation/runs", headers=candidate).status_code == 403
        assert client.get("/metrics").status_code == 404
        client.delete("/auth/me", headers=candidate)


def test_langfuse_observations_are_correlated_and_redacted(monkeypatch):
    exporter = InMemorySpanExporter()
    client_sdk = Langfuse(
        public_key="pk-lf-local-test",
        secret_key="sk-lf-local-test",
        base_url="http://127.0.0.1:1",
        tracer_provider=TracerProvider(),
        span_exporter=exporter,
    )
    monkeypatch.setitem(telemetry.__dict__, "client", client_sdk)
    with TestClient(app) as client:
        headers = register(client)
        account_id = client.get("/auth/me", headers=headers).json()["id"]
        unique = "private-marker-23c1719"
        response = client.post("/agent/chat", headers=headers, json={"message": f"Find remote jobs {unique}"})
        assert response.status_code == 200, response.text
        reply = response.json()
        client.delete("/auth/me", headers=headers)
    client_sdk.flush()
    spans = exporter.get_finished_spans()
    turn = next(item for item in spans if item.name == "agent.turn")
    graph_span = next(item for item in spans if item.name == "agent.plan")
    tool = next(item for item in spans if item.name == "agent.tool")
    assert reply["trace_id"] == f"{turn.context.trace_id:032x}"
    assert graph_span.parent.span_id == turn.context.span_id
    assert tool.parent.span_id == turn.context.span_id
    exported = " ".join(str(item.attributes) for item in spans)
    assert unique not in exported and account_id not in exported
    assert reply["conversation_id"] in exported


def test_ingestion_reuses_embeddings_and_updates_source_url():
    from datetime import timedelta

    from sqlalchemy import delete, select
    from sqlalchemy.orm import Session

    from app.db import engine
    from app.ingestion import ingest
    from app.models import Job, JobSource

    key = uuid4().hex
    name = "Test " + key
    row = next(seed_rows(1))
    row.update(external_id=key, source_url=f"https://example.test/{key}")
    try:
        assert ingest([row], name=name, provenance="Test fixture", synthetic=True)["embedded"] == 1
        assert ingest([row], name=name, provenance="Test fixture", synthetic=True)["embedded"] == 0
        row["source_url"] += "/updated"
        row["updated_at"] += timedelta(seconds=1)
        result = ingest([row], name=name, provenance="Test fixture", synthetic=True)
        assert result["indexed"] == 1 and result["embedded"] == 0
        with Session(engine) as db:
            saved = db.scalar(select(Job).where(Job.external_id == key))
            assert saved.source_url == row["source_url"]
    finally:
        with Session(engine) as db:
            db.execute(delete(Job).where(Job.external_id == key))
            db.execute(delete(JobSource).where(JobSource.name == name))
            db.commit()


def test_optional_deepeval_with_mock_model(monkeypatch):
    monkeypatch.setenv("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
    metrics = pytest.importorskip("deepeval.metrics")
    models = pytest.importorskip("deepeval.models")

    from app.evaluations.judges import DeepEvalJudge

    class SyntheticJudge(models.DeepEvalBaseLLM):
        def load_model(self):
            return None

        def get_model_name(self):
            return "synthetic-test-judge"

        def generate(self, prompt, schema=None):
            evidence = "The posting lists Python."
            if schema is None:
                return '{"reason":"Supported by the supplied posting."}'
            for field_name in ("statements", "truths", "claims"):
                if field_name in schema.model_fields:
                    return schema.model_validate({field_name: [evidence]})
            if "verdicts" in schema.model_fields:
                return schema.model_validate({"verdicts": [{"verdict": "yes", "reason": evidence}]})
            return schema.model_validate({"reason": evidence})

        async def a_generate(self, prompt, schema=None):
            return self.generate(prompt, schema)

    mock_model = SyntheticJudge()

    def inject_model(metric):
        def new_metric(**kwargs):
            return metric(**{**kwargs, "model": mock_model})

        return new_metric

    monkeypatch.setattr(metrics, "AnswerRelevancyMetric", inject_model(metrics.AnswerRelevancyMetric))
    monkeypatch.setattr(metrics, "FaithfulnessMetric", inject_model(metrics.FaithfulnessMetric))
    results = DeepEvalJudge("synthetic-test-judge").evaluate(
        "Which skills does the job list?",
        "The posting lists Python.",
        ["Required skills are Python and Git."],
    )
    assert all(value["passed"] and value["score"] == 1 for value in results.values()), results


def test_greenhouse_mapping_keeps_unknown_metadata_empty():
    from app.greenhouse import normalize_posting

    posting = normalize_posting(
        {
            "id": 27,
            "title": "Software Engineering Intern",
            "company_name": "Test Employer",
            "location": {"name": "Remote - Canada"},
            "updated_at": "2026-11-10T08:00:00Z",
            "absolute_url": "https://job-boards.greenhouse.io/employer/jobs/27",
            "content": "&lt;p&gt;Build accessible web apps.&lt;/p&gt;&lt;p&gt;Review pull requests.&lt;/p&gt;",
        }
    )
    assert posting.employment_type == "internship"
    assert posting.remote_policy == "remote"
    assert posting.description == "Build accessible web apps.\nReview pull requests."
    assert posting.posted_at is None and posting.min_years is None
    assert posting.company_category is None and not posting.required_skills


def test_llama_provider_validates_schema_and_rejects_invalid_json(monkeypatch):
    config = settings().model_copy(update={"max_model_calls_per_turn": 1})
    monkeypatch.setattr(llm, "settings", lambda: config)
    model = object.__new__(llm.LlamaProvider)
    model.model = "test-local-model"
    model.client = Mock()
    create = model.client.chat.completions.create
    create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"answer":"Practice explaining your approach."}')
            )
        ],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=8),
    )
    reply = model.generate("career-advice", "How should I prepare?", agent.CareerAdvice)
    assert reply.answer == "Practice explaining your approach."
    assert create.call_args.kwargs["response_format"]["type"] == "json_schema"
    create.return_value.choices[0].message.content = "invalid"
    with pytest.raises(llm.ModelFailure):
        model.generate("career-advice", "How should I prepare?", agent.CareerAdvice)


def test_langfuse_smoke_reads_current_observations_api(capsys):
    from app.evaluations.langfuse_tools import smoke

    trace_id = "6aaeabcf08db4b5eafcedac39f76f672"
    client = Mock()
    client.api.observations.get_many.return_value = SimpleNamespace(data=[SimpleNamespace(trace_id=trace_id)])
    smoke(client, trace_id)

    client.flush.assert_called_once()
    request = client.api.observations.get_many.call_args.kwargs
    assert request["trace_id"] == trace_id
    assert request["from_start_time"] < request["to_start_time"]
    assert trace_id in capsys.readouterr().out


def test_himalayas_posting_preserves_application_link_and_provider_metadata():
    from app.live_search import normalize_himalayas, public_role

    application = "https://himalayas.app/companies/example/jobs/data-intern?utm_source=api"
    job = normalize_himalayas(
        {
            "title": "Data Science Intern",
            "companyName": "Example",
            "excerpt": "Build forecasts",
            "employmentType": "Intern",
            "locationRestrictions": ["India"],
            "pubDate": 1795000000,
            "expiryDate": 1796000000,
            "applicationLink": application,
            "guid": "https://himalayas.app/companies/example/jobs/data-intern",
        }
    )
    assert str(job.source_url) == application
    assert job.remote_policy == "remote" and job.employment_type == "internship" and job.location == "India"
    assert job.expires_at > job.posted_at
    assert public_role("find machine learning jobs for alice@example.test") == "machine learning"
