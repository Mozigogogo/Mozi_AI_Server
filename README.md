# Crypto Analyst Assistant

基于LangChain的虚拟货币分析助手，提供智能化的加密货币市场分析服务。

## 功能特性

- 🚀 **智能路由**: 基于LangChain的智能体动态选择分析工具
- 📊 **多维度分析**: 市场数据、新闻、衍生品、技术分析等
- 🔄 **流式响应**: 支持SSE流式输出，实时展示分析过程
- 🛠️ **模块化工具**: 独立的功能模块，易于扩展和维护
- 🔧 **配置管理**: 基于环境变量的配置系统
- 🌐 **RESTful API**: 标准HTTP接口，便于前端集成

## 项目结构

```
crypto-analyst-assistant/
├── app/
│   ├── main.py              # FastAPI应用入口
│   ├── api/                 # API路由和模型
│   ├── core/               # 核心配置和异常处理
│   ├── agents/             # LangChain智能体和工具
│   ├── services/           # 数据服务和LLM服务
│   └── utils/              # 工具函数
├── config/                 # 配置文件
├── requirements.txt        # 依赖包
└── README.md              # 项目说明
```

## 快速开始

### 1. 环境准备

```bash
# 克隆项目
git clone <repository-url>
cd crypto-analyst-assistant

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate     # Windows

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

复制环境变量模板并配置：

```bash
cp config/.env.example .env
```

编辑`.env`文件，配置以下信息：
- MySQL数据库连接信息
- DeepSeek API密钥
- 其他应用配置

### 3. 启动服务

```bash
python -m app.main
```

服务将在 `http://localhost:8000` 启动。

### 4. API文档

启动服务后访问：
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## API接口

### 分析接口

**POST** `/api/v1/analyze`

请求体：
```json
{
  "symbol": "BTC",
  "question": "请分析BTC的当前市场状况",
  "lang": "zh"
}
```

响应：SSE流式输出

### 聊天接口

**POST** `/api/v1/chat`

请求体：
```json
{
  "message": "BTC最近表现如何？",
  "conversation_id": "optional-conversation-id",
  "lang": "zh"
}
```

响应：SSE流式输出

## 工具模块

### 市场数据工具
- 获取K线数据
- 获取币种基本信息

### 新闻数据工具
- 从MySQL获取相关新闻
- 新闻情感分析

### 衍生品数据工具
- 获取买卖比例
- 获取持仓量数据
- 获取交易量数据
- 获取资金费率

### 分析工具
- 技术分析工具
- 量化分析工具
- 新闻解读工具
- 多空结构分析工具
- 综合总结工具

## 工具调用示例

智能体会根据用户的问题自动选择合适的工具进行分析。以下是一些示例问题：

### 市场数据查询
- "BTC的当前价格是多少？" → 调用 `get_header_data` 工具
- "获取ETH的市场数据" → 调用 `get_market_data` 工具
- "显示BTC最近30天的K线数据" → 调用 `get_kline_data` 工具

### 新闻分析
- "BTC最近有什么新闻？" → 调用 `get_recent_news` 工具
- "ETH相关的新闻有多少条？" → 调用 `get_news_count` 工具
- "分析SOL的新闻情绪" → 调用 `news_analysis` 工具

### 衍生品分析
- "BTC的买卖比例数据" → 调用 `get_buy_sell_ratio` 工具
- "ETH的持仓量数据" → 调用 `get_open_interest` 工具
- "分析BTC的衍生品市场" → 调用 `derivatives_analysis` 工具

### 技术分析
- "分析BTC的技术面" → 调用 `technical_analysis` 工具
- "BTC当前价格趋势如何？" → 调用 `technical_analysis` 工具
- "ETH的支撑位和阻力位在哪里？" → 调用 `technical_analysis` 工具

### 量化分析
- "对BTC进行量化评分" → 调用 `quantitative_analysis` 工具
- "BTC的六因子评分是多少？" → 调用 `quantitative_analysis` 工具

### 综合总结
- "总结一下BTC的总体情况" → 调用 `summary_analysis` 工具
- "对ETH做个全面的分析总结" → 调用 `summary_analysis` 工具

### API调用示例

```bash
# 获取BTC市场数据
curl -X POST "http://localhost:8000/api/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "BTC的当前价格是多少？",
    "chat_history": []
  }'

# 技术分析
curl -X POST "http://localhost:8000/api/v1/analyze" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BTC",
    "question": "当前价格趋势如何？",
    "lang": "zh"
  }'
```

## 配置说明

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| MYSQL_HOST | MySQL主机地址 | - |
| MYSQL_PORT | MySQL端口 | 3306 |
| MYSQL_USER | MySQL用户名 | - |
| MYSQL_PASSWORD | MySQL密码 | - |
| MYSQL_DATABASE | 数据库名 | exchange |
| DEEPSEEK_API_KEY | DeepSeek API密钥 | - |
| DEEPSEEK_API_BASE | DeepSeek API地址 | https://api.deepseek.com/v1 |
| API_HOST | API服务主机 | 0.0.0.0 |
| API_PORT | API服务端口 | 8000 |

### 应用配置

在`config/settings.py`中可以调整：
- 数据获取限制
- LLM参数
- 缓存设置
- 超时配置

## 开发指南

### 添加新工具

1. 在`app/agents/tools/`目录下创建新工具文件
2. 继承`BaseTool`类，实现`_run`方法
3. 在`app/agents/crypto_agent.py`中注册新工具
4. 更新工具描述以便智能体正确路由

### 扩展数据源

1. 在`app/services/data_service.py`中添加新的数据获取函数
2. 创建对应的格式化工具在`app/utils/formatters.py`
3. 创建对应的LangChain工具

### 测试

```bash
# 运行单元测试
pytest tests/

# 运行API测试
pytest tests/api/
```

## 部署

### Docker部署

```bash
# 构建镜像
docker build -t crypto-analyst-assistant .

# 运行容器
docker run -p 8000:8000 --env-file .env crypto-analyst-assistant
```

### 生产环境建议

1. 使用Nginx反向代理
2. 配置SSL证书
3. 设置数据库连接池
4. 启用API限流
5. 配置监控和日志
