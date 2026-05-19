"""BigOrder 依赖管理 - Redis 可选"""
from config.settings import get_settings

consumer = None
scorer = None
llm_analyzer = None
history = None
_init_error: str | None = None


def is_redis_available() -> bool:
    """检查 bigorder 功能是否可用（Redis 已成功连接）"""
    return consumer is not None


def get_status() -> dict:
    """供 /bigorder/v1/status 诊断：区分「未开启」与「已开启但连不上」"""
    settings = get_settings()
    if not settings.redis_enabled:
        return {
            "redis_enabled": False,
            "redis_connected": False,
            "ready": False,
            "message": "REDIS_ENABLED 未开启，请在 Railway 设置 REDIS_ENABLED=true 并重新部署",
            "init_error": _init_error,
            "redis_host": settings.redis_host,
            "redis_port": settings.redis_port,
        }
    if consumer is None:
        return {
            "redis_enabled": True,
            "redis_connected": False,
            "ready": False,
            "message": "REDIS_ENABLED=true，但 Redis 连接失败，请检查 REDIS_HOST/PORT/PASSWORD 及防火墙",
            "init_error": _init_error,
            "redis_host": settings.redis_host,
            "redis_port": settings.redis_port,
        }
    return {
        "redis_enabled": True,
        "redis_connected": True,
        "ready": True,
        "message": "BigOrder 已就绪",
        "init_error": None,
        "redis_host": settings.redis_host,
        "redis_port": settings.redis_port,
    }


def init_bigorder_deps():
    """初始化 bigorder 依赖（仅在 redis_enabled=True 时调用）"""
    global consumer, scorer, llm_analyzer, history, _init_error

    settings = get_settings()
    _init_error = None

    if not settings.redis_enabled:
        print("BigOrder: REDIS_ENABLED=false, 跳过初始化")
        _init_error = "REDIS_ENABLED=false"
        return

    try:
        from app.bigorder.consumer import RedisConsumer
        from app.bigorder.history import HistoryTracker
        from app.bigorder.scorer import AnomalyScorer
        from app.bigorder.llm_analyzer import LLMAnalyzer

        consumer = RedisConsumer()
        if not consumer.ping():
            consumer = None
            _init_error = f"Redis ping 失败 ({settings.redis_host}:{settings.redis_port})"
            print(f"BigOrder: {_init_error}")
            return

        history = HistoryTracker(consumer.client)
        scorer = AnomalyScorer(consumer, history)
        llm_analyzer = LLMAnalyzer()
        print("BigOrder: 依赖初始化成功")

    except ImportError as e:
        consumer = None
        _init_error = f"缺少依赖包: {e}"
        print(f"BigOrder: {_init_error}")
    except Exception as e:
        consumer = None
        _init_error = str(e)
        print(f"BigOrder: 初始化失败: {e}")
