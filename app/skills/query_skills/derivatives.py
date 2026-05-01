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
        return intent.intent_type == "query_derivatives"

    def get_required_apis(self) -> list:
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
        tasks = []

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

        raw_data = {}
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, api_name in enumerate(api_calls):
                result = results[i]
                if isinstance(result, Exception):
                    print(f"  警告: {api_name} 调用失败: {str(result)}")
                else:
                    raw_data[api_name] = result

        # 精简数据后传给LLM
        llm_data = self._summarize_data(symbol, raw_data)

        return SkillResult(
            skill_name=self.name,
            data=llm_data,
            timestamp=self._get_timestamp(),
            api_calls=api_calls
        )

    def _summarize_data(self, symbol: str, raw: dict) -> dict:
        """将原始API数据精简为LLM友好的格式"""
        result = {"币种": symbol}

        # 多空比：每个交易所只保留最新值
        if "get_buy_sell_ratio" in raw:
            ratio_raw = raw["get_buy_sell_ratio"]
            ratio_summary = {}
            if isinstance(ratio_raw, dict):
                for exchange, exchange_data in ratio_raw.items():
                    if isinstance(exchange_data, dict):
                        ls = exchange_data.get("longShortData", [])
                        ratio_summary[exchange] = {
                            "多空比": ls[-1] if ls else "N/A",
                            "多头占比": exchange_data.get("longData", [None])[-1],
                            "空头占比": exchange_data.get("shortData", [None])[-1],
                        }
            result["多空比"] = ratio_summary

        # 持仓量/成交额：每个交易所只保留最近3天
        for key in ("get_open_interest", "get_trading_volume"):
            if key in raw:
                oi_raw = raw[key]
                label = "持仓量" if "interest" in key else "成交额"
                if isinstance(oi_raw, dict) and "data" in oi_raw:
                    metric = oi_raw.get("metric", label)
                    unit = oi_raw.get("unit", "")
                    if "(bar)" in unit:
                        unit = unit.replace("(bar)", "").strip()
                    summary = {}
                    for exchange, values in oi_raw["data"].items():
                        if isinstance(values, list):
                            # 只保留最近3天非null值
                            recent = [v for v in values[-5:] if v is not None]
                            summary[exchange] = recent[-1] if recent else None
                    result[label] = {"指标": metric, "单位": unit, "各交易所最新": summary}

        # 资金费率：直接传（数据量小）
        if "get_funding_rate" in raw:
            result["资金费率"] = raw["get_funding_rate"]

        return result
