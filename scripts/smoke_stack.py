"""Exercise the running application through the Next.js API proxy."""

import argparse
import io
import subprocess
from time import monotonic, sleep
from uuid import uuid4

import httpx
from reportlab.pdfgen.canvas import Canvas


def verify(response, status):
    if response.status_code != status:
        raise AssertionError(
            f"{response.request.method} {response.request.url.path}: {response.status_code}"
            f" expected {status}: {response.text[:300]}"
        )
    return response.json() if status != 204 else None


def wait_ready(client, path):
    deadline = monotonic() + 30
    while monotonic() < deadline:
        try:
            if client.get(path).status_code == 200:
                return
        except httpx.TransportError:
            pass
        sleep(1)
    raise RuntimeError("The API did not become ready after restart")


def smoke(base_url, restart_api=False):
    with httpx.Client(base_url=base_url, timeout=30) as client:
        assert "CareerLens" in client.get("/").raise_for_status().text
        prefix = "/api/careerlens"
        assert verify(client.get(prefix + "/ready"), 200)["status"] == "ready"
        credentials = {"email": f"{uuid4().hex}@smoke.invalid", "password": uuid4().hex + "-demo"}
        verify(client.post(prefix + "/auth/register", json=credentials), 201)
        auth = verify(client.post(prefix + "/auth/login", json=credentials), 200)
        client.headers["Authorization"] = "Bearer " + auth["access_token"]
        try:
            pdf = io.BytesIO()
            page = Canvas(pdf)
            page.drawString(30, 800, "Python, PostgreSQL, PyTorch")
            page.save()
            draft = verify(
                client.post(
                    prefix + "/resumes/upload",
                    files={"file": ("resume.pdf", pdf.getvalue(), "application/pdf")},
                ),
                201,
            )
            assert "Python" in draft["draft"]["skills"]
            verify(
                client.post(f"{prefix}/resumes/{draft['id']}/confirm", json={"profile": draft["draft"]}), 200
            )
            search = verify(
                client.post(
                    prefix + "/jobs/search",
                    json={
                        "query": "artificial intelligence research",
                        "mode": "hybrid",
                        "filters": {
                            "employment_type": "internship",
                            "remote_policy": "remote",
                            "skills": ["Python"],
                        },
                    },
                ),
                200,
            )
            assert search["items"] and all(item["job"]["synthetic"] for item in search["items"])
            first = verify(
                client.post(
                    prefix + "/agent/chat",
                    json={
                        "message": "Find remote AI internships requiring Python",
                    },
                ),
                200,
            )
            conversation_id = first["conversation_id"]
            second = verify(
                client.post(
                    prefix + "/agent/chat",
                    json={
                        "message": "Only show startups",
                        "conversation_id": conversation_id,
                    },
                ),
                200,
            )
            assert (
                second["filters"]["remote_policy"] == "remote"
                and second["filters"]["company_category"] == "startup"
            )
            job_id = second["recommendations"][0]["job"]["id"]
            verify(client.post(f"{prefix}/jobs/{job_id}/save"), 204)
            assert verify(client.get(prefix + "/jobs/saved"), 200)[0]["id"] == job_id
            verify(
                client.post(prefix + "/recommendations/feedback", json={"job_id": job_id, "kind": "helpful"}),
                201,
            )
            verify(
                client.post(prefix + "/agent/chat", json={"message": "Remember that I prefer Python roles"}),
                200,
            )
            if restart_api:
                subprocess.run(["docker", "compose", "restart", "api"], check=True)
                wait_ready(client, prefix + "/ready")
            new_session = verify(
                client.post(prefix + "/agent/chat", json={"message": "What are my preferences?"}), 200
            )
            assert "python" in new_session["message"] and new_session["conversation_id"] != conversation_id
            verify(client.get(prefix + "/evaluation/runs"), 403)
            print(
                "Verified health, login, PDF review, job search, multi-turn chat, saved jobs, feedback, and memory."
            )
        finally:
            verify(client.delete(prefix + "/auth/me"), 204)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:3000")
    parser.add_argument(
        "--restart-api", action="store_true", help="Restart the Compose API before checking saved memory"
    )
    arguments = parser.parse_args()
    smoke(arguments.url, arguments.restart_api)
