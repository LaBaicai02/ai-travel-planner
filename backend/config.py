"""Process environment configuration. No dotenv files are loaded."""
import os
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)
    mode: Literal["demo", "live"] = "demo"
    dashscope_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_model: str = "qwen3.7-plus"
    bocha_api_key: SecretStr = SecretStr("")
    bocha_base_url: str = "https://api.bocha.cn"

    trip_timeout_seconds: float = Field(default=120, gt=0, le=600, allow_inf_nan=False)
    llm_timeout_seconds: float = Field(default=45, gt=0, le=300, allow_inf_nan=False)
    search_timeout_seconds: float = Field(default=10, gt=0, le=60, allow_inf_nan=False)
    max_search_calls: int = Field(default=6, ge=1, le=20)
    max_model_calls: int = Field(default=5, ge=1, le=20)
    max_search_http_attempts: int = Field(default=8, ge=1, le=40)
    max_model_http_attempts: int = Field(default=6, ge=1, le=40)
    max_upstream_retries: int = Field(default=1, ge=0, le=3)

    @classmethod
    def from_env(cls):
        return cls(
            **{
                name: os.environ[name.upper()]
                for name in (
                    "trip_timeout_seconds", "llm_timeout_seconds", "search_timeout_seconds",
                    "max_search_calls", "max_model_calls", "max_search_http_attempts",
                    "max_model_http_attempts", "max_upstream_retries",
                )
                if name.upper() in os.environ
            },
            mode=os.environ.get("TRAVEL_MODE", "demo").strip(),
            dashscope_api_key=os.environ.get("DASHSCOPE_API_KEY", "").strip(),
            llm_base_url=os.environ.get("LLM_BASE_URL", "").strip(),
            llm_model=os.environ.get("LLM_MODEL", "qwen3.7-plus").strip(),
            bocha_api_key=os.environ.get("BOCHA_API_KEY", "").strip(),
            bocha_base_url=os.environ.get("BOCHA_BASE_URL", "https://api.bocha.cn").strip(),
        )

    @property
    def configuration_issues(self) -> list[str]:
        if self.mode == "demo":
            return []
        issues = []
        for name, value in [
            ("DASHSCOPE_API_KEY", self.dashscope_api_key.get_secret_value()),
            ("BOCHA_API_KEY", self.bocha_api_key.get_secret_value()),
            ("LLM_MODEL", self.llm_model),
        ]:
            if not value:
                issues.append(name)
        for name, value in [("LLM_BASE_URL", self.llm_base_url), ("BOCHA_BASE_URL", self.bocha_base_url)]:
            try:
                url = urlsplit(value)
                valid = url.scheme == "https" and bool(url.hostname) and not url.username and not url.password and not url.query and not url.fragment
                _ = url.port
            except ValueError:
                valid = False
            if not valid:
                issues.append(name)
        return issues

    @property
    def configured(self) -> bool:
        return not self.configuration_issues
