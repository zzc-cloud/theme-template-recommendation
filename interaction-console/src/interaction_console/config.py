"""服务配置和路径常量。

配置只读取 interaction-console 自己的 `.env` 和环境变量，避免和仓库中其它 Python
服务的配置混用。所有上游地址拼接集中在这里，便于本地、测试或部署环境切换。
"""

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 从 src/interaction_console/config.py 回到 interaction-console 工程根目录。
ROOT_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT_DIR / "web"

load_dotenv(ROOT_DIR / ".env")


class Settings(BaseSettings):
    """运行时配置；环境变量优先，缺省值适合本地开发。"""

    host: str = Field(default="0.0.0.0", validation_alias="INTERACTION_CONSOLE_HOST")
    port: int = Field(default=5174, validation_alias="INTERACTION_CONSOLE_PORT")
    deepagents_base_url: str = Field(default="http://localhost:8000", validation_alias="DEEPAGENTS_BASE_URL")

    model_config = SettingsConfigDict(extra="ignore")

    @property
    def deepagents_recommend_url(self) -> str:
        """上游推荐接口固定为 base URL 下的 /api/v1/recommend。"""
        return f"{self.deepagents_base_url.rstrip('/')}/api/v1/recommend"


@lru_cache
def get_settings() -> Settings:
    """缓存配置对象，避免每次请求重复解析环境变量。"""
    return Settings()
