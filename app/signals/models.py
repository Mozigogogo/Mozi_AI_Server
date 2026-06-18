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
    C = "C"  # 低置信度信号，仅供参考


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

    def format_card(self, lang: str = "zh") -> str:
        """格式化输出信号卡文本（中英双语）"""
        en = lang == "en"

        direction_label = {
            "long": "Long" if en else "做多",
            "short": "Short" if en else "做空",
            "neutral": "Neutral" if en else "观望",
        }
        grade_emoji = {"S": "🔴", "A": "🟡", "B": "⚪", "C": "🔸"}
        source_names = {
            "bigorder_anomaly": "BigOrder" if en else "大单异动",
            "quantitative": "Quant6F" if en else "量化六因子",
            "technical": "Technical" if en else "技术分析",
        }

        def _fp(v):
            # 统一价格格式：>=1 → 2 位小数；<1 → 5 位小数；极小数 → 紧凑记号 0.0{N}xx
            from app.utils.formatters import format_price_change
            return f"${format_price_change(v)}"

        lines = [
            f"📊 {self.coin} {'Signal Card' if en else '交易信号卡'} | {grade_emoji.get(self.grade, '⚪')}{self.grade}{'-Grade' if en else '级'} | {'Conf' if en else '置信度'} {self.confidence:.0f}%",
        ]
        if self.grade == SignalGrade.C:
            lines.append(f"│  ⚠️ {'Weak signal, for reference only' if en else '信号偏弱，仅供参考'}")
        lines += [
            f"├─ {'Direction' if en else '方向'}：{direction_label.get(self.direction, self.direction)}",
            f"├─ {'Price' if en else '当前价格'}：{_fp(self.current_price)}",
            f"├─ {'Entry Zone' if en else '进场区间'}：{_fp(self.entry_low)} - {_fp(self.entry_high)}",
            f"├─ {'Stop Loss' if en else '止损'}：{_fp(self.stop_loss)} ({(self.stop_loss / self.current_price - 1) * 100:+.1f}%)",
            f"├─ {'Take Profit' if en else '止盈'}：{_fp(self.take_profit)} ({(self.take_profit / self.current_price - 1) * 100:+.1f}%)",
            f"├─ {'R:R' if en else '盈亏比'}：{self.risk_reward_ratio:.1f} : 1",
            f"├─ {'Position' if en else '建议仓位'}：{'Capital ' if en else '总资金 '}{self.position_pct:.0f}%（{'Risk-adjusted' if en else '经风险调整后的实际仓位'}）",
        ]

        if self.math and self.math.kelly_fraction > 0:
            lines.append(
                f"├─ Kelly {'Pos' if en else '仓位'}：{self.math.kelly_fraction:.1%}（"
                f"{'Theoretical optimal, use half to limit drawdown' if en else '理论最优仓位，建议仓位取其半值防回撤'}）"
            )

        sources_str = " + ".join(
            f"{source_names.get(s.name, s.name)}({s.score:.0f})"
            for s in self.sources
        )
        lines.append(f"├─ {'Sources' if en else '依据'}：{sources_str}")

        if self.math:
            math_lines = []
            if self.math.hurst is not None:
                math_lines.append(f"Hurst={self.math.hurst:.2f}")
            if self.math.monte_carlo_bull_prob is not None:
                if self.direction == SignalDirection.LONG:
                    math_lines.append(f"MC {'Bull' if en else '看涨'}{self.math.monte_carlo_bull_prob:.0%}")
                else:
                    math_lines.append(f"MC {'Bear' if en else '看跌'}{self.math.monte_carlo_bear_prob:.0%}")
            if self.math.vol_regime:
                math_lines.append(f"{'Vol' if en else '波动率'}={self.math.vol_regime}")
            if self.math.market_regime:
                math_lines.append(f"{'Mkt' if en else '市场'}={self.math.market_regime}")
            if self.math.kelly_fraction > 0:
                math_lines.append(f"Kelly={self.math.kelly_fraction:.1%}")
            if math_lines:
                lines.append(f"├─ {'Math' if en else '数学推导'}：{' | '.join(math_lines)}")

            if self.math.key_findings:
                for finding in self.math.key_findings[:3]:
                    lines.append(f"│  · {finding}")

        wr = f"{self.win_rate:.0f}%" if self.win_rate is not None else "--"
        sc = f"{self.sample_count}" if self.sample_count is not None else "--"
        ap = f"{self.avg_profit_pct:+.1f}%" if self.avg_profit_pct is not None else "--"
        lines.append(f"├─ {'Win Rate' if en else '历史胜率'}：{wr}（{'30d' if en else '近30天'} {sc} {'trades' if en else '次'}）")
        lines.append(f"├─ {'Avg PnL' if en else '平均盈利'}：{ap}")

        if self.strategy:
            lines.append(
                f"├─ {'Strat' if en else '策略'}v{self.strategy.strategy_version} | {self.strategy.regime} | "
                f"{'Global WR' if en else '全局胜率'}{self.strategy.global_win_rate * 100:.0f}%"
            )

        if self.invalidation_price:
            action = "breaks above" if self.direction == SignalDirection.SHORT else "breaks below"
            if not en:
                action = "突破" if self.direction == SignalDirection.SHORT else "跌破"
            lines.append(f"└─ {'Invalid' if en else '失效条件'}：{action} {_fp(self.invalidation_price)}")
        else:
            lines.append(f"└─ {'Generated' if en else '生成时间'}：{self.created_at}")

        return "\n".join(lines)
