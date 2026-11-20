"""Measure job-search request latency through a running local stack."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median, quantiles
from time import perf_counter
from uuid import uuid4

import httpx

DATASET = Path(__file__).parents[1] / "evaluation/datasets/careerlens-retrieval-v1.json"


def benchmark(url, concurrency):
    cases = json.loads(DATASET.read_text())
    credentials = {"email": f"{uuid4().hex}@benchmark.invalid", "password": uuid4().hex}
    with httpx.Client(base_url=url.rstrip("/") + "/api/careerlens", timeout=30) as client:
        auth = client.post("/auth/register", json=credentials).raise_for_status().json()
        client.headers["Authorization"] = "Bearer " + auth["access_token"]

        def search(case):
            started = perf_counter()
            client.post(
                "/jobs/search",
                json={
                    "query": case["query"],
                    "filters": case["filters"],
                    "mode": "hybrid",
                    "limit": 10,
                },
            ).raise_for_status()
            return (perf_counter() - started) * 1000

        try:
            search(cases[0])
            started = perf_counter()
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                durations = list(pool.map(search, cases))
            elapsed = perf_counter() - started
        finally:
            client.delete("/auth/me").raise_for_status()

    return {
        "requests": len(durations),
        "concurrency": concurrency,
        "latency_p50_ms": round(median(durations), 2),
        "latency_p95_ms": round(quantiles(durations, n=20, method="inclusive")[18], 2),
        "requests_per_second": round(len(durations) / elapsed, 2),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:3000")
    parser.add_argument("--concurrency", default=4, type=int, choices=range(1, 17), metavar="[1–16]")
    arguments = parser.parse_args()
    print(json.dumps(benchmark(arguments.url, arguments.concurrency), indent=2))
