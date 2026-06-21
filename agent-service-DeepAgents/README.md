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
| 维度确认 | 通过 `AskUserQuestion_tools` 触发 DeepAgents HITL interrupt |
| 模板覆盖率 | 按匹配指标别名计算模板覆盖率，并区分达标推荐与降级展示 |
| 统一输出 | 最终输出 Markdown 文本，不要求前端解析内部业务过程 JSON |
| LLM trace | 记录 provider 边界的最终请求 payload、原始响应 payload 和错误事件，供排障使用 |
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
│ - TracedChatOpenAI provider 边界 trace                           │
│ - llm_trace.py trace context / callback error                    │
│ - llm_trace_store.py MySQL 落库                                  │
│ - FilesystemBackend 加载 skills/                               │
│ - MemorySaver checkpointer 保存 thread 状态与暂停点              │
│ - interrupt_on 捕获 AskUserQuestion_tools                    │
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
│   │   ├── traced_chat_openai.py   # 在 OpenAI-compatible provider 边界采集 LLM input/output trace
│   │   ├── llm_trace.py            # trace 请求上下文、保存函数和 callback error 事件
│   │   ├── llm_trace_store.py      # LLM trace MySQL 存取
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
| `thread_id` | string | 是 | DeepAgents/LangGraph 线程 ID；同窗口连续追问和同一轮 HITL 确认必须保持一致 |
| `user_input` | string | 是 | 用户自然语言输入；可为首次问题、普通追问、确认回复或补充说明 |

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
| 需要用户确认 | DeepAgents raw interrupt，其中包含 `AskUserQuestion_tools` 的确认页面参数 |
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

### 查询 LLM trace

```http
GET /api/v1/traces/{thread_id}
```

查询指定 `thread_id` 下已经落库的 LLM trace 事件，用于排查一次推荐请求中实际发给 provider 的请求内容、provider 原始响应和异常。

查询参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `limit` | integer | 否 | 返回条数，默认 200，范围 1–1000 |
| `event_type` | string | 否 | 事件类型过滤：`llm_input` / `llm_output` / `llm_error` |
| `request_id` | string | 否 | 单次 `/recommend` 调用 ID，用于区分同一 thread 下多次继续执行 |

返回结构：

```json
{
  "thread_id": "test-001",
  "events": [
    {
      "event_type": "llm_input",
      "request_id": "...",
      "model_name": "...",
      "token_usage": null,
      "payload": {}
    }
  ]
}
```

事件类型说明：

| 事件类型 | 说明 |
| --- | --- |
| `llm_input` | provider 最终 request payload，即 LangChain 完成 tool binding、response_format、tool_choice 和模型参数合并后的 OpenAI-compatible 请求 |
| `llm_output` | provider 直接 response payload，即 `raw_response.parse()` 后、转换成 LangChain `ChatResult` 前的 OpenAI-compatible 响应 |
| `llm_error` | LangChain 或 provider 调用链路中的异常类型、异常消息和 run id |

---

## DeepAgents 运行逻辑

### 启动流程

1. `main.py` 创建 FastAPI 应用。
2. `lifespan()` 调用 `init_semaphore()` 初始化进程级并发信号量。
3. `lifespan()` 调用 `get_agent()` 预热 DeepAgents agent。
4. `agent_factory.py` 创建模型客户端、工具白名单、`FilesystemBackend` 和 `MemorySaver checkpointer`。
5. `create_deep_agent(...)` 加载 `skills/`，并配置：

```python
interrupt_on={"AskUserQuestion_tools": True}
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
过程文本 / 工具调用 / AskUserQuestion_tools interrupt
```

当 Skill 调用 `AskUserQuestion_tools(...)` 时，DeepAgents 因 `interrupt_on` 暂停。该工具参数会作为前端确认页的 interrupt payload 透传给路由层，路由层会：

1. 从 chunk 中识别 `__interrupt__`。
2. 将 raw interrupt 暂存在进程内 `_pending_interrupts[thread_id]`。
3. 通过 SSE 返回 raw interrupt。
4. 结束本次 SSE。

### `thread_id`、checkpoint 与连续追问

`thread_id` 是 LangGraph checkpointer 的线程 key，不是业务数据库主键，也不是前端事件缓存 ID。API 层每次只提交本轮输入，但在 `agent.astream(...)` 的 config 中带上同一个 `thread_id`：

```python
agent.astream(
    input_data,
    config={"configurable": {"thread_id": thread_id}},
    stream_mode="updates",
)
```

DeepAgents / LangGraph 会用这个 ID 从 `MemorySaver` 中读取该线程最新 checkpoint。checkpoint 是图状态快照，`messages` 只是其中一个 channel；它还包含当前节点、interrupt、channel version、pending writes 和 middleware state 等运行时信息。

普通连续追问时，API 层仍只构造本轮消息：

```python
input_data = {"messages": [HumanMessage(content=req.user_input)]}
```

这不是完整历史，也不会覆盖旧状态。它是一次 state update：LangGraph 先恢复 `thread_id` 对应的 checkpoint，再通过 messages channel 的 reducer 把本轮 `HumanMessage` 合并进已有 state。之后 DeepAgents context management 决定本次模型实际看到多少历史、摘要和工具上下文。

因此关系是：

