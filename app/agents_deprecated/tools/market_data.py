from typing import Dict, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from app.agents.tools.base import CryptoAnalystTool, SymbolInput
from app.services.data_service import get_kline_data, get_header_data
from app.utils.formatters import format_kline_data, format_header_data
from app.utils.validators import validate_symbol


class MarketDataInput(SymbolInput):
    """市场数据输入模型"""
    pass


class MarketDataTool(CryptoAnalystTool):
    """市场数据工具 - 获取加密货币的市场数据，包括K线数据和基本信息"""

    name: str = "get_market_data"
    description: str = "获取加密货币的市场数据，包括K线数据和基本信息。当用户询问币种价格、历史数据、基本信息时使用此工具。"
    args_schema: type = MarketDataInput

    def execute(self, symbol: str) -> str:
        """执行市场数据获取"""
        symbol = validate_symbol(symbol)

        # 获取数据
        kline_data = get_kline_data(symbol)
        header_data = get_header_data(symbol)

        # 格式化数据
        formatted_kline = format_kline_data(kline_data)
        formatted_header = format_header_data(header_data)

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{formatted_header}\n\nK线数据（最近30天）：\n{formatted_kline}\n\n已获取{symbol}的市场数据，包括30天K线数据和基本信息。"


class KlineDataTool(CryptoAnalystTool):
    """K线数据工具 - 专门获取加密货币的K线数据"""

    name: str = "get_kline_data"
    description: str = "获取加密货币的K线数据（价格历史数据）。当用户询问价格走势、历史价格、技术分析时使用此工具。"
    args_schema: type = MarketDataInput

    def execute(self, symbol: str) -> str:
        """执行K线数据获取"""
        symbol = validate_symbol(symbol)

        kline_data = get_kline_data(symbol)
        formatted_kline = format_kline_data(kline_data)

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"K线数据（最近30天）：\n{formatted_kline}\n\n已获取{symbol}的K线数据，包含最近30天的价格信息。"


class HeaderDataTool(CryptoAnalystTool):
    """基本信息工具 - 获取加密货币的基本信息"""

    name: str = "get_header_data"
    description: str = "获取加密货币的基本信息，包括价格、市值、排名、供应量等。当用户询问币种概况、基本信息时使用此工具。"
    args_schema: type = MarketDataInput

    def execute(self, symbol: str) -> str:
        """执行基本信息获取"""
        symbol = validate_symbol(symbol)

        header_data = get_header_data(symbol)
        formatted_header = format_header_data(header_data)

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{formatted_header}\n\n已获取{symbol}的基本信息，包括价格、市值、排名等关键指标。"