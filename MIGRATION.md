# 迁移指南

从原始 `test.py` 迁移到基于LangChain的加密货币分析助手

## 主要变化

### 1. 架构重构
- **单文件脚本** → **模块化Web服务**
- **硬编码配置** → **环境变量配置**
- **固定分析流程** → **智能体动态路由**
- **CLI界面** → **RESTful API + Web界面**

### 2. 功能模块化

| 原功能 (test.py) | 新模块 | 说明 |
|-----------------|--------|------|
| `get_kline_data()` | `app/services/data_service.py` | 数据获取服务 |
| `get_header_data()` | `app/services/data_service.py` | 数据获取服务 |
| `get_news_from_mysql()` | `app/services/data_service.py` | 数据获取服务 |
| `get_but_sell_ratio()` 等衍生品函数 | `app/services/data_service.py` | 数据获取服务 |
| `format_kline_data()` | `app/utils/formatters.py` | 数据格式化 |
| `format_header_data()` | `app/utils/formatters.py` | 数据格式化 |
| `call_llm_stream()` | `app/services/llm_service.py` | LLM服务 |
| `analyze_crypto_stream()` | `app/agents/tools/analysis_tools.py` | 分析工具集合 |
| F1-F7提示词构建函数 | `app/agents/tools/prompt_tools.py` | 提示词工具 |

### 3. 修复的问题

#### 3.1 `analyze_crypto_stream` 函数修复
**原代码问题** (第464-465行):
```python
formatted_kline = format_kline_data(kline_data)  # kline_data未定义
formatted_header = format_header_data(header_data)  # header_data未定义
```

**修复方案**:
在 `app/agents/tools/analysis_tools.py` 的 `TechnicalAnalysisTool` 中:
```python
# 获取数据
kline_data = get_kline_data(symbol)
header_data = get_header_data(symbol)

# 创建提示词
prompt = create_analysis_prompt(symbol, question, kline_data, header_data)
```

#### 3.2 配置管理迁移
**原硬编码配置**:
```python
MYSQL_CONF = dict(host="...", port=23209, ...)
client = OpenAI(base_url="...", api_key="sk-...")
```

**新环境变量配置** (`.env`文件):
```
MYSQL_HOST=bj-cynosdbmysql-grp-6dowbi62.sql.tencentcdb.com
MYSQL_PORT=23209
MYSQL_USER=root
MYSQL_PASSWORD=your_password
DEEPSEEK_API_KEY=sk-your-api-key
```

### 4. 新功能特性

#### 4.1 LangChain智能体
- **动态工具选择**: 智能体根据用户问题自动选择合适工具
- **对话记忆**: 支持多轮对话，保持上下文
- **工具链**: 17个专用工具，覆盖市场数据、新闻、衍生品、分析等

#### 4.2 API接口
- **RESTful API**: 标准HTTP接口，易于集成
- **流式响应**: Server-Sent Events (SSE) 实时输出
- **完整文档**: Swagger UI 和 ReDoc 自动生成

#### 4.3 工具列表
1. **市场数据工具** (3个): 获取K线、基本信息等
2. **新闻数据工具** (3个): 获取新闻、统计数量等
3. **衍生品数据工具** (5个): 买卖比例、持仓量、交易量、资金费率等
4. **分析工具** (5个): 技术分析、新闻分析、衍生品分析、量化分析、总结分析
5. **提示词工具** (2个): 构建提示词、获取系统提示词

### 5. 使用方式对比

#### 5.1 原使用方式 (CLI)
```bash
python test.py
# 交互式输入: BTC, zh
```

#### 5.2 新使用方式 (API)
```bash
# 启动服务
python -m app.main

# 使用API
curl -X POST http://localhost:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BTC", "question": "请分析当前市场状况", "lang": "zh"}'
```

#### 5.3 新使用方式 (流式)
```bash
# 流式分析
curl -X POST http://localhost:8000/api/v1/analyze/stream \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"symbol": "BTC", "question": "请分析当前市场状况", "lang": "zh"}'
```

### 6. 代码示例对比

#### 6.1 原代码调用
```python
from test import analyze_crypto_stream

for chunk in analyze_crypto_stream("BTC", "zh"):
    print(chunk, end="", flush=True)
```

#### 6.2 新代码调用 (Python客户端)
```python
import requests

# 非流式分析
response = requests.post(
    "http://localhost:8000/api/v1/analyze",
    json={"symbol": "BTC", "question": "请分析当前市场状况", "lang": "zh"}
)
print(response.json()["response"])

# 流式分析
response = requests.post(
    "http://localhost:8000/api/v1/analyze/stream",
    json={"symbol": "BTC", "question": "请分析当前市场状况", "lang": "zh"},
    stream=True,
    headers={"Accept": "text/event-stream"}
)

for line in response.iter_lines():
    if line:
        print(line.decode('utf-8'))
```

### 7. 部署选项

#### 7.1 本地开发
```bash
# 使用启动脚本
./start.sh  # Linux/Mac
start.bat   # Windows

# 或直接运行
python -m app.main
```

#### 7.2 Docker部署
```bash
# 构建镜像
docker build -t crypto-analyst-assistant .

# 运行容器
docker run -p 8000:8000 --env-file .env crypto-analyst-assistant

# 或使用docker-compose
docker-compose up -d
```

#### 7.3 生产部署
- Nginx反向代理 + SSL
- Gunicorn/Uvicorn工作进程
- 数据库连接池
- 监控和日志

### 8. 扩展开发

#### 8.1 添加新工具
1. 在 `app/agents/tools/` 创建新工具文件
2. 继承 `CryptoAnalystTool` 基类
3. 实现 `execute` 方法
4. 在 `crypto_agent.py` 中注册工具

#### 8.2 添加新数据源
1. 在 `app/services/data_service.py` 添加数据获取函数
2. 在 `app/utils/formatters.py` 添加格式化函数
3. 创建对应的LangChain工具

#### 8.3 自定义分析流程
1. 修改 `app/agents/crypto_agent.py` 中的系统提示词
2. 调整工具描述以改进路由
3. 配置智能体参数（温度、最大迭代次数等）

### 9. 故障排除

#### 9.1 常见问题
1. **服务无法启动**: 检查环境变量配置
2. **数据库连接失败**: 检查MySQL配置和网络连接
3. **API调用失败**: 检查服务状态和端口占用
4. **流式响应中断**: 检查网络连接和超时设置

#### 9.2 调试模式
```bash
# 设置调试模式
export DEBUG=True  # Linux/Mac
set DEBUG=True     # Windows

# 或修改.env文件
DEBUG=True
```

### 10. 性能优化建议

1. **缓存**: 添加Redis缓存频繁访问的数据
2. **连接池**: 配置数据库连接池
3. **异步处理**: 使用异步IO提高并发性能
4. **CDN**: 静态资源使用CDN加速
5. **监控**: 添加性能监控和告警

## 总结

新的基于LangChain的加密货币分析助手提供了：

1. **更好的架构**: 模块化、可扩展、易于维护
2. **更强的功能**: 智能体动态路由、多工具集成
3. **更友好的接口**: RESTful API、流式响应、完整文档
4. **更安全的配置**: 环境变量管理、输入验证、错误处理
5. **更易的部署**: Docker支持、生产就绪

迁移到新系统后，您可以：
- 通过API轻松集成到前端应用
- 扩展新的分析工具和数据源
- 部署到生产环境服务更多用户
- 利用智能体能力提供更精准的分析