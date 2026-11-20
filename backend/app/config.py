from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_RANK_WEIGHTS = {
    "relevance": 0.35,
    "skills": 0.25,
    "preference": 0.15,
    "experience": 0.10,
    "freshness": 0.10,
    "feedback": 0.05,
}


def validate_rank_weights(value):
    if value is not None and (
        value.keys() != DEFAULT_RANK_WEIGHTS.keys()
        or any(not 0 <= weight <= 1 for weight in value.values())
        or not sum(value.values())
    ):
        raise ValueError("Provide all ranking weights between 0 and 1 with a nonzero total")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="CAREERLENS_")

    database_url: str = "postgresql+psycopg://careerlens:careerlens@127.0.0.1:5432/careerlens"
    evaluation_database_url: str | None = None
    redis_url: str = "redis://127.0.0.1:6379/0"
    jwt_secret: SecretStr = SecretStr("local-development-secret-replace-before-production")
    environment: Literal["development", "production", "test"] = "development"
    allowed_origins: list[str] = ["http://localhost:3000"]
    admins: list[str] = []
    agent_live_search: bool = False
    greenhouse_boards: str = "figma,duolingo"
    himalayas_search_enabled: bool = True
    live_feed_cache_seconds: int = Field(900, ge=60, le=86400)
    embedding_model: str = "local-hash-v1"
    gemini_api_key: SecretStr | None = None
    cross_encoder: str = ""
    retrieval_min_candidates: int = Field(60, ge=10, le=400)
    retrieval_oversample: int = Field(4, ge=1, le=12)
    retrieval_rrf_k: int = Field(60, ge=1, le=200)
    rerank_candidates: int = Field(40, ge=10, le=200)
    llm_provider: Literal["local", "openai", "groq"] = "local"
    agent_model_provider: Literal["inherit", "local", "openai", "groq", "llama"] = "inherit"
    llama_url: str = "http://127.0.0.1:8080/v1"
    llama_model: str = "qwen2.5-0.5b-instruct"
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-20b"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    llm_input_usd_per_million: float | None = Field(None, ge=0)
    llm_output_usd_per_million: float | None = Field(None, ge=0)
    model_timeout: float = Field(12, gt=0, le=60)
    model_output_tokens: int = Field(1000, ge=100, le=4000)
    max_model_calls_per_turn: int = Field(2, ge=1, le=4)
    rank_weights: dict[str, float] = DEFAULT_RANK_WEIGHTS.copy()
    telemetry_enabled: bool = True
    online_eval_rate: float = Field(0, ge=0, le=1)
    trace_salt: SecretStr | None = None
    prompt_label: str = "production"

    check_rank_weights = field_validator("rank_weights")(validate_rank_weights)


@lru_cache
def settings() -> Settings:
    config = Settings()
    if config.environment == "production":
        secret = config.jwt_secret.get_secret_value()
        if len(secret) < 32 or secret == Settings.model_fields["jwt_secret"].default.get_secret_value():
            raise RuntimeError("Set a unique CAREERLENS_JWT_SECRET of at least 32 characters in production")
    return config