```text
业务层本轮输入
  {"messages": [本轮用户消息]}
        │
        ▼
configurable.thread_id = thread-A
        │
        ▼
LangGraph 读取 checkpoint(thread-A)
        │
        ▼
恢复旧 graph state，其中包含 messages channel
        │
        ▼
把本轮用户消息合并进 messages channel
        │
        ▼
DeepAgents context management 组装本次 LLM 输入
```

注意：`interaction-console` 的 `session_store` 只缓存控制台事件，用于 UI 展示和调试；它不参与模型上下文，也不向本服务回传完整历史。

### 普通追问与 HITL resume 的区别

同一个 `/api/v1/recommend` 端点同时承担两类输入：

| 场景 | 输入形态 | 语义 |
| --- | --- | --- |
| 首次问题或完成后的普通追问 | `{"messages": [HumanMessage(...)]}` | 给同一 `thread_id` 的 checkpoint 追加本轮用户消息，继续普通 agent 执行 |
| 正在等待用户确认 | `Command(resume=...)` | 从 checkpoint 中保存的 interrupt 暂停点恢复执行 |

`Command(resume=...)` 只能用于图真实停在 HITL interrupt 上的情况。为避免内存标记残留导致完成后的普通追问被误判为 resume，路由层会先读取 checkpoint snapshot：

```text
_pending_interrupts[thread_id] 存在
        │
        ▼
agent.aget_state({"configurable": {"thread_id": thread_id}})
        │
        ├─ snapshot 仍有 interrupts/next
        │      → 使用 Command(resume=...)
        │
        └─ snapshot 已无 pending interrupt
               → 清理残留 _pending_interrupts
               → 使用 {"messages": [本轮用户消息]}
```

这样既保留 HITL 确认后的恢复能力，又支持最终推荐完成后在同一个窗口、同一个 `thread_id` 继续追问。

### 确认回复继续执行

同一 `thread_id` 再次调用 `/api/v1/recommend` 时，如果 checkpoint 真实存在待确认的暂停点，路由层会把本次 `user_input` 作为用户确认回复交回 DeepAgents，从暂停点继续执行。

API 层不解析用户是否确认、拒绝或修改维度；这些语义由继续执行后的 Skill 判断。DeepAgents 会根据同一 `thread_id` 从 `MemorySaver` 中找回暂停点并继续执行。

### LLM trace 采集链路

```text
routes._stream_agent
  ↓ 设置 request_id + llm_trace_context
TracedChatOpenAI._get_request_payload
  ↓ 写 llm_input：最终 provider request payload
OpenAI-compatible provider
  ↓ raw_response.parse()
TracedChatOpenAI._save_provider_output
  ↓ 写 llm_output：provider 原始 response payload
LLMTraceCallback.on_llm_error
  ↓ 写 llm_error：异常信息
llm_trace_store.save_trace_event
  ↓ MySQL llm_trace_events
GET /api/v1/traces/{thread_id}
  ↓ 查询 trace
```

采集边界说明：

- `llm_input.payload` 不是 LangChain callback messages，而是 `ChatOpenAI._get_request_payload()` 构造完成后的最终 provider request payload。
- `llm_output.payload` 不是 LangChain `LLMResult`、`ChatGeneration` 或 `AIMessage` wrapper，而是 OpenAI-compatible provider 直接返回的 response payload。
- 当前项目通过 `langchain_openai.ChatOpenAI` 访问 OpenAI-compatible endpoint，因此 trace payload 是 OpenAI-compatible shape，不是 Anthropic 原生 Messages API shape。
- `LLMTraceCallback` 只保留 `llm_error` 采集；input/output 由 `TracedChatOpenAI` 在 provider 边界采集，避免把中间对象误当作真实 wire payload。

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
AskUserQuestion_tools
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
| `AskUserQuestion_tools` | 触发 DeepAgents HITL interrupt |

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

LLM trace 写入复用 `MYSQL_CONFIG`，落库表为 `llm_trace_events`。README 只说明配置用途和表名，不展开 MySQL 连接等敏感默认值。

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

也可以单独运行 LLM trace 相关测试：

```bash
venv/bin/python -m pytest \
  tests/test_llm_trace.py \
  tests/test_llm_trace_store.py \
  tests/test_traced_chat_openai.py
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
- `thread_id` 是 DeepAgents/LangGraph checkpoint 的线程 key：同窗口连续追问应复用同一个 `thread_id`，点击新会话才更换。
- API 层普通追问只提交本轮 `user_input` 对应的 messages update，不维护完整 messages 历史；旧状态由 checkpointer 按 `thread_id` 恢复并合并。
- 维度确认后的继续执行也走同一个 `/api/v1/recommend`，不使用单独 `/resume` 接口。
- `Command(resume=...)` 只用于 checkpoint 真实停在 HITL interrupt 上的场景；最终推荐完成后的普通追问不能走 resume。
- `_pending_interrupts` 只是 API 层的进程内分流提示，真正暂停状态以 DeepAgents checkpointer 的 snapshot 为准。
- 最终输出为面向用户的 Markdown 文本，前端按文本渲染。
- `llm_output.payload` 必须保持 provider 原始响应，不要改回 LangChain `LLMResult` / `AIMessage` wrapper。
- `llm_input.payload` 必须保持最终 provider request payload，不要改回 LangChain callback messages。
- 当前模型调用仍使用 OpenAI-compatible `ChatOpenAI`，不要把 trace 文档或代码改写成 Anthropic SDK / Anthropic Messages API 形态。
- 如果文档与源码出现差异，以当前源码为准，并同步更新 README。
