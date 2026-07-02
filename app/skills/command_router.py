"""指令路由 - 根据用户问题返回对应指令（/price /ai /chat /bigorder /predict /alert）"""
import json
import re

from app.core.config import get_settings
from app.core.llm_client import get_llm_client
from app.utils.logger import get_logger

logger = get_logger("app.skills.command_router")
settings = get_settings()

VALID_COMMANDS = {"/price", "/ai", "/chat", "/bigorder", "/predict", "/alert"}

FALLBACK_TEXT = (
    "我是加密货币分析助手，可以帮你：\n"
    "💰 /price — 查询价格、涨跌幅\n"
    "🤖 /ai — 深度分析（趋势/技术面/信号）\n"
    "💬 /chat — 闲聊\n"
    "📊 /bigorder — 大单侦测、主力资金\n"
    "🎯 /predict — 预测涨跌玩法\n"
    "🔔 /alert — 添加监控报警\n\n"
    "试试问我：\"BTC现在多少钱\" 或 \"分析一下ETH\""
)

PROMPT_TEMPLATE = """分析用户问题，返回对应指令。禁止输出 JSON 以外的内容。

用户问题：{question}

可用指令：
- /price — 纯价格/市值/涨跌幅查询（"多少钱"、"涨了多少"、"价格"、"最新价"）
- /ai — 深度分析（300+ 字多维分析）。**只有用户明确要求深度分析时才用**：含"分析/详细/深度/走势会怎样/后市如何/技术面/量化/综合分析"等关键词
- /chat — 简短问答（150 字内）。**默认选项**：币种相关的简短问题（"能买吗/怎么样/是什么/介绍一下"）+ 普通闲聊/问候
- /bigorder — 大单/主力/异动/资金流向（"大单"、"主力"、"异动"）
- /predict — 预测下注玩法（"猜"、"赌"、"预测"、"下注"、"看涨看跌"）
- /alert — 监控报警（"监控"、"报警"、"提醒"、"通知"、"跌破/涨破X提醒我"）

判定优先级（从上到下，匹配即停）：
1. 含"监控/报警/提醒/通知"等 → /alert
2. 含"猜/赌/预测/下注/看涨看跌" → /predict
3. 含"大单/主力/异动/资金流向" → /bigorder
4. 含"多少钱/价格/最新价/涨跌幅/市值"（纯数据查询，不要求分析） → /price
5. **明确要求深度分析**（含"分析/详细/深度/走势会怎样/后市如何/技术面/量化/综合分析"） → /ai
6. **默认**（币种简短问题"能买吗/怎么样/是什么" + 闲聊/问候） → /chat

示例：
- "btc可以买进吗" → /chat（简短建议，不需深度分析）
- "btc怎么样" → /chat（简短评价）
- "btc是什么" → /chat（简短介绍）
- "分析一下btc" → /ai（明确"分析"）
- "btc走势会怎样" → /ai（明确"走势会怎样"）
- "btc后市如何" → /ai（明确"后市如何"）
- "btc技术面" → /ai（明确"技术面"）
- "btc量化分析" → /ai（明确"量化分析"）

输出格式：
{{"command":"/price","coin_symbol":"BTC","language":"zh","confidence":0.95,"reason":"用户询问BTC当前价格"}}

只输出 JSON："""


class CommandRouter:
    """6 分类指令路由器"""

    def __init__(self):
        self.client = get_llm_client()

    async def classify(self, question: str) -> dict:
        """识别意图。返回 dict（直接喂给 RouteResponse）。

        成功：{command, coin_symbol, confidence, reason, language, fallback_text=None}
        失败：{command=None, ..., fallback_text=FALLBACK_TEXT}
        """
        try:
            response = await self.client.chat.completions.create(
                model=settings.deepseek_model,
                max_tokens=200,
                timeout=10.0,
                messages=[{"role": "user", "content": PROMPT_TEMPLATE.format(question=question)}],
            )
            content = response.choices[0].message.content.strip()
            data = self._parse_json(content)

            command = (data.get("command") or "").strip()
            if command not in VALID_COMMANDS:
                logger.warning(f"LLM 返回未知指令: {command}, raw={content[:200]}")
                return self._fallback(f"未知指令: {command}")

            coin = data.get("coin_symbol")
            if coin:
                coin = str(coin).strip().upper()

            return {
                "command": command,
                "coin_symbol": coin,
                "confidence": float(data.get("confidence", 0.0)),
                "reason": str(data.get("reason", ""))[:200],
                "language": data.get("language", "zh"),
                "fallback_text": None,
            }
        except Exception as e:
            logger.exception(f"指令路由失败: {e}")
            return self._fallback(f"{type(e).__name__}: {e}")

    @staticmethod
    def _parse_json(content: str) -> dict:
        """兼容 ```json``` 包裹 + 截断补全（参考 intent_analyzer._parse_json_response）"""
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        m = re.search(r'\{[\s\S]*\}', content)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        for suffix in ['}', '"}']:
            try:
                return json.loads(content + suffix)
            except json.JSONDecodeError:
                continue
        return {}

    @staticmethod
    def _fallback(reason: str) -> dict:
        return {
            "command": None,
            "coin_symbol": None,
            "confidence": 0.0,
            "reason": reason,
            "language": "zh",
            "fallback_text": FALLBACK_TEXT,
        }


command_router = CommandRouter()
