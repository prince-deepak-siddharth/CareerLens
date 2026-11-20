FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /bin/uv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/srv/backend/.venv/bin:$PATH" CAREERLENS_DATASETS_DIR=/srv/evaluation/datasets
WORKDIR /srv/backend
COPY backend/pyproject.toml backend/uv.lock ./
ARG UV_EXTRAS=""
RUN uv sync --locked --no-dev --no-install-project $UV_EXTRAS
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY evaluation/datasets ../evaluation/datasets
RUN useradd --create-home --uid 10001 careerlens && install -d -o careerlens -g careerlens /model-cache
USER careerlens
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
