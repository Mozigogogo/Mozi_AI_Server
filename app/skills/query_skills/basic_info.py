"""基本信息查询 Skill"""
import asyncio
from app.skills.base import BaseSkill, IntentInfo, SkillResult
from app.services.data_service import get_header_data


class BasicInfoSkill(BaseSkill):
    """基本信息查询 Skill - 获取价格、市值、排名等"""

    name = "basic_info"
    description = "查询价格、市值、排名等基本信息"

    def match(self, intent: IntentInfo, mode: str = "chat") -> bool:
        """匹配意图"""
        return (
            intent.intent_type == "query_price" and
            "get_header_data" in intent.required_apis
        )

    def get_required_apis(self) -> list:
        """只需要调用 header_data API"""
        return ["get_header_data"]

    async def execute_async(
        self,
        symbol: str,
        intent: IntentInfo
    ) -> SkillResult:
        """执行查询（只调用必要的 API）"""
        # 只调用 get_header_data
        header_data = await asyncio.to_thread(get_header_data, symbol)

        return SkillResult(
            skill_name=self.name,
            data=header_data,
            timestamp=self._get_timestamp(),
            api_calls=["get_header_data"]
        )
