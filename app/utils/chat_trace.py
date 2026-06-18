"""统一链路日志 — 用 request_id 串联一次 SSE 请求的全部步骤。

用法:
    from app.utils.chat_trace import trace, Timer, mask

    trace(rid, "enter", endpoint="signals_chat", conv=conv_id, msg=mask(user_message))

    with Timer(rid, "step1.llm_route"):
        route_resp = await ...

    try:
        ...
    except Exception as e:
        trace(rid, "step1.error", error=f"{type(e).__name__}: {e}")
        raise

底层用 logging 模块（name=chat_trace），默认 INFO 级别直出 stdout。
Railway/容器环境默认采集 stdout，无需额外配置即可看到日志。
如需静默，设环境变量 CHAT_TRACE_LEVEL=WARNING 即可。
"""
import logging
import os
import sys
import time as _time
from typing import Any, Optional


# 独立 logger（不污染 root logger，避免影响其他 print-based 日志）
_logger = logging.getLogger("chat_trace")
if not _logger.handlers:
    _level = os.environ.get("CHAT_TRACE_LEVEL", "INFO").upper()
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_handler)
    _logger.setLevel(getattr(logging, _level, logging.INFO))
    _logger.propagate = False  # 不向 root 传播，避免重复输出


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.2f}"
    if isinstance(v, str) and len(v) > 80:
        return v[:77] + "..."
    return str(v)


def trace(rid: Optional[str], step: str, **kw: Any) -> None:
    """打印一条链路日志（INFO 级别）。

    Args:
        rid: 请求 ID（前端传入，串联整条链）
        step: 步骤名（建议用 dot 分层，如 step1.llm_route.end）
        **kw: 任意键值对（自动截断长字符串）
    """
    parts = [f"[{rid or 'no-rid'}]", step]
    for k, v in kw.items():
        parts.append(f"{k}={_fmt(v)}")
    _logger.info(" ".join(parts))


class Timer:
    """计时上下文管理器 — 自动打印 .start / .end（含 duration_ms）。

    异常退出时打印 .error，并 re-raise（不吞异常）。
    """

    __slots__ = ("rid", "step", "t0", "extra")

    def __init__(self, rid: Optional[str], step: str, **extra: Any):
        self.rid = rid
        self.step = step
        self.t0: float = 0.0
        self.extra = extra

    def __enter__(self):
        self.t0 = _time.time()
        trace(self.rid, f"{self.step}.start", **self.extra)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        dur_ms = (_time.time() - self.t0) * 1000
        if exc_type is None:
            trace(self.rid, f"{self.step}.end", duration_ms=dur_ms, **self.extra)
        else:
            trace(
                self.rid,
                f"{self.step}.error",
                error=f"{exc_type.__name__}: {exc_val}",
                duration_ms=dur_ms,
                **self.extra,
            )
        return False  # 不吞异常


def mask(text: str, keep: int = 40) -> str:
    """截断长 message，避免日志爆炸。"""
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    return text if len(text) <= keep else text[:keep - 3] + "..."
