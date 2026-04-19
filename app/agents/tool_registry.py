"""工具注册中心 - 直接实例化 BaseTool 子类，保留 args_schema"""
from typing import List
from langchain.tools import BaseTool

from app.agents.tools.market_data import (
    MarketDataTool,
    KlineDataTool,
    HeaderDataTool,
)
from app.agents.tools.news_data import (
    NewsDataTool,
    RecentNewsTool,
    NewsCountTool,
)
from app.agents.tools.derivatives_data import (
    DerivativesDataTool,
    BuySellRatioTool,
    OpenInterestTool,
    TradingVolumeTool,
    FundingRateTool,
)
from app.agents.tools.analysis_tools import (
    TechnicalAnalysisTool,
    NewsAnalysisTool,
    DerivativesAnalysisTool,
    QuantitativeAnalysisTool,
    SummaryAnalysisTool,
)
from app.agents.tools.prompt_tools import (
    PromptBuilderTool,
    SystemPromptTool,
)


class ToolRegistry:
    """创建和缓存工具实例，保留完整的 Pydantic args_schema"""

    _instances: List[BaseTool] = None

    @classmethod
    def create_tools(cls) -> List[BaseTool]:
        if cls._instances is not None:
            return cls._instances

        cls._instances = [
            # 市场数据工具
            MarketDataTool(),
            KlineDataTool(),
            HeaderDataTool(),
            # 新闻数据工具
            NewsDataTool(),
            RecentNewsTool(),
            NewsCountTool(),
            # 衍生品数据工具
            DerivativesDataTool(),
            BuySellRatioTool(),
            OpenInterestTool(),
            TradingVolumeTool(),
            FundingRateTool(),
            # 分析工具
            TechnicalAnalysisTool(),
            NewsAnalysisTool(),
            DerivativesAnalysisTool(),
            QuantitativeAnalysisTool(),
            SummaryAnalysisTool(),
            # 提示词工具
            PromptBuilderTool(),
            SystemPromptTool(),
        ]
        return cls._instances
