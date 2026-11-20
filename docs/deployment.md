# Development and deployment

Use `docker compose up --build -d --wait` for local dependencies and serving. Compose binds the exposed application ports to loopback. Read `docker compose logs migrate api worker` to diagnose readiness errors.

For host development, start a pgvector PostgreSQL server and Redis. Create application and evaluation databases. From `backend`, set `CAREERLENS_DATABASE_URL`, `CAREERLENS_EVALUATION_DATABASE_URL`, and `CAREERLENS_REDIS_URL`. Apply migrations to both databases and seed each database before running tests.

```sh
uv sync --locked --python 3.12
uv run --locked alembic upgrade head
uv run --locked python -m app.ingestion seed --count 5000
CAREERLENS_DATABASE_URL="$CAREERLENS_EVALUATION_DATABASE_URL" uv run --locked alembic upgrade head
CAREERLENS_DATABASE_URL="$CAREERLENS_EVALUATION_DATABASE_URL" uv run --locked python -m app.ingestion seed --count 120
uv run --locked uvicorn app.main:app --reload
```

Run `uv run --locked python -m app.queueing` from a separate backend shell. In `frontend`, run `npm ci && npm run dev`. The default Next.js rewrite points to the local FastAPI port. Set `CAREERLENS_API_ORIGIN` at frontend build time for another deployment.

For production, build the Docker images with the desired public Langfuse URL and internal API origin. Use persistent PostgreSQL and Redis services with restricted credentials, backups, TLS, and network isolation. Provision pgvector and apply migrations with a dedicated operator. Use different roles and grants for application and evaluation databases. Set `CAREERLENS_ENVIRONMENT=production`, a unique `CAREERLENS_JWT_SECRET`, the exact browser origin, and a release SHA in `CAREERLENS_CODE_COMMIT`. Configure the gateway to preserve authenticated traffic, enforce body limits, and keep administrative and telemetry endpoints private.

Run the API and worker separately. Add multiple API replicas behind the proxy as needed and scrape each process independently. Run the retention job on a scheduler. Enable cloud tracing and live generation only after configuring credentials and verifying test trace delivery on the target deployment. Use `CAREERLENS_UV_EXTRAS` to install optional model components during the image build.
