"""大单侦测数据模型"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum


class SignalLevel(str, Enum):
    STRONG = "strong"      # >=70 红标
    MEDIUM = "medium"      # 50~69 黄标
    NONE = "none"          # <50 不展示


class TickData(BaseModel):
    """单条成交"""
    symbol: str
    deal_price: str
    deal_quantity: str
    deal_timestamp: int
    is_maker: bool
    side: str  # buy / sell
    exchange: str
    amount: float = 0.0  # price * quantity

    def calc_amount(self) -> float:
        try:
            self.amount = float(self.deal_price) * float(self.deal_quantity)
        except (ValueError, TypeError):
            self.amount = 0.0
        return self.amount


class DimensionScore(BaseModel):
    """单维度得分"""
    raw_value: float = 0.0
    history_mean: float = 0.0
    history_std: float = 0.0
    score: float = 0.0  # 0~100


class SignalScore(BaseModel):
    """四维得分"""
    net_flow: DimensionScore = Field(default_factory=DimensionScore)
    density: DimensionScore = Field(default_factory=DimensionScore)
    ratio: DimensionScore = Field(default_factory=DimensionScore)
    price_change: DimensionScore = Field(default_factory=DimensionScore)
    total_score: float = 0.0
    level: SignalLevel = SignalLevel.NONE


class AnomalySignal(BaseModel):
    """完整异动信号"""
    coin: str
    exchange: str
    score: SignalScore = Field(default_factory=SignalScore)
    buy_amount: float = 0.0
    sell_amount: float = 0.0
    net_flow: float = 0.0
    buy_count: int = 0
    sell_count: int = 0
    price_start: float = 0.0
    price_end: float = 0.0
    price_change_pct: float = 0.0
    top_orders: List[TickData] = []
    llm_analysis: Optional[str] = None
    timestamp: int = 0
    created_at: str = ""


class OrderFlowStats(BaseModel):
    """资金流向统计"""
    coin: str
    window_minutes: int = 5
    buy_amount: float = 0.0
    sell_amount: float = 0.0
    net_flow: float = 0.0
    buy_count: int = 0
    sell_count: int = 0
    buy_ratio: float = 0.0
    exchanges: Dict[str, Dict[str, float]] = {}


class ExchangeCompare(BaseModel):
    """交易所对比"""
    coin: str
    exchanges: Dict[str, Dict[str, Any]] = {}


class ChatRequest(BaseModel):
    """对话请求"""
    request_id: str
    user_id: str
    message: str
    conversation_id: Optional[str] = None
