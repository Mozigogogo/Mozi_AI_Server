import os
from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _env(name: str) -> str | None:
    """读取 Railway / 系统环境变量。"""
    val = os.environ.get(name)
    if val is None or val == "":
        return None
    return val


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Railway Variables 注入到 os.environ，优先于 .env 文件
    )

    # ── MySQL（agent 用） ──
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "community"
    mysql_charset: str = "utf8mb4"

    # ── DeepSeek API（共享） ──
    deepseek_api_key: str = ""
    deepseek_api_base: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"

    # ── 应用配置 ──
    app_name: str = "Crypto Analyst Assistant"
    app_version: str = "1.0.0"
    debug: bool = False

    # ── API配置 ──
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, validation_alias="PORT")
    api_prefix: str = "/api/v1"

    # ── Agent 数据获取配置 ──
    kline_api_base: str = "https://moziinnovations.com"
    derivatives_api_base: str = "https://moziinnovations.com/derivatives"
    api_timeout: int = 10
    api_max_retries: int = 2
    api_retry_delay: float = 0.5

    # ── Agent LLM 配置 ──
    max_news_items: int = 100
    kline_days_limit: int = 30
    llm_temperature: float = 0.5
    llm_max_tokens: int = 1200
    chat_llm_temperature: float = 0.3
    chat_llm_max_tokens: int = 800
    analysis_llm_temperature: float = 0.5
    analysis_llm_max_tokens: int = 2000
    tool_call_max_retries: int = 1

    # ── Redis（bigorder；Railway 变量名 REDIS_*） ──
    redis_enabled: bool = Field(default=False, validation_alias="REDIS_ENABLED")
    redis_host: str = Field(default="localhost", validation_alias="REDIS_HOST")
    redis_port: int = Field(default=6379, validation_alias="REDIS_PORT")
    redis_db: int = Field(default=0, validation_alias="REDIS_DB")
    redis_password: str = Field(default="", validation_alias="REDIS_PASSWORD")

    # ── Bigorder MySQL（独立数据库） ──
    bigorder_mysql_host: str = ""
    bigorder_mysql_port: int = 3306
    bigorder_mysql_user: str = ""
    bigorder_mysql_password: str = ""
    bigorder_mysql_database: str = "exchange"

    # ── Bigorder LLM（独立模型） ──
    bigorder_deepseek_model: str = "deepseek-v4-flash"

    # ── Bigorder 引擎参数 ──
    scan_interval: int = 30  # BigOrder 大单侦测扫描间隔（秒）
    signal_scan_interval: int = 1800  # 信号卡全市场扫描间隔（秒），30分钟
    history_window_count: int = 288
    score_threshold_strong: int = 70
    score_threshold_medium: int = 50
    flow_window_seconds: int = 300
    price_window_seconds: int = 900
    weight_net_flow: float = 0.35
    weight_density: float = 0.30
    weight_ratio: float = 0.20
    weight_price: float = 0.15
    sigma_net_flow: float = 2.0
    sigma_density: float = 3.0
    ratio_upper: float = 0.7
    ratio_lower: float = 0.3
    price_change_pct: float = 0.015
    exchanges: List[str] = ["Binance", "OKX", "Bybit", "Bitget", "Gate"]

    @field_validator("redis_enabled", mode="before")
    @classmethod
    def redis_enabled_from_railway_env(cls, v):
        """强制优先读 os.environ['REDIS_ENABLED']（Railway Variables）。"""
        raw = _env("REDIS_ENABLED")
        if raw is not None:
            return _parse_bool(raw)
        return _parse_bool(v)

    @field_validator("redis_host", mode="before")
    @classmethod
    def redis_host_from_railway_env(cls, v):
        return _env("REDIS_HOST") or v or "localhost"

    @field_validator("redis_port", mode="before")
    @classmethod
    def redis_port_from_railway_env(cls, v):
        raw = _env("REDIS_PORT")
        if raw is not None:
            return int(raw)
        return v if v is not None else 6379

    @field_validator("redis_db", mode="before")
    @classmethod
    def redis_db_from_railway_env(cls, v):
        raw = _env("REDIS_DB")
        if raw is not None:
            return int(raw)
        return v if v is not None else 0

    @field_validator("redis_password", mode="before")
    @classmethod
    def redis_password_from_railway_env(cls, v):
        raw = _env("REDIS_PASSWORD")
        if raw is not None:
            return raw
        return v or ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


# 兼容：from config.settings import settings
settings = get_settings()
