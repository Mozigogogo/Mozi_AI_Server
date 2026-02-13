from typing import Any, Dict
from fastapi import HTTPException, status


class CryptoAnalystException(HTTPException):
    """基础异常类"""

    def __init__(
        self,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail: str = "Internal server error",
        headers: Dict[str, Any] = None,
    ):
        super().__init__(status_code=status_code, detail=detail, headers=headers)


class DataFetchException(CryptoAnalystException):
    """数据获取异常"""

    def __init__(self, detail: str = "Failed to fetch data"):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )


class DatabaseException(CryptoAnalystException):
    """数据库异常"""

    def __init__(self, detail: str = "Database error"):
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail,
        )


class LLMException(CryptoAnalystException):
    """LLM服务异常"""

    def __init__(self, detail: str = "LLM service error"):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )


class ValidationException(CryptoAnalystException):
    """验证异常"""

    def __init__(self, detail: str = "Validation error"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )


class SymbolNotFoundException(CryptoAnalystException):
    """币种未找到异常"""

    def __init__(self, symbol: str):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Symbol '{symbol}' not found or not supported",
        )