import argparse
import json
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep
from uuid import NAMESPACE_URL, uuid5

from langfuse.experiment import Evaluation
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import engine
from ..models import EvaluationResult, EvaluationRun
from ..observability import telemetry
from .runner import dataset_file


def artifact(run_id):
    with Session(engine) as db:
        run = db.get(EvaluationRun, run_id)
        if run is None or run.status != "completed":
            raise ValueError("Select a completed evaluation run")
        dataset = run.configuration["dataset"]
        outcomes = {
            row.case_id: row.outcome
            for row in db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run.id))
        }
        return dataset, run.configuration, outcomes


def publish(client, run_id):
    name, config, outcomes = artifact(run_id)
    dataset_name = f"careerlens-{name}-v1"
    client.create_dataset(name=dataset_name, description="Versioned CareerLens synthetic fixture cases")
    for item in json.loads(dataset_file(name).read_text()):
        client.create_dataset_item(
            dataset_name=dataset_name,
            id=str(uuid5(NAMESPACE_URL, f"{dataset_name}/{item['id']}")),
            input={"case_id": item["id"]},
            expected_output={"fixture_version": dataset_name},
        )
    dataset = client.get_dataset(dataset_name)

    def task(*, item, **kwargs):
        outcome = outcomes[item.input["case_id"]]
        return {key: value for key, value in outcome.items() if isinstance(value, (bool, int, float))}

    def evaluate(*, output, **kwargs):
        return [Evaluation(name=key, value=float(value)) for key, value in output.items()]

    result = dataset.run_experiment(
        name=config["label"],
        run_name=str(run_id),
        task=task,
        evaluators=[evaluate],
        metadata={key: str(value) for key, value in config.items()},
    )
    client.flush()
    print(result.format())


def smoke(client, trace_id):
    client.flush()
    deadline = monotonic() + 45
    window_start = datetime.now(UTC) - timedelta(hours=1)
    while monotonic() < deadline:
        observations = client.api.observations.get_many(
            fields="core,basic",
            trace_id=trace_id,
            from_start_time=window_start,
            to_start_time=datetime.now(UTC),
            limit=1,
        )
        if any(observation.trace_id == trace_id for observation in observations.data):
            print(f"Langfuse received trace {trace_id}")
            return
        sleep(2)
    raise RuntimeError("Trace did not arrive before the verification deadline")


def main():
    from uuid import UUID

    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["publish", "smoke"])
    parser.add_argument("identifier")
    args = parser.parse_args()
    if not telemetry.client:
        parser.error("Enable Langfuse and configure both Langfuse keys")
    if args.operation == "publish":
        publish(telemetry.client, UUID(args.identifier))
    else:
        smoke(telemetry.client, args.identifier)


if __name__ == "__main__":
    main()
