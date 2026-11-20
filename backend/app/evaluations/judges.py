from dataclasses import dataclass
from time import perf_counter


@dataclass
class DeepEvalJudge:
    model: str
    threshold: float = 0.7

    def evaluate(self, question, answer, evidence):
        from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
        from deepeval.test_case import LLMTestCase

        case = LLMTestCase(input=question, actual_output=answer, retrieval_context=evidence)
        output = {}
        for name, metric in {
            "answer_relevancy": AnswerRelevancyMetric(
                model=self.model, threshold=self.threshold, async_mode=False
            ),
            "faithfulness": FaithfulnessMetric(model=self.model, threshold=self.threshold, async_mode=False),
        }.items():
            started = perf_counter()
            try:
                metric.measure(case)
                output[name] = {
                    "score": metric.score,
                    "reason": metric.reason,
                    "passed": metric.is_successful(),
                }
            except Exception as exc:  # noqa: BLE001 - external judging failures are explicit outcomes
                output[name] = {
                    "score": None,
                    "reason": type(exc).__name__,
                    "passed": False,
                    "review_required": True,
                }
            output[name].update(
                model=self.model,
                evaluator="deepeval",
                threshold=self.threshold,
                latency_ms=(perf_counter() - started) * 1000,
            )
        return output
