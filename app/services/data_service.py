import requests
import json
import pymysql
import time
import threading
from typing import Dict, List, Any, Optional
from app.core.config import get_settings
from app.core.exceptions import DataFetchException, DatabaseException
from app.utils.logger import get_logger

settings = get_settings()
logger = get_logger("app.services.data_service")

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
_api_semaphore = threading.Semaphore(10)  # 限制最多10个并发API请求（匹配扫描并发数）

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
                logger.warning(f"API超时，{settings.api_retry_delay}秒后重试 ({attempt + 1}/{max_retries})...")
                import time
                time.sleep(settings.api_retry_delay)
        except requests.HTTPError as e:
            last_exception = DataFetchException(f"{e.response.status_code}: {str(e)}")
            status_code = e.response.status_code if e.response else 0
            # 502/503 通常是临时问题，可以重试
            if status_code in (502, 503) and attempt < max_retries - 1:
                logger.warning(f"API {status_code}错误，{settings.api_retry_delay}秒后重试 ({attempt + 1}/{max_retries})...")
                import time
                time.sleep(settings.api_retry_delay)
        except Exception as e:
            last_exception = DataFetchException(f"Failed to fetch data from {url}: {str(e)}")

    # 所有重试都失败
    raise last_exception if last_exception else DataFetchException(f"API请求失败: {url}")


KLINE_TYPE_META = {
    1: {"name": "hourly_72h", "label": "小时K线(72h)", "limit": 72},
    2: {"name": "daily_60d", "label": "日线(60d)", "limit": 60},
    3: {"name": "weekly_1y", "label": "周线(近1年)", "limit": 52},
    4: {"name": "monthly_all", "label": "月线(全量)", "limit": None},
}


def kline_url(symbol: str, kline_type: int) -> str:
    """K线 REST URL（get_kline_data 与 ws_kline 缓存失效共用，保证 key 一致）"""
    return f"{settings.kline_api_base}/detail/kline?symbol={symbol}&type={kline_type}"


def invalidate_cache(url: str) -> None:
    """旁路失效指定 URL 的缓存（ws_kline 推送时调用，下次请求立刻拉新）"""
    with _cache_lock:
        _api_cache.pop(url, None)


def get_kline_data(symbol: str, kline_type: int = 2) -> Dict[str, Any]:
    """获取K线数据，kline_type: 1=小时 2=天 3=周 4=月"""
    url = kline_url(symbol, kline_type)
    try:
        data = fetch_json_cached(url)
        if data.get("code") == 0:
            return data.get("data") or {}
        else:
            raise DataFetchException(f"API返回错误: {data.get('errorMsg', '未知错误')}")
    except Exception as e:
        raise DataFetchException(f"获取K线数据失败: {str(e)}")


def trim_kline_data(kline_data: Dict[str, Any], kline_type: int) -> Dict[str, Any]:
    """按业务周期裁剪K线：1=24h小时线，2=30d日线，3=近1年周线，4=月线全量。"""
    if not kline_data or not isinstance(kline_data, dict):
        return {}

    meta = KLINE_TYPE_META.get(kline_type, KLINE_TYPE_META[2])
    limit = meta.get("limit")
    if not limit:
        result = dict(kline_data)
    else:
        result = dict(kline_data)
        for key in ("values", "categoryData", "xAxisData"):
            value = result.get(key)
            if isinstance(value, list):
                result[key] = value[-limit:]

    result["klineType"] = kline_type
    result["periodName"] = meta["name"]
    result["periodLabel"] = meta["label"]
    return result


def get_kline_data_for_period(symbol: str, kline_type: int = 2) -> Dict[str, Any]:
    """获取并按业务周期裁剪后的K线数据。"""
    return trim_kline_data(get_kline_data(symbol, kline_type), kline_type)


def get_multi_timeframe_klines(symbol: str, types: tuple = (1, 2, 3, 4)) -> Dict[str, Any]:
    """获取多周期K线，失败的周期返回空字典，避免单一周期拖垮信号卡。"""
    result = {}
    for kline_type in types:
        meta = KLINE_TYPE_META.get(kline_type, {"name": f"type_{kline_type}"})
        try:
            result[meta["name"]] = get_kline_data_for_period(symbol, kline_type)
        except Exception as e:
            logger.error(f"获取{meta['name']}失败({symbol}): {e}")
            result[meta["name"]] = {}
    return result


