import requests
import json
import pymysql
import re
from typing import Dict, List, Any, Optional, Union
from app.core.config import get_settings
from app.core.exceptions import DataFetchException, DatabaseException

settings = get_settings()


def clean_numeric_value(value: Any) -> Union[float, str]:
    """
    清理数值数据，处理中文单位和特殊格式
    例如: "$329.83亿" -> 329830000000
         "$1.36万亿" -> 1360000000000  # 注意：万亿=亿万
         "1999.27万" -> 19992700
         "$68210.09" -> 68210.09
    """
    if value is None:
        return 0

    value_str = str(value)

    # 移除美元符号和空格
    value_str = value_str.replace('$', '').replace(',', '').strip()

    # 处理中文单位（注意顺序：先检查万亿，再检查亿和万）
    if '万亿' in value_str:
        # 提取数字部分并乘以1万亿（1万亿 = 10^12）
        # 移除"万亿"单位，只保留数字
        num_str = value_str.replace('万亿', '').strip()
        try:
            num = float(num_str)
            return num * 1000000000000  # 1万亿
        except ValueError:
            return 0
    elif '亿' in value_str:
        # 提取数字部分并乘以1亿
        num_str = value_str.replace('亿', '').strip()
        try:
            num = float(num_str)
            return num * 100000000  # 1亿
        except ValueError:
            return 0
    elif '万' in value_str:
        # 提取数字部分并乘以1万
        num_str = value_str.replace('万', '').strip()
        try:
            num = float(num_str)
            return num * 10000  # 1万
        except ValueError:
            return 0
    elif value_str:
        # 尝试直接转换为浮点数
        try:
            return float(value_str)
        except ValueError:
            return value_str  # 如果转换失败，返回原字符串

    return 0


def format_number(value: Union[float, int, str, None], precision: int = 2) -> str:
    """
    格式化数值显示，处理大数值的可读性
    """
    if value is None:
        return "N/A"

    try:
        num = float(value)
    except (ValueError, TypeError):
        return str(value)

    # 处理大数值
    if num >= 100000000:  # 1亿以上
        return f"${num / 100000000:.{precision}f}亿"
    elif num >= 10000:  # 1万以上
        return f"${num / 10000:.{precision}f}万"
    else:
        return f"{num:.{precision}f}"


def format_volume(value: Union[float, int, str, None]) -> str:
    """
    格式化交易量显示
    """
    if value is None:
        return "N/A"

    try:
        num = float(value)
    except (ValueError, TypeError):
        return str(value)

    # 处理交易量单位
    if num >= 1000000000:  # 10亿以上
        return f"{num / 1000000000:.2f}B"
    elif num >= 1000000:  # 100万以上
        return f"{num / 1000000:.2f}M"
    elif num >= 1000:  # 1千以上
        return f"{num / 1000:.2f}K"
    else:
        return f"{num:.2f}"


def format_percentage(value: Union[float, int, str, None]) -> str:
    """
    格式化百分比显示
    """
    if value is None:
        return "N/A"

    try:
        num = float(value)
        return f"{num:.2f}%"
    except (ValueError, TypeError):
        return str(value)


