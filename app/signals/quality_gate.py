"""
信号质量门（Phase 4 Layer 2）— 方向无关的质量评分。

所有 alpha 触发器生成的候选必须过这道门，与方向（long/short）无关。
未达阈值的候选不进入 fusion 加权。

三维度评分（每维 0-1，加权求和）：
1. statistical_significance (40%): source 数量 + score 强度
   多个高分 source = 强信号 = 高质量
2. information_content (30%): entropy predictability + tf_agreement
   可预测性高 + 多周期共振 = 高质量
3. independent_evidence_count (30%): sources 里独立来源数
   bigorder / quantitative / technical 算独立，flag/vol 算衍生
"""
import os
from typing import Any, Dict, List, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_THRESHOLD = float(os.getenv("QUALITY_GATE_THRESHOLD", "0.6"))
ENABLED = os.getenv("QUALITY_GATE_ENABLED", "1") == "1"

INDEPENDENT_SOURCE_PREFIXES = (
    "bigorder", "quant", "technical",
    "alpha_breakout", "alpha_mean_rev", "alpha_funding",
)


def _statistical_significance(sources: List[Any]) -> float:
    """source 数量 + score 强度 → 0-1 分。"""
    if not sources:
        return 0.0
    n = len(sources)
    avg_score = sum(getattr(s, "score", 0) or 0 for s in sources) / n
    # 数量维度：3 源 = 满分，2 源 = 0.7，1 源 = 0.3
    count_factor = min(1.0, n / 3.0) if n >= 2 else 0.3
    # score 维度：50 = 中等，70+ = 强
    score_factor = min(1.0, max(0.0, (avg_score - 30) / 50))
    return round(count_factor * 0.5 + score_factor * 0.5, 3)


def _information_content(math_result: Any, dual_tf_info: Optional[Dict]) -> float:
    """entropy predictability + tf_agreement → 0-1 分。"""
    score = 0.0
    # entropy 维度（占 50%）
    if math_result and getattr(math_result, "entropy", None):
        predictability = getattr(math_result.entropy, "predictability", None)
        if predictability is not None:
            score += min(1.0, max(0.0, predictability)) * 0.5

    # tf_agreement 维度（占 50%）
    if dual_tf_info:
        tfa = (dual_tf_info.get("tf_agreement") or "").lower()
        if tfa == "agreement":
            score += 0.5
        elif tfa == "neutral":
            score += 0.25
        elif tfa == "disagreement":
            score += 0.0  # 周期分歧扣分
        # insufficient 数据时给 0.1（避免一刀切）
        elif tfa.startswith("insufficient"):
            score += 0.1

    return round(min(1.0, score), 3)


def _independent_evidence_count(sources: List[Any]) -> float:
    """sources 里独立来源数 → 0-1 分。"""
    if not sources:
        return 0.0
    independent_names = set()
    for s in sources:
        name = (getattr(s, "name", "") or "").lower()
        for prefix in INDEPENDENT_SOURCE_PREFIXES:
            if name.startswith(prefix) or prefix in name:
                independent_names.add(prefix)
                break
        else:
            # 未匹配前缀的也算独立（避免漏计）
            independent_names.add(name or "unknown")
    n = len(independent_names)
    # 3 独立源 = 满分，2 = 0.7，1 = 0.3
    return round(min(1.0, n / 3.0) if n >= 2 else 0.3, 3)


def score_signal(
    sources: List[Any],
    math_result: Any = None,
    dual_tf_info: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    评估信号质量，返回详细评分明细。
    fail-open：异常返回 1.0（不过门不阻塞，让 fusion 自己决定）。
    """
    try:
        sig = _statistical_significance(sources)
        info = _information_content(math_result, dual_tf_info)
        evid = _independent_evidence_count(sources)
        total = round(0.4 * sig + 0.3 * info + 0.3 * evid, 3)
        return {
            "total": total,
            "significance": sig,
            "information": info,
            "evidence": evid,
            "source_count": len(sources),
            "passed": total >= DEFAULT_THRESHOLD,
        }
    except Exception as e:
        logger.warning(f"quality_gate 评分异常: {type(e).__name__}: {e}")
        return {
            "total": 1.0,
            "significance": 0.0,
            "information": 0.0,
            "evidence": 0.0,
            "source_count": len(sources) if sources else 0,
            "passed": True,  # fail-open
            "error": str(e),
        }


def should_drop(quality_score: Dict[str, Any]) -> bool:
    """是否应该丢弃（未过门）。env 关闭时永远 False。"""
    if not ENABLED:
        return False
    return not quality_score.get("passed", True)
