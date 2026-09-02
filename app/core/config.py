import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SESSION_SECRET = "dev-secret-change-me"


class Settings(BaseSettings):
    app_name: str = "practiceloop"
    app_env: str = "development"  # development | production

    # LLM
    llm_provider: str = "groq"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    aws_region: str = "ap-south-1"
    bedrock_model_id: str = ""
    llm_min_interval_seconds: float = 2.1
    llm_daily_budget: int = 20  # per-user calls/day across all AI-backed routes
    # Deployment-wide ceiling across every user, checked alongside the per-user
    # budget. 0 disables it -- the right default for a self-hosted single-user
    # install, where the per-user cap already bounds everything. A public deploy
    # with open signup wants a real number here (see docs in .env.example).
    llm_global_daily_budget: int = 0

    # Database
    database_url: str = "postgresql://postgres:postgres@127.0.0.1:5435/practiceloop"

    # Auth
    session_secret: str = DEFAULT_SESSION_SECRET
    disable_rate_limits: bool = False  # tests flip this on -- the limiter is per-IP

    # Public identity -- used for canonical URLs, Open Graph tags, the
    # sitemap, and llms.txt. Must be the absolute origin the site is
    # actually served from: a canonical pointing somewhere else is worse
    # for search than having none at all.
    public_base_url: str = "https://practiceloop.onrender.com"

    # Observability
    sentry_dsn: str = ""  # unset disables error reporting entirely, no-op at import
    sentry_traces_sample_rate: float = 0.0

    # Retrieval
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    retrieval_top_k: int = 5

    # Jobs (Phase 1: scheduled discovery)
    jobs_cron_token: str = ""  # required to call POST /jobs/cron/discover
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    adzuna_country: str = "in"  # Adzuna's country code, e.g. in | us | gb
    jobs_max_listings_per_user: int = 25  # per source, per run -- keeps each run bounded

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


DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:5435/practiceloop"


def verify_production_config() -> None:
    """Boot-time guard for the ways a production deploy can be wrong in a
    way nothing else would surface.

    Two of these refuse to start, because running anyway is worse than not
    running: a default session secret means every session cookie is
    forgeable by anyone who has read this repo, and the default DATABASE_URL
    in production means the app is pointed at a localhost database that
    isn't there (or, far worse on a shared host, one that is).

    The missing-LLM case only warns. A deliberately AI-free deploy is a
    supported configuration -- most of the app degrades to a real
    deterministic path -- but it silently turns off diagnostics and Writing
    Lab entirely, which has no non-LLM equivalent, so it should never be
    something you discover from a confused user rather than from the logs.
    """
    problems = []
    if settings.app_env == "production":
        if settings.session_secret == DEFAULT_SESSION_SECRET:
            problems.append(
                "SESSION_SECRET is still the default -- set a real random value before deploying."
            )
        if settings.database_url == DEFAULT_DATABASE_URL:
            problems.append(
                "DATABASE_URL is still the local development default -- point it at the real database."
            )

    if problems:
        raise RuntimeError("APP_ENV=production, but: " + " ".join(problems))

    if settings.app_env == "production":
        # Imported here, not at module scope: core.llm imports core.config,
        # so doing it at the top would be a cycle.
        from app.core.llm import is_configured

        if not is_configured():
            logging.getLogger(__name__).warning(
                "No LLM provider is configured (LLM_PROVIDER=%s). The app will run, but "
                "diagnostics and Writing Lab are unavailable and every other AI-backed "
                "feature falls back to its deterministic path.",
                settings.llm_provider,
            )


def configure_error_reporting() -> None:
    """Wire up Sentry when SENTRY_DSN is set, and do nothing at all when it
    isn't -- unhandled exceptions otherwise only reach stdout, and free-tier
    log retention is short enough that a crash reported by a user hours
    later has already scrolled out of existence.

    A missing sentry-sdk is a warning, not a crash: the dependency is
    optional (pip install -e ".[sentry]") and a deploy that sets the DSN
    without installing it should still serve traffic.
    """
    dsn = settings.sentry_dsn.strip()
    if not dsn:
        return

    try:
        import sentry_sdk
    except ImportError:
        logging.getLogger(__name__).warning(
            "SENTRY_DSN is set but sentry-sdk isn't installed -- "
            'error reporting is off. Install it with: pip install -e ".[sentry]"'
        )
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.app_env,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # Session cookies, resume text, mentor conversations and typed
        # answers all pass through this app. None of it belongs in an
        # error report, and the default here is the one that leaks least.
        send_default_pii=False,
    )
    logging.getLogger(__name__).info("Sentry error reporting enabled (environment=%s)", settings.app_env)
