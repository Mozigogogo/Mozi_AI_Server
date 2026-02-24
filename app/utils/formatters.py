import json
from typing import Dict, List, Any, Union
from app.core.config import get_settings

settings = get_settings()


def format_price(value: Union[float, int, str, None], symbol: str = "") -> str:
    """
    格式化价格显示，处理不同量级的价格
    """
    if value is None:
        return "N/A"

    try:
        num = float(value)
    except (ValueError, TypeError):
        return str(value)

    # 对于加密货币，根据大小选择合适的精度
    if num >= 1:
        return f"${num:.2f}"
    elif num >= 0.01:
        return f"${num:.4f}"
    else:
        return f"${num:.6f}"


def format_large_number(value: Union[float, int, str, None]) -> str:
    """
    格式化大数值显示（市值、交易量等）
    """
    if value is None:
        return "N/A"

    try:
        num = float(value)
    except (ValueError, TypeError):
        return str(value)

    if num >= 1000000000000:  # 1万亿以上
        return f"${num / 1000000000000:.2f}T"
    elif num >= 1000000000:  # 10亿以上
        return f"${num / 1000000000:.2f}B"
    elif num >= 1000000:  # 100万以上
        return f"${num / 1000000:.2f}M"
    elif num >= 1000:  # 1千以上
        return f"${num / 1000:.2f}K"
    else:
        return f"${num:.2f}"


def format_supply(value: Union[float, int, str, None]) -> str:
    """
    格式化供应量显示
    """
    if value is None:
        return "N/A"

    try:
        num = float(value)
    except (ValueError, TypeError):
        return str(value)

    if num >= 100000000:  # 1亿以上
        return f"{num / 100000000:.2f}亿"
    elif num >= 10000:  # 1万以上
        return f"{num / 10000:.2f}万"
    else:
        return f"{num:.0f}"


def format_percentage_value(value: Union[float, int, str, None]) -> str:
    """
    格式化百分比显示，带颜色标识（用于显示）
    """
    if value is None:
        return "N/A"

    try:
        num = float(value)
        sign = "+" if num >= 0 else ""
        return f"{sign}{num:.2f}%"
    except (ValueError, TypeError):
        return str(value)


def format_kline_data(kline_data: Dict[str, Any], for_quantitative: bool = False) -> str:
    """
    格式化K线数据

    Args:
        kline_data: K线数据字典
        for_quantitative: 是否用于量化分析（为True时提供更详细的价格统计）
    """
    values = kline_data.get("values", [])
    category_data = kline_data.get("categoryData", [])

    if not values or not category_data:
        return "无有效的K线数据"

    # 只取最近N天的数据
    recent_values = values[-settings.kline_days_limit:] if len(values) > settings.kline_days_limit else values
    recent_dates = category_data[-settings.kline_days_limit:] if len(category_data) > settings.kline_days_limit else category_data

    if for_quantitative:
        # 为量化分析提供更多统计信息
        if not recent_values:
            return "无有效的K线数据"

        # 提取价格数据（假设格式为 [开盘价, 收盘价, 最低价, 最高价]）
        closes = []
        opens = []
        highs = []
        lows = []

        for candle in recent_values:
            if len(candle) >= 4:
                try:
                    opens.append(float(candle[0]))
                    closes.append(float(candle[1]))
                    lows.append(float(candle[2]))
                    highs.append(float(candle[3]))
                except (ValueError, IndexError):
                    continue

        if not closes:
            return "K线数据格式错误，无法进行量化分析"

        # 计算统计指标
        current_price = closes[-1] if closes else 0
        previous_price = closes[-2] if len(closes) > 1 else current_price
        price_change = current_price - previous_price
        price_change_pct = (price_change / previous_price * 100) if previous_price else 0

        # 计算移动平均线
        ma_7 = sum(closes[-7:]) / 7 if len(closes) >= 7 else sum(closes) / len(closes)
        ma_30 = sum(closes[-30:]) / 30 if len(closes) >= 30 else sum(closes) / len(closes)

        # 计算RSI（简化版，14周期）
        rsi = calculate_rsi(closes) if len(closes) >= 15 else 50

        # 计算波动率
        volatility = (max(highs) - min(lows)) / ((max(highs) + min(lows)) / 2) * 100 if highs and lows else 0

        # 价格区间
        highest = max(highs) if highs else 0
        lowest = min(lows) if lows else 0

        quantitative_stats = f"""
【K线数据统计（最近{len(recent_values)}天）】
- 当前价格: ${current_price:.2f}
- 前一日价格: ${previous_price:.2f}
- 价格变化: ${price_change:+.2f} ({price_change_pct:+.2f}%)
- 最高价: ${highest:.2f}
- 最低价: ${lowest:.2f}
- 价格区间: ${lowest:.2f} ~ ${highest:.2f}

【技术指标】
- 7日均线(MA7): ${ma_7:.2f}
- 30日均线(MA30): ${ma_30:.2f}
- 相对强弱指数(RSI): {rsi:.2f}
- 波动率: {volatility:.2f}%
- 价格位置: {((current_price - lowest) / (highest - lowest) * 100):.1f}% (当前价格在区间中的位置)

【价格序列】
"""
        # 添加最近10天的价格数据
        for i in range(max(0, len(recent_dates) - 10), len(recent_dates)):
            if i < len(recent_values) and len(recent_values[i]) >= 4:
                try:
                    date = recent_dates[i]
                    candle = recent_values[i]
                    quantitative_stats += f"{date}: 开盘${float(candle[0]):.2f} 收盘${float(candle[1]):.2f} 最低${float(candle[2]):.2f} 最高${float(candle[3]):.2f}\n"
                except (ValueError, IndexError):
                    continue

        return quantitative_stats
    else:
        # 标准格式化
        return f"""
日期数组: {json.dumps(recent_dates)}
K线数据数组: {json.dumps(recent_values)}
"""


