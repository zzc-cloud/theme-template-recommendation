# Theme Template Recommendation Agent Service

基于 LangChain/LangGraph 的主题模板推荐 API 服务，将用户自然语言问题映射到魔数师平台的业务主题与分析模板。

---

## 目录

- [项目概述](#项目概述)
- [技术架构](#技术架构)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [本体层构建](#本体层构建)
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
| 重试机制 | 按错误类型差异化重试（限流/超时/5xx/认证/格式/未知），指数退避 + jitter |
| 会话恢复 | 基于 TTLMemorySaver 的 Checkpointer（1天TTL自动清理） |
| 并发控制 | 基于 Semaphore 的并发上限保护（默认10并发，可配置） |

---

## 技术架构

```
┌──────────────────────────────────────────────────────────────────┐
│                       FastAPI (REST API)                           │
│  POST /api/v1/recommend — 发起推荐（SSE）                           │
│  POST /api/v1/resume    — 恢复执行（SSE）                           │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                     LangGraph Agent Graph                          │
│  extract_phrases → classify_and_iterate → [interrupt]             │
│  wait_for_confirmation → aggregate_themes → complete_indicators   │
│  → judge_themes → retrieve_templates → analyze_templates          │
│  → format_output                                                   │
│                                                                     │
│  [TTLMemorySaver Checkpointer — 会话状态持久化 + 1天TTL自动清理]    │
│  注：wait_for_confirmation 节点会触发 interrupt，暂停执行等待用户确认  │
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
| 持久化 | LangGraph `TTLMemorySaver` Checkpointer（TTL=1天自动清理） |
| 并发控制 | `asyncio.Semaphore`（可配置上限，默认10并发） |
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
│       │   ├── routes.py         # API 路由（SSE 流式）
│       │   └── schemas.py         # Pydantic 请求/响应模型
│       ├── graph/
│       │   ├── __init__.py
│       │   ├── state.py          # LangGraph State 定义（TypedDict）
│       │   ├── nodes.py          # 各阶段节点函数
│       │   └── graph.py          # LangGraph 图构建 + Checkpointer
│       ├── utils/
│       │   ├── __init__.py
│       │   └── ttl_memory_saver.py  # TTLMemorySaver 实现（会话TTL自动清理）
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
├── scripts/                      # 本体层构建脚本（ETL）
│   ├── config.py                 # ETL 配置（MySQL、Neo4j、源表映射）
│   ├── extract_indicators.py     # 魔数师指标层抽取
│   ├── extract_templates.py      # 模板层抽取（INSIGHT / COMBINEDQUERY）
│   ├── build_hierarchy.py        # 层级结构构建
│   ├── neo4j_loader.py           # Neo4j 数据加载器
│   ├── init_ontology.py          # 全量初始化脚本
│   ├── update_ontology.py        # 增量更新脚本
│   ├── .env.example              # 环境变量模板
│   └── README.md                 # 脚本使用说明
├── tests/                        # 测试文件（含并发与TTL清理测试）
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

# 测试流式推荐接口
curl -s -N -X POST http://localhost:8000/api/v1/recommend \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "test-'$(date +%s)'",
    "question": "我想分析南京分行的小微企业贷款风险"
  }'
```

服务运行在 http://localhost:8000
API 文档：http://localhost:8000/docs

---

## 本体层构建

本服务依赖 Neo4j 图数据库存储业务知识图谱（主题、指标、模板）。`scripts/` 目录提供了本体层的 ETL 构建脚本。

### 本体层结构

| 节点类型 | 说明 | 数据来源 |
|---------|------|---------|
| `SECTOR` | 板块 | t_restree |
| `CATEGORY` | 分类 | t_restree |
| `THEME` | 主题（核心） | t_restree |
| `SUBPATH` | 子路径 | t_restree |
| `INDICATOR` | 指标（核心） | t_restree |
| `INSIGHT_TEMPLATE` | 透视分析模板 | T_EXT_INSIGHT |
| `COMBINEDQUERY_TEMPLATE` | 万能查询模板 | T_EXT_COMBINEDQUERY |

| 关系类型 | 说明 |
|---------|------|
| `HAS_CHILD` | 层级导航（树形结构） |
| `CONTAINS` | 模板包含指标（带 position 属性） |

### 首次初始化

```bash
cd scripts

# 1. 配置环境变量
cp .env.example .env
# 编辑 .env 填写 MySQL 和 Neo4j 连接信息

# 2. 执行全量初始化
python init_ontology.py
```

执行流程：
1. 测试数据库连接
2. 抽取魔数师指标层数据（~17万条）
3. 构建层级结构
4. 抽取模板数据（INSIGHT + COMBINEDQUERY）
5. 计算模板热度
6. 创建 Neo4j 约束和索引
7. 导入节点和关系
8. 清理临时板块

### 增量更新（每月执行）

```bash
# 自动读取上次更新时间
python update_ontology.py

# 指定更新时间
python update_ontology.py --last-update "2024-01-01 00:00:00"

# 强制全量更新
python update_ontology.py --full
```

### 自动化调度

```bash
# crontab 配置：每月 1 日凌晨 2 点执行
0 2 1 * * cd /path/to/agent-service/scripts && python3 update_ontology.py >> logs/update.log 2>&1
```

### 验证

```cypher
// Neo4j Browser 查询
// 查看节点统计
MATCH (n)
RETURN labels(n)[0] as type, count(n) as count
ORDER BY count DESC;

// 查看热门模板
MATCH (t:INSIGHT_TEMPLATE)
WHERE t.heat > 0
RETURN t.alias, t.heat, t.theme_id
ORDER BY t.heat DESC
LIMIT 10;
```

详细说明请参考 [scripts/README.md](scripts/README.md)。

---

## API 参考

### 健康检查

```
GET /health
```

返回服务状态及依赖服务（Neo4j）连接状态。

### 内存状态检查

```
GET /health/memory
```

返回 TTLMemorySaver 的会话内存状态，包含活跃/过期 thread 数量等指标。

```json
{
  "status": "ok",
  "ttl_seconds": 86400,
  "total_threads": 10,
  "active_threads": 8,
  "expired_threads": 2
}
```

| 字段 | 说明 |
|------|------|
| `ttl_seconds` | TTL 超时时间（秒），默认 86400（1天） |
| `total_threads` | 当前注册的全部 thread 数量 |
| `active_threads` | 仍在 TTL 有效期内的 thread 数量 |
| `expired_threads` | 已过期但尚未被清理的 thread 数量 |

### 流式推荐

```
POST /api/v1/recommend
```

**请求体**

```json
{
  "thread_id": "550e8400-e29b-41d4-a716-446655440000",
  "question": "我想分析南京分行的小微企业贷款风险",
  "top_k_themes": 3,
  "top_k_templates": 5,
  "template_type": null
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `thread_id` | string | ✅ | 会话唯一标识，由前端生成（推荐 UUID），每个新问题必须使用全新的 thread_id |
| `question` | string | ✅ | 用户自然语言问题，长度 1~500 字符 |
| `top_k_themes` | int | ❌ | 返回的主题数量上限，默认 3，范围 1~10 |
| `top_k_templates` | int | ❌ | 每种类型返回的模板数量上限，默认 5，范围 1~20 |
| `template_type` | string | ❌ | 模板类型过滤：INSIGHT / COMBINEDQUERY / null（全部） |

返回 SSE 流，包含以下事件：

| 事件类型 | 说明 |
|---------|------|
| `stage_complete` | 节点完成事件，含可选 `markdown` 进度文字 |
| `progress` | 节点内部细粒度进度（含预渲染 `markdown` 和原始 `raw` 数据） |
| `interrupt` | 需要用户确认（维度确认 或 低置信度换词） |
| `final` | 最终推荐结果 |
| `error` | 错误信息 |

### 恢复执行

```
POST /api/v1/resume
```

当 `/recommend` 流中收到 `interrupt` 事件后，用户完成维度确认，调用此接口恢复执行。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `thread_id` | string | ✅ | 与 /recommend 请求完全相同的 thread_id |
| `confirmed_dimensions` | array\<string\> | ✅ | 用户勾选的分析维度（search_term 值） |
| `confirmed_question` | string | ❌ | 用户确认/修改的问题描述，空则使用规范化问题 |

**请求示例**

```json
{
  "thread_id": "550e8400-e29b-41d4-a716-446655440000",
  "confirmed_dimensions": ["小微企业贷款", "不良率"],
  "confirmed_question": "分析南京分行2024年小微企业贷款不良率"
}
```

**示例**

```
event: message
data: {"event_type": "stage_complete", "stage": "extract_phrases", "markdown": null, "timestamp": 1710000000.0}

event: message
data: {"event_type": "progress", "markdown": "│ **[0.2] 词组分类** 正在执行...", "raw": {"stage": "classify_and_iterate", "step": "classifying", "status": "in_progress"}, "timestamp": 1710000001.0}

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
| `LLM_MODEL` | `Pro/zai-org/GLM-5` | LLM 模型名称 |
| `LLM_TEMPERATURE` | 0.7 | LLM 温度参数 |
| `LLM_MAX_TOKENS` | 4096 | LLM 最大 token 数 |
| `EMBEDDING_MODEL` | `Qwen/Qwen3-Embedding-8B` | Embedding 模型名称 |
| `EMBEDDING_DIM` | 1024 | Embedding 向量维度 |
| `CHROMA_PATH` | 自动推断 | Chroma 向量库路径 |
| `VECTOR_SEARCH_TOP_K` | 20 | 向量搜索返回数量 |
| `MAX_ITERATION_ROUNDS` | 3 | 最大迭代轮次 |
| `CONVERGENCE_SIMILARITY_THRESHOLD` | 0.80 | 收敛相似度阈值 |
| `LOW_CONFIDENCE_THRESHOLD` | 0.60 | 低置信度阈值 |
| `MAX_CONCURRENT_REQUESTS` | 10 | 最大并发请求数（超限返回429） |
| `CONCURRENT_TIMEOUT_SECONDS` | 5.0 | 等待信号量的超时时间（秒） |
| `LLM_CALL_TIMEOUT_SECONDS` | 60.0 | LLM 单次调用超时时间（秒） |
| `LLM_MAX_RETRIES_*` | 按类型 | 各错误类型的最大重试次数（见下表） |
| `LLM_BASE_DELAY_*` | 按类型 | 各错误类型的初始退避延迟（秒） |
| `LLM_MAX_DELAY_*` | 按类型 | 各错误类型的最大退避延迟（秒） |

### ETL 脚本配置（scripts/.env）

本体层构建脚本需要额外的 MySQL 配置：

| 变量 | 说明 | 示例 |
|------|------|------|
| `MYSQL_HOST` | MySQL 主机 | `localhost` |
| `MYSQL_PORT` | MySQL 端口 | `3306` |
| `MYSQL_USER` | MySQL 用户名 | `root` |
| `MYSQL_PASSWORD` | MySQL 密码 | `password` |
| `MYSQL_DATABASE` | 数据库名 | `chatbi_metadata` |

> **注意**：ETL 脚本的 `.env` 文件位于 `scripts/` 目录下，与服务主配置分离。

### LLM 重试配置

`invoke_structured()` 内置按错误类型差异化的重试机制，通过环境变量集中管理：

| 错误类型 | 识别关键词 | 最大重试 | 初始延迟 | 最大延迟 |
|---------|-----------|---------|---------|---------|
| `RATE_LIMIT` | 429, rate limit, too many | 3 | 5.0s | 60s |
| `TIMEOUT` | timeout, timed out | 2 | 2.0s | 10s |
| `SERVER_ERROR` | 500, 502, 503, 504 | 2 | 1.0s | 8s |
| `SCHEMA_ERROR` | validation, schema, parse, json | 1 | 0.5s | 2s |
| `AUTH_ERROR` | 401, 403, unauthorized | 0 | - | - |
| `UNKNOWN` | 其他 | 1 | 1.0s | 5s |

**关键设计**：
- `AUTH_ERROR` 不重试（401/403 重试无意义，立即失败）
- `RATE_LIMIT` 退避最长（5s 起），避免加剧限流
- 指数退避 + 20% jitter，防止多个并发请求同时重试造成惊群效应
- 所有参数均可通过环境变量覆盖，无需修改代码

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

> ⚠️ **不要使用 `invoke_llm_json()`**，该函数已废弃。所有 LLM 调用必须通过 `invoke_structured()` 或专用函数（`extract_phrases`、`classify_phrases` 等）。

### 添加新的节点

1. 在 `graph/nodes.py` 中实现节点函数
2. 在 `graph/graph.py` 的 `build_agent_graph()` 中添加节点和边
3. 在 `api/routes.py` 的 `node_order` 中注册

### 调试技巧

- 使用流式接口可实时观察各阶段进度
- `TTLMemorySaver` Checkpointer 存储在内存中，支持同一 `thread_id` 的会话恢复，超过 1 天未活跃的 thread 自动清理
- LangGraph 支持时间旅行调试，详见 [官方文档](https://langchain-ai.github.io/langgraph/concepts/persistence/)
- 使用 `GET /health/memory` 接口监控当前会话内存状态

### TTL Memory 管理

服务内置基于 `TTLMemorySaver` 的自动内存管理机制：

| 参数 | 值 | 说明 |
|------|----|------|
| TTL | 86400 秒（1天） | thread 超过此时间未活跃则标记为过期 |
| 清理间隔 | 600 秒（10分钟） | 后台任务周期性执行清理 |
| 线程安全 | 是 | 使用 `Lock` 保护时间戳字典 |

**清理机制**：
- 每次 `put()`（写入 checkpoint）时更新 thread 的最后活跃时间
- 后台协程每 10 分钟扫描所有 thread，超出 TTL 的自动清理
- 清理范围：`storage`、`writes`、`_timestamps` 三处数据

**监控接口**：
```bash
curl http://localhost:8000/health/memory
```

**线程安全性**：
- `put()` / `aput()` 时记录时间戳（带锁）
- `cleanup_expired()` 遍历 + 删除（带锁）
- `stats()` 查询统计（带锁）
- 所有对 `_timestamps` 的读写均受锁保护

### 并发控制

服务内置基于 `asyncio.Semaphore` 的并发保护机制：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `MAX_CONCURRENT_REQUESTS` | 10 | 最大并发请求数上限 |
| `CONCURRENT_TIMEOUT_SECONDS` | 5.0 | 等待信号量的超时时间（秒） |

**处理机制**：
- 当前并发数 >= 上限时，新请求快速返回 429（Too Many Requests）
- 未满载时，请求等待获取信号量，超时返回 429
- 无论请求成功或异常，信号量都会在 `finally` 中释放

**监控接口**：
```bash
curl http://localhost:8000/health
```

**响应示例**：
```json
{
  "status": "healthy",
  "concurrency": {
    "current": 3,
    "max": 10,
    "available": 7
  }
}
```

**前端注意事项**：
- 收到 429 响应时，展示"系统繁忙，请稍后重试"
- 可实现指数退避重试策略
- 详见 [API.md](API.md) 第 11 节

### LLM 重试机制

LLM 调用通过 `invoke_structured()` 实现，核心特性：

**错误分类**：6 种类型，差异化处理

| 类型 | 说明 | 重试策略 |
|------|------|---------|
| `RATE_LIMIT` | 429 限流 | 长退避（5s起），最多3次 |
| `TIMEOUT` | 调用超时 | 中等退避（2s起），最多2次 |
| `SERVER_ERROR` | 5xx 错误 | 短退避（1s起），最多2次 |
| `SCHEMA_ERROR` | 结构化输出格式错误 | 极短退避（0.5s起），最多1次 |
| `AUTH_ERROR` | 401/403 认证错误 | 不重试，立即失败 |
| `UNKNOWN` | 其他未知错误 | 短退避（1s起），最多1次 |

**重试流程**：
1. 每次调用通过 `ThreadPoolExecutor` 包装，强制超时（默认 60s）
2. 失败后分类错误类型，查询重试配置
3. 计算延迟：`min(base_delay × 2^attempt, max_delay) + jitter`
4. 等待后重试，最多尝试 `1 + max_retries` 次
5. 最终失败抛出 `RuntimeError`，包含错误类型和原因

**无需额外处理**：所有 LLM 调用（`extract_phrases`、`classify_phrases` 等）已内置重试，无需在业务代码中处理。

---

## 相关文档

| 文档 | 说明 |
|------|------|
| [API 对接文档](API.md) | 完整 API 对接指南（SSE 事件、请求/响应示例、前端集成） |
| [DEPLOY.md](DEPLOY.md) | 完整部署指南（Docker、生产环境） |
| [scripts/README.md](scripts/README.md) | 本体层构建脚本使用说明（ETL、初始化、增量更新） |
| [LangGraph 文档](https://langchain-ai.github.io/langgraph/) | LangGraph 官方文档 |
| [LangChain 文档](https://python.langchain.com/docs) | LangChain 官方文档 |
