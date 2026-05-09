# Crypto Analyst Assistant - 项目架构文档

> **文档版本**: 1.0.0
> **更新日期**: 2026-03-15
> **项目状态**: 生产就绪

---

## 目录

- [1. 项目概述](#1-项目概述)
- [2. 技术栈概览](#2-技术栈概览)
- [3. 目录结构说明](#3-目录结构说明)
- [4. 架构设计](#4-架构设计)
- [5. 核心开发规范](#5-核心开发规范)
- [6. 数据请求约定](#6-数据请求约定)
- [7. 会话管理规范](#7-会话管理规范)
- [8. 状态管理规范](#8-状态管理规范)
- [8. 会话管理规范](#8-会话管理规范)
- [9. API接口规范](#9-api接口规范)
- [10. 工具开发规范](#10-工具开发规范)
- [11. 已知问题与改进建议](#11-已知问题与改进建议)
- [12. 快速上手](#12-快速上手)

---

## 1. 项目概述

**项目名称**: Crypto Analyst Assistant（加密货币分析助手）

**项目定位**: 基于LangChain的智能加密货币市场分析服务

**核心功能**:
- 多维度加密货币分析（市场数据、新闻、衍生品、技术面、量化）
- 智能工具路由（根据用户问题自动选择合适工具）
- 流式/非流式响应
- RESTful API接口
- 中英双语支持

**设计原则**:
- 模块化、可扩展、易维护
- 分层架构，职责清晰
- 合规优先（不提供投资建议，强调风险）

---

## 2. 技术栈概览

### 2.1 核心框架

| 技术 | 版本 | 用途 |
|------|------|------|
| **LangChain** | >= 1.2.0 | AI智能体框架，负责工具编排和LLM调用 |
| **FastAPI** | >= 0.104.0 | Web框架，提供RESTful API |
| **Uvicorn** | >= 0.24.0 | ASGI服务器 |
| **Pydantic** | >= 2.0.0 | 数据验证、配置管理 |
| **pydantic-settings** | >= 2.0.0 | 环境变量管理 |

### 2.2 AI/LLM相关

| 技术 | 用途 |
|------|------|
| **langchain-openai** | LangChain的OpenAI适配器 |
| **OpenAI SDK** | 实际调用DeepSeek API（OpenAI兼容接口） |
| **DeepSeek** | 底层大语言模型提供商 |

### 2.3 数据访问

| 技术 | 用途 |
|------|------|
| **Requests** | HTTP客户端，获取外部API数据 |
| **PyMySQL** | MySQL数据库连接，查询新闻数据 |

### 2.4 其他依赖

| 技术 | 用途 |
|------|------|
| **sse-starlette** | Server-Sent Events支持，流式响应 |
| **python-dotenv** | 环境变量加载 |
| **python-multipart** | 表单数据处理 |

### 2.5 部署工具

| 技术 | 用途 |
|------|------|
| **Docker** | 容器化部署 |
| **Docker Compose** | 多服务编排 |

---

## 3. 目录结构说明

```
crypto-analyst-assistant/
├── app/                           # 主应用目录
│   ├── main.py                    # FastAPI应用入口
│   ├── api/                       # API层
│   │   ├── endpoints.py           # API路由定义
│   │   ├── schemas.py             # Pydantic数据模型
│   │   └── __init__.py
│   ├── core/                      # 核心配置层
│   │   ├── config.py              # 配置管理（LRU缓存单例）
│   │   ├── exceptions.py          # 自定义异常类
│   │   └── __init__.py
│   ├── agents/                    # LangChain智能体层
│   │   ├── crypto_agent.py        # 主智能体类（全局单例）
│   │   └── tools/               # 工具模块
│   │       ├── base.py           # 工具基类和输入模型
│   │       ├── market_data.py     # 市场数据工具（3个）
│   │       ├── news_data.py        # 新闻数据工具（3个）
│   │       ├── derivatives_data.py # 衍生品数据工具（5个）
│   │       ├── analysis_tools.py  # 分析工具（5个）
│   │       ├── prompt_tools.py    # 提示词工具（2个）
│   │       └── __init__.py
│   ├── services/                  # 业务服务层
│   │   ├── llm_service.py        # LLM服务封装（全局单例）
│   │   ├── data_service.py       # 数据获取服务
│   │   └── __init__.py
│   ├── utils/                     # 工具函数层
│   │   ├── validators.py         # 输入验证
│   │   ├── formatters.py         # 数据格式化
│   │   └── __init__.py
│   └── __init__.py
├── config/                        # 配置目录
│   ├── settings.py               # Pydantic Settings类定义
│   └── __init__.py
├── examples/                      # 使用示例
│   └── api_examples.py           # API调用示例
├── data/                          # 数据存储目录（预留）
├── logs/                          # 日志目录（预留）
├── requirements.txt               # Python依赖列表
├── Dockerfile                     # Docker镜像构建文件
├── docker-compose.yml             # Docker Compose编排文件
├── start.sh / start.bat           # 启动脚本
├── .env.example                   # 环境变量模板
├── README.md                     # 项目说明文档
└── PROJECT_ARCHITECTURE.md       # 本文档
```

### 3.1 目录职责划分

| 目录 | 职责 | 关键文件 |
|------|------|----------|
| `app/main.py` | 应用入口，中间件配置，路由注册 | main.py |
| `app/api/` | HTTP接口定义，请求/响应模型 | endpoints.py, schemas.py |
| `app/core/` | 配置管理，异常定义 | config.py, exceptions.py |
| `app/agents/` | LangChain智能体和工具 | crypto_agent.py, tools/ |
| `app/services/` | 数据服务，LLM服务 | llm_service.py, data_service.py |
| `app/utils/` | 验证器，格式化工具 | validators.py, formatters.py |
| `config/` | 配置类定义 | settings.py |

---

## 4. 架构设计

### 4.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                       客户端层                               │
│  (Web/Mobile/第三方应用调用)                                 │
└─────────────────────────────────────────────────────────────────┘
                              │ HTTP/HTTPS
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       API层 (FastAPI)                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  /analyze  /chat  /tools  /health                    │  │
│  │  非流式/流式响应 + 输入验证                           │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    智能体层 (LangChain)                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  CryptoAnalystAgent                                    │  │
│  │  - 智能工具路由                                        │  │
│  │  - 对话记忆管理                                        │  │
│  │  - 17个专用工具                                       │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      工具层 (17 Tools)                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  市场数据: market_data工具(3)                        │  │
│  │  新闻数据: news_data工具(3)                           │  │
│  │  衍生品: derivatives_data工具(5)                       │  │
│  │  分析: analysis_tools工具(5)                           │  │
│  │  提示词: prompt_tools工具(2)                           │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     服务层 (Services)                       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  LLMService: DeepSeek API调用                          │  │
│  │  DataService: 数据获取封装                              │  │
│  │    - fetch_json(): HTTP请求                             │  │
│  │    - pymysql(): MySQL查询                              │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      数据源层                                │
│  ┌───────────────────┐  ┌──────────────────┐               │
│  │  HTTP API        │  │  MySQL数据库      │               │
│  │  - K线数据       │  │  - 新闻数据      │               │
│  │  - 衍生品数据    │  │  - 市场数据      │               │
│  │  (moziinnovations) │  │  (exchange DB)   │               │
│  └───────────────────┘  └──────────────────┘               │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 分层架构原则

```
┌─────────────────────────────────────────────────────────────┐
│                    API层 (app/api/)                       │
│  职责：HTTP接口定义、输入验证、响应封装                     │
│  约定：不包含业务逻辑，只做参数验证和调用下层               │
└─────────────────────────────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                 智能体层 (app/agents/)                   │
│  职责：工具编排、LLM调用、对话记忆管理                    │
│  约定：智能体为全局单例，工具返回字符串给LLM               │
└─────────────────────────────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   工具层 (app/agents/tools/)              │
│  职责：具体业务逻辑执行、数据获取、格式化                  │
│  约定：所有工具继承CryptoAnalystTool，execute返回字符串      │
└─────────────────────────────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                 服务层 (app/services/)                     │
│  职责：数据获取、LLM调用等底层服务封装                    │
│  约定：服务为全局单例，提供统一接口                       │
└─────────────────────────────────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                 工具层 (app/utils/)                       │
│  职责：验证、格式化等通用工具函数                        │
│  约定：纯函数，无状态，可复用                           │
└─────────────────────────────────────────────────────────────┘
```

### 4.3 数据流图

```
用户请求
   │
   ▼
[API Endpoints]
   │ 输入验证
   ▼
[CryptoAnalystAgent]
   │ 调用智能体
   ▼
[LangChain Agent]
   │ 动态选择工具
   ▼
[Tool.execute()]
   │ 获取数据
   ▼
[DataService]
   │ fetch_json / pymysql
   ▼
[External API / MySQL]
   │ 返回原始数据
   ▼
[Formatters]
   │ 格式化为字符串
   ▼
[Tool返回字符串]
   │ LLM处理
   ▼
[CryptoAnalystAgent]
   │ 更新对话历史
   ▼
[API Response]
   │
   ▼
用户响应
```

---

## 5. 核心开发规范

### 5.1 代码风格约定

| 规范项 | 约定 |
|--------|------|
| **命名风格** | 类名PascalCase，函数/变量snake_case，常量UPPER_CASE |
| **类型注解** | 所有函数参数和返回值必须添加类型注解 |
| **文档字符串** | 公开函数必须添加docstring |
| **导入顺序** | 标准库 → 第三方库 → 本地模块 |
| **行长度** | 不超过100字符 |
| **缩进** | 4空格，不使用Tab |

### 5.2 导入规范

```python
# 1. 标准库
import json
import re
from typing import List, Dict, Any, Optional

# 2. 第三方库
import requests
import pymysql
from fastapi import APIRouter
from langchain.agents import create_agent
from pydantic import BaseModel

# 3. 本地模块
from app.core.config import get_settings
from app.core.exceptions import CryptoAnalystException
from app.agents.tools.base import CryptoAnalystTool
from app.services.data_service import get_kline_data
```

### 5.3 异常处理规范

**自定义异常继承体系：**

```python
# app/core/exceptions.py
class CryptoAnalystException(HTTPException):
    """基础异常类 - 所有自定义异常的父类"""
    pass

class DataFetchException(CryptoAnalystException):
    """数据获取异常 - HTTP API调用失败"""
    status_code = 503

class DatabaseException(CryptoAnalystException):
    """数据库异常 - MySQL操作失败"""
    status_code = 500

class LLMException(CryptoAnalystException):
    """LLM服务异常 - DeepSeek API调用失败"""
    status_code = 503

class ValidationException(CryptoAnalystException):
    """验证异常 - 输入数据不合法"""
    status_code = 400
```

**异常处理约定：**

```python
# ✅ 正确做法
try:
    data = fetch_json(url)
except DataFetchException as e:
    raise CryptoAnalystException(f"获取数据失败: {str(e)}")
except Exception as e:
    raise CryptoAnalystException(f"未知错误: {str(e)}")

# ❌ 错误做法
try:
    data = fetch_json(url)
except:
    pass  # 不要静默吞噬异常
```

### 5.4 输入验证规范

**验证器统一位置：** `app/utils/validators.py`

**验证函数命名：** `validate_<param_name>()`

```python
def validate_symbol(symbol: str) -> str:
    """验证币种符号"""
    if not symbol:
        raise ValidationException("币种符号不能为空")
    symbol = symbol.strip().upper()
    if not re.match(r'^[A-Z0-9]{1,10}$', symbol):
        raise ValidationException(f"无效的币种符号: {symbol}")
    return symbol
```

**在API端点中使用：**

```python
@router.post("/analyze")
async def analyze(request: AnalyzeRequest):
    symbol = validate_symbol(request.symbol)
    question = validate_question(request.question)
    lang = validate_language(request.lang.value)
    # ... 处理逻辑
```

### 5.5 数据模型规范

**Pydantic模型定义：** `app/api/schemas.py`

```python
class AnalyzeRequest(BaseModel):
    """分析请求模型"""
    symbol: str = Field(..., description="加密货币符号", min_length=1, max_length=10)
    question: str = Field(..., description="分析问题", min_length=2, max_length=1000)
    lang: Language = Field(default=Language.ZH, description="语言")
```

**Field约定：**
- 必填字段使用 `Field(...)`
- 可选字段使用 `Field(default=...)` 或 `Field(default=None)`
- 添加 `description` 描述字段用途
- 添加 `example` 提供示例值

---

## 6. 数据请求约定

### 6.1 数据服务层封装

**位置：** `app/services/data_service.py`

**HTTP请求封装函数：**

```python
def fetch_json(url: str, timeout: int = 30) -> Any:
    """通用JSON数据获取函数"""
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        raise DataFetchException(f"Failed to fetch data from {url}: {str(e)}")
```

**使用约定：**
- 所有HTTP请求必须通过 `fetch_json()` 封装
- 超时默认30秒，可根据需要调整
- 异常统一转换为 `DataFetchException`

### 6.2 数据获取函数列表

| 函数 | 数据源 | 参数 | 返回值 |
|------|--------|------|--------|
| `get_kline_data(symbol)` | HTTP API | symbol | Dict[str, Any] |
| `get_header_data(symbol)` | HTTP API | symbol | Dict[str, Any] |
| `get_news_from_mysql(symbol, limit)` | MySQL | symbol, limit | List[str] |
| `get_but_sell_ratio(symbol)` | HTTP API | symbol | Dict[str, Any] |
| `get_open_interest(symbol)` | HTTP API | symbol | Dict[str, Any] |
| `get_trading_volume(symbol)` | HTTP API | symbol | Dict[str, Any] |
| `get_funding_rate(symbol)` | HTTP API | symbol | List[Any] |
| `get_all_derivatives_data(symbol)` | 组合调用 | symbol | Dict[str, Any] |

### 6.3 数据格式化约定

**位置：** `app/utils/formatters.py`

**格式化函数命名：** `format_<data_type>()`

```python
def format_kline_data(kline_data: Dict[str, Any]) -> str:
    """格式化K线数据 - 返回LLM可读的字符串"""
    # ... 格式化逻辑
    return formatted_string
```

**重要约定：**
- 格式化函数返回**字符串**，不是字典或JSON
- 字符串格式要便于LLM理解和处理
- 包含必要的上下文信息和标题

### 6.4 SQL查询规范

**⚠️ 重要警告：当前实现存在SQL注入风险**

**当前实现（需修复）：**
```python
# ❌ 存在SQL注入风险
sql = f"""SELECT title, content FROM ods_news_feed_processed_di
           WHERE coins RLIKE '{symbol}'"""
cursor.execute(sql)
```

**正确实现：**
```python
# ✅ 使用参数化查询
cursor.execute(
    "SELECT title, content FROM ods_news_feed_processed_di WHERE coins RLIKE %s",
    (symbol,)
)
```

**修复优先级：高**

---

## 7. 会话管理规范

### 7.1 会话隔离架构

**设计目标**: 支持多用户独立会话，避免对话历史混淆

**实现方案**: 内存缓存 + MySQL持久化

```
┌─────────────────────────────────────────────────────────────┐
│                    会话管理层                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │  SessionService（全局单例）                    │  │
│  │  ┌────────────────────────────────────────┐    │  │
│  │  │  LRUCache (内存, 100会话)        │    │  │
│  │  │  - session_id -> List[(role, content)] │    │  │
│  │  └────────────────────────────────────────┘    │  │
│  │  ┌────────────────────────────────────────┐    │  │
│  │  │  MySQL (持久化)                     │    │  │
│  │  │  - session_history 表                 │    │  │
│  │  │  - 自动清理 (保留50轮)              │    │  │
│  │  └────────────────────────────────────────┘    │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 会话服务API

| 方法 | 参数 | 返回值 | 说明 |
|------|------|----------|------|
| `get_history(session_id, limit=50)` | List[Dict] | 获取会话历史，先查缓存，未命中查DB |
| `add_message(session_id, role, content)` | None | 添加消息到会话，同步更新缓存和DB |
| `clear_session(session_id)` | None | 清除指定会话的缓存和DB记录 |
| `clear_all()` | None | 清除所有内存缓存 |

### 7.3 会话生命周期

```
用户请求 → 检查conversation_id
                    ↓
         有conversation_id?    无conversation_id?
                    ↓                    ↓
              加载会话历史           无状态模式
                    ↓                    ↓
          查LRU缓存?            直接处理
              ↓     ↓
         命中   未命中
           ↓       ↓
      返回历史   查MySQL → 更新缓存
                    ↓
              构建消息列表(历史+新消息)
                    ↓
              调用LLM生成响应
                    ↓
              保存用户消息和AI响应
                    ↓
              更新LRU缓存 + 写入MySQL
                    ↓
              自动清理(超过100条→保留最新100条)
```

### 7.4 数据库表设计

**表名**: `session_history`

| 字段 | 类型 | 说明 | 索引 |
|------|------|------|--------|
| id | BIGINT AUTO_INCREMENT | 主键ID | PRIMARY |
| session_id | VARCHAR(100) NOT NULL | 会话ID | idx_session_id |
| role | VARCHAR(20) NOT NULL | 角色（user/assistant） | - |
| content | TEXT NOT NULL | 消息内容 | - |
| created_at | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | 创建时间 | idx_created_at |

**自动清理逻辑**:
- 超过50轮(100条消息)时，删除最旧的记录
- 保留最新的100条消息供后续查询

### 7.5 优雅降级策略

```
请求 → 数据库操作失败?
        ↓
     是      否
        ↓       ↓
  返回空列表   正常处理
        ↓
  降级为无状态模式
        ↓
  继续对话，记录错误日志
```

**降级触发条件**:
- MySQL连接失败
- SQL执行失败
- 表不存在且创建失败

**降级行为**:
- `get_history()` 返回空列表
- `add_message()` 记录错误但不中断
- 服务继续运行，不抛出异常

---

## 8. 状态管理规范

### 7.1 全局状态存储

| 状态 | 位置 | 模式 | 生命周期 |
|------|------|------|----------|
| **配置** | `config/settings.py` + `.env` | 单例 + LRU缓存 | 应用启动时加载，缓存到内存 |
| **LLM客户端** | `app/services/llm_service.py` | 全局单例 | 应用启动时初始化 |
| **智能体** | `app/agents/crypto_agent.py` | 全局单例 | 应用启动时初始化 |
| **会话缓存** | `SessionService._cache` | LRU缓存 | 应用运行期间，最多100个会话 |
| **会话历史** | `session_history` 表 | MySQL持久化 | 应用运行期间持续累积，自动清理 |

### 7.2 配置管理规范

**获取配置：**

```python
from app.core.config import get_settings

settings = get_settings()
```

**配置类定义：**

```python
# config/settings.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # MySQL配置
    mysql_host: str
    mysql_port: int = 3306
    mysql_user: str
    mysql_password: str
    mysql_database: str = "exchange"

    # DeepSeek API配置
    deepseek_api_key: str
    deepseek_api_base: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    # 应用配置
    app_name: str = "Crypto Analyst Assistant"
    app_version: str = "1.0.0"
    debug: bool = False

    # API配置
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_prefix: str = "/api/v1"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

# 使用LRU缓存
settings = Settings()
```

**环境变量配置：** 读取自 `.env` 文件

### 7.3 对话历史管理

**⚠️ 重要问题：当前实现存在多用户会话隔离问题**

**当前实现：**
```python
class CryptoAnalystAgent:
    def __init__(self):
        self.chat_history = []  # 所有用户共享同一历史！
```

**问题：**
- 对话历史是全局共享的
- 多用户环境下会互相干扰
- 重启应用后历史丢失

**改进建议（优先级：高）：**
```python
# 方案1：使用conversation_id隔离
from collections import defaultdict

class CryptoAnalystAgent:
    def __init__(self):
        self.chat_histories = defaultdict(list)

    def get_history(self, conversation_id: str) -> list:
        return self.chat_histories[conversation_id]

    def add_to_history(self, conversation_id: str, message):
        self.chat_histories[conversation_id].append(message)

    def clear_history(self, conversation_id: str = None):
        if conversation_id:
            self.chat_histories[conversation_id] = []
        else:
            self.chat_histories.clear()
```

### 7.4 全局服务实例

**LLM服务：**
```python
# app/services/llm_service.py
class LLMService:
    def __init__(self):
        self.client = OpenAI(...)
        self.model = ...
        self.temperature = ...

    def call_llm(self, prompt: str, lang: str = "zh") -> str:
        # ...

llm_service = LLMService()  # 全局单例
```

**智能体：**
```python
# app/agents/crypto_agent.py
class CryptoAnalystAgent:
    def __init__(self):
        self.settings = get_settings()
        self.llm = self._create_llm()
        self.tools = self._create_tools()
        self.chat_history = []
        self.agent = self._create_agent()

crypto_agent = CryptoAnalystAgent()  # 全局单例
```

---

## 8. API接口规范

### 8.1 路由定义

**位置：** `app/api/endpoints.py`

**路由注册：**
```python
router = APIRouter()

@router.get("/health")
async def health_check():
    return HealthResponse(...)

# 在main.py中注册
app.include_router(
    api_router,
    prefix=f"{settings.api_prefix}",  # /api/v1
    tags=["API"]
)
```

### 8.2 端点列表

| 端点 | 方法 | 功能 | 流式支持 |
|------|------|------|----------|
| `/` | GET | 根路径，应用信息 | - |
| `/health` | GET | 健康检查 | - |
| `/tools` | GET | 获取可用工具列表 | - |
| `/analyze` | POST | 非流式分析 | 否 |
| `/analyze/stream` | POST | 流式分析 | 是（SSE） |
| `/chat` | POST | 非流式对话 | 否 |
| `/chat/stream` | POST | 流式对话 | 是（SSE） |
| `/clear` | POST | 清除对话记忆 | - |
| `/symbols` | GET | 获取支持的币种列表 | - |
| `/docs` | GET | Swagger UI | - |
| `/redoc` | GET | ReDoc | - |

### 8.3 请求/响应模型

**位置：** `app/api/schemas.py`

**约定：**
- 请求模型以 `Request` 结尾
- 响应模型以 `Response` 结尾
- 使用 Pydantic Field 进行字段验证

**示例：**
```python
class AnalyzeRequest(BaseModel):
    symbol: str = Field(..., description="加密货币符号")
    question: str = Field(..., description="分析问题")
    lang: Language = Field(default=Language.ZH)

class AnalyzeResponse(BaseModel):
    symbol: str
    question: str
    response: str
    intermediate_steps: List[Dict[str, Any]]
    lang: str
```

### 8.4 流式响应实现

**SSE流式响应：**
```python
from sse_starlette.sse import EventSourceResponse

@router.post("/analyze/stream")
async def analyze_stream(request: AnalyzeRequest):
    async def event_generator():
        try:
            for chunk in crypto_agent.analyze_stream(...):
                yield {
                    "event": "message",
                    "data": json.dumps({"data": chunk, "type": "chunk"})
                }
            yield {
                "event": "message",
                "data": json.dumps({"data": "", "type": "complete"})
            }
        except Exception as e:
            yield {
                "event": "message",
                "data": json.dumps({"data": f"错误: {str(e)}", "type": "error"})
            }

    return EventSourceResponse(event_generator())
```

---

## 9. 工具开发规范

### 9.1 工具基类

**位置：** `app/agents/tools/base.py`

```python
from abc import ABC, abstractmethod
from langchain.tools import BaseTool
from app.core.exceptions import CryptoAnalystException

class CryptoAnalystTool(BaseTool, ABC):
    """加密货币分析工具基类"""

    def _run(self, *args, **kwargs) -> Any:
        """LangChain调用的入口方法 - 处理参数适配"""
        # LangChain 1.x参数适配逻辑
        if not args and kwargs:
            if "__arg1" in kwargs:
                return self.execute(kwargs["__arg1"])
            elif "input" in kwargs:
                return self.execute(kwargs["input"])
            elif len(kwargs) == 1:
                return self.execute(next(iter(kwargs.values())))
            else:
                return self.execute(**kwargs)
        return self.execute(*args, **kwargs)

    @abstractmethod
    def execute(self, *args, **kwargs) -> Any:
        """工具具体执行逻辑 - 子类必须实现"""
        pass

    def _arun(self, *args, **kwargs) -> Any:
        """异步执行方法（暂不支持）"""
        raise NotImplementedError("此工具不支持异步执行")
```

### 9.2 工具开发模板

**新工具开发步骤：**

1. 在 `app/agents/tools/` 创建新文件
2. 继承 `CryptoAnalystTool` 基类
3. 定义 `name`、`description`、`args_schema`
4. 实现 `execute` 方法
5. 在 `crypto_agent.py` 中注册工具

**模板示例：**

```python
from typing import Dict, Any
from pydantic import BaseModel, Field
from app.agents.tools.base import CryptoAnalystTool, SymbolInput
from app.utils.validators import validate_symbol

# 定义输入模型
class MyToolInput(BaseModel):
    symbol: str = Field(description="加密货币符号")
    param1: str = Field(description="参数1", default="default_value")

# 定义工具类
class MyNewTool(CryptoAnalystTool):
    """新工具 - 简短描述"""

    name: str = "my_new_tool"
    description: str = "详细描述工具功能和适用场景"
    args_schema: type = MyToolInput

    def execute(self, symbol: str, param1: str = "default_value") -> str:
        """执行工具逻辑"""
        # 1. 验证输入
        symbol = validate_symbol(symbol)

        # 2. 获取数据或执行业务逻辑
        # data = get_xxx_data(symbol)

        # 3. 格式化数据
        # formatted = format_xxx_data(data)

        # 4. 返回字符串（重要！）
        return f"{symbol}分析结果：\n\n{formatted}"
```

### 9.3 工具注册

**在 `crypto_agent.py` 中注册：**

```python
from app.agents.tools.my_new_tool import MyNewTool

class CryptoAnalystAgent:
    def _create_tools(self) -> List[Tool]:
        tools = []

        # 注册新工具
        tools.append(Tool.from_function(
            func=MyNewTool()._run,
            name="my_new_tool",
            description="工具描述"
        ))

        return tools
```

### 9.4 工具描述规范

**description约定：**
- 以"当用户询问...时使用此工具"结尾
- 明确工具的使用场景
- 简洁明了

**示例：**
```python
description: str = "获取加密货币的市场数据，包括K线数据和基本信息。当用户询问币种价格、历史数据、基本信息时使用此工具。"
```

### 9.5 工具返回值规范

**重要：工具的execute方法必须返回字符串**

```python
# ✅ 正确
def execute(self, symbol: str) -> str:
    data = get_xxx_data(symbol)
    return f"分析结果：\n{data}"

# ❌ 错误
def execute(self, symbol: str) -> dict:
    return {"result": data}  # LangChain无法正确处理
```

---

## 10. 已知问题与改进建议

### 11.1 严重问题（必须修复）

| 问题 | 位置 | 影响 | 修复建议 | 状态 |
|------|------|------|----------|------|
| **SQL注入风险** | `data_service.py:63` | 数据库安全风险 | 使用参数化查询 | 待修复 |
| **多用户会话未隔离** | `crypto_agent.py` | 用户隐私泄露 | ✅ 已实现conversation_id隔离 | 已修复 |

### 10.2 中等问题（建议修复）

| 问题 | 位置 | 影响 | 修复建议 |
|------|------|------|----------|
| **缺少数据库连接池** | `data_service.py:53` | 性能差，资源浪费 | 使用SQLAlchemy或连接池 |
| **对话历史无限增长** | `crypto_agent.py` | 内存泄漏 | 添加历史长度限制 |
| **异常处理不完善** | `analysis_tools.py:230` | 程序稳定性 | 完善异常捕获和处理 |
| **无用户认证** | `app/api/` | 任何人可访问 | 实现JWT或OAuth认证 |
| **无速率限制** | `app/api/` | 可能被滥用 | 使用slowapi实现限流 |

### 10.3 轻微问题（可选修复）

| 问题 | 位置 | 影响 | 修复建议 |
|------|------|------|----------|
| **诊断脚本错误** | `diagnose_agent.py:55` | 影响调试使用 | 修复返回值类型错误 |
| **表情符号清理不精确** | `analysis_tools.py:74` | 可能误删字符 | 使用更精确的正则 |
| **无日志系统** | `logs/` 目录为空 | 难以排查问题 | 实现logging模块 |
| **无缓存机制** | - | 性能浪费 | 添加Redis缓存 |

### 10.4 架构改进建议

| 改进项 | 优先级 | 说明 |
|--------|--------|------|
| **数据库连接池** | 高 | 提升性能，减少连接开销 |
| **用户会话管理** | 高 | 支持多用户，历史持久化 |
| **Redis缓存** | 中 | 缓存API响应，减少重复请求 |
| **消息队列** | 中 | 异步处理耗时任务 |
| **微服务拆分** | 低 | 拆分数据服务和AI服务 |

---

## 11. 快速上手

### 11.1 环境配置

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，配置必要参数

# 3. 启动服务
python -m app.main
```

### 11.2 API测试

```bash
# 健康检查
curl http://localhost:8000/api/v1/health

# 分析接口
curl -X POST http://localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BTC", "question": "分析当前价格趋势", "lang": "zh"}'

# 对话接口
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "BTC最近表现如何？", "lang": "zh"}'
```

### 11.3 访问文档

启动服务后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 11.4 添加新工具清单

- [ ] 在 `app/agents/tools/` 创建新文件
- [ ] 继承 `CryptoAnalystTool` 基类
- [ ] 定义 `name`、`description`、`args_schema`
- [ ] 实现 `execute` 方法，返回字符串
- [ ] 在 `crypto_agent.py` 中注册工具
- [ ] 更新工具描述便于智能体路由
- [ ] 编写单元测试

### 11.5 扩展数据源清单

- [ ] 在 `data_service.py` 添加数据获取函数
- [ ] 在 `formatters.py` 添加格式化函数
- [ ] 创建对应的LangChain工具
- [ ] 在 `crypto_agent.py` 中注册工具
- [ ] 测试数据获取和格式化

---

## 附录

### A. 配置参数说明

| 参数 | 说明 | 默认值 | 必填 |
|------|------|--------|------|
| `MYSQL_HOST` | MySQL主机地址 | - | 是 |
| `MYSQL_PORT` | MySQL端口 | 3306 | 否 |
| `MYSQL_USER` | MySQL用户名 | - | 是 |
| `MYSQL_PASSWORD` | MySQL密码 | - | 是 |
| `MYSQL_DATABASE` | 数据库名 | exchange | 否 |
| `DEEPSEEK_API_KEY` | DeepSeek API密钥 | - | 是 |
| `DEEPSEEK_API_BASE` | DeepSeek API地址 | https://api.deepseek.com/v1 | 否 |
| `DEEPSEEK_MODEL` | 模型名称 | deepseek-chat | 否 |
| `API_HOST` | API服务主机 | 0.0.0.0 | 否 |
| `API_PORT` | API服务端口 | 8000 | 否 |
| `DEBUG` | 调试模式 | False | 否 |

### B. 工具完整列表

**市场数据工具（3个）：**
- `get_market_data`: 获取市场数据
- `get_kline_data`: 获取K线数据
- `get_header_data`: 获取基本信息

**新闻数据工具（3个）：**
- `get_news_data`: 获取新闻数据
- `get_recent_news`: 获取近期新闻
- `get_news_count`: 统计新闻数量

**衍生品数据工具（5个）：**
- `get_derivatives_data`: 获取衍生品数据
- `get_buy_sell_ratio`: 获取买卖比例
- `get_open_interest`: 获取持仓量
- `get_trading_volume`: 获取交易量
- `get_funding_rate`: 获取资金费率

**分析工具（5个）：**
- `technical_analysis`: 技术分析
- `news_analysis`: 新闻分析
- `derivatives_analysis`: 衍生品分析
- `quantitative_analysis`: 量化分析
- `summary_analysis`: 综合总结

**提示词工具（2个）：**
- `build_analysis_prompt`: 构建分析提示词
- `get_system_prompt`: 获取系统提示词

### C. 错误代码说明

| 错误代码 | 说明 |
|----------|------|
| 400 | 请求参数验证失败 |
| 404 | 币种未找到 |
| 500 | 内部服务器错误 |
| 503 | 外部服务不可用（API/数据库） |

---

**文档维护说明：**
- 重大架构变更时请更新本文档
- 添加新功能时请更新相关章节
- 发现新问题时请更新已知问题列表

**最后更新**: 2026-03-15
