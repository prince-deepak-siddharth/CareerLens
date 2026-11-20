# Evaluations and experiments

The runner uses a separate PostgreSQL database for job fixtures, generated accounts and conversations. It refuses an evaluation URL with the same database name as the application URL. Evaluation metadata and results go into the application database for administrators to inspect.

## Datasets

- `careerlens-agent-single-turn-v1` and `careerlens-agent-multiturn-v1` specify required and forbidden tools, state changes, grounding, and expected failure recovery.
- `careerlens-prompt-injection-v1` uses bounded defensive scenarios and confirms that instructions do not create unauthorized state changes.
- `careerlens-retrieval-v1` contains 105 queries with graded synthetic fixture-role expectations. The runner also reads a literal `judgments` mapping of job external IDs to grades when present. Use that format with separately collected reviewer judgments and version the dataset before comparing models on it.
- `careerlens-resume-extraction-v1` checks extraction against synthetic resume text with expected fields.
- `careerlens-job-explanations-v1` verifies evidence, confirmed skills, and completeness. DeepEval can additionally judge answer relevancy and faithfulness over the same synthetic cases.

Metrics with no denominator or unavailable costs remain null. Retrieval uses all eligible fixture jobs as the recall denominator. Empty relevance sets are excluded from Recall, MRR and NDCG means and tracked through empty-result accuracy. Operational failure cases deliberately lower tool validity or execution success while the case can still pass if recovery behaves as expected.

## Compare and review

```sh
docker compose exec api python -m app.evaluations.runner agent-single-turn \
  --label baseline --prompt-label production --output /tmp/baseline.json
docker compose exec api python -m app.evaluations.runner agent-single-turn \
  --label candidate --prompt-label candidate --output /tmp/candidate.json
docker compose exec api python -m app.evaluations.compare /tmp/baseline.json /tmp/candidate.json --tolerance 0.02
```

Prompt comparison requires configured Langfuse prompts and the OpenAI provider. Reports containing the local fallback prompt should be treated as local runs. The regression CLI exits nonzero when a shared quality metric declines beyond the threshold. Review quality, latency, and reported cost before moving a candidate to the active prompt label. Preserve the previous known-good Langfuse version for rollback.

Use the Evaluations screen to select a completed run, open a case, record a supported, unsupported, or abstain verdict, and give its rationale. The original run metrics remain intact. Use independently reviewed labels when measuring judge agreement. There is no calibrated human-label benchmark bundled with this repository.

Run DeepEval with the `judges` extra, a configured judge credential, and `job-explanations --judge-model MODEL`. The runner records judge failures as unavailable scores and flags their cases for review. The manual live workflow restricts each run to one synthetic dataset with an execution deadline. Model costs appear only with reported token usage and configured prices.

Use `python -m app.evaluations.langfuse_tools publish RUN_ID` to sync synthetic case identifiers and publish a stored run as a Langfuse experiment.

The OpenAI adapter records the actual planner prompt versions observed during each agent run, including the fallback when a configured label cannot be fetched. The optional judge can also evaluate grounded agent turns when a judge model is selected for an agent dataset.
