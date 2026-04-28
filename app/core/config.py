from config.settings import Settings


# 移除 lru_cache 以便每次都能读取最新的环境变量配置
def get_settings() -> Settings:
    """获取配置实例"""
    return Settings()