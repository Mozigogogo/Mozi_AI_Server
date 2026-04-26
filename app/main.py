import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi

from app.core.config import get_settings
from app.core.exceptions import CryptoAnalystException
from app.api.endpoints import router as api_router
from app.api.skill_endpoints import router as skill_test_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    print(f"启动 {settings.app_name} v{settings.app_version}")
    print(f"API地址: http://{settings.api_host}:{settings.api_port}")
    print(f"调试模式: {settings.debug}")

    yield

    # 关闭时
    print("服务关闭")


# 创建FastAPI应用
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="基于Skill架构的虚拟货币分析助手，提供智能化的加密货币市场分析服务",
    docs_url=None,  # 自定义文档
    redoc_url=None,
    lifespan=lifespan
)


# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制来源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 全局异常处理
@app.exception_handler(CryptoAnalystException)
async def crypto_analyst_exception_handler(request: Request, exc: CryptoAnalystException):
    """处理自定义异常"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "detail": str(exc) if settings.debug else None,
            "code": exc.status_code
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """处理通用异常"""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "内部服务器错误",
            "detail": str(exc) if settings.debug else None,
            "code": status.HTTP_500_INTERNAL_SERVER_ERROR
        }
    )


# 自定义OpenAPI文档
def custom_openapi():
    """自定义OpenAPI文档"""
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=settings.app_name,
        version=settings.app_version,
        description="""
        ## 基于Skill架构的虚拟货币分析助手

        ### 功能特性
        - 🎯 **精准意图识别**: 基于LLM理解用户问题，精准匹配需要的数据
        - 💬 **语言跟随**: 自动检测用户语言，用相同语言回答
        - 📊 **多维度分析**: 市场数据、新闻、衍生品、技术分析等
        - 🔄 **流式响应**: 支持SSE流式输出，实时展示分析过程
        - 🚀 **双模式**: 对话模式(简洁) vs 思考模式(深度)

        ### 快速开始
        1. 使用 `/chat` 端点进行简洁对话
        2. 使用 `/analyze` 端点进行深度分析
        3. 使用 `/test/skill` 端点测试 Skill 系统

        ### 注意事项
        - 所有分析仅供参考，不构成投资建议
        - 加密货币市场具有高风险性
        - 请谨慎决策，自负风险
        """,
        routes=app.routes,
    )

    # 添加服务器信息
    openapi_schema["servers"] = [
        {
            "url": f"http://{settings.api_host}:{settings.api_port}",
            "description": "本地开发服务器"
        },
        {
            "url": "https://your-production-domain.com",
            "description": "生产服务器"
        }
    ]

    # 添加标签
    openapi_schema["tags"] = [
        {
            "name": "分析",
            "description": "加密货币分析相关接口"
        },
        {
            "name": "对话",
            "description": "对话式交互接口"
        },
        {
            "name": "Skill System Test",
            "description": "Skill 系统测试接口"
        },
        {
            "name": "系统",
            "description": "系统状态和健康检查"
        }
    ]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


# 自定义文档页面
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    """自定义Swagger UI"""
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{settings.app_name} - Swagger UI",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
        swagger_favicon_url="https://fastapi.tiangolo.com/img/favicon.png",
    )


@app.get("/redoc", include_in_schema=False)
async def redoc_html():
    """自定义ReDoc"""
    return get_redoc_html(
        openapi_url=app.openapi_url,
        title=f"{settings.app_name} - ReDoc",
        redoc_js_url="https://cdn.jsdelivr.net/npm/redoc@next/bundles/redoc.standalone.js",
        redoc_favicon_url="https://fastapi.tiangolo.com/img/favicon.png",
    )


# 根路径
@app.get("/")
async def root():
    """根路径"""
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "docs": "/docs",
        "redoc": "/redoc",
        "health": "/api/v1/health"
    }


# 注册API路由
app.include_router(
    api_router,
    prefix=f"{settings.api_prefix}",
    tags=["API"]
)

# 注册 Skill 测试路由
app.include_router(
    skill_test_router,
    tags=["Skill System Test"]
)


# 中间件：添加请求处理时间
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """添加请求处理时间头"""
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
        log_level="info" if settings.debug else "warning"
    )