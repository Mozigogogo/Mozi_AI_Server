from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    # MySQL配置
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "exchange"
    mysql_charset: str = "utf8mb4"

    # DeepSeek API配置
    deepseek_api_key: str = ""
    deepseek_api_base: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"

    # 应用配置
    app_name: str = "Crypto Analyst Assistant"
    app_version: str = "1.0.0"
    debug: bool = False

    # API配置
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, alias="PORT")
    api_prefix: str = "/api/v1"

    # 数据获取配置
    kline_api_base: str = "https://moziinnovations.com"
    derivatives_api_base: str = "https://moziinnovations.com/derivatives"

    # API超时和重试配置
    api_timeout: int = 5  # API请求超时时间（秒）
    api_max_retries: int = 2  # API最大重试次数（502/503/超时自动重试）
    api_retry_delay: float = 0.3  # 重试延迟（秒）

    # 其他配置
    max_news_items: int = 100
    kline_days_limit: int = 30
    llm_temperature: float = 0.5
    llm_max_tokens: int = 1200

    # 对话模式 LLM 配置（简洁快速）
    chat_llm_temperature: float = 0.3
    chat_llm_max_tokens: int = 800

    # 分析模式 LLM 配置（深度全面）
    analysis_llm_temperature: float = 0.5
    analysis_llm_max_tokens: int = 2000

    # 工具调用强制重试次数
    tool_call_max_retries: int = 1

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()