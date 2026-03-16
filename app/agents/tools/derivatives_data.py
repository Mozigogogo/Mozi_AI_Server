from typing import Dict, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from app.agents.tools.base import CryptoAnalystTool, SymbolInput
from app.services.data_service import (
    get_derivatives_agg,
    get_trading_value,
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
    description: str = "获取加密货币的衍生品市场数据，包括持仓量、交易量、资金费率等聚合数据。当用户询问衍生品市场、多空结构、资金流向时使用此工具。"
    args_schema: type = DerivativesDataInput

    def execute(self, symbol: str) -> str:
        """执行衍生品数据获取"""
        symbol = validate_symbol(symbol)

        derivatives_data = get_all_derivatives_data(symbol)
        formatted_data = format_derivatives_data(derivatives_data)

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{symbol}衍生品市场数据：\n\n{formatted_data}\n\n已获取{symbol}的衍生品市场数据，包括持仓量、交易量和资金费率。"


class OpenInterestTool(CryptoAnalystTool):
    """持仓量工具 - 获取加密货币的持仓量数据"""

    name: str = "get_open_interest"
    description: str = "获取加密货币的持仓量数据，反映市场参与度和多空博弈强度。当用户询问市场深度、持仓变化时使用此工具。"
    args_schema: type = DerivativesDataInput

    def execute(self, symbol: str) -> str:
        """执行持仓量数据获取"""
        symbol = validate_symbol(symbol)

        agg_data = get_derivatives_agg(symbol)

        if not agg_data:
            return f"{symbol}暂无持仓量数据"

        # 格式化持仓量数据
        formatted = f"{symbol}持仓量数据：\n"
        formatted += f"  币种: {agg_data.get('coin', 'N/A')}\n"
        formatted += f"  指标: {agg_data.get('metric', 'N/A')}\n"
        formatted += f"  单位: {agg_data.get('unit', 'N/A')}\n"
        formatted += f"  交易所: {', '.join(agg_data.get('exchanges', []))}\n"
        formatted += f"  数据点数: {len(agg_data.get('dates', []))}天\n"

        # 显示最新持仓数据
        data_by_exchange = agg_data.get("data", {})
        if data_by_exchange:
            formatted += f"\n  最新持仓数据：\n"
            for ex, values in data_by_exchange.items():
                if values:
                    # 找到最后一个非空值
                    latest_value = None
                    for v in reversed(values):
                        if v is not None:
                            latest_value = v
                            break
                    if latest_value is not None:
                        formatted += f"    {ex}: {latest_value}\n"

        return formatted


class TradingVolumeTool(CryptoAnalystTool):
    """交易量工具 - 获取加密货币的交易量数据"""

    name: str = "get_trading_volume"
    description: str = "获取加密货币的交易量数据，反映市场活跃度和流动性。当用户询问交易活跃度、流动性时使用此工具。"
    args_schema: type = DerivativesDataInput

    def execute(self, symbol: str) -> str:
        """执行交易量数据获取"""
        symbol = validate_symbol(symbol)

        trading_value = get_trading_value(symbol)

        if not trading_value:
            return f"{symbol}暂无交易量数据"

        # 格式化交易量数据
        formatted = f"{symbol}交易量数据：\n"
        formatted += f"  币种: {trading_value.get('coin', 'N/A')}\n"
        formatted += f"  指标: {trading_value.get('metric', 'N/A')}\n"
        formatted += f"  单位: {trading_value.get('unit', 'N/A')}\n"
        formatted += f"  交易所: {', '.join(trading_value.get('exchanges', []))}\n"

        # 计算总交易量（如果数据可用）
        data_by_exchange = trading_value.get("data", {})
        if data_by_exchange:
            formatted += f"\n  最新交易量：\n"
            for ex, values in data_by_exchange.items():
                if values:
                    latest_value = None
                    for v in reversed(values):
                        if v is not None:
                            latest_value = v
                            break
                    if latest_value is not None:
                        formatted += f"    {ex}: {latest_value}\n"

        return formatted


class FundingRateTool(CryptoAnalystTool):
    """资金费率工具 - 获取加密货币的资金费率数据"""

    name: str = "get_funding_rate"
    description: str = "获取加密货币的资金费率数据，反映永续合约市场的多空平衡。当用户询问资金费率、市场情绪时使用此工具。"
    args_schema: type = DerivativesDataInput

    def execute(self, symbol: str) -> str:
        """执行资金费率数据获取"""
        symbol = validate_symbol(symbol)

        funding_rate = get_funding_rate(symbol)

        if not funding_rate:
            return f"{symbol}暂无资金费率数据（该币种可能未开通永续合约交易）"

        # 格式化资金费率数据
        formatted = f"{symbol}资金费率数据：\n"
        formatted += f"  币种: {funding_rate.get('coin', 'N/A')}\n"
        formatted += f"  指标: {funding_rate.get('metric', 'N/A')}\n"

        exchanges_data = funding_rate.get("exchanges", {})
        if exchanges_data:
            formatted += f"  交易所资金费率：\n"
            for ex, rate_info in exchanges_data.items():
                if isinstance(rate_info, dict):
                    rate = rate_info.get("rate", "N/A")
                    formatted += f"    {ex}: {rate}\n"
                else:
                    formatted += f"    {ex}: {rate_info}\n"
        else:
            formatted += f"  暂无交易所数据\n"

        # 资金费率解读
        if exchanges_data:
            # 计算平均费率
            rates = []
            for ex, rate_info in exchanges_data.items():
                if isinstance(rate_info, dict):
                    rate = rate_info.get("rate")
                    if isinstance(rate, (int, float)):
                        rates.append(rate)
            if rates:
                avg_rate = sum(rates) / len(rates)
                formatted += f"\n  平均资金费率: {avg_rate:.6f}\n"

                if avg_rate > 0.01:
                    analysis = "多头情绪浓厚，多头支付空头利息"
                elif avg_rate > 0:
                    analysis = "略微偏多，多头支付少量空头利息"
                elif avg_rate < -0.01:
                    analysis = "空头情绪浓厚，空头支付多头利息"
                elif avg_rate < 0:
                    analysis = "略微偏空，空头支付少量多头利息"
                else:
                    analysis = "多空相对平衡"

                formatted += f"  市场解读: {analysis}\n"

        return formatted
