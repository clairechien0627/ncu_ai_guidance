from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        # "ignore" silently drops unknown env vars instead of accepting them,
        # which catches typos that would otherwise be invisible.
        extra="ignore",
    )

    azure_openai_api_key: SecretStr = SecretStr("")
    azure_openai_endpoint: str = ""
    # Preview version enables structured-output features; bump to stable once GA.
    azure_openai_api_version: str = "2024-12-01-preview"
    azure_chat_deployment: str = "gpt-4o"
    azure_mini_deployment: str = "gpt-4o-mini"
    azure_embedding_deployment: str = "text-embedding-3-large"
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_tracing: str = "true"
    langsmith_project: str = "report-agent"
    database_url: str = "postgresql://postgres:postgres@127.0.0.1:5432/reportdb"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr = SecretStr("")
    # Blob Storage（本地開發留空則繼續用本地磁碟）
    azure_storage_connection_string: SecretStr = SecretStr("")
    azure_storage_container: str = "uploads"
    upload_dir: str = "./uploads"
    # Pydantic maps this to env var LLAMA_CLOUD_API_KEY (not LLAMAPARSE_API_KEY).
    # If your .env uses LLAMAPARSE_API_KEY, rename it to LLAMA_CLOUD_API_KEY.
    llama_cloud_api_key: SecretStr = SecretStr("")
    azure_document_intelligence_endpoint: str = ""
    azure_document_intelligence_key: SecretStr = SecretStr("")
    batch_folder: str = ""  # Absolute path to batch import folder; defaults to ../各系大專生計畫(104-114)

    # Logging
    log_level: str = "INFO"      # DEBUG | INFO | WARNING | ERROR
    log_format: str = "json"     # json | text

    # Auth / JWT
    jwt_secret_key: str = "change-me-in-production-please"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24
    google_client_id: str = ""  # OAuth 2.0 Client ID from Google Cloud Console

    # Langfuse
    langfuse_enabled: bool = True
    langfuse_public_key: SecretStr = SecretStr("")
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_base_url: str = "http://localhost:3000"
    # Backward-compatible alias used by older local env files.
    langfuse_host: str = "http://localhost:3000"


settings = Settings()
