"""Skill 基类 - 定义所有 Skills 的统一接口"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from datetime import datetime
from pydantic import BaseModel


class IntentInfo(BaseModel):
    """意图信息 - LLM 分析结果"""
    language: str = "zh"  # zh 或 en
    intent_type: str  # 意图类型
    coin_symbol: Optional[str] = None  # 币种符号
    required_apis: List[str] = []  # 需要调用的 API 列表
    answer_requirements: List[str] = []  # 回答需要包含的内容
    raw_question: str = ""  # 原始问题
    confidence: float = 0.0  # 置信度


class SkillResult(BaseModel):
    """Skill 执行结果"""
    skill_name: str
    data: Dict[str, Any]
    timestamp: str
    api_calls: List[str] = []  # 实际调用的 API 列表


class BaseSkill(ABC):
    """Skill 基类"""

    name: str = ""
    description: str = ""

    @abstractmethod
    def match(self, intent: IntentInfo, mode: str = "chat") -> bool:
        """
        匹配意图，判断当前 Skill 是否适合处理该意图

        Args:
            intent: 意图信息
            mode: 模式（chat/think）

        Returns:
            bool: 是否匹配
        """
        pass

    @abstractmethod
    def get_required_apis(self) -> List[str]:
        """
        获取此 Skill 需要调用的 API 列表

        Returns:
            List[str]: API 名称列表
        """
        pass

    @abstractmethod
    async def execute_async(
        self,
        symbol: str,
        intent: IntentInfo
    ) -> SkillResult:
        """
        异步执行 Skill

        Args:
            symbol: 币种符号
            intent: 意图信息

        Returns:
            SkillResult: 执行结果
        """
        pass

    def _get_timestamp(self) -> str:
        """获取当前时间戳"""
        return datetime.now().isoformat()
