import requests
import json
import pymysql
import time
import threading
from typing import Dict, List, Any, Optional
from app.core.config import get_settings
from app.core.exceptions import DataFetchException, DatabaseException

settings = get_settings()

# MySQL 连接池（全局）
_db_pool = None
_connections = []  # 简化的连接管理

# ============================================================
# API 响应缓存（TTL + 并发请求去重）
# ============================================================
_api_cache: Dict[str, tuple] = {}  # {url: (data, expire_time)}
_cache_lock = threading.Lock()
_inflight: Dict[str, threading.Event] = {}  # {url: Event} 去重并发请求
_inflight_lock = threading.Lock()
_CACHE_TTL = 30  # 默认缓存30秒
_api_semaphore = threading.Semaphore(3)  # 限制最多3个并发API请求

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


def _get_cached(url: str):
    """获取缓存数据，过期返回 None"""
    with _cache_lock:
        if url in _api_cache:
            data, expire_at = _api_cache[url]
            if time.time() < expire_at:
                return data
            del _api_cache[url]
    return None


def _set_cached(url: str, data: Any, ttl: int = _CACHE_TTL):
    """写入缓存"""
    with _cache_lock:
        _api_cache[url] = (data, time.time() + ttl)


def fetch_json_cached(url: str, timeout: int = None, max_retries: int = None, ttl: int = _CACHE_TTL) -> Any:
    """带缓存和并发去重的 fetch_json。同一 URL 在缓存有效期内只请求一次。"""
    cached = _get_cached(url)
    if cached is not None:
        return cached

    # 并发去重：如果已有线程在请求同一 URL，等待其结果
    my_event = None
    with _inflight_lock:
        if url in _inflight:
            my_event = _inflight[url]
        else:
            my_event = threading.Event()
            _inflight[url] = my_event

    if my_event is not _inflight.get(url):
        # 有其他线程在请求，等待
        my_event.wait(timeout=30)
        cached = _get_cached(url)
        if cached is not None:
            return cached
        # 等待超时或请求失败，走正常流程
    else:
        # 我是第一个请求者
        try:
            data = fetch_json(url, timeout, max_retries)
            _set_cached(url, data, ttl)
            return data
        finally:
            with _inflight_lock:
                _inflight.pop(url, None)
                my_event.set()

    # fallback: 正常请求
    data = fetch_json(url, timeout, max_retries)
    _set_cached(url, data, ttl)
    return data


def fetch_json(url: str, timeout: int = None, max_retries: int = None) -> Any:
    """通用JSON数据获取函数（支持重试和动态超时，带并发限流）"""
    if timeout is None:
        timeout = settings.api_timeout
    if max_retries is None:
        max_retries = settings.api_max_retries

    last_exception = None

    for attempt in range(max_retries):
        try:
            _api_semaphore.acquire()
            try:
                response = requests.get(url, timeout=timeout)
            finally:
                _api_semaphore.release()
            response.raise_for_status()
            return response.json()
        except requests.Timeout as e:
            last_exception = DataFetchException(f"API请求超时（{timeout}秒）: {url}")
            if attempt < max_retries - 1:
                print(f"API超时，{settings.api_retry_delay}秒后重试 ({attempt + 1}/{max_retries})...")
                import time
                time.sleep(settings.api_retry_delay)
        except requests.HTTPError as e:
            last_exception = DataFetchException(f"{e.response.status_code}: {str(e)}")
            status_code = e.response.status_code if e.response else 0
            # 502/503 通常是临时问题，可以重试
            if status_code in (502, 503) and attempt < max_retries - 1:
                print(f"API {status_code}错误，{settings.api_retry_delay}秒后重试 ({attempt + 1}/{max_retries})...")
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
        data = fetch_json_cached(url)
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
        data = fetch_json_cached(url)
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
        data = fetch_json_cached(url, timeout=8, max_retries=2)
        if data.get("code") == 0:
            return data.get("data", {})
        return {}
    except Exception as e:
        print(f"获取衍生品聚合数据异常: {str(e)}")
        return {}


def get_trading_value(symbol: str) -> Dict[str, Any]:
    """获取成交额数据"""
    url = f"{settings.derivatives_api_base}/histTradingVal/forllm?coin={symbol}"
    try:
        data = fetch_json_cached(url)
        if data.get("code") == 0:
            return data.get("data", {})
        return {}
    except Exception:
        return {}


def get_funding_rate(symbol: str) -> Dict[str, Any]:
    """获取资金费率数据"""
    url = f"{settings.derivatives_api_base}/foundrate/forllm?coin={symbol}"
    try:
        data = fetch_json_cached(url)
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
    """获取买卖比例 - 并发调用Binance和Kraken两个交易所接口"""
    import concurrent.futures
    result = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(get_binance_buy_sell_ratio, symbol): "binance",
            executor.submit(get_kraken_buy_sell_ratio, symbol): "kraken",
        }
        for future in concurrent.futures.as_completed(futures, timeout=15):
            exchange = futures[future]
            try:
                data = future.result()
                if data:
                    result[exchange] = data
            except Exception:
                pass
    return result if result else {"binance": {}, "kraken": {}}


def get_open_interest(symbol: str) -> Dict[str, Any]:
    """获取持仓量 - 直接返回各交易所数据，不做汇总"""
    try:
        agg_data = get_derivatives_agg(symbol)
        if not agg_data:
            return {}
        return agg_data
    except Exception as e:
        print(f"获取持仓量数据异常: {str(e)}")
        return {}


def get_binance_buy_sell_ratio(symbol: str) -> Dict[str, Any]:
    """获取 Binance 交易所的买卖比例"""
    url = f"{settings.derivatives_api_base}/histratio?coin={symbol}&exchange=Binance&type=but_sell_ratio"
    try:
        data = fetch_json_cached(url)
        if data.get("code") == 0:
            return data.get("data", {})
        return {}
    except Exception:
        return {}


def get_kraken_buy_sell_ratio(symbol: str) -> Dict[str, Any]:
    """获取 Kraken 交易所的买卖比例"""
    url = f"{settings.derivatives_api_base}/histratio?coin={symbol}&exchange=Kraken&type=but_sell_ratio"
    try:
        data = fetch_json_cached(url)
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