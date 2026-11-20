import argparse
import json
from pathlib import Path

QUALITY_DIRECTIONS = {
    "task_completion_rate": 1,
    "tool_selection_accuracy": 1,
    "trajectory_correctness": 1,
    "state_consistency": 1,
    "memory_accuracy": 1,
    "tool_argument_validity": 1,
    "ndcg_at_10": 1,
    "recall_at_10": 1,
    "skill_f1": 1,
    "faithfulness": 1,
    "answer_relevancy": 1,
    "groundedness": 1,
    "structured_output_validity": 1,
    "hallucination_violation_rate": -1,
    "hallucination_violation": -1,
}


def compare(baseline, candidate, tolerance=0.02):
    if baseline["dataset"] != candidate["dataset"]:
        raise ValueError("Compare runs on the same dataset")

    def changes(before, after, prefix=""):
        result = []
        for key, original in before.items():
            current = after.get(key)
            if isinstance(original, dict) and isinstance(current, dict):
                result.extend(changes(original, current, prefix + key + "."))
            elif isinstance(original, (int, float)) and isinstance(current, (int, float)):
                delta = current - original
                direction = QUALITY_DIRECTIONS.get(key, 0)
                result.append(
                    {
                        "metric": prefix + key,
                        "baseline": original,
                        "candidate": current,
                        "delta": delta,
                        "regressed": direction * delta < -tolerance,
                    }
                )
        return result

    values = changes(baseline["metrics"], candidate["metrics"])
    return {
        "dataset": baseline["dataset"],
        "baseline_run": baseline["run_id"],
        "candidate_run": candidate["run_id"],
        "tolerance": tolerance,
        "changes": values,
        "regressions": [item["metric"] for item in values if item["regressed"]],
    }


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("baseline", type=Path)
    cli.add_argument("candidate", type=Path)
    cli.add_argument("--tolerance", type=float, default=0.02)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    if not 0 <= args.tolerance <= 1:
        cli.error("Tolerance must be between 0 and 1")
    report = compare(
        json.loads(args.baseline.read_text()), json.loads(args.candidate.read_text()), args.tolerance
    )
    output = json.dumps(report, indent=2) + "\n"
    print(output, end="")
    if args.output:
        args.output.write_text(output)
    if report["regressions"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
