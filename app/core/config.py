from functools import lru_cache
from config.settings import Settings


@lru_cache()
def get_settings() -> Settings:
    """获取配置实例（缓存）"""
    return Settings()