from pydantic_settings import BaseSettings
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
    deepseek_api_base: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    # 应用配置
    app_name: str = "Crypto Analyst Assistant"
    app_version: str = "1.0.0"
    debug: bool = False

    # API配置
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_prefix: str = "/api/v1"

    # 数据获取配置
    kline_api_base: str = "https://moziinnovations.com"
    derivatives_api_base: str = "https://moziinnovations.com/derivatives"

    # 其他配置
    max_news_items: int = 100
    kline_days_limit: int = 30
    llm_temperature: float = 0.5
    llm_max_tokens: int = 1200

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()