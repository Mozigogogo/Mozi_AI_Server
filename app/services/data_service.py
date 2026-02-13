import requests
import json
import pymysql
from typing import Dict, List, Any, Optional
from app.core.config import get_settings
from app.core.exceptions import DataFetchException, DatabaseException

settings = get_settings()


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
            return data.get("data", {})
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
    exchanges = ["binance", "bitget"]
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