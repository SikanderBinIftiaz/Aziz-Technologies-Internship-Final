"""Application settings, loaded from environment variables (and a local .env file)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- LLM ----
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"
    llm_max_tokens: int = 1024
    llm_timeout_seconds: float = 60.0
    price_input_per_mtok: float = 3.0
    price_output_per_mtok: float = 15.0
    agent_max_steps: int = 4
    history_messages: int = 6

    # ---- Embeddings and retrieval ----
    embedding_backend: str = "auto"  # auto | local | hash
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    chunk_size: int = 800
    chunk_overlap: int = 150
    top_k: int = 6
    similarity_threshold: float | None = None

    # ---- Storage ----
    database_url: str = "sqlite:///./data/documind.db"
    chroma_path: str = "./data/chroma"
    upload_dir: str = "./data/uploads"
    max_upload_mb: int = 15

    # ---- Security ----
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 120
    rate_limit_chat: str = "30/minute"
    rate_limit_auth: str = "10/minute"
    rate_limit_upload: str = "20/minute"

    # ---- App ----
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:8000"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimated USD cost for a request, based on the configured per-million-token prices."""
        return (
            input_tokens * self.price_input_per_mtok + output_tokens * self.price_output_per_mtok
        ) / 1_000_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
