# Theme Template Recommendation Agent Service

基于 LangChain/LangGraph 的主题模板推荐 API 服务，将用户自然语言问题映射到魔数师平台的业务主题与分析模板。

---

## 目录

- [项目概述](#项目概述)
- [技术架构](#技术架构)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [API 参考](#api-参考)
- [配置说明](#配置说明)
- [开发指南](#开发指南)
- [相关文档](#相关文档)

---

## 项目概述

### 目标

将用户自然语言问题（如"我想分析南京分行的小微企业贷款风险"）通过以下流程转换为可直接使用的推荐结果：

1. **需求澄清** — 从问题中提取业务词组，分类为筛选条件与分析概念
2. **主题推荐** — 聚合匹配指标对应的业务主题，判断主题可用性
3. **模板推荐** — 推荐可直接使用的透视分析/万能查询模板，并评估可用性

### 核心能力

| 能力 | 说明 |
|------|------|
| 语义搜索 | 基于向量数据库（Chroma）实现指标语义匹配 |
| 迭代精炼 | 最多 3 轮搜索词修正，自动收敛 |
| 结构化输出 | 使用 `with_structured_output()` 替代手动 JSON 解析 |
| 流式推理 | SSE 流式输出各阶段进度 |
| 会话恢复 | 基于 InMemorySaver 的 Checkpointer 支持 |

---

## 技术架构

```
┌──────────────────────────────────────────────────────────────────┐
│                       FastAPI (REST API)                           │
│  POST /api/v1/recommend       — 同步推荐                          │
│  POST /api/v1/recommend/stream — SSE 流式推荐                      │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                     LangGraph Agent Graph                          │
│  extract_phrases → classify_and_iterate → aggregate_themes        │
│  → complete_indicators → judge_themes → retrieve_templates        │
│  → analyze_templates → format_output                              │
│                                                                     │
│  [InMemorySaver Checkpointer — 会话状态持久化]                      │
└──────────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Chroma         │  │  Neo4j          │  │  SiliconFlow    │
│  (向量数据库)    │  │  (图数据库)      │  │  (LLM API)      │
│  指标语义搜索    │  │  主题/模板/指标   │  │  推理 + 嵌入     │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

### 技术栈

| 类别 | 技术 |
|------|------|
| Agent 框架 | LangChain + LangGraph |
| LLM 调用 | LangChain OpenAI 客户端（兼容 SiliconFlow） |
| 结构化输出 | `with_structured_output()` + Pydantic |
| Streaming | LangGraph v2 streaming + `get_stream_writer()` |
| 持久化 | LangGraph `InMemorySaver` Checkpointer |
| Web 框架 | FastAPI + uvicorn |
| 向量数据库 | Chroma |
| 图数据库 | Neo4j |
| 流式传输 | Server-Sent Events (SSE) |

---

## 目录结构

```
agent-service/
├── src/
│   └── agent_service/
│       ├── __init__.py
│       ├── main.py              # FastAPI 应用入口 + 生命周期管理
│       ├── config.py             # 配置管理（.env 加载）
│       ├── api/
│       │   ├── __init__.py
│       │   ├── routes.py         # API 路由（同步 + 流式）
│       │   └── schemas.py         # Pydantic 请求/响应模型
│       ├── graph/
│       │   ├── __init__.py
│       │   ├── state.py          # LangGraph State 定义（TypedDict）
│       │   ├── nodes.py          # 各阶段节点函数
│       │   └── graph.py          # LangGraph 图构建 + Checkpointer
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── vector_search.py  # Chroma 向量搜索
│       │   ├── theme_tools.py    # Neo4j 主题/指标查询
│       │   └── template_tools.py  # Neo4j 模板查询
│       └── llm/
│           ├── __init__.py
│           ├── client.py          # LLM 客户端（结构化输出）
│           ├── models.py           # Pydantic 响应模型
│           └── prompts.py          # 各阶段 Prompt 定义
├── tests/                        # 测试文件
├── Dockerfile                    # Docker 构建文件
├── docker-compose.yml           # Docker Compose 配置
├── requirements.txt             # Python 依赖
├── pyproject.toml               # 项目元数据（src-layout）
├── .env.example                 # 环境变量模板
└── DEPLOY.md                    # 部署文档（独立文件）
```

---

## 快速开始

### 前置依赖

- Python 3.11+
- Neo4j 数据库（bolt://localhost:7687）
- Chroma 向量库（已向量化数据）
- SiliconFlow API Key

### 1. 安装依赖

```bash
# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -e .
```

### 2. 配置环境变量

```bash
# 从模板复制并编辑
cp .env.example .env
```

参考 [配置说明](#配置说明) 填写必要参数。

### 3. 启动服务

```bash
# 开发模式（支持热重载）
uvicorn agent_service.main:app --reload --port 8000

# 或直接运行
python -m agent_service.main
```

### 4. 验证

```bash
# 健康检查
curl http://localhost:8000/health

# 测试推荐接口
curl -X POST http://localhost:8000/api/v1/recommend \
  -H "Content-Type: application/json" \
  -d '{"question": "我想分析南京分行的小微企业贷款风险"}'

# 测试流式接口
curl -s -N -X POST http://localhost:8000/api/v1/recommend/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "我想分析南京分行的小微企业贷款风险"}'
```

服务运行在 http://localhost:8000
API 文档：http://localhost:8000/docs

---

## API 参考

### 健康检查

```
GET /health
```

返回服务状态及依赖服务（Neo4j）连接状态。

### 同步推荐

```
POST /api/v1/recommend
```

**请求体**

```json
{
  "question": "我想分析南京分行的小微企业贷款风险",
  "top_k_themes": 3,
  "top_k_templates": 5,
  "template_type": null
}
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `question` | string | 必填 | 用户自然语言问题 |
| `top_k_themes` | int | 3 | 返回的主题数量上限 |
| `top_k_templates` | int | 5 | 每种类型返回的模板数量上限 |
| `template_type` | string | null | 模板类型过滤：INSIGHT / COMBINEDQUERY / null（全部） |

**响应体**

```json
{
  "request_id": "uuid",
  "normalized_question": "分析南京分行的小微企业贷款风险",
  "filter_indicators": [...],
  "analysis_dimensions": [...],
  "is_low_confidence": false,
  "recommended_themes": [
    {
      "theme_id": "THEME.xxx",
      "theme_alias": "对公贷款借据",
      "is_supported": true,
      "selected_filter_indicators": [...],
      "selected_analysis_indicators": [...]
    }
  ],
  "recommended_templates": [
    {
      "template_id": "TEMPLATE.xxx",
      "template_alias": "贷款风险透视",
      "coverage_ratio": 0.85,
      "usability": {...}
    }
  ],
  "execution_time_ms": 5230.5,
  "iteration_rounds": 2
}
```

### 流式推荐

```
POST /api/v1/recommend/stream
```

返回 SSE 流，包含以下事件：

| 事件类型 | 说明 |
|---------|------|
| `stage_complete` | 节点完成事件 |
| `custom` | 节点内部进度事件（如 LLM 分类、迭代搜索等） |
| `final` | 最终推荐结果 |
| `error` | 错误信息 |

**示例**

```
event: message
data: {"event_type": "stage_complete", "stage": "extract_phrases", "timestamp": 1710000000.0}

event: message
data: {"event_type": "custom", "data": {"stage": "classify_and_iterate", "step": "classifying", "status": "in_progress"}, "timestamp": 1710000001.0}

event: message
data: {"event_type": "final", "data": {...}, "timestamp": 1710000005.0}
```

---

## 配置说明

通过 `.env` 文件配置，所有配置项均可在运行时通过环境变量覆盖。

### 必需配置

| 变量 | 说明 | 示例 |
|------|------|------|
| `SILICONFLOW_API_KEY` | SiliconFlow API Key | `sk-xxx` |
| `NEO4J_URI` | Neo4j 连接地址 | `bolt://localhost:7687` |
| `NEO4J_USER` | Neo4j 用户名 | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j 密码 | `password` |

### 可选配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SILICONFLOW_BASE_URL` | `https://api.siliconflow.cn/v1` | SiliconFlow API 地址 |
| `LLM_MODEL` | `Pro/zai-org/` | LLM 模型名称 |
| `LLM_TEMPERATURE` | `0.1` | LLM 温度参数 |
| `LLM_MAX_TOKENS` | `4096` | LLM 最大 token 数 |
| `EMBEDDING_MODEL` | `Pro/BAAI/bge-m3` | Embedding 模型名称 |
| `EMBEDDING_DIM` | `1024` | Embedding 向量维度 |
| `CHROMA_PATH` | 自动推断 | Chroma 向量库路径 |
| `VECTOR_SEARCH_TOP_K` | `20` | 向量搜索返回数量 |
| `MAX_ITERATION_ROUNDS` | `3` | 最大迭代轮次 |
| `CONVERGENCE_SIMILARITY_THRESHOLD` | `0.80` | 收敛相似度阈值 |
| `LOW_CONFIDENCE_THRESHOLD` | `0.60` | 低置信度阈值 |

---

## 开发指南

### 项目结构设计原则

- **src-layout**：代码位于 `src/agent_service/`，通过 `pyproject.toml` 管理包
- **节点函数**：每个分析阶段对应一个节点函数，返回更新的状态字段（而非完整状态）
- **结构化输出**：使用 `with_structured_output()` + Pydantic 模型，强制 LLM 返回结构化数据
- **工具层**：直接复用 MCP 服务器逻辑（Chroma、Neo4j），避免引入 MCP 协议开销

### 添加新的 LLM 调用

1. 在 `llm/models.py` 中定义 Pydantic 响应模型
2. 在 `llm/client.py` 中添加调用函数
3. 在 `graph/nodes.py` 的对应节点中调用

### 添加新的节点

1. 在 `graph/nodes.py` 中实现节点函数
2. 在 `graph/graph.py` 的 `build_agent_graph()` 中添加节点和边
3. 在 `api/routes.py` 的 `node_order` 中注册

### 调试技巧

- 使用流式接口可实时观察各阶段进度
- `InMemorySaver` Checkpointer 存储在内存中，支持同一 `thread_id` 的会话恢复
- LangGraph 支持时间旅行调试，详见 [官方文档](https://langchain-ai.github.io/langgraph/concepts/persistence/)

---

## 相关文档

| 文档 | 说明 |
|------|------|
| [DEPLOY.md](DEPLOY.md) | 完整部署指南（Docker、生产环境） |
| [LangGraph 文档](https://langchain-ai.github.io/langgraph/) | LangGraph 官方文档 |
| [LangChain 文档](https://python.langchain.com/docs) | LangChain 官方文档 |
