from typing import Dict, Any, List, Optional
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from app.agents.tools.base import CryptoAnalystTool, SymbolAndLimitInput
from app.services.data_service import get_news_from_mysql
from app.utils.formatters import format_news_data
from app.utils.validators import validate_symbol, validate_limit


class NewsDataInput(SymbolAndLimitInput):
    """新闻数据输入模型"""
    pass


class NewsDataTool(CryptoAnalystTool):
    """新闻数据工具 - 获取加密货币相关新闻"""

    name: str = "get_news_data"
    description: str = "获取加密货币相关的新闻数据。当用户询问新闻、市场情绪、事件影响时使用此工具。"
    args_schema: type = NewsDataInput

    def execute(self, symbol: str, limit: Optional[int] = None) -> str:
        """执行新闻数据获取"""
        symbol = validate_symbol(symbol)
        limit = validate_limit(limit)

        news = get_news_from_mysql(symbol, limit)
        formatted_news = format_news_data(news)

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{symbol}相关新闻（共{len(news)}条）：\n\n{formatted_news}\n\n已获取{symbol}相关的{len(news)}条新闻数据。"


class RecentNewsTool(CryptoAnalystTool):
    """近期新闻工具 - 获取加密货币的近期新闻"""

    name: str = "get_recent_news"
    description: str = "获取加密货币的近期新闻（默认最近20条）。当用户询问最新消息、近期动态时使用此工具。"
    args_schema: type = NewsDataInput

    def execute(self, symbol: str, limit: Optional[int] = 20) -> str:
        """执行近期新闻获取"""
        symbol = validate_symbol(symbol)
        limit = validate_limit(limit, max_limit=50)

        news = get_news_from_mysql(symbol, limit)
        formatted_news = format_news_data(news)

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{symbol}近期新闻（最近{limit}条，实际{len(news)}条）：\n\n{formatted_news}\n\n已获取{symbol}的{len(news)}条近期新闻。"


class NewsCountTool(CryptoAnalystTool):
    """新闻数量工具 - 统计加密货币相关新闻数量"""

    name: str = "get_news_count"
    description: str = "统计加密货币相关新闻的数量。当用户询问新闻热度、关注度时使用此工具。"
    args_schema: type = NewsDataInput

    def execute(self, symbol: str, limit: Optional[int] = None) -> str:
        """执行新闻数量统计"""
        symbol = validate_symbol(symbol)
        limit = validate_limit(limit)

        news = get_news_from_mysql(symbol, limit)
        time_period = f"最近{limit}条记录" if limit else "所有记录"

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{symbol}相关的新闻数量统计：\n\n时间段：{time_period}\n新闻数量：{len(news)}条\n\n{symbol}相关的新闻数量为{len(news)}条。"