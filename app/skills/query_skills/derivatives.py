"""衍生品查询 Skill"""
import asyncio
from app.skills.base import BaseSkill, IntentInfo, SkillResult
from app.services.data_service import (
    get_buy_sell_ratio,
    get_open_interest,
    get_trading_volume,
    get_funding_rate
)


class DerivativesQuerySkill(BaseSkill):
    """衍生品查询 Skill - 查询持仓、资金费率等"""

    name = "derivatives_query"
    description = "查询持仓、资金费率、买卖比等衍生品数据"

    def match(self, intent: IntentInfo, mode: str = "chat") -> bool:
        """匹配意图"""
        return intent.intent_type == "query_derivatives"

    def get_required_apis(self) -> list:
        """根据需要的 API 调用"""
        return [
            "get_buy_sell_ratio",
            "get_open_interest",
            "get_trading_volume",
            "get_funding_rate"
        ]

    async def execute_async(
        self,
        symbol: str,
        intent: IntentInfo
    ) -> SkillResult:
        """执行查询（根据 intent.required_apis 调用必要的 API）"""
        api_calls = []
        data = {}

        # 创建所有需要调用的任务
        tasks = []

        # 根据需要的 API 调用对应函数（并发执行）
        if "get_buy_sell_ratio" in intent.required_apis:
            tasks.append(asyncio.to_thread(get_buy_sell_ratio, symbol))
            api_calls.append("get_buy_sell_ratio")

        if "get_open_interest" in intent.required_apis:
            tasks.append(asyncio.to_thread(get_open_interest, symbol))
            api_calls.append("get_open_interest")

        if "get_trading_volume" in intent.required_apis:
            tasks.append(asyncio.to_thread(get_trading_volume, symbol))
            api_calls.append("get_trading_volume")

        if "get_funding_rate" in intent.required_apis:
            tasks.append(asyncio.to_thread(get_funding_rate, symbol))
            api_calls.append("get_funding_rate")

        # 并发执行所有任务
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 提取结果
            if "get_buy_sell_ratio" in intent.required_apis:
                data["buy_sell_ratio"] = results[api_calls.index("get_buy_sell_ratio")]

            if "get_open_interest" in intent.required_apis:
                data["open_interest"] = results[api_calls.index("get_open_interest")]

            if "get_trading_volume" in intent.required_apis:
                data["trading_volume"] = results[api_calls.index("get_trading_volume")]

            if "get_funding_rate" in intent.required_apis:
                data["funding_rate"] = results[api_calls.index("get_funding_rate")]

        return SkillResult(
            skill_name=self.name,
            data=data,
            timestamp=self._get_timestamp(),
            api_calls=api_calls
        )
