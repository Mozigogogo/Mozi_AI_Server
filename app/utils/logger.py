"""统一日志配置。

所有模块统一通过 `from app.utils.logger import get_logger` 获取 logger，
避免每个文件自己 `logging.getLogger(__name__)` 时因 basicConfig 顺序导致日志丢失。

约定：
- INFO  — 正常流程节点（启动、扫描完成、结算张数等）
- WARNING — 可恢复的降级路径（API 超时兜底、数据缺失回退等）
- ERROR — 异常但进程继续（写入失败、单币种扫描异常等）
"""
from __future__ import annotations

import logging
import sys
from typing import Optional

_CONFIGURED = False
_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: Optional[str] = None) -> None:
    """初始化 root logger。幂等，重复调用安全。

    应在 app.main 启动时调用一次，确保所有后续 getLogger 都拿到正确配置。
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_level = (level or "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format=_DEFAULT_FORMAT,
        datefmt=_DEFAULT_DATEFMT,
        stream=sys.stdout,
        force=True,  # 覆盖 uvicorn / 第三方库可能预设的 handler
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """获取模块 logger。自动触发 configure_logging 保证默认配置就位。"""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)
