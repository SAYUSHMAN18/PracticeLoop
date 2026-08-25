from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "practiceloop"

    # LLM
    llm_provider: str = "groq"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    gemini_api_key: str = ""
    aws_region: str = "ap-south-1"
    bedrock_model_id: str = ""
    llm_min_interval_seconds: float = 2.1

    # Database
    database_url: str = "postgresql://postgres:postgres@127.0.0.1:5435/practiceloop"

    # Auth
    session_secret: str = "dev-secret-change-me"

    # Retrieval
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    retrieval_top_k: int = 5

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
