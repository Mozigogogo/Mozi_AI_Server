"""BigOrder 依赖管理 - Redis 可选"""
from config.settings import settings

consumer = None
scorer = None
llm_analyzer = None
history = None


def is_redis_available() -> bool:
    """检查 bigorder 功能是否可用"""
    return consumer is not None


def init_bigorder_deps():
    """初始化 bigorder 依赖（仅在 redis_enabled=True 时调用）"""
    global consumer, scorer, llm_analyzer, history

    if not settings.redis_enabled:
        print("BigOrder: REDIS_ENABLED=false, 跳过初始化")
        return

    try:
        from app.bigorder.consumer import RedisConsumer
        from app.bigorder.history import HistoryTracker
        from app.bigorder.scorer import AnomalyScorer
        from app.bigorder.llm_analyzer import LLMAnalyzer

        consumer = RedisConsumer()
        if not consumer.ping():
            consumer = None
            print("BigOrder: Redis ping 失败，依赖未初始化")
            return

        history = HistoryTracker(consumer.client)
        scorer = AnomalyScorer(consumer, history)
        llm_analyzer = LLMAnalyzer()
        print("BigOrder: 依赖初始化成功")

    except ImportError as e:
        print(f"BigOrder: 缺少依赖包({e})，功能禁用")
    except Exception as e:
        print(f"BigOrder: 初始化失败: {e}")
        consumer = None
