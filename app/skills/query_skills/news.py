"""新闻查询 Skill"""
import asyncio
from app.skills.base import BaseSkill, IntentInfo, SkillResult
from app.services.data_service import get_recent_news


class NewsQuerySkill(BaseSkill):
    """新闻查询 Skill - 获取最新新闻"""

    name = "news_query"
    description = "查询新闻、热点事件"

    def match(self, intent: IntentInfo, mode: str = "chat") -> bool:
        """匹配意图"""
        return (
            intent.intent_type == "query_news" and
            "get_recent_news" in intent.required_apis
        )

    def get_required_apis(self) -> list:
        """只需要调用 news API"""
        return ["get_recent_news"]

    async def execute_async(
        self,
        symbol: str,
        intent: IntentInfo
    ) -> SkillResult:
        """执行查询（只调用必要的 API）"""
        # 调用 get_recent_news
        news_data = await asyncio.to_thread(get_recent_news, symbol, limit=5)

        return SkillResult(
            skill_name=self.name,
            data={
                "news": news_data,
                "count": len(news_data) if isinstance(news_data, list) else 0
            },
            timestamp=self._get_timestamp(),
            api_calls=["get_recent_news"]
        )
