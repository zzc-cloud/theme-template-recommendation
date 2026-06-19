# agent-service-DeepAgents

基于 DeepAgents 的魔数师主题和模板推荐 API 服务。服务接收用户自然语言问题，通过指标语义搜索、主题定位、维度确认和模板覆盖率计算，经 SSE 返回过程文本、interrupt payload、最终 Markdown 文本和错误 payload。

---

## 目录

- [项目概述](#项目概述)
- [技术架构](#技术架构)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [API 说明](#api-说明)
- [DeepAgents 运行逻辑](#deepagents-运行逻辑)
- [工具与 Skill](#工具与-skill)
- [配置说明](#配置说明)
- [测试与验证](#测试与验证)
- [注意事项](#注意事项)

---

## 项目概述

### 目标

将用户自然语言问题转换为魔数师平台中可操作的主题、指标和模板推荐结果，例如：

> 我想分析南京分行的小微企业贷款风险

服务会按以下流程处理：

1. **需求澄清**：识别筛选值、分析概念，并通过向量搜索匹配候选指标。
2. **维度确认**：在进入主题和模板推荐前，通过 DeepAgents interrupt 暂停，等待用户确认或调整分析维度。
3. **主题推荐**：基于确认后的指标在 Neo4j 本体图谱中聚合候选主题，并返回完整主题路径。
4. **模板推荐**：计算主题下模板对匹配指标的覆盖率，推荐可直接使用的透视分析或万能查询模板。
5. **展示输出**：最终输出面向用户的 Markdown 文本，由前端按文本渲染。

### 核心能力

| 能力 | 说明 |
| --- | --- |
| 语义搜索 | 使用 Chroma 指标向量库和 SiliconFlow embedding API 匹配用户分析概念 |
| 主题定位 | 使用 Neo4j 中的板块、分类、主题、指标层级关系聚合候选主题 |
| 维度确认 | 通过 `AskUserQuestion_tools_tools` 触发 DeepAgents HITL interrupt |
| 模板覆盖率 | 按匹配指标别名计算模板覆盖率，并区分达标推荐与降级展示 |
| 统一输出 | 最终输出 Markdown 文本，不要求前端解析内部业务过程 JSON |
| 并发保护 | API 层使用进程级 `asyncio.Semaphore` 控制并发请求数 |

---

## 技术架构

```text
┌──────────────────────────────────────────────────────────────┐
│ FastAPI                                                       │
│ - GET  /health                                               │
│ - POST /api/v1/recommend 统一推荐入口（SSE）                   │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│ API 边界层 src/agent_service/api/routes.py                    │
│ - 并发控制                                                     │
│ - 首次输入 / 确认回复继续执行                                  │
│ - DeepAgents chunk → SSE message                              │
│ - interrupt / 文本输出 / ErrorPayload 转发                     │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│ DeepAgents 运行时 src/agent_service/deepagents/                │
│ - ChatOpenAI 兼容模型客户端                                     │
│ - FilesystemBackend 加载 skills/                               │
│ - MemorySaver checkpointer 保存暂停点                           │
│ - interrupt_on 捕获 AskUserQuestion_tools_tools             │
│ - tool_registry 暴露工具白名单                                  │
└──────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌──────────────────────┐
│ skills/         │ │ Chroma          │ │ Neo4j                │
│ 推荐业务流程     │ │ 指标语义搜索      │ │ 主题/指标/模板图谱       │
└─────────────────┘ └─────────────────┘ └──────────────────────┘
```

这里没有手写 LangGraph 业务状态图。业务步骤写在 `skills/` 的 Skill 文档中，DeepAgents runtime 根据 Skill、模型和工具执行。

---

## 目录结构

```text
agent-service-DeepAgents/
├── src/agent_service/
│   ├── main.py                    # FastAPI 应用入口、生命周期、健康检查
│   ├── config.py                  # 环境变量与运行配置
│   ├── api/
│   │   ├── routes.py              # /api/v1/recommend SSE 接口
│   │   └── schemas.py             # API 边界 Pydantic 模型
│   ├── deepagents/
│   │   ├── agent_factory.py       # 创建并缓存 DeepAgents agent
│   │   ├── router.py              # 选择要加载的 Skill 根路径
│   │   ├── skill_protocol.py      # API DTO 到 DeepAgents 输入的适配
│   │   └── tool_registry.py       # DeepAgents 工具白名单
│   └── tools/
│       ├── confirmation_tools.py  # 前端确认 HITL interrupt 工具
│       ├── vector_search.py       # Chroma + embedding 指标语义搜索
│       ├── theme_tools.py         # Neo4j 主题/指标/路径查询
│       └── template_tools.py      # Neo4j 模板覆盖率计算
├── skills/
│   └── theme-template-recommendation/  # 主题与模板推荐 Skill
├── scripts/                       # 本体构建、向量化、健康检查脚本
├── tests/                         # 契约测试
├── Dockerfile
├── pyproject.toml
└── README.md
```

`skills/` 不是 Python 包，也不是 API 层手动读取的目录。它由 `FilesystemBackend(root_dir=agent-service-DeepAgents 根目录)` 和 `skills=["skills"]` 交给 DeepAgents runtime 加载。

---

## 快速开始

### 前置依赖

- Python 3.11+
- Neo4j 数据库
- Chroma 指标向量库
- SiliconFlow LLM API Key
- SiliconFlow Embedding API Key

本项目包含两套同名 Python 包实现。运行或测试 DeepAgents 版本时，必须使用本目录下的虚拟环境：

```bash
/Users/yyzz/Desktop/MyClaudeCode/theme-template-recommendation/agent-service-DeepAgents/venv
```

### 1. 安装依赖

```bash
cd /Users/yyzz/Desktop/MyClaudeCode/theme-template-recommendation/agent-service-DeepAgents
venv/bin/python -m pip install -e .
```

如需开发测试依赖：

```bash
venv/bin/python -m pip install -e '.[dev]'
```

### 2. 配置环境变量

服务配置由 `src/agent_service/config.py` 读取，优先级为：

1. `AGENT_ENV_FILE` 指向的环境文件。
2. 从 `src/agent_service/config.py` 所在目录向上搜索到的第一个 `.env`。
3. 当前进程环境变量。

示例：

```bash
export AGENT_ENV_FILE=/path/to/agent-service-DeepAgents/.env
```

### 3. 启动服务

```bash
cd /Users/yyzz/Desktop/MyClaudeCode/theme-template-recommendation/agent-service-DeepAgents
venv/bin/python -m agent_service.main
```

或使用 uvicorn：

```bash
venv/bin/python -m uvicorn agent_service.main:app --host 0.0.0.0 --port 8000 --reload
```

服务默认监听：

- API：<http://localhost:8000>
- OpenAPI 文档：<http://localhost:8000/docs>

### 4. 健康检查

```bash
curl http://localhost:8000/health
```

`/health` 会检查：

- DeepAgents agent 是否可创建。
- Neo4j 是否可连接。
- 当前并发占用与可用并发数。

---

## API 说明

### 健康检查

```http
GET /health
```

返回示例：

```json
{
  "status": "healthy",
  "version": "1.0.0",
  "services": {
    "neo4j": true,
    "deepagents": true
  },
  "concurrency": {
    "current": 0,
    "max": 5,
    "available": 5
  }
}
```

### 流式推荐

```http
POST /api/v1/recommend
```

请求体：

```json
{
  "thread_id": "550e8400-e29b-41d4-a716-446655440000",
  "user_input": "我想分析南京分行的小微企业贷款风险"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `thread_id` | string | 是 | 请求线程 ID；同一维度确认流程必须保持一致 |
| `user_input` | string | 是 | 用户自然语言输入；可为首次问题、确认回复或补充说明 |

调用示例：

```bash
curl -s -N -X POST http://localhost:8000/api/v1/recommend \
  -H 'Content-Type: application/json' \
  -d '{
    "thread_id": "test-001",
    "user_input": "我想分析南京分行的小微企业贷款风险"
  }'
```

### SSE 输出

当前接口统一使用 SSE `message` 事件。`data` 可能是以下内容：

| 场景 | `data` 内容 |
| --- | --- |
| 过程输出 | Skill 或模型产生的普通文本 |
| 需要用户确认 | DeepAgents raw interrupt，其中包含 `AskUserQuestion_tools_tools` 的确认页面参数 |
| 执行完成 | 最后一条 assistant Markdown 文本 |
| 执行失败 | `ErrorPayload` JSON |

最终结果示例：

```md
## 魔数师主题与模板推荐结果

### 需求澄清
...

### 推荐主题
...

### 推荐模板
...

### 使用建议
...
```

---

## DeepAgents 运行逻辑

### 启动流程

1. `main.py` 创建 FastAPI 应用。
2. `lifespan()` 调用 `init_semaphore()` 初始化进程级并发信号量。
3. `lifespan()` 调用 `get_agent()` 预热 DeepAgents agent。
4. `agent_factory.py` 创建模型客户端、工具白名单、`FilesystemBackend` 和 `MemorySaver checkpointer`。
5. `create_deep_agent(...)` 加载 `skills/`，并配置：

```python
interrupt_on={"AskUserQuestion_tools_tools": True}
```

### 首次 `/api/v1/recommend`

```text
RecommendRequest
  ↓
routes.recommend_stream
  ↓
skill_protocol.build_recommend_messages
  ↓
agent.astream(..., config={"configurable": {"thread_id": thread_id}})
  ↓
DeepAgents 执行 theme-template-recommendation Skill
  ↓
过程文本 / 工具调用 / AskUserQuestion_tools_tools interrupt
```

当 Skill 调用 `AskUserQuestion_tools_tools(...)` 时，DeepAgents 因 `interrupt_on` 暂停。该工具参数会作为前端确认页的 interrupt payload 透传给路由层，路由层会：

1. 从 chunk 中识别 `__interrupt__`。
2. 将 raw interrupt 暂存在进程内 `_pending_interrupts[thread_id]`。
3. 通过 SSE 返回 raw interrupt。
4. 结束本次 SSE。

### 确认回复继续执行

同一 `thread_id` 再次调用 `/api/v1/recommend` 时，如果该线程存在待确认的暂停点，路由层会把本次 `user_input` 作为用户确认回复交回 DeepAgents，从暂停点继续执行。

API 层不解析用户是否确认、拒绝或修改维度；这些语义由继续执行后的 Skill 判断。DeepAgents 会根据同一 `thread_id` 从 `MemorySaver` 中找回暂停点并继续执行。

---

## 工具与 Skill

`tool_registry.py` 只向 DeepAgents 暴露以下工具：

```text
search_indicators_by_vector
batch_get_indicator_themes
aggregate_themes_from_indicators
get_sectors_from_root
get_sector_themes
get_theme_filter_indicators
get_theme_analysis_indicators
get_theme_templates_with_coverage
AskUserQuestion_tools_tools
```

工具分工：

| 工具 | 作用 |
| --- | --- |
| `search_indicators_by_vector` | 使用 Chroma + embedding API 做指标语义搜索 |
| `batch_get_indicator_themes` | 批量查询指标所属主题 |
| `aggregate_themes_from_indicators` | 按匹配指标聚合候选主题 |
| `get_sectors_from_root` / `get_sector_themes` | 沿 Neo4j 层级导航板块和主题 |
| `get_theme_filter_indicators` | 获取主题下时间、机构等筛选指标 |
| `get_theme_analysis_indicators` | 获取主题下分析指标 |
| `get_theme_templates_with_coverage` | 计算模板对匹配指标的覆盖率 |
| `AskUserQuestion_tools_tools` | 触发 DeepAgents HITL interrupt |

工具白名单是运行时边界：Skill 只能调用这里注册的工具。`_safe_tool()` 会把底层异常包装为稳定错误结构，避免单个工具异常直接撕裂 SSE 流。

---

## 配置说明

### 必填配置

| 变量 | 说明 |
| --- | --- |
| `SILICONFLOW_LLM_API_KEY` | LLM API Key |
| `SILICONFLOW_EMBEDDING_API_KEY` | Embedding API Key |
| `NEO4J_URI` | Neo4j Bolt 地址 |
| `NEO4J_USER` | Neo4j 用户名 |
| `NEO4J_PASSWORD` | Neo4j 密码 |
| `CHROMA_PATH` | Chroma 持久化目录 |

### 常用可选配置

| 变量 | 说明 |
| --- | --- |
| `SILICONFLOW_BASE_URL` | OpenAI-compatible LLM API base URL |
| `SILICONFLOW_EMBEDDING_URL` | Embedding API URL |
| `LLM_MODEL` | LLM 模型名 |
| `LLM_TEMPERATURE` | LLM 温度参数 |
| `LLM_MAX_TOKENS` | LLM 最大输出 token 数 |
| `EMBEDDING_MODEL` | Embedding 模型名 |
| `EMBEDDING_DIM` | Embedding 维度 |
| `COLLECTION_NAME` | Chroma collection 名称 |
| `DEFAULT_TOP_K_THEMES` | 默认主题返回数量 |
| `DEFAULT_TOP_K_TEMPLATES` | 默认模板返回数量 |
| `TEMPLATE_COVERAGE_THRESHOLD` | 模板覆盖率推荐阈值 |
| `MAX_CONCURRENT_REQUESTS` | 最大并发请求数 |
| `CONCURRENT_TIMEOUT_SECONDS` | 等待并发名额的超时时间 |

> 注意：配置默认值以 `src/agent_service/config.py` 为准。README 只说明变量用途，不展开敏感默认值。

---

## 测试与验证

### 运行测试

必须使用 DeepAgents 目录下的虚拟环境：

```bash
cd /Users/yyzz/Desktop/MyClaudeCode/theme-template-recommendation/agent-service-DeepAgents
venv/bin/python -m pytest
```

也可以按契约测试分组运行：

```bash
venv/bin/python -m pytest \
  tests/test_display_result.py \
  tests/test_result_contract.py \
  tests/test_router.py \
  tests/test_skill_contract.py \
  tests/test_tool_registry.py
```

### 健康检查脚本

`scripts/healthcheck.py` 用于检查环境变量、LLM、Embedding、Neo4j、Chroma、向量搜索和 HTTP 健康检查等依赖。示例：

```bash
venv/bin/python scripts/healthcheck.py
venv/bin/python scripts/healthcheck.py --only neo4j
venv/bin/python scripts/healthcheck.py --only vector
```

健康检查脚本中的外部依赖项需要对应服务和数据真实可用；如果 Neo4j、Chroma 或 API Key 未配置，失败通常表示运行环境未就绪，而不是 README 或注释改动本身失败。

### Docker

当前 Dockerfile 使用 `python:3.11-slim`，安装项目后复制 `src/`、`skills/` 和 `scripts/`，并通过 uvicorn 启动：

```bash
docker build -t theme-template-agent-deepagents .
docker run --rm -p 8000:8000 --env-file .env theme-template-agent-deepagents
```

容器健康检查访问：

```text
GET http://localhost:8000/health
```

---

## 注意事项

- 当前 DeepAgents 版本只有一个推荐入口：`POST /api/v1/recommend`。
- 维度确认后的继续执行也走同一个 `/api/v1/recommend`，不使用单独 `/resume` 接口。
- `thread_id` 是恢复暂停点的关键，同一轮确认流程必须保持一致。
- `_pending_interrupts` 只是 API 层的进程内分流标记，真正暂停状态由 DeepAgents checkpointer 管理。
- 最终输出为面向用户的 Markdown 文本，前端按文本渲染。
- 如果文档与源码出现差异，以当前源码为准，并同步更新 README。
