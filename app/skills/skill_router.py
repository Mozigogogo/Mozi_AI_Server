"""Skill 路由器 - 根据 required_apis 精准匹配 Skill"""
from typing import Dict

from app.skills.base import BaseSkill, IntentInfo
from app.skills.query_skills import (
    BasicInfoSkill,
    MarketTrendSkill,
    NewsQuerySkill,
    DerivativesQuerySkill
)
from app.skills.analysis_skills import (
    TechnicalAnalysisSkill,
    SentimentAnalysisSkill,
    ComprehensiveAnalysisSkill,
    QuantitativeAnalysisSkill
)


class SkillRouter:
    """Skill 路由器 - 根据意图精准匹配 Skill"""

    def __init__(self):
        self.skills: Dict[str, BaseSkill] = {}
        self._register_skills()

    def _register_skills(self):
        """注册所有 Skills"""
        # 查询类 Skills
        self.skills["basic_info"] = BasicInfoSkill()
        self.skills["market_trend"] = MarketTrendSkill()
        self.skills["news_query"] = NewsQuerySkill()
        self.skills["derivatives_query"] = DerivativesQuerySkill()

        # 分析类 Skills
        self.skills["technical_analysis"] = TechnicalAnalysisSkill()
        self.skills["sentiment_analysis"] = SentimentAnalysisSkill()
        self.skills["comprehensive_analysis"] = ComprehensiveAnalysisSkill()
        self.skills["quantitative_analysis"] = QuantitativeAnalysisSkill()

    def route(
        self,
        intent: IntentInfo,
        mode: str = "chat"
    ) -> BaseSkill:
        """
        根据意图路由到合适的 Skill

        Args:
            intent: 意图信息
            mode: 模式（chat/think）

        Returns:
            BaseSkill: 匹配的 Skill
        """
        # 检查是否是简单对话
        if intent.intent_type == "simple_chat":
            return GeneralChatSkill()

        # 遍历所有 Skills，找到匹配的
        for skill_name, skill in self.skills.items():
            if skill.match(intent, mode):
                print(f"匹配到 Skill: {skill_name}")
                return skill

        # 如果没有匹配到，返回通用查询 Skill
        print("未找到匹配的 Skill，使用通用查询 Skill")
        return BasicInfoSkill()


class GeneralChatSkill(BaseSkill):
    """通用对话 Skill - 处理简单对话"""

    name = "general_chat"
    description = "处理简单的问候和对话"

    def match(self, intent: IntentInfo, mode: str = "chat") -> bool:
        """只匹配简单对话意图"""
        return intent.intent_type == "simple_chat"

    def get_required_apis(self) -> list:
        """不需要调用任何 API"""
        return []

    async def execute_async(
        self,
        symbol: str,
        intent: IntentInfo
    ):
        """不需要执行任何操作"""
        from app.skills.base import SkillResult
        return SkillResult(
            skill_name=self.name,
            data={},
            timestamp=self._get_timestamp(),
            api_calls=[]
        )
