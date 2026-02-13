import re
from typing import List, Optional
from app.core.exceptions import ValidationException


def validate_symbol(symbol: str) -> str:
    """验证币种符号"""
    if not symbol:
        raise ValidationException("币种符号不能为空")

    symbol = symbol.strip().upper()
    if not re.match(r'^[A-Z0-9]{1,10}$', symbol):
        raise ValidationException(f"无效的币种符号: {symbol}")

    # 常见币种验证（可选）
    common_symbols = ["BTC", "ETH", "BNB", "XRP", "SOL", "ADA", "AVAX", "DOT", "DOGE", "MATIC"]
    if symbol not in common_symbols:
        # 可以记录日志，但不抛出异常
        pass

    return symbol


def validate_language(lang: str) -> str:
    """验证语言设置"""
    if not lang:
        return "zh"

    lang = lang.strip().lower()
    if lang not in ["zh", "en"]:
        raise ValidationException(f"不支持的语言: {lang}，支持的语言: zh, en")

    return lang


def validate_limit(limit: Optional[int], max_limit: int = 100) -> int:
    """验证数量限制"""
    if limit is None:
        return max_limit

    if not isinstance(limit, int):
        raise ValidationException("limit必须是整数")

    if limit <= 0:
        raise ValidationException("limit必须大于0")

    if limit > max_limit:
        raise ValidationException(f"limit不能超过{max_limit}")

    return limit


def validate_question(question: str) -> str:
    """验证问题内容"""
    if not question:
        raise ValidationException("问题不能为空")

    question = question.strip()
    if len(question) < 2:
        raise ValidationException("问题太短")

    if len(question) > 1000:
        raise ValidationException("问题太长，最多1000个字符")

    # 检查是否包含恶意内容（简单检查）
    malicious_patterns = [
        r"<script.*?>",
        r"javascript:",
        r"onload=",
        r"onerror=",
        r"eval\(",
    ]

    for pattern in malicious_patterns:
        if re.search(pattern, question, re.IGNORECASE):
            raise ValidationException("问题包含不安全内容")

    return question


def validate_conversation_id(conversation_id: Optional[str]) -> Optional[str]:
    """验证会话ID"""
    if conversation_id is None:
        return None

    conversation_id = conversation_id.strip()
    if not conversation_id:
        return None

    if len(conversation_id) > 100:
        raise ValidationException("会话ID太长，最多100个字符")

    # 验证会话ID格式
    if not re.match(r'^[a-zA-Z0-9_-]+$', conversation_id):
        raise ValidationException("会话ID只能包含字母、数字、下划线和连字符")

    return conversation_id