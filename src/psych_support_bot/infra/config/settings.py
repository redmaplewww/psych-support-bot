import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AI Psychological Support Bot"
    environment: str = "development"
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")
    database_url: str = "sqlite:///./psych_support_bot.db"
    redis_url: str = "redis://localhost:6379/0"
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="https://cloud.langfuse.com", alias="LANGFUSE_HOST")
    default_conversation_mode: str = "support"
    app_debug: bool = False
    # LLM-as-judge model (separate model for evaluation scoring)
    judge_api_key: str = Field(default="", alias="JUDGE_API_KEY")
    judge_base_url: str = Field(default="", alias="JUDGE_BASE_URL")
    judge_model: str = Field(default="DeepSeek-V4-Flash", alias="JUDGE_MODEL")
    # M2 首答延迟优化：规则判 low/elevated 且支持模式时，风险 LLM 分类与回复
    # 生成并行投机；风险升级 high/critical 则丢弃投机回复走危机路径。
    # 测试环境由 conftest 置 false，避免单测触发真实回复生成。
    speculative_reply_enabled: bool = Field(default=True, alias="SPECULATIVE_REPLY_ENABLED")

    @model_validator(mode="after")
    def apply_dashscope_fallbacks(self) -> "Settings":
        if not self.openai_api_key:
            self.openai_api_key = os.getenv("DASHSCOPE_API_KEY", "")
        if not self.openai_base_url:
            self.openai_base_url = os.getenv("DASHSCOPE_BASE_URL", "")
        if self.openai_model in {"", "gpt-4.1-mini"}:
            self.openai_model = os.getenv("DASHSCOPE_MODEL", self.openai_model)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