def clean_header_data(header_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    清理和标准化header数据
    - 转换中文单位为数值
    - 统一日期格式
    - 清理百分比格式
    """
    if not header_data:
        return {}

    cleaned_data = {}
    numeric_fields = [
        'currentPrice', 'marketCap', 'fullyDilutedValuation',
        'totalVolume', 'high_24h', 'low_24h', 'priceChange_24h',
        'marketCapChange_24h', 'circulatingSupply', 'totalSupply',
        'ath', 'atl'
    ]

    percentage_fields = [
        'priceChangePercentage_24h', 'marketCapChangePercentage_24h',
        'athChangePercentage', 'atlChangePercentage'
    ]

    for key, value in header_data.items():
        if value is None:
            cleaned_data[key] = None
            continue

        if key in numeric_fields:
            # 清理数值字段
            cleaned_value = clean_numeric_value(value)
            cleaned_data[key] = cleaned_value
        elif key in percentage_fields:
            # 清理百分比字段
            value_str = str(value).replace('%', '').strip()
            try:
                cleaned_data[key] = float(value_str)
            except ValueError:
                cleaned_data[key] = value
        else:
            # 其他字段保持原样
            cleaned_data[key] = value

    return cleaned_data


def fetch_json(url: str, timeout: int = 30) -> Any:
    """通用JSON数据获取函数"""
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise DataFetchException(f"Failed to fetch data from {url}: {str(e)}")


def get_kline_data(symbol: str) -> Dict[str, Any]:
    """获取K线数据"""
    url = f"{settings.kline_api_base}/detail/kline?symbol={symbol}&type=2"
    try:
        data = fetch_json(url)
        if data.get("code") == 0:
            return data.get("data", {})
        else:
            raise DataFetchException(f"API返回错误: {data.get('errorMsg', '未知错误')}")
    except Exception as e:
        raise DataFetchException(f"获取K线数据失败: {str(e)}")


def get_header_data(symbol: str) -> Dict[str, Any]:
    """获取币种基本信息"""
    url = f"{settings.kline_api_base}/detail/header?symbol={symbol}"
    try:
        data = fetch_json(url)
        if data.get("code") == 0:
            raw_data = data.get("data", {})
            # 清理和标准化数据
            cleaned_data = clean_header_data(raw_data)
            return cleaned_data
        else:
            raise DataFetchException(f"API返回错误: {data.get('errorMsg', '未知错误')}")
    except Exception as e:
        raise DataFetchException(f"获取基础信息失败: {str(e)}")


def get_news_from_mysql(symbol: str, limit: int = None) -> List[str]:
    """从MySQL获取新闻数据"""
    if limit is None:
        limit = settings.max_news_items

    try:
        mysql = pymysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            database=settings.mysql_database,
            charset=settings.mysql_charset
        )
        cursor = mysql.cursor()

        sql = f"""
        SELECT title, content, create_time, topic
        FROM ods_news_feed_processed_di
        WHERE coins RLIKE '{symbol}'
        ORDER BY create_time DESC
        LIMIT {limit}
        """
        cursor.execute(sql)
        rows = cursor.fetchall()

        cursor.close()
        mysql.close()

        news = []
        for title, content, ct, topic in rows:
            news.append(f"{ct}｜{title}｜{topic}")
        return news
    except Exception as e:
        raise DatabaseException(f"获取新闻数据失败: {str(e)}")


def get_but_sell_ratio(symbol: str) -> Dict[str, Any]:
    """获取买卖比例数据"""
    exchanges = ["Binance", "Kraken"]
    data = {}
    for ex in exchanges:
        url = f"{settings.derivatives_api_base}/histratio?coin={symbol}&exchange={ex}&type=but_sell_ratio"
        data[ex] = fetch_json(url)
    return data


def get_open_interest(symbol: str) -> Dict[str, Any]:
    """获取持仓量数据"""
    exchanges = ["binance", "bitget","bitfinex","bybit","coinbase","htx"]
    data = {}
    for ex in exchanges:
        url = f"{settings.derivatives_api_base}/histUsd?coin={symbol}&exchange={ex}"
        data[ex] = fetch_json(url)
    return data


def get_trading_volume(symbol: str) -> Dict[str, Any]:
    """获取交易量数据"""
    exchanges = [
        "Binance", "Bybit", "Bitget", "Okx", "Coinbase",
        "Bitfinex", "Gate", "Kucoin", "Bitmart", "mexc"
    ]
    data = {}
    for ex in exchanges:
        url = f"{settings.derivatives_api_base}/historytradingval?coin={symbol}&exchange={ex}"
        data[ex] = fetch_json(url)
    return data


def get_funding_rate() -> Any:
    """获取资金费率数据"""
    url = f"{settings.derivatives_api_base}/foundrate"
    return fetch_json(url)


def get_all_derivatives_data(symbol: str) -> Dict[str, Any]:
    """获取所有衍生品数据"""
    return {
        "but_sell_ratio": get_but_sell_ratio(symbol),
        "open_interest": get_open_interest(symbol),
        "trading_volume": get_trading_volume(symbol),
        "funding_rate": get_funding_rate()
    }


def calculate_technical_indicators(kline_data: Dict[str, Any]) -> Dict[str, float]:
    """
    计算技术指标

    Args:
        kline_data: K线数据

    Returns:
        包含技术指标的字典: ma7, ma30, rsi, volatility, price_position等
    """
    values = kline_data.get("values", [])

    if not values or len(values) < 15:
        return {
            "ma7": 0,
            "ma30": 0,
            "rsi": 50,
            "volatility": 0,
            "price_position": 50,
            "current_price": 0,
            "previous_price": 0,
            "price_change_pct": 0
        }

    # 提取价格数据 [开盘价, 收盘价, 最低价, 最高价]
    closes = []
    opens = []
    highs = []
    lows = []

    for candle in values:
        if len(candle) >= 4:
            try:
                opens.append(float(candle[0]))
                closes.append(float(candle[1]))
                lows.append(float(candle[2]))
                highs.append(float(candle[3]))
            except (ValueError, IndexError):
                continue

    if not closes:
        return {
            "ma7": 0,
            "ma30": 0,
            "rsi": 50,
            "volatility": 0,
            "price_position": 50,
            "current_price": 0,
            "previous_price": 0,
            "price_change_pct": 0
        }

    current_price = closes[-1]
    previous_price = closes[-2] if len(closes) > 1 else current_price
    price_change = current_price - previous_price
    price_change_pct = (price_change / previous_price * 100) if previous_price else 0

    # 计算移动平均线
    ma_7 = sum(closes[-7:]) / 7 if len(closes) >= 7 else sum(closes) / len(closes)
    ma_30 = sum(closes[-30:]) / 30 if len(closes) >= 30 else sum(closes) / len(closes)

    # 计算RSI（简化版，14周期）
    rsi = calculate_rsi(closes)

    # 计算波动率
    highest = max(highs) if highs else current_price
    lowest = min(lows) if lows else current_price
    volatility = (highest - lowest) / ((highest + lowest) / 2 * 100) if (highest + lowest) > 0 else 0

    # 计算价格位置
    if highest != lowest:
        price_position = ((current_price - lowest) / (highest - lowest)) * 100
    else:
        price_position = 50

    return {
        "ma7": round(ma_7, 2),
        "ma30": round(ma_30, 2),
        "rsi": round(rsi, 2),
        "volatility": round(volatility, 2),
        "price_position": round(price_position, 1),
        "current_price": round(current_price, 2),
        "previous_price": round(previous_price, 2),
        "price_change_pct": round(price_change_pct, 2),
        "highest": round(highest, 2),
        "lowest": round(lowest, 2)
    }


def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """
    计算RSI指标

    Args:
        prices: 价格列表
        period: RSI周期，默认14

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