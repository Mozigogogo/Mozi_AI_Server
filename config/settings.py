from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional, List


class Settings(BaseSettings):
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
    api_port: int = Field(default=8000, alias="PORT")
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

    # ── Redis（bigorder，可选） ──
    redis_enabled: bool = False
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    # ── Bigorder MySQL（独立数据库） ──
    bigorder_mysql_host: str = ""
    bigorder_mysql_port: int = 3306
    bigorder_mysql_user: str = ""
    bigorder_mysql_password: str = ""
    bigorder_mysql_database: str = "exchange"

    # ── Bigorder LLM（独立模型） ──
    bigorder_deepseek_model: str = "deepseek-v4-flash"

    # ── Bigorder 引擎参数 ──
    scan_interval: int = 30
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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()