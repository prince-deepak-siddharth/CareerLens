import io
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def register(client):
    email = f"{uuid4().hex}@example.test"
    credentials = {"email": email, "password": "strong testing password"}
    response = client.post("/auth/register", json=credentials)
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_profile_pdf_and_job_flow(client):
    owner, stranger = register(client), register(client)
    assert client.get("/profile").status_code == 401
    stream = io.BytesIO()
    canvas = Canvas(stream)
    canvas.drawString(20, 800, "Python, PostgreSQL, and PyTorch")
    canvas.save()
    draft = client.post(
        "/resumes/upload", headers=owner, files={"file": ("resume.pdf", stream.getvalue(), "application/pdf")}
    )
    assert draft.status_code == 201, draft.text
    draft = draft.json()
    assert {"Python", "PostgreSQL", "PyTorch"} <= set(draft["draft"]["skills"])
    path = f"/resumes/{draft['id']}/confirm"
    assert client.post(path, headers=stranger, json={"profile": draft["draft"]}).status_code == 404
    assert client.post(path, headers=owner, json={"profile": draft["draft"]}).status_code == 200
    response = client.post(
        "/jobs/search",
        headers=owner,
        json={
            "query": "machine learning",
            "filters": {
                "remote_policy": "remote",
                "employment_type": "internship",
                "skills": ["python"],
                "max_required_years": 0,
            },
        },
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items and all("python" in item["job"]["required_skills"] for item in items)
    job = items[0]["job"]["id"]
    assert client.post(f"/jobs/{job}/save", headers=owner).status_code == 204
    assert client.get("/jobs/saved", headers=stranger).json() == []
    assert client.get("/jobs/saved", headers=owner).json()[0]["id"] == job
    assert (
        client.post(
            "/recommendations/feedback", headers=owner, json={"job_id": job, "kind": "helpful"}
        ).status_code
        == 201
    )
    assert client.delete(f"/resumes/{draft['id']}", headers=owner).status_code == 204
    assert client.delete("/auth/me", headers=owner).status_code == 204
    assert client.delete("/auth/me", headers=stranger).status_code == 204


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid", "personalized"])
def test_retrieval_strategies(client, mode):
    owner = register(client)
    result = client.post(
        "/jobs/search",
        headers=owner,
        json={
            "query": "Python",
            "mode": mode,
            "filters": {"skills": ["python"]},
            "limit": 2,
        },
    )
    assert result.status_code == 200, result.text
    assert len(result.json()["items"]) == 2
    assert all(item["job"]["synthetic"] for item in result.json()["items"])
    client.delete("/auth/me", headers=owner)


def test_upload_rejects_invalid_pdf(client):
    owner = register(client)
    response = client.post(
        "/resumes/upload", headers=owner, files={"file": ("a.pdf", b"fake", "application/pdf")}
    )
    assert response.status_code == 415
    client.delete("/auth/me", headers=owner)


def test_agent_multiturn_memory_and_authorization(client):
    owner, stranger = register(client), register(client)
    base = "/agent/chat"
    first = client.post(base, headers=owner, json={"message": "Find remote AI internships requiring Python"})
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["recommendations"]
    assert result["trajectory"][0]["name"] == "search_jobs"
    thread = result["conversation_id"]
    second = client.post(
        base,
        headers=owner,
        json={
            "message": "Only show early-stage startups",
            "conversation_id": thread,
        },
    ).json()
    assert second["filters"]["remote_policy"] == "remote"
    assert second["filters"]["company_category"] == "startup"
    assert second["filters"]["employment_type"] == "internship"
    third = client.post(
        base,
        headers=owner,
        json={
            "message": "Why did you recommend the third job?",
            "conversation_id": thread,
        },
    ).json()
    assert third["recommendations"][0]["job"]["id"] == second["recommendations"][2]["job"]["id"]
    assert (
        client.post(
            base, headers=stranger, json={"message": "show jobs", "conversation_id": thread}
        ).status_code
        == 404
    )
    assert client.get(f"/agent/conversations/{thread}", headers=stranger).status_code == 404
    assert (
        client.post(base, headers=owner, json={"message": "Remember that I prefer Python roles"}).status_code
        == 200
    )
    with TestClient(app) as restarted:
        result = restarted.post(base, headers=owner, json={"message": "What are my preferences?"}).json()
        assert "python" in result["message"]
    assert client.get(f"/agent/conversations/{thread}", headers=owner).status_code == 200
    client.delete("/auth/me", headers=owner)
    client.delete("/auth/me", headers=stranger)


def test_catalog_pages_and_sources(client):
    owner = register(client)
    try:
        first = client.get("/jobs/catalog?source=demo&page_size=2", headers=owner).json()
        second = client.get("/jobs/catalog?source=demo&page_size=2&page=2", headers=owner).json()
        assert first["total"] > 2
        assert len(first["items"]) == len(second["items"]) == 2
        assert {job["id"] for job in first["items"]}.isdisjoint(job["id"] for job in second["items"])
        assert all(job["synthetic"] and job["source_provenance"] for job in first["items"])
        assert sum(source["total"] for source in first["sources"] if source["synthetic"]) == first["total"]
    finally:
        client.delete("/auth/me", headers=owner)


def test_semantic_search_respects_live_job_scope(client):
    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from app.db import engine
    from app.ingestion import ingest, seed_rows
    from app.models import Job, JobSource

    owner = register(client)
    identifier = uuid4().hex
    source = "Test employer " + identifier
    data = next(seed_rows(1))
    data.update(external_id=identifier, source_url=f"https://example.test/jobs/{identifier}")
    try:
        ingest([data], name=source, provenance="Test fixture published board")
        response = client.post(
            "/jobs/search",
            headers=owner,
            json={
                "query": "machine intelligence",
                "mode": "semantic",
                "filters": {"source": "live"},
            },
        )
        assert response.status_code == 200, response.text
        results = response.json()
        assert results["embedding_model"]
        assert any(item["job"]["external_id"] == identifier for item in results["items"])
        assert all(not item["job"]["synthetic"] for item in results["items"])
    finally:
        with Session(engine) as db:
            db.execute(delete(Job).where(Job.external_id == identifier))
            db.execute(delete(JobSource).where(JobSource.name == source))
            db.commit()
        client.delete("/auth/me", headers=owner)


@pytest.mark.parametrize("mode", ["semantic", "hybrid", "personalized"])
def test_machine_learning_internship_search_covers_related_titles(client, mode):
    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from app.db import engine
    from app.ingestion import RELATED_INTERNSHIPS, ingest, seed_rows
    from app.models import Job, JobSource

    owner = register(client)
    tag = uuid4().hex
    name = "Related roles test " + tag
    demo_rows = list(seed_rows(0, include_related=True))
    for index, row in enumerate(demo_rows):
        row["external_id"] = f"{tag}-{index}"
    try:
        ingest(demo_rows, name=name, provenance="Test fixtures", synthetic=True)
        result = client.post(
            "/jobs/search",
            headers=owner,
            json={
                "query": "machine learning intern",
                "mode": mode,
                "limit": 8,
            },
        )
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["filters"]["employment_type"] == "internship"
        jobs = [item["job"] for item in data["items"]]
        assert {row[0] for row in RELATED_INTERNSHIPS} <= {job["title"] for job in jobs}
        assert all(job["employment_type"] == "internship" for job in jobs)
        titles = [job["title"] for job in jobs]
        assert set(titles[: len(set(titles))]) == set(titles)
    finally:
        with Session(engine) as db:
            db.execute(delete(Job).where(Job.external_id.startswith(tag)))
            db.execute(delete(JobSource).where(JobSource.name == name))
            db.commit()
        client.delete("/auth/me", headers=owner)


def test_llm_resume_draft_adds_to_saved_profile_on_confirmation(client, monkeypatch):
    from app import profiles

    class ResumeModel:
        def generate(self, name, content, schema):
            assert name == "resume-extraction" and "Polars" in content
            return schema(
                education=["MSc Statistics, 2024"],
                skills=["Polars", "python"],
                projects=["Forecast dashboard"],
                work_experience=[],
                tools=[],
                certifications=[],
                domains=["Machine Learning"],
                experience_level=None,
                years_experience=None,
            )

    monkeypatch.setattr(profiles, "provider", lambda: ResumeModel())
    monkeypatch.setattr(profiles, "resume_model_config", lambda: ("groq", "test-model", True))
    owner = register(client)
    try:
        client.patch(
            "/profile", headers=owner, json={"profile": {"skills": ["Python", "SQL"]}}
        ).raise_for_status()
        stream = io.BytesIO()
        pdf = Canvas(stream)
        pdf.drawString(25, 800, "Built and evaluated a forecast dashboard using Polars")
        pdf.save()
        parsed = client.post(
            "/resumes/upload",
            headers=owner,
            files={"file": ("resume.pdf", stream.getvalue(), "application/pdf")},
        )
        assert parsed.status_code == 201, parsed.text
        data = parsed.json()
        assert data["extraction_model"] == "test-model"
        assert data["draft"]["skills"] == ["Python", "SQL", "Polars"]
        assert client.get("/profile", headers=owner).json()["profile"]["skills"] == ["Python", "SQL"]
        saved = client.post(f"/resumes/{data['id']}/confirm", headers=owner, json={"profile": data["draft"]})
        assert saved.status_code == 200, saved.text
        assert saved.json()["profile"]["skills"] == ["Python", "SQL", "Polars"]
        assert saved.json()["profile"]["education"] == ["MSc Statistics, 2024"]
    finally:
        client.delete("/auth/me", headers=owner)


def test_agent_live_search_uses_verified_sources_and_links_to_interndoor(client, monkeypatch):
    from app import agent_tools
    from app.live_search import SourceCheck

    calls = []

    def check(query):
        calls.append(query)
        return [SourceCheck(name="Himalayas", status="checked", fetched_count=4)]

    monkeypatch.setattr(agent_tools, "check_public_sources", check)
    owner = register(client)
    try:
        result = (
            client.post(
                "/agent/chat",
                headers=owner,
                json={
                    "message": "Search the web and InternDoor for data science internships",
                },
            )
            .raise_for_status()
            .json()
        )
        assert result["trajectory"][0]["name"] == "search_live_jobs"
        assert result["source_checks"] == [{"name": "Himalayas", "status": "checked", "fetched_count": 4}]
        assert calls[0].filters.source == "live"
        assert calls[0].filters.employment_type == "internship"
        assert all(not item["job"]["synthetic"] for item in result["recommendations"])
        assert result["external_links"][0]["url"] == "https://interndoor.com/?q=data+science+internship"

        handoff = (
            client.post(
                "/agent/chat",
                headers=owner,
                json={
                    "message": "Find machine learning internships only on InternDoor",
                },
            )
            .raise_for_status()
            .json()
        )
        assert len(calls) == 1
        assert not handoff["recommendations"] and not handoff["source_checks"]
        assert handoff["external_links"][0]["url"] == "https://interndoor.com/?q=machine+learning+internship"
    finally:
        client.delete("/auth/me", headers=owner)


def test_live_search_keeps_filters_on_followup(client, monkeypatch):
    from app import agent_tools
    from app.live_search import SourceCheck

    monkeypatch.setattr(
        agent_tools,
        "check_public_sources",
        lambda query: [SourceCheck(name="Himalayas", status="cached", fetched_count=4)],
    )
    owner = register(client)
    try:
        client.patch("/profile", headers=owner, json={"profile": {"years_experience": 0}}).raise_for_status()
        start = (
            client.post(
                "/agent/chat",
                headers=owner,
                json={
                    "message": "Find live data science internships",
                },
            )
            .raise_for_status()
            .json()
        )
        reply = (
            client.post(
                "/agent/chat",
                headers=owner,
                json={
                    "message": "Only remote",
                    "conversation_id": start["conversation_id"],
                },
            )
            .raise_for_status()
            .json()
        )
        assert reply["filters"]["employment_type"] == "internship"
        assert reply["filters"]["remote_policy"] == "remote"
        assert reply["filters"]["source"] == "live"
        assert reply["filters"]["max_required_years"] is None
        strict = (
            client.post(
                "/agent/chat",
                headers=owner,
                json={
                    "message": "Only roles with no experience",
                    "conversation_id": start["conversation_id"],
                },
            )
            .raise_for_status()
            .json()
        )
        assert strict["filters"]["max_required_years"] == 0
    finally:
        client.delete("/auth/me", headers=owner)
