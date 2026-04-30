import requests
import json
import pymysql
from typing import Dict, List, Any, Optional
from app.core.config import get_settings
from app.core.exceptions import DataFetchException, DatabaseException

settings = get_settings()

# MySQL 连接池（全局）
_db_pool = None
_connections = []  # 简化的连接管理

def get_db_pool():
    """获取 MySQL 连接（简化版本，避免连接池兼容性问题）"""
    return None  # 返回None，使用单连接模式

def get_db_connection():
    """获取数据库连接（单连接模式）"""
    import pymysql
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset=settings.mysql_charset,
        connect_timeout=10,
        read_timeout=30
    )

def close_db_pool():
    """关闭数据库连接"""
    global _connections
    for conn in _connections:
        try:
            conn.close()
        except Exception:
            pass
    _connections = []


def fetch_json(url: str, timeout: int = None, max_retries: int = None) -> Any:
    """通用JSON数据获取函数（支持重试和动态超时）"""
    if timeout is None:
        timeout = settings.api_timeout
    if max_retries is None:
        max_retries = settings.api_max_retries

    last_exception = None

    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except requests.Timeout as e:
            last_exception = DataFetchException(f"API请求超时（{timeout}秒）: {url}")
            if attempt < max_retries - 1:
                print(f"API超时，{settings.api_retry_delay}秒后重试 ({attempt + 1}/{max_retries})...")
                import time
                time.sleep(settings.api_retry_delay)
        except requests.HTTPError as e:
            last_exception = DataFetchException(f"API HTTP错误: {str(e)}")
            # 502 Bad Gateway 可能是临时问题，可以重试
            if "502" in str(e) and attempt < max_retries - 1:
                print(f"API 502错误，{settings.api_retry_delay}秒后重试 ({attempt + 1}/{max_retries})...")
                import time
                time.sleep(settings.api_retry_delay)
        except Exception as e:
            last_exception = DataFetchException(f"Failed to fetch data from {url}: {str(e)}")

    # 所有重试都失败
    raise last_exception if last_exception else DataFetchException(f"API请求失败: {url}")


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
    """从MySQL获取新闻数据（使用单连接模式）"""
    if limit is None:
        limit = settings.max_news_items

    mysql = None
    cursor = None
    try:
        # 直接创建数据库连接（单连接模式）
        mysql = get_db_connection()
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

        news = []
        for title, content, ct, topic in rows:
            news.append(f"{ct}｜{title}｜{topic}")
        return news
    except Exception as e:
        # 容错处理：不抛出异常，返回空列表
        print(f"获取新闻数据失败: {str(e)}")
        return []
    finally:
        # 确保连接和游标被正确关闭
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if mysql:
            try:
                mysql.close()
            except Exception:
                pass


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
            api_data = data.get("data", {})
            # 返回完整的data对象，包含所有字段
            # 包括: coin, metric, unit, exchanges, dates, data (各交易所的数据)
            return api_data
        return {}
    except Exception as e:
        print(f"获取衍生品聚合数据异常: {str(e)}")
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


# 别名，保持向后兼容（如果其他地方还在使用）
def get_buy_sell_ratio(symbol: str) -> Dict[str, Any]:
    """获取买卖比例（从衍生品聚合数据中提取）- 已废弃"""
    try:
        agg_data = get_derivatives_agg(symbol)
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
        # 从聚合数据中提取持仓量相关信息
        agg_data = get_derivatives_agg(symbol)

        if not agg_data:
            return {"open_interest": 0, "oi_change": 0}

        # 尝试从聚合数据中提取持仓量相关字段
        # 根据API测试，histUsdAgg返回的数据结构中包含完整信息
        # 数据应该在 agg_data.data 中
        nested_data = agg_data.get("data")

        if not isinstance(nested_data, dict):
            return {"open_interest": 0, "oi_change": 0}

        # 从各交易所数据中提取持仓量
        # 数据结构可能包含: openInterest, oi, open_interest_total等
        open_interest_values = []

        for exchange, data in nested_data.items():
            if isinstance(data, list) and data:
                # 尝试获取持仓量值（可能是最新值或列表）
                # 根据API测试，data可能是一个数组
                if len(data) > 0:
                    open_interest_values.append(data[-1])  # 取最新值

        # 计算总持仓量
        total_oi = sum(open_interest_values) if open_interest_values else 0

        # 获取持仓量变化（如果有历史数据）
        oi_change = 0
        if len(open_interest_values) > 1:
            # 计算变化（最新值 - 前一天值）
            oi_change = open_interest_values[-1] - open_interest_values[-2]

        # 获取时间戳
        timestamp = agg_data.get("timestamp") or agg_data.get("dates", [])[0] if isinstance(agg_data.get("dates"), list) else None

        return {
            "open_interest": total_oi,
            "oi_change": oi_change,
            "timestamp": timestamp
        }

    except Exception as e:
        print(f"获取持仓量数据异常: {str(e)}")
        return {"open_interest": 0, "oi_change": 0}


def get_binance_buy_sell_ratio(symbol: str) -> Dict[str, Any]:
    """获取 Binance 交易所的买卖比例"""
    url = f"{settings.derivatives_api_base}/histratio?coin={symbol}&exchange=Binance&type=but_sell_ratio"
    try:
        data = fetch_json(url)
        if data.get("code") == 0:
            return data.get("data", {})
        return {}
    except Exception:
        return {}


def get_kraken_buy_sell_ratio(symbol: str) -> Dict[str, Any]:
    """获取 Kraken 交易所的买卖比例"""
    url = f"{settings.derivatives_api_base}/histratio?coin={symbol}&exchange=Kraken&type=but_sell_ratio"
    try:
        data = fetch_json(url)
        if data.get("code") == 0:
            return data.get("data", {})
        return {}
    except Exception:
        return {}


def get_trading_volume(symbol: str) -> Dict[str, Any]:
    """获取成交量（从成交额数据中提取）- 直接返回各交易所数据，不做汇总"""
    try:
        # 直接调用 get_trading_value 获取成交额API的完整返回
        trading_data = get_trading_value(symbol)

        # 不做任何计算或汇总，直接返回原始数据
        # 让Skill层或回答生成器处理数据展示和分析
        return trading_data

    except Exception as e:
        print(f"获取成交量数据异常: {str(e)}")
        return {"volume": 0, "volume_change": 0}


# 别名，保持向后兼容
get_recent_news = get_news_from_mysql