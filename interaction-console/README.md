# Interaction Console

独立 Agent 交互控制台。第一版使用 Python + FastAPI 提供网页 UI，作为浏览器与现有 `agent-service-DeepAgents` 的交互网关：代理上游 `POST /api/v1/recommend` SSE 流，解析 DeepAgents 过程事件，并转换为 Claude Code 风格的可观察事件时间线。

---

## 目录

- [项目概述](#项目概述)
- [技术架构](#技术架构)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [API 说明](#api-说明)
- [事件标准化逻辑](#事件标准化逻辑)
- [前端交互逻辑](#前端交互逻辑)
- [配置说明](#配置说明)
- [测试与验证](#测试与验证)
- [注意事项](#注意事项)

---

## 项目概述

### 目标

为 `agent-service-DeepAgents` 提供一个独立浏览器交互界面，让用户不仅能看到最终推荐结果，还能观察完整执行过程，例如：

> 我想分析南京分行的小微企业贷款风险

交互控制台会按以下流程处理：

1. **选择 Agent**：第一版内置 `theme-template-recommendation-deepagents`。
2. **发送用户输入**：浏览器调用本服务 `POST /api/chat/stream`。
3. **代理上游 SSE**：本服务转发到上游 `http://localhost:8000/api/v1/recommend`。
4. **事件标准化**：解析 Skill 加载、middleware、模型消息、工具调用、工具返回和 HITL interrupt。
5. **时间线展示**：前端按事件类型渲染 Claude Code 风格时间线，并提供 raw JSON 详情。
6. **用户确认继续执行**：遇到 DeepAgents interrupt 时，前端渲染表单；提交后用同一 `thread_id` 继续调用上游。

### 核心能力

| 能力 | 说明 |
| --- | --- |
| 独立 Web UI | 使用 FastAPI 直接服务静态 HTML/CSS/JavaScript，无 Node/npm 依赖 |
| Agent 网关 | 作为浏览器和 `agent-service-DeepAgents` 之间的交互代理 |
| SSE 解析 | 消费上游 SSE，并过滤 `: ping` heartbeat |
| 统一事件模型 | 将上游混杂 chunk 转换为 `skill_loaded`、`tool_use`、`interrupt` 等稳定事件 |
| 过程可观察 | 时间线展示 Skill、middleware、assistant message、工具调用和返回 |
| Assistant Markdown | `assistant_message` 使用本地 vendored `markdown-it` + `DOMPurify` 安全渲染 Markdown |
| HITL 表单 | 将 `action_requests` + `review_configs` 渲染为 checkbox/radio/freeform 表单 |
| 本地会话 | 使用浏览器 `localStorage` 保存当前 `agent_id`、`thread_id` 和事件列表 |

---

## 技术架构

```text
┌──────────────────────────────────────────────────────────────┐
│ Browser UI                                                    │
│ - Agent 列表 / Session                                        │
│ - 事件时间线                                                   │
│ - assistant message Markdown 渲染                              │
│ - 当前事件 raw JSON                                            │
│ - interrupt 表单                                               │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│ Interaction Console FastAPI                                   │
│ - GET  /                                                       │
│ - GET  /api/agents                                             │
│ - POST /api/chat/stream 标准化 SSE                             │
│ - GET  /api/sessions/{thread_id}/events                        │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│ 事件适配层 src/interaction_console/                            │
│ - upstream_client.py 调用上游 DeepAgents SSE                   │
│ - event_normalizer.py 解析真实 chunk 并生成 ConsoleEvent        │
│ - agent_registry.py 声明可用 Agent                             │
│ - session_store.py 保存本进程内事件缓存                         │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│ agent-service-DeepAgents                                      │
│ - POST /api/v1/recommend                                      │
│ - DeepAgents Skill / tools / interrupt / Markdown 文本输出      │
└──────────────────────────────────────────────────────────────┘
```

本工程不实现主题模板推荐业务逻辑，只做交互网关、事件标准化和浏览器展示。推荐逻辑仍由上游 `agent-service-DeepAgents` 负责。

---

## 目录结构

```text
interaction-console/
├── README.md
├── pyproject.toml                 # 独立 Python 工程依赖
├── .env.example                   # 本服务环境变量示例
├── .gitignore
├── scripts/
│   └── run.sh                     # 使用 interaction-console/venv 启动
├── src/interaction_console/
│   ├── __init__.py
│   ├── main.py                    # FastAPI 应用入口和 API 路由
│   ├── config.py                  # 环境变量与路径配置
│   ├── schemas.py                 # API 与 ConsoleEvent Pydantic 模型
│   ├── agent_registry.py          # Agent 列表；第一版只包含 DeepAgents 推荐 Agent
│   ├── upstream_client.py         # httpx 流式调用上游 /api/v1/recommend
│   ├── event_normalizer.py        # 上游 SSE chunk 到统一事件模型的转换
│   ├── session_store.py           # 进程内事件缓存
│   └── static.py                  # 静态资源挂载和首页响应
├── web/
│   ├── index.html                 # 页面布局
│   └── assets/
│       ├── app.js                 # 前端状态、SSE 消费、事件渲染、interrupt 表单
│       ├── styles.css             # 页面样式
│       └── vendor/                # 本地 vendored 前端依赖：markdown-it、DOMPurify
└── tests/
    └── test_event_normalizer.py   # 事件标准化单元测试
```

`interaction-console/` 是独立工程目录，拥有自己的 `venv/`。不要使用系统 Python 直接启动，也不要复用 `agent-service/venv` 或 `agent-service-DeepAgents/venv`。

---

## 快速开始

### 前置依赖

- Python 3.11+
- 已可启动的上游 `agent-service-DeepAgents`
- 上游服务默认监听 `http://localhost:8000`

本工程必须使用自己的虚拟环境：

```bash
/Users/yyzz/Desktop/MyClaudeCode/theme-template-recommendation/interaction-console/venv
```

### 1. 创建 venv 并安装依赖

```bash
cd /Users/yyzz/Desktop/MyClaudeCode/theme-template-recommendation/interaction-console
python3.11 -m venv venv
venv/bin/python -m pip install -e .
```

如需运行测试：

```bash
venv/bin/python -m pip install -e '.[test]'
```

### 2. 启动上游 DeepAgents 服务

```bash
cd /Users/yyzz/Desktop/MyClaudeCode/theme-template-recommendation/agent-service-DeepAgents
venv/bin/python -m uvicorn agent_service.main:app --host 0.0.0.0 --port 8000 --reload
```

上游服务默认提供：

- API：<http://localhost:8000>
- 推荐接口：`POST http://localhost:8000/api/v1/recommend`

### 3. 启动 Interaction Console

```bash
cd /Users/yyzz/Desktop/MyClaudeCode/theme-template-recommendation/interaction-console
venv/bin/python -m interaction_console.main
```

或使用脚本：

```bash
cd /Users/yyzz/Desktop/MyClaudeCode/theme-template-recommendation/interaction-console
./scripts/run.sh
```

服务默认监听：

- Web UI：<http://localhost:5174>
- Agent 列表：<http://localhost:5174/api/agents>

### 4. 浏览器验证

打开：

```text
http://localhost:5174
```

输入：

```text
我想分析南京分行的小微企业贷款风险
```

期望时间线能看到：

- Skill 加载事件。
- `[主题和模板推荐] 开始执行` 等 assistant message。
- `search_indicators_by_vector` 工具调用和返回。
- `batch_get_indicator_themes` 工具调用和返回。
- `AskUserQuestion_tools` 相关工具调用或 interrupt。
- interrupt 表单，包含候选筛选条件和候选分析维度。

---

## API 说明

### 页面入口

```http
GET /
```

返回静态网页 UI。

### Agent 列表

```http
GET /api/agents
```

返回示例：

```json
[
  {
    "id": "theme-template-recommendation-deepagents",
    "name": "魔数师主题模板推荐 Agent（DeepAgents）",
    "description": "调用 agent-service-DeepAgents 的主题模板推荐能力",
    "upstream_url": "http://localhost:8000/api/v1/recommend"
  }
]
```

### 会话事件缓存

```http
GET /api/sessions/{thread_id}/events
```

返回当前进程内缓存的标准化事件列表。该缓存只用于第一版调试和刷新恢复辅助，不是持久化存储。

### 流式对话

```http
POST /api/chat/stream
```

请求体：

```json
{
  "agent_id": "theme-template-recommendation-deepagents",
  "thread_id": "thread-001",
  "user_input": "我想分析南京分行的小微企业贷款风险"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `agent_id` | string | 是 | 目标 Agent ID；第一版固定为 `theme-template-recommendation-deepagents` |
| `thread_id` | string | 是 | 会话线程 ID；interrupt 提交后必须保持同一个值 |
| `user_input` | string | 是 | 用户首次问题、确认回复或补充说明 |

内部转发到上游：

```http
POST http://localhost:8000/api/v1/recommend
```

上游请求体：

```json
{
  "thread_id": "thread-001",
  "user_input": "我想分析南京分行的小微企业贷款风险"
}
```

返回标准化 SSE。单条事件 envelope 示例：

```json
{
  "type": "tool_use",
  "thread_id": "thread-001",
  "agent_id": "theme-template-recommendation-deepagents",
  "seq": 12,
  "timestamp": "2026-06-17T00:00:00+00:00",
  "payload": {
    "tool_name": "search_indicators_by_vector",
    "args": {
      "query": "小微企业贷款风险",
      "top_k": 20
    },
    "id": "call_1"
  },
  "raw": {}
}
```

---

## 事件标准化逻辑

核心实现在 `src/interaction_console/event_normalizer.py`。

### 统一事件类型

| 类型 | 说明 |
| --- | --- |
| `skill_loaded` | 由 `SkillsMiddleware.before_agent.skills_metadata` 转换而来 |
| `middleware` | 由 `*Middleware.*` 过程 chunk 转换而来 |
| `assistant_message` | 由 `model.messages[*].content` 中的非空文本转换而来 |
| `tool_use` | 由 `model.messages[*].tool_calls[]` 转换而来 |
| `tool_result` | 由 `tools.messages[]` 转换而来 |
| `interrupt` | 由 `action_requests` + `review_configs` 转换而来 |
| `error` | 由 `status=error` 或代理异常转换而来 |
| `raw` | 未知结构或解析失败时保留原始数据 |
| `done` | 上游流结束后由本服务补充输出 |

### 上游 SSE 处理规则

- `: ping ...` heartbeat 不作为业务事件展示。
- `data: [DONE]` 转换为 `done`。
- JSON `data` 会进入结构化解析。
- 非 JSON `data` 或未知结构会输出为 `raw`，避免丢数据。

### LangChain 字符串化对象解析

当前上游可能返回字符串化对象，例如：

```text
content='[主题和模板推荐] 开始执行' ... tool_calls=[{'name': 'search_indicators_by_vector', ...}]
```

或：

```text
content='{...}' name='search_indicators_by_vector' ... tool_call_id='call_xxx'
```

第一版解析策略：

1. 使用正则提取 `content='...'` 或 `content="..."`。
2. 使用 `ast.literal_eval` 尝试解析 `tool_calls=[...]`。
3. 工具返回的 `content` 优先尝试 `json.loads`。
4. 任一解析失败时保留 `raw`，不让事件流中断。

### Interrupt 转换

上游真实形态：

```json
{
  "action_requests": [
    {
      "name": "AskUserQuestion_tools",
      "args": {
        "interrupt_type": "dimension_and_filters_confirmation",
        "thread_id": "thread-001",
        "sections": []
      }
    }
  ],
  "review_configs": [
    {
      "allowed_decisions": ["approve", "edit", "reject", "respond"]
    }
  ]
}
```

标准化后：

```json
{
  "type": "interrupt",
  "payload": {
    "interrupt_type": "dimension_and_filters_confirmation",
    "thread_id": "thread-001",
    "sections": [],
    "action_request": {},
    "review_config": {},
    "allowed_decisions": ["approve", "edit", "reject", "respond"]
  }
}
```

---

## 前端交互逻辑

核心实现在 `web/assets/app.js`。

### 页面区域

| 区域 | 说明 |
| --- | --- |
| 左侧 | Agent 列表、当前 `thread_id`、新会话和清空事件按钮 |
| 中间 | 事件时间线和底部输入框 |
| 右侧 | 当前选中事件的完整 JSON 详情 |

### 事件展示

| 事件 | 展示方式 |
| --- | --- |
| `skill_loaded` | 显示已加载 Skill 数量，可展开 metadata |
| `assistant_message` | 时间线正文使用 `markdown-it` 渲染 Markdown，并经 `DOMPurify` 清洗；右侧仍显示 JSON 详情 |
| `tool_use` | 显示工具名，默认折叠参数 JSON |
| `tool_result` | 显示工具名，默认折叠返回 JSON |
| `interrupt` | 渲染用户确认表单 |
| `middleware` / `raw` | 作为调试事件折叠展示 |
| `error` | 显示错误信息 |

### 前端 vendored 依赖

`assistant_message` 的 Markdown 渲染使用本地 vendored 前端依赖，不依赖 CDN 或 Node/npm 构建链：

| 库 | 版本 | 本地文件 | 来源 | License |
| --- | --- | --- | --- | --- |
| `markdown-it` | `14.1.0` | `web/assets/vendor/markdown-it/markdown-it.min.js` | `https://cdn.jsdelivr.net/npm/markdown-it@14.1.0/dist/markdown-it.min.js` | MIT |
| `DOMPurify` | `3.2.6` | `web/assets/vendor/dompurify/purify.min.js` | `https://cdn.jsdelivr.net/npm/dompurify@3.2.6/dist/purify.min.js` | Apache-2.0 OR MPL-2.0 |

安全策略：

- `markdown-it` 配置 `html: false`，不渲染原始 HTML。
- Markdown 输出进入 DOM 前先经过 `DOMPurify.sanitize`。
- 渲染后统一处理链接，只允许 `http:`、`https:`、`mailto:`、`tel:` 协议，并为链接添加 `target="_blank"` 与 `rel="noopener noreferrer nofollow"`。
- 不启用 mermaid、KaTeX、语法高亮等二次渲染插件。

升级方式：下载固定版本浏览器构建文件覆盖上述本地文件，并同步更新本节版本、来源和安全验证结果。

### Interrupt 表单规则

前端根据 `payload.sections` 渲染表单：

| 字段 | 渲染方式 |
| --- | --- |
| `select_mode = multiple` | checkbox |
| `select_mode = single` 或缺省 | radio |
| `allow_freeform = true` | 补充说明 textarea |

提交时不会改变上游 DeepAgents resume 契约。前端会把用户选择组织成自然语言，再用同一个 `thread_id` 调用：

```http
POST /api/chat/stream
```

提交文本示例：

```text
User has answered your questions:
已确认以下内容：
候选筛选条件：已确认 南京分行
候选分析维度：已确认 小微客户贷款余额、小微客户不良贷款余额
补充说明：无
请继续
```

### 本地状态

浏览器 `localStorage` 保存：

- `interaction.agent_id`
- `interaction.thread_id`
- `interaction.events`

刷新页面后可以恢复本地事件视图。第一版没有数据库持久化。

---

## 配置说明

配置由 `src/interaction_console/config.py` 读取，并会加载 `interaction-console/.env`。

### 常用配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `INTERACTION_CONSOLE_HOST` | `0.0.0.0` | 本服务监听地址 |
| `INTERACTION_CONSOLE_PORT` | `5174` | 本服务监听端口 |
| `DEEPAGENTS_BASE_URL` | `http://localhost:8000` | 上游 DeepAgents 服务 base URL |

`.env.example`：

```env
INTERACTION_CONSOLE_HOST=0.0.0.0
INTERACTION_CONSOLE_PORT=5174
DEEPAGENTS_BASE_URL=http://localhost:8000
```

上游实际推荐接口由 `DEEPAGENTS_BASE_URL` 拼接得到：

```text
{DEEPAGENTS_BASE_URL}/api/v1/recommend
```

---

## 测试与验证

### 运行单元测试

必须使用 `interaction-console/venv`：

```bash
cd /Users/yyzz/Desktop/MyClaudeCode/theme-template-recommendation/interaction-console
venv/bin/python -m pytest
```

当前测试重点覆盖：

- `SkillsMiddleware.before_agent` → `skill_loaded`
- `model.messages` 中的 `tool_calls` → `tool_use`
- `tools.messages` → `tool_result`
- `action_requests` → `interrupt`
- 未知结构 → `raw`
- `: ping` heartbeat 被忽略

### API 快速验证

启动本服务后：

```bash
venv/bin/python - <<'PY'
import urllib.request
print(urllib.request.urlopen('http://127.0.0.1:5174/api/agents', timeout=5).read().decode())
print(urllib.request.urlopen('http://127.0.0.1:5174/', timeout=5).status)
PY
```

预期：

- `/api/agents` 返回内置 Agent。
- `/` 返回 HTTP 200。

### 端到端验证

1. 启动 `agent-service-DeepAgents`。
2. 启动 `interaction-console`。
3. 浏览器访问 <http://localhost:5174>。
4. 输入：

```text
我想分析南京分行的小微企业贷款风险
```

5. 检查时间线是否展示 Skill、工具调用、工具返回、interrupt 表单和最终结果。

---

## 注意事项

- 本工程只新增独立交互控制台，不修改 `agent-service-DeepAgents`、`agent-service`、`mcp-server` 或已有 Skill。
- 第一版只内置一个 Agent：`theme-template-recommendation-deepagents`。
- `thread_id` 是 DeepAgents interrupt 后继续执行的关键；同一轮确认流程必须保持一致。
- interrupt 继续执行仍走上游同一个 `/api/v1/recommend`，不使用单独 `/resume` 接口。
- 进程内 `session_store` 不是数据库；服务重启后会丢失。
- 浏览器 `localStorage` 仅用于本地恢复视图，不代表上游 DeepAgents 状态。
- 上游 SSE 中字符串化 LangChain 对象的格式如果变化，可能需要同步增强 `event_normalizer.py`。
- 如果 README 与代码出现差异，以当前代码为准，并同步更新文档。
