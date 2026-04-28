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


def validate_coin_exists(symbol: str) -> bool:
    """验证币种是否存在"""
    try:
        url = f"{settings.kline_api_base}/search/iscoin?coin={symbol}"
        response = fetch_json(url)
        if response.get("code") == 0:
            return response.get("data", {}).get("isCoin", False)
        return False
    except Exception:
        # 验证失败时默认为存在，避免误拒
        return True


def get_derivatives_agg(symbol: str) -> Dict[str, Any]:
    """获取合约持仓、成交、资金费率聚合数据"""
    url = f"{settings.derivatives_api_base}/histUsdAgg/forllm?coin={symbol}"
    try:
        data = fetch_json(url)
        if data.get("code") == 0:
            return data.get("data", {})
        return {}
    except Exception:
        return {}


def get_trading_value(symbol: str) -> Dict[str, Any]:
    """获取成交额数据"""
    url = f"{settings.derivatives_api_base}/histTradingVal/forllm?coin={symbol}"
    try:
        data = fetch_json(url)
        if data.get("code") == 0:
            return data.get("data", {})
        return {}
    except Exception:
        return {}


def get_funding_rate(symbol: str) -> Dict[str, Any]:
    """获取资金费率数据"""
    url = f"{settings.derivatives_api_base}/foundrate/forllm?coin={symbol}"
    try:
        data = fetch_json(url)
        if data.get("code") == 0:
            return data.get("data", {})
        return {}
    except Exception:
        return {}


def get_all_derivatives_data(symbol: str) -> Dict[str, Any]:
    """获取所有衍生品数据（使用新接口）"""
    return {
        "derivatives_agg": get_derivatives_agg(symbol),
        "trading_value": get_trading_value(symbol),
        "funding_rate": get_funding_rate(symbol)
    }


def get_buy_sell_ratio(symbol: str) -> Dict[str, Any]:
    """获取买卖比例（从衍生品聚合数据中提取）"""
    try:
        agg_data = get_derivatives_agg(symbol)
        # 从聚合数据中提取买卖比例
        return {
            "buy_ratio": agg_data.get("buy_ratio", 0.5),
            "sell_ratio": agg_data.get("sell_ratio", 0.5),
            "timestamp": agg_data.get("timestamp")
        }
    except Exception:
        return {"buy_ratio": 0.5, "sell_ratio": 0.5}


def get_open_interest(symbol: str) -> Dict[str, Any]:
    """获取持仓量（从衍生品聚合数据中提取）"""
    try:
        agg_data = get_derivatives_agg(symbol)
        return {
            "open_interest": agg_data.get("open_interest", 0),
            "oi_change": agg_data.get("oi_change", 0),
            "timestamp": agg_data.get("timestamp")
        }
    except Exception:
        return {"open_interest": 0, "oi_change": 0}


def get_trading_volume(symbol: str) -> Dict[str, Any]:
    """获取成交量（从成交额数据中提取）"""
    try:
        trading_data = get_trading_value(symbol)
        return {
            "volume": trading_data.get("volume", 0),
            "volume_change": trading_data.get("volume_change", 0),
            "timestamp": trading_data.get("timestamp")
        }
    except Exception:
        return {"volume": 0, "volume_change": 0}


# 别名，保持向后兼容
get_recent_news = get_news_from_mysql