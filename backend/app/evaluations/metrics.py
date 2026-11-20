import math
from statistics import median, quantiles


def retrieval_scores(ids, grades):
    hits = [grades.get(key, 0) for key in ids]
    ideal = sorted(grades.values(), reverse=True)
    relevant = sum(value > 0 for value in ideal)

    def dcg(values, k):
        return sum((2**grade - 1) / math.log2(i + 2) for i, grade in enumerate(values[:k]))

    return {
        "precision_at_5": sum(value > 0 for value in hits[:5]) / 5,
        "recall_at_5": sum(value > 0 for value in hits[:5]) / relevant if relevant else None,
        "recall_at_10": sum(value > 0 for value in hits[:10]) / relevant if relevant else None,
        "mrr": next((1 / i for i, value in enumerate(hits, 1) if value > 0), 0) if relevant else None,
        "ndcg_at_5": dcg(hits, 5) / dcg(ideal, 5) if relevant else None,
        "ndcg_at_10": dcg(hits, 10) / dcg(ideal, 10) if relevant else None,
        "empty_correct": not hits if not relevant else None,
    }


def latency(values):
    return {
        "latency_p50_ms": median(values) if values else None,
        "latency_p95_ms": quantiles(values, n=20, method="inclusive")[18]
        if len(values) > 1
        else next(iter(values), None),
    }


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None
