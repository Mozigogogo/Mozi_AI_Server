from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from app.core.config import get_settings
from app.core.exceptions import CryptoAnalystException

settings = get_settings()


class CryptoAnalystTool(BaseTool, ABC):
    """加密货币分析工具基类"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def _run(self, *args, **kwargs) -> Any:
        """工具执行方法"""
        try:
            # 处理LangChain 1.x的参数格式
            # LangChain可能传递 {"__arg1": "value"} 或 {"input": "value"}
            # 或者传递多个参数如 {"symbol": "BTC", "question": "...", "lang": "zh"}
            # 我们需要将其转换为工具期望的参数格式

            # 如果没有位置参数但有kwargs，检查是否是LangChain格式
            if not args and kwargs:
                # 检查是否是LangChain 1.x的调用格式
                if "__arg1" in kwargs:
                    # LangChain格式: {"__arg1": "value"}
                    # 将__arg1作为第一个位置参数传递
                    return self.execute(kwargs["__arg1"])
                elif "input" in kwargs:
                    # 其他可能的格式: {"input": "value"}
                    return self.execute(kwargs["input"])
                elif len(kwargs) == 1:
                    # 如果只有一个关键字参数，使用其值
                    value = next(iter(kwargs.values()))
                    return self.execute(value)
                else:
                    # 多个关键字参数，可能是工具的多参数调用
                    # 直接将这些参数传递给execute方法
                    return self.execute(**kwargs)

            # 否则使用原始参数
            return self.execute(*args, **kwargs)
        except CryptoAnalystException as e:
            raise e
        except Exception as e:
            raise CryptoAnalystException(f"工具执行失败: {str(e)}")

    @abstractmethod
    def execute(self, *args, **kwargs) -> Any:
        """工具具体执行逻辑"""
        pass

    def _arun(self, *args, **kwargs) -> Any:
        """异步执行方法（暂不支持）"""
        raise NotImplementedError("此工具不支持异步执行")


class SymbolInput(BaseModel):
    """币种输入模型"""
    symbol: str = Field(description="加密货币符号，如BTC、ETH")


class SymbolAndLimitInput(SymbolInput):
    """币种和限制输入模型"""
    limit: Optional[int] = Field(
        default=None,
        description="限制返回数量，默认100"
    )


class SymbolAndQuestionInput(SymbolInput):
    """币种和问题输入模型"""
    question: str = Field(description="用户提出的问题")


class SymbolAndLangInput(SymbolInput):
    """币种和语言输入模型"""
    lang: str = Field(
        default="zh",
        description="语言，zh（中文）或en（英文），默认zh"
    )