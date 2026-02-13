import json
from typing import Dict, List, Any
from app.core.config import get_settings

settings = get_settings()


def format_kline_data(kline_data: Dict[str, Any]) -> str:
    """格式化K线数据"""
    values = kline_data.get("values", [])
    category_data = kline_data.get("categoryData", [])

    if not values or not category_data:
        return "无有效的K线数据"

    # 只取最近N天的数据
    recent_values = values[-settings.kline_days_limit:] if len(values) > settings.kline_days_limit else values
    recent_dates = category_data[-settings.kline_days_limit:] if len(category_data) > settings.kline_days_limit else category_data

    return f"""
日期数组: {json.dumps(recent_dates)}
K线数据数组: {json.dumps(recent_values)}
"""


def format_header_data(header_data: Dict[str, Any]) -> str:
    """格式化币种基本信息用于提示词"""
    mapping = {
        "symbol": "币种",
        "currentPrice": "当前价格",
        "marketCap": "市值",
        "marketCapRank": "市值排名",
        "fullyDilutedValuation": "完全稀释的市值",
        "totalVolume": "总交易额（usd）",
        "high_24h": "24小时最高价",
        "low_24h": "24小时最低价",
        "priceChange_24h": "24小时价格变化",
        "priceChangePercentage_24h": "24小时价格变化百分比",
        "marketCapChange_24h": "24小时市值变化",
        "marketCapChangePercentage_24h": "24小时市值变化百分比",
        "circulatingSupply": "流通供应量",
        "totalSupply": "总供应量",
        "ath": "历史最高价",
        "athChangePercentage": "历史最高价变化百分比",
        "athDate": "历史最高价日期",
        "atl": "历史最低价",
        "atlChangePercentage": "历史最低价变化百分比",
        "atlDate": "历史最低价日期"
    }

    formatted_data = []
    for key, display_name in mapping.items():
        value = header_data.get(key, "N/A")
        formatted_data.append(f"{display_name}: {value}")

    return "\n".join(formatted_data)


def format_news_data(news: List[str]) -> str:
    """格式化新闻数据"""
    if not news:
        return "暂无相关新闻"

    news_text = "\n".join([f"- {n}" for n in news[:20]])  # 限制显示20条新闻
    return news_text


def format_derivatives_data(derivatives_data: Dict[str, Any]) -> str:
    """格式化衍生品数据"""
    formatted_data = []

    # 买卖比例
    but_sell_ratio = derivatives_data.get("but_sell_ratio", {})
    if but_sell_ratio:
        formatted_data.append("买卖比例:")
        for exchange, data in but_sell_ratio.items():
            if data and isinstance(data, dict):
                formatted_data.append(f"  {exchange}: {json.dumps(data, ensure_ascii=False)}")

    # 持仓量
    open_interest = derivatives_data.get("open_interest", {})
    if open_interest:
        formatted_data.append("\n持仓量:")
        for exchange, data in open_interest.items():
            if data and isinstance(data, dict):
                formatted_data.append(f"  {exchange}: {json.dumps(data, ensure_ascii=False)}")

    # 交易量
    trading_volume = derivatives_data.get("trading_volume", {})
    if trading_volume:
        formatted_data.append("\n交易量:")
        for exchange, data in trading_volume.items():
            if data and isinstance(data, dict):
                formatted_data.append(f"  {exchange}: {json.dumps(data, ensure_ascii=False)}")

    # 资金费率
    funding_rate = derivatives_data.get("funding_rate", {})
    if funding_rate:
        formatted_data.append("\n资金费率:")
        formatted_data.append(json.dumps(funding_rate, ensure_ascii=False))

    return "\n".join(formatted_data)


def create_analysis_prompt(
    symbol: str,
    question: str,
    kline_data: Dict[str, Any],
    header_data: Dict[str, Any]
) -> str:
    """创建分析提示词模板"""
    formatted_kline = format_kline_data(kline_data)
    formatted_header = format_header_data(header_data)

    prompt = f"""
你是一位专业、谨慎且遵守合规要求的加密货币分析师。我将为你提供某个特定虚拟货币的详细基础信息和30天的日线数据（K线数据，顺序为`[开盘价, 收盘价, 最低价, 最高价]`）。你的任务是基于这些确切的数据，对该币种的走势、现状和潜在风险进行综合分析，并回答用户提出的相关问题。

# 背景知识（请严格基于此数据进行分析）：

## 币种（{symbol}）基础信息
{formatted_header}

## 币种（{symbol}）30天日线数据
{formatted_kline}

你的核心工作流程与要求：

1. 数据理解与计算：
   - 首先，理解并解析提供的日线数据。计算关键的技术指标（如短期（7日）和长期（30日）移动平均线、相对强弱指数（RSI）等），以判断趋势和动量。
   - 识别关键的技术位（如支撑位、阻力位）。
   - 将基础信息（如市值、排名、历史价格）作为分析的背景和上下文。

2. 分析框架：
   - 技术面分析：分析价格趋势（上升/下降/盘整）、波动率、以及关键技术指标的信号。
   - 基本面分析：结合市值排名评估其市场地位，结合供应量数据评估其稀缺性，结合历史价格评估当前所处周期位置。
   - 市场情绪分析：结合24小时变化等数据，评估短期市场情绪。
   - 风险提示：必须始终强调加密货币市场的高风险性、高波动性，以及任何分析都不构成财务建议。

3. 回答用户问题的原则：
   - 基于数据：所有结论必须有数据支撑。
   - 逻辑清晰：遵循"描述现状 -> 分析原因 -> 展望可能性 -> 强调风险"的逻辑链。
   - 避免绝对化：不使用"肯定"、"必然"等词汇。使用"可能"、"概率较大"、"需要警惕"等谨慎措辞。
   - 禁止提供具体投资建议：例如"你应该买入"或"目标价XX"是绝对禁止的。可以提供分析性结论，但必须同时指出风险。

最终输出格式：
请使用Markdown格式组织你的回答，包含以下部分：
1. 摘要：用一两句话概括当前该币种的整体状况。
2. 详细分析：
   - 技术面解读
   - 基本面解读
   - 市场情绪解读
3. 综合判断：基于以上分析，给出中立的、多空双方的可能性分析。
4. 风险提示：再次强调市场风险、波动风险以及本次分析的局限性。

现在，请回答以下关于{symbol}的问题：
{question}
"""
    return prompt