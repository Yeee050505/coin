import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str = "sqlite:///./research.db"
    deepseek_api_key: str = ""
    deepseek_api_base: str = "https://api.deepseek.com/v1"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    llm_model: str = "deepseek-chat"
    llm_provider: str = "deepseek"
    local_model_path: str = ""
    tavily_api_key: str = ""
    max_retries: int = 3
    circuit_breaker_threshold: int = 5
    circuit_breaker_recovery: int = 30
    rate_limit_global: int = 20
    rate_limit_user: int = 5

    class Config:
        env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
        env_file_encoding = "utf-8"

settings = Settings()
