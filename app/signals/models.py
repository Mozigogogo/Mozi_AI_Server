"""交易信号卡 - 数据模型（含数学推导 + 自适应策略字段）"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime


class SignalDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class SignalGrade(str, Enum):
    S = "S"  # 多维共振 + 数学推导确认
    A = "A"  # 单维强信号 + 数学推导支持
    B = "B"  # 中等信号


class SignalStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    HIT_TP = "hit_tp"
    HIT_SL = "hit_sl"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class RegimeType(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    MEAN_REVERTING = "mean_reverting"
    VOLATILE = "volatile"
    QUIET = "quiet"


class SignalSource(BaseModel):
    """单个信号源"""
    name: str
    score: float
    direction: SignalDirection
    weight: float
    detail: str = ""


class MathDerivationSummary(BaseModel):
    """数学推导摘要（嵌入 SignalCard）"""
    hurst: Optional[float] = None
    hurst_interpretation: str = ""
    entropy_predictability: Optional[float] = None
    kelly_fraction: float = 0.0
    monte_carlo_bull_prob: Optional[float] = None
    monte_carlo_bear_prob: Optional[float] = None
    monte_carlo_var95: Optional[float] = None
    vol_regime: str = ""
    vol_percentile: Optional[float] = None
    market_regime: str = ""
    market_regime_confidence: Optional[float] = None
    math_score_adjustment: float = 0.0
    math_confidence: float = 0.5
    key_findings: List[str] = []


class StrategyMeta(BaseModel):
    """策略迭代元数据"""
    strategy_version: int = 1
    regime: str = "quiet"
    adaptive_weights: Dict[str, float] = {}
    global_win_rate: float = 0.5
    evolution_count: int = 0


class SignalCard(BaseModel):
    """交易信号卡"""
    id: Optional[int] = None
    coin: str
    direction: SignalDirection
    grade: SignalGrade

    # 价格区间
    current_price: float
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float

    # 置信度
    confidence: float
    sources: List[SignalSource] = []

    # 历史胜率
    win_rate: Optional[float] = None
    sample_count: Optional[int] = None
    avg_profit_pct: Optional[float] = None

    # 仓位建议
    position_pct: float = 5.0

    # 失效条件
    invalidation_price: Optional[float] = None
    expires_at: Optional[str] = None

    # 数学推导（第一性原理）
    math: Optional[MathDerivationSummary] = None

    # 策略元数据（自适应引擎）
    strategy: Optional[StrategyMeta] = None

    # 状态
    status: SignalStatus = SignalStatus.PENDING

    # 元数据
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    updated_at: str = ""
    settled_at: Optional[str] = None
    settled_price: Optional[float] = None
    pnl_pct: Optional[float] = None

    def format_card(self) -> str:
        """格式化输出信号卡文本"""
        direction_label = {"long": "做多", "short": "做空", "neutral": "观望"}
        grade_emoji = {"S": "🔴", "A": "🟡", "B": "⚪"}

        lines = [
            f"📊 {self.coin} 交易信号卡 | {grade_emoji.get(self.grade, '⚪')}{self.grade}级 | 置信度 {self.confidence:.0f}%",
            f"├─ 方向：{direction_label.get(self.direction, self.direction)}",
            f"├─ 当前价格：${self.current_price:,.2f}",
            f"├─ 进场区间：${self.entry_low:,.2f} - ${self.entry_high:,.2f}",
            f"├─ 止损：${self.stop_loss:,.2f} ({(self.stop_loss / self.current_price - 1) * 100:+.1f}%)",
            f"├─ 止盈：${self.take_profit:,.2f} ({(self.take_profit / self.current_price - 1) * 100:+.1f}%)",
            f"├─ 盈亏比：{self.risk_reward_ratio:.1f} : 1",
            f"├─ 仓位建议：总资金 {self.position_pct:.0f}%",
        ]

        # 信号源依据
        source_names = {
            "bigorder_anomaly": "大单异动",
            "quantitative": "量化六因子",
            "technical": "技术分析",
        }
        sources_str = " + ".join(
            f"{source_names.get(s.name, s.name)}({s.score:.0f})"
            for s in self.sources
        )
        lines.append(f"├─ 依据：{sources_str}")

        # 历史胜率
        if self.win_rate is not None:
            lines.append(f"├─ 历史胜率：{self.win_rate:.0f}%（近30天 {self.sample_count} 次）")
        if self.avg_profit_pct is not None:
            lines.append(f"├─ 平均盈利：{self.avg_profit_pct:+.1f}%")

        # 数学推导摘要
        if self.math:
            math_lines = []
            if self.math.hurst is not None:
                math_lines.append(f"Hurst={self.math.hurst:.2f}")
            if self.math.monte_carlo_bull_prob is not None:
                if self.direction == SignalDirection.LONG:
                    math_lines.append(f"MC看涨{self.math.monte_carlo_bull_prob:.0%}")
                else:
                    math_lines.append(f"MC看跌{self.math.monte_carlo_bear_prob:.0%}")
            if self.math.vol_regime:
                math_lines.append(f"波动率={self.math.vol_regime}")
            if self.math.market_regime:
                math_lines.append(f"市场={self.math.market_regime}")
            if self.math.kelly_fraction > 0:
                math_lines.append(f"Kelly={self.math.kelly_fraction:.1%}")
            if math_lines:
                lines.append(f"├─ 数学推导：{' | '.join(math_lines)}")

            if self.math.key_findings:
                for finding in self.math.key_findings[:3]:
                    lines.append(f"│  · {finding}")

        # 策略元数据
        if self.strategy:
            lines.append(f"├─ 策略v{self.strategy.strategy_version} | {self.strategy.regime} | 全局胜率{self.strategy.global_win_rate:.0%}")

        # 失效条件
        if self.invalidation_price:
            lines.append(f"└─ 失效条件：跌破 ${self.invalidation_price:,.2f}")
        else:
            lines.append(f"└─ 生成时间：{self.created_at}")

        return "\n".join(lines)
