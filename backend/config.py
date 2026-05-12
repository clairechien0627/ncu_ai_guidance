from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-12-01-preview"
    azure_chat_deployment: str = "gpt-4o"
    azure_mini_deployment: str = "gpt-4o-mini"
    azure_embedding_deployment: str = "text-embedding-3-large"
    langsmith_api_key: str = ""
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_tracing: str = "true"
    langsmith_project: str = "report-agent"
    database_url: str = "postgresql://postgres:postgres@127.0.0.1:5432/reportdb"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    # Blob Storage（本地開發留空則繼續用本地磁碟）
    azure_storage_connection_string: str = ""
    azure_storage_container: str = "uploads"
    upload_dir: str = "./uploads"
    llama_cloud_api_key: str = ""
    azure_document_intelligence_endpoint: str = ""
    azure_document_intelligence_key: str = ""
    batch_folder: str = ""  # Absolute path to batch import folder; defaults to ../各系大專生計畫(104-114)
    
    # Arize Phoenix (OpenInference / OTel)
    phoenix_enabled: bool = True
    phoenix_collector_endpoint: str = "http://127.0.0.1:4317"

    # Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"

    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()
