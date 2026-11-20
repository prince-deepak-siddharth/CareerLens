# Langfuse observability

Set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_BASE_URL` in the local `.env`. CareerLens enables tracing when both keys are present. Set `CAREERLENS_TELEMETRY_ENABLED=false` to disable tracing explicitly. Langfuse Cloud is the default base URL. Self-hosted instances can use their own base URL. Set `LANGFUSE_SAMPLE_RATE` to tune trace export. [Langfuse SDK documentation](https://langfuse.com/docs/observability/sdk/python)

Langfuse records observations from agent turns, tool calls, retrieval, ranking, and model generation. It uses the conversation ID as the session ID and a keyed hash as the user ID. Exported metadata excludes raw resumes, chat messages, and user profiles. Model observations include prompt versions, usage, and configured cost estimates. [Langfuse self-hosting](https://langfuse.com/self-hosting)

After enabling tracing, send a chat message and read its `trace_id` in the API response. Verify delivery with:

```sh
docker compose exec api python -m app.evaluations.langfuse_tools smoke TRACE_ID
```

Set `CAREERLENS_ONLINE_EVAL_RATE` to sample source-consistency scores for traced recommendations. Offline evaluation results are available in the workspace's Evaluations screen, and completed runs can be published to Langfuse as experiments.