def get_trade_volume(symbol: str) -> List[Dict[str, Any]]:
    """获取每日成交量/成交额"""
    url = f"{settings.kline_api_base}/detail/spot/tradevolume?symbol={symbol}"
    try:
        data = fetch_json_cached(url)
        if data.get("code") == 0:
            return data.get("data") or []
        else:
            raise DataFetchException(f"API返回错误: {data.get('errorMsg', '未知错误')}")
    except Exception as e:
        raise DataFetchException(f"获取成交量数据失败: {str(e)}")


def get_header_data(symbol: str) -> Dict[str, Any]:
    """获取币种基本信息"""
    url = f"{settings.kline_api_base}/detail/header?symbol={symbol}"
    try:
        data = fetch_json_cached(url)
        if data.get("code") == 0:
            return data.get("data") or {}
        else:
            raise DataFetchException(f"API返回错误: {data.get('errorMsg', '未知错误')}")
    except Exception as e:
        raise DataFetchException(f"获取基础信息失败: {str(e)}")


def get_price_change(symbol: str) -> Dict[str, str]:
    """获取币种区间涨跌（1日/7日/1月/1年，值为带 % 的字符串）

    GET /easy/getReturnInvestment?symbol={BASE}
    data 是数组：[{"symbol":"BTC","priceChange1Day":"-1.49%",...}]（与美股版对象形态不同）
    """
    url = f"{settings.kline_api_base}/easy/getReturnInvestment?symbol={symbol}"
    try:
        data = fetch_json_cached(url)
        if data.get("code") == 0:
            items = data.get("data") or []
            if isinstance(items, list) and items:
                item = items[0]
                return {k: v for k, v in item.items() if k != "symbol"}
        return {}
    except Exception as e:
        logger.error(f"获取区间涨跌失败: {str(e)}")
        return {}


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
        logger.error(f"获取新闻数据失败: {str(e)}")
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


# 非交易币种黑名单（稳定币、法币、衍生品）
_COIN_BLACKLIST = {"U", "EUR", "PAXG", "XAUT", "WBTC", "WBETH", "BTCB"}
# 币种列表缓存（5分钟刷新一次）
_coin_list_cache: List[str] = []
_coin_list_expire: float = 0
_coin_list_lock = threading.Lock()


def get_discovery_coins() -> List[str]:
    """从 discovery API 动态获取全市场币种列表（带5分钟缓存）"""
    global _coin_list_cache, _coin_list_expire
    with _coin_list_lock:
        if _coin_list_cache and time.time() < _coin_list_expire:
            return _coin_list_cache

    try:
        url = f"{settings.kline_api_base}/discovery/coin?pageNo=1&pageSize=200"
        data = fetch_json(url, timeout=10, max_retries=2)
        if data.get("code") != 0:
            return _coin_list_cache or []

        coins = []
        for item in data.get("data", {}).get("list", []):
            symbol = item.get("symbol", "")
            # 过滤：url="null" 的是非币种，黑名单跳过
            if not symbol:
                continue
            if symbol in _COIN_BLACKLIST:
                continue
            coins.append(symbol)

        with _coin_list_lock:
            _coin_list_cache = coins
            _coin_list_expire = time.time() + 300  # 5分钟缓存
        return coins
    except Exception as e:
        logger.error(f"获取币种列表失败: {e}")
        return _coin_list_cache or []


def get_derivatives_agg(symbol: str) -> Dict[str, Any]:
    """获取合约持仓、成交、资金费率聚合数据"""
    url = f"{settings.derivatives_api_base}/histUsdAgg/forllm?coin={symbol}"
    try:
        data = fetch_json_cached(url, timeout=8, max_retries=2)
        if data.get("code") == 0:
            return data.get("data", {})
        return {}
    except Exception as e:
        logger.error(f"获取衍生品聚合数据异常: {str(e)}")
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
            executor.submit(get_okx_buy_sell_ratio, symbol): "okx",
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
        logger.error(f"获取持仓量数据异常: {str(e)}")
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


