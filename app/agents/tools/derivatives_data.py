from typing import Dict, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from app.agents.tools.base import CryptoAnalystTool, SymbolInput
from app.services.data_service import (
    get_but_sell_ratio,
    get_open_interest,
    get_trading_volume,
    get_funding_rate,
    get_all_derivatives_data
)
from app.utils.formatters import format_derivatives_data
from app.utils.validators import validate_symbol


class DerivativesDataInput(SymbolInput):
    """衍生品数据输入模型"""
    pass


class DerivativesDataTool(CryptoAnalystTool):
    """衍生品数据工具 - 获取加密货币的衍生品市场数据"""

    name: str = "get_derivatives_data"
    description: str = "获取加密货币的衍生品市场数据，包括买卖比例、持仓量、交易量、资金费率等。当用户询问衍生品市场、多空结构、资金流向时使用此工具。"
    args_schema: type = DerivativesDataInput

    def execute(self, symbol: str) -> str:
        """执行衍生品数据获取"""
        symbol = validate_symbol(symbol)

        derivatives_data = get_all_derivatives_data(symbol)
        formatted_data = format_derivatives_data(derivatives_data)

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{symbol}衍生品市场数据：\n\n{formatted_data}\n\n已获取{symbol}的衍生品市场数据，包括买卖比例、持仓量、交易量和资金费率。"


class BuySellRatioTool(CryptoAnalystTool):
    """买卖比例工具 - 获取加密货币的买卖比例数据"""

    name: str = "get_buy_sell_ratio"
    description: str = "获取加密货币的买卖比例数据，反映市场多空力量对比。当用户询问市场情绪、多空力量时使用此工具。"
    args_schema: type = DerivativesDataInput

    def execute(self, symbol: str) -> str:
        """执行买卖比例数据获取"""
        symbol = validate_symbol(symbol)

        buy_sell_ratio = get_but_sell_ratio(symbol)

        # 格式化买卖比例数据
        formatted_ratio = f"{symbol}买卖比例数据：\n"
        for exchange, data in buy_sell_ratio.items():
            formatted_ratio += f"\n{exchange}:\n"
            # 简单格式化数据
            if isinstance(data, dict):
                for key, value in data.items():
                    formatted_ratio += f"  {key}: {value}\n"
            else:
                formatted_ratio += f"  {data}\n"

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{formatted_ratio}\n已获取{symbol}的买卖比例数据，包含多个交易所的数据。"


class OpenInterestTool(CryptoAnalystTool):
    """持仓量工具 - 获取加密货币的持仓量数据"""

    name: str = "get_open_interest"
    description: str = "获取加密货币的持仓量数据，反映市场参与度和多空博弈强度。当用户询问市场深度、持仓变化时使用此工具。"
    args_schema: type = DerivativesDataInput

    def execute(self, symbol: str) -> str:
        """执行持仓量数据获取"""
        symbol = validate_symbol(symbol)

        open_interest = get_open_interest(symbol)

        # 格式化持仓量数据
        formatted_interest = f"{symbol}持仓量数据：\n"
        for exchange, data in open_interest.items():
            formatted_interest += f"\n{exchange}:\n"
            # 简单格式化数据
            if isinstance(data, dict):
                for key, value in data.items():
                    formatted_interest += f"  {key}: {value}\n"
            else:
                formatted_interest += f"  {data}\n"

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{formatted_interest}\n已获取{symbol}的持仓量数据，包含多个交易所的数据。"


class TradingVolumeTool(CryptoAnalystTool):
    """交易量工具 - 获取加密货币的交易量数据"""

    name: str = "get_trading_volume"
    description: str = "获取加密货币的交易量数据，反映市场活跃度和流动性。当用户询问交易活跃度、流动性时使用此工具。"
    args_schema: type = DerivativesDataInput

    def execute(self, symbol: str) -> str:
        """执行交易量数据获取"""
        symbol = validate_symbol(symbol)

        trading_volume = get_trading_volume(symbol)

        # 格式化交易量数据
        formatted_volume = f"{symbol}交易量数据：\n"
        for exchange, data in trading_volume.items():
            formatted_volume += f"\n{exchange}:\n"
            # 简单格式化数据
            if isinstance(data, dict):
                for key, value in data.items():
                    formatted_volume += f"  {key}: {value}\n"
            else:
                formatted_volume += f"  {data}\n"

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{formatted_volume}\n已获取{symbol}的交易量数据，包含多个交易所的数据。"


class FundingRateTool(CryptoAnalystTool):
    """资金费率工具 - 获取加密货币的资金费率数据"""

    name: str = "get_funding_rate"
    description: str = "获取加密货币的资金费率数据，反映永续合约市场的多空平衡。当用户询问资金费率、市场情绪时使用此工具。"
    args_schema: type = DerivativesDataInput

    def execute(self, symbol: str) -> str:
        """执行资金费率数据获取"""
        symbol = validate_symbol(symbol)

        funding_rate = get_funding_rate()

        # 格式化资金费率数据
        formatted_rate = f"{symbol}资金费率数据：\n"
        if isinstance(funding_rate, dict):
            for key, value in funding_rate.items():
                formatted_rate += f"{key}: {value}\n"
        else:
            formatted_rate += f"{funding_rate}\n"

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{formatted_rate}\n已获取{symbol}的资金费率数据，反映永续合约市场的多空平衡。"