def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """
    计算RSI指标

    Args:
        prices: 价格列表
        period: RSI周期

    Returns:
        RSI值 (0-100)
    """
    if len(prices) < period + 1:
        return 50  # 数据不足，返回中性值

    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]

    gains = [delta if delta > 0 else 0 for delta in deltas]
    losses = [-delta if delta < 0 else 0 for delta in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


def format_header_data(header_data: Dict[str, Any], for_llm: bool = True) -> str:
    """
    格式化币种基本信息

    Args:
        header_data: 原始header数据
        for_llm: 是否用于LLM提示词（为True时使用数值格式，为False时使用易读格式）
    """
    if not header_data:
        return "无可用数据"

    formatted_data = []
    symbol = header_data.get("symbol", "N/A")

    if for_llm:
        # 用于LLM提示词，使用标准化的数值格式
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

        for key, display_name in mapping.items():
            value = header_data.get(key, "N/A")
            # 对数值进行格式化
            if key in ["currentPrice", "high_24h", "low_24h", "ath", "atl"]:
                value = format_price(value, symbol)
            elif key in ["marketCap", "fullyDilutedValuation", "totalVolume", "marketCapChange_24h"]:
                value = format_large_number(value)
            elif key in ["priceChangePercentage_24h", "marketCapChangePercentage_24h",
                         "athChangePercentage", "atlChangePercentage"]:
                value = format_percentage_value(value)
            elif key in ["circulatingSupply", "totalSupply"]:
                value = format_supply(value)

            formatted_data.append(f"{display_name}: {value}")
    else:
        # 用于用户显示，使用更友好的格式
        formatted_data.append(f"【{symbol} 基本信息】")
        formatted_data.append(f"当前价格: {format_price(header_data.get('currentPrice'), symbol)}")
        formatted_data.append(f"24h价格变化: {format_percentage_value(header_data.get('priceChangePercentage_24h'))}")
        formatted_data.append(f"24h最高/最低: {format_price(header_data.get('high_24h'), symbol)} / {format_price(header_data.get('low_24h'), symbol)}")
        formatted_data.append(f"市值: {format_large_number(header_data.get('marketCap'))} (排名: {header_data.get('marketCapRank', 'N/A')})")
        formatted_data.append(f"24h交易额: {format_large_number(header_data.get('totalVolume'))}")
        formatted_data.append(f"完全稀释市值: {format_large_number(header_data.get('fullyDilutedValuation'))}")
        formatted_data.append(f"流通/总供应: {format_supply(header_data.get('circulatingSupply'))} / {format_supply(header_data.get('totalSupply'))}")
        formatted_data.append(f"历史最高: {format_price(header_data.get('ath'), symbol)} ({format_percentage_value(header_data.get('athChangePercentage'))})")
        formatted_data.append(f"历史最低: {format_price(header_data.get('atl'), symbol)} ({format_percentage_value(header_data.get('atlChangePercentage'))})")

    return "\n".join(formatted_data)


def format_news_data(news: List[str]) -> str:
    """格式化新闻数据"""
    if not news:
        return "暂无相关新闻"

    news_text = "\n".join([f"- {n}" for n in news[:20]])  # 限制显示20条新闻
    return news_text


def format_derivatives_data(derivatives_data: Dict[str, Any], for_quantitative: bool = False) -> str:
    """
    格式化衍生品数据

    Args:
        derivatives_data: 衍生品数据字典
        for_quantitative: 是否用于量化分析（为True时提供更详细的统计）
    """
    formatted_data = []

    if for_quantitative:
        # 为量化分析提供更结构化的数据
        formatted_data.append("【衍生品市场数据分析】")

        # 买卖比例分析
        but_sell_ratio = derivatives_data.get("but_sell_ratio", {})
        if but_sell_ratio:
            formatted_data.append("\n【买卖比例（多空力量）】")
            for exchange, data in but_sell_ratio.items():
                if data and isinstance(data, dict):
                    formatted_data.append(f"{exchange}: {json.dumps(data, ensure_ascii=False)}")

        # 持仓量分析
        open_interest = derivatives_data.get("open_interest", {})
        if open_interest:
            formatted_data.append("\n【持仓量变化（市场参与度）】")
            for exchange, data in open_interest.items():
                if data and isinstance(data, dict):
                    formatted_data.append(f"{exchange}: {json.dumps(data, ensure_ascii=False)}")

        # 交易量分析
        trading_volume = derivatives_data.get("trading_volume", {})
        if trading_volume:
            formatted_data.append("\n【交易量数据（市场活跃度）】")
            for exchange, data in trading_volume.items():
                if data and isinstance(data, dict):
                    formatted_data.append(f"{exchange}: {json.dumps(data, ensure_ascii=False)}")

        # 资金费率分析
        funding_rate = derivatives_data.get("funding_rate", {})
        if funding_rate:
            formatted_data.append("\n【资金费率（多空情绪）】")
            formatted_data.append(json.dumps(funding_rate, ensure_ascii=False))

        formatted_data.append("\n【衍生品数据说明】")
        formatted_data.append("- 买卖比例 > 50% 表示多头占优，< 50% 表示空头占优")
        formatted_data.append("- 持仓量增加表示市场参与度提升")
        formatted_data.append("- 资金费率为正表示多头支付给空头，市场看多")
        formatted_data.append("- 资金费率为负表示空头支付给多头，市场看空")
    else:
        # 标准格式化
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