def get_okx_buy_sell_ratio(symbol: str) -> Dict[str, Any]:
    """获取 OKX 交易所的买卖比例"""
    url = f"{settings.derivatives_api_base}/histratio?coin={symbol}&exchange=OKX&type=but_sell_ratio"
    try:
        data = fetch_json_cached(url)
        if data.get("code") == 0:
            return data.get("data", {})
        return {}
    except Exception:
        return {}


LONGSHORT_RATIO_TYPES = {
    "global_account_ratio": "全球用户账户",
    "top_account_ratio": "大户账户",
    "top_hold_ratio": "大户持仓",
    "but_sell_ratio": "买卖量比",
    "global_hold_ratio": "全球持仓",
}


def get_longshort_snapshot(symbol: str, ratio_type: str = "global_account_ratio") -> Dict[str, Dict[str, float]]:
    """最新一轮各交易所多空比快照（一次请求拿全 5+ 交易所）

    返回 {交易所: {"long": 0.56, "short": 0.44}}，百分数已转小数；失败返回 {}。
    """
    url = f"{settings.derivatives_api_base}/longshort?coin={symbol}&type={ratio_type}"
    try:
        data = fetch_json_cached(url)
        if data.get("code") != 0:
            return {}
        entries = (data.get("data") or {}).get("list") or []
        result: Dict[str, Dict[str, float]] = {}
        for e in entries:
            name = e.get("name")
            if not name:
                continue
            try:
                long_v = round(float(str(e.get("long", "")).replace("%", "")) / 100, 4)
                short_v = round(float(str(e.get("short", "")).replace("%", "")) / 100, 4)
            except (ValueError, TypeError):
                continue
            result[name] = {"long": long_v, "short": short_v}
        return result
    except Exception:
        return {}


def get_fear_greed() -> Dict[str, Any]:
    """获取加密市场恐惧贪婪指数（每日更新一次，缓存10分钟）

    主源：后端 /easy/getFearGreedIndex（MySQL ods_get_fear_index_di，仅最新值）
    备源：alternative.me（额外含昨日值与变化）
    返回 {"today": {"value", "classification", "date"[, "change"]}, "yesterday": {...}}，
    失败返回 {}（调用方跳过即可）。
    """
    from datetime import datetime

    # 主源：后端
    try:
        data = fetch_json_cached(f"{settings.kline_api_base}/easy/getFearGreedIndex", ttl=600)
        d = data.get("data") or {}
        if data.get("code") == 0 and d.get("value") is not None:
            return {"today": {
                "value": int(d["value"]),
                "classification": d.get("category", ""),
                "date": datetime.now().strftime("%Y-%m-%d"),
            }, "source": "backend"}
    except Exception as e:
        logger.warning(f"后端恐惧贪婪接口失败，回退 alternative.me: {e}")

    # 备源：alternative.me
    url = f"{settings.fear_greed_api_url}?limit=2"
    try:
        data = fetch_json_cached(url, timeout=8, max_retries=2, ttl=600)
        entries = data.get("data") or []
        if not entries:
            return {}

        def _parse(e: Dict[str, Any]) -> Dict[str, Any]:
            date = ""
            try:
                date = datetime.fromtimestamp(int(e.get("timestamp", 0))).strftime("%Y-%m-%d")
            except (ValueError, OSError, TypeError, OverflowError):
                pass
            return {
                "value": int(e.get("value", 0)),
                "classification": e.get("value_classification", ""),
                "date": date,
            }

        today = _parse(entries[0])
        result: Dict[str, Any] = {"today": today}
        if len(entries) > 1:
            yesterday = _parse(entries[1])
            result["yesterday"] = yesterday
            today["change"] = today["value"] - yesterday["value"]
        result["source"] = "alternative.me"
        return result
    except Exception as e:
        logger.error(f"获取恐惧贪婪指数失败: {e}")
        return {}


# ── 美股数据 ──────────────────────────────────────────────

US_KLINE_INTERVALS = ("1m", "5m", "15m", "1h", "1d", "1w", "1mon")


def get_us_quote(symbol: str) -> Dict[str, Any]:
    """美股实时报价 + 档案（现价/涨跌幅/日内高低/量 + 市值/行业/52周/简介等全套）"""
    url = f"{settings.kline_api_base}/stock/detail/header?symbol={symbol}"
    try:
        data = fetch_json_cached(url)
        if data.get("code") == 0 and data.get("data"):
            return data["data"]
        return {}
    except Exception:
        return {}


def get_us_price_change(symbol: str) -> Dict[str, Any]:
    """美股轻量报价（现价 + 涨跌幅 + 量，轻量轮询用）"""
    url = f"{settings.kline_api_base}/stock/search/lastpricechange?symbol={symbol}"
    try:
        data = fetch_json_cached(url)
        if data.get("code") == 0 and data.get("data"):
            return data["data"]
        return {}
    except Exception:
        return {}


def get_us_kline(symbol: str, kline_type: int = 2) -> Dict[str, Any]:
    """美股K线 — 历史链路（MySQL）：type 1=小时(24根) 2=日(30) 3=周(52) 4=月(全量)

    返回 {"symbol", "list": [{openPrice, highPrice, lowPrice, closePrice, volume, quoteVolume, dt}]}
    """
    url = f"{settings.kline_api_base}/stock/detail/kline?symbol={symbol}&type={kline_type}"
    try:
        data = fetch_json_cached(url)
        if data.get("code") == 0 and data.get("data"):
            return data["data"]
        return {}
    except Exception:
        return {}


def get_us_kline_realtime(symbol: str, interval: str = "15m",
                          limit: int = None, page: int = 1) -> Dict[str, Any]:
    """美股K线 — Redis 实时分页链路：interval 1m/5m/15m/1h/1d/1w/1mon（无 4h）

    每页 50 根，page=1 为最新一页；limit 为窗口上限（如 1h≤720、1d≤90）。
    """
    if interval not in US_KLINE_INTERVALS:
        interval = "15m"
    params = f"symbol={symbol}&interval={interval}&page={page}"
    if limit:
        params += f"&limit={int(limit)}"
    url = f"{settings.kline_api_base}/stock/detail/kline?{params}"
    try:
        data = fetch_json_cached(url)
        if data.get("code") == 0 and data.get("data"):
            return data["data"]
        return {}
    except Exception:
        return {}


def search_us_symbol(keyword: str, limit: int = 10) -> list:
    """美股 ticker 搜索：symbol 前缀 + 英文名模糊。

    中文关键词暂不支持（后端宽表无中文列）——中文公司名靠意图层 LLM 先转 ticker 再校验。
    """
    url = f"{settings.kline_api_base}/stock/search/suggest?keyword={keyword}&limit={limit}"
    try:
        data = fetch_json_cached(url)
        if data.get("code") == 0:
            d = data.get("data")
            if isinstance(d, dict):
                return d.get("list") or []
            return d or []
        return []
    except Exception:
        return []


def validate_us_ticker(symbol: str) -> Dict[str, Any]:
    """ticker 校验（美股优先、加密兜底）→ {"valid": bool, "type": "stock"|"crypto"|...}"""
    url = f"{settings.kline_api_base}/search/validate?symbol={symbol}"
    try:
        data = fetch_json_cached(url)
        if data.get("code") == 0 and data.get("data"):
            return data["data"]
        return {"valid": False, "type": None}
    except Exception:
        return {"valid": False, "type": None}


def get_us_session(symbol: str) -> Dict[str, Any]:
    """美股交易时段（pre_market/regular/post_market 窗口 + 当前 status/nextEventTs）"""
    url = f"{settings.kline_api_base}/stock/search/session?symbol={symbol}"
    try:
        data = fetch_json_cached(url)
        if data.get("code") == 0 and data.get("data"):
            return data["data"]
        return {}
    except Exception:
        return {}


def get_us_return_investment(symbol: str) -> Dict[str, Any]:
    """美股区间涨跌（1日/7日/1月/1年）"""
    url = f"{settings.kline_api_base}/stock/detail/getReturnInvestment?symbol={symbol}"
    try:
        data = fetch_json_cached(url)
        if data.get("code") == 0 and data.get("data"):
            return data["data"]
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
        logger.error(f"获取成交量数据异常: {str(e)}")
        return {"volume": 0, "volume_change": 0}


# 别名，保持向后兼容
get_recent_news = get_news_from_mysql
