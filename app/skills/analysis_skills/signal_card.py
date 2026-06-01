"""信号卡 Skill — 对话中生成交易信号卡"""
from app.skills.base import BaseSkill, IntentInfo, SkillResult


class SignalCardSkill(BaseSkill):
    """信号卡 Skill — 用户问交易建议时返回结构化信号卡"""

    name = "signal_card"
    description = "交易信号卡（进场/止损/止盈/仓位建议）"

    def match(self, intent: IntentInfo, mode: str = "chat") -> bool:
        return intent.intent_type == "analyze_signal"

    def get_required_apis(self) -> list:
        return ["get_kline_data"]

    async def execute_async(
        self,
        symbol: str,
        intent: IntentInfo,
    ) -> SkillResult:
        # 延迟导入避免循环依赖
        from app.signals.fusion import generate_card_for_chat

        tier = getattr(intent, "tier", "lite")
        card_event = generate_card_for_chat(symbol, tier)

        if card_event:
            data = {"signal_card_event": card_event}
        else:
            data = {
                "signal_card_event": None,
                "no_signal": True,
                "coin": symbol,
            }

        return SkillResult(
            skill_name=self.name,
            data=data,
            timestamp=self._get_timestamp(),
            api_calls=["get_kline_data"],
        )
