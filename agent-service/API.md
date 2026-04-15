# 主题模板推荐服务 API 对接文档

> **版本**：v1.6（迭代精炼机制重构：收敛判定客观化、LLM 职责单一化、规范化问题只生成一次）
> **协议**：HTTP + SSE（Server-Sent Events）
> **Base URL**：`http://{host}/api/v1`

---

## 目录

- [接口总览](#1-接口总览)
- [核心概念：SSE 流式通信](#2-核心概念sse-流式通信)
- [接口一：发起推荐 /recommend](#3-接口一发起推荐-recommend)
- [接口二：恢复执行 /resume](#4-接口二恢复执行-resume)
- [SSE 事件类型完整说明](#5-sse-事件类型完整说明)
- [人机交互环节详解](#6-人机交互环节详解)
- [多轮对话（追问）](#7-多轮对话追问)
- [完整交互时序图](#8-完整交互时序图)
- [前端状态机设计](#9-前端状态机设计)
- [错误处理](#10-错误处理)
- [并发控制说明](#11-并发控制说明)
- [完整调用示例](#12-完整调用示例)

---

## 1. 接口总览

### 流式接口（SSE）

| 接口 | 方法 | 说明 | 何时调用 |
|------|------|------|----------|
| `/api/v1/recommend` | POST | 发起推荐，建立 SSE 流 | 用户提交新问题时 |
| `/api/v1/resume` | POST | 恢复执行，建立 SSE 流 | 用户完成维度确认后 |

> **重要**：两个流式接口均返回 SSE 流，不是普通 JSON 响应。前端必须使用流式读取方式处理。


### 健康检查接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查（根路径，无 /api/v1 前缀） |
| `/health/memory` | GET | 内存状态检查（TTL=1天） |

---

## 2. 核心概念：SSE 流式通信

### 2.1 什么是 SSE

服务端在处理过程中会持续推送事件，前端需要逐行读取，而不是等待一个完整的 JSON 响应。

```
前端发起请求 → 服务端建立长连接 → 服务端逐步推送事件 → 前端实时处理 → 连接关闭
```

### 2.2 SSE 数据格式

每条事件的原始格式如下：

```
event: message\n
data: {"event_type": "stage_complete", "stage": "extract_phrases", ...}\n
\n
```

前端只需关注 `data` 字段内的 JSON 内容，所有业务逻辑均通过 JSON 中的 `event_type` 字段区分。

### 2.3 event_type 一览

| event_type | 含义 | 前端动作 |
|------------|------|----------|
| `stage_complete` | 某个处理阶段完成，含可选 markdown 进度文字 | 更新步骤状态；若 markdown 非 null 则渲染 |
| `progress` | 阶段内部细粒度进度，含预渲染 markdown 文字和原始 raw 数据 | 渲染 markdown 进度描述 |
| `interrupt` | 需要用户确认（两种子类型） | 展示确认界面或换词引导 |
| `final` | 流程完成，携带完整推荐结果（结构化数据） | 展示推荐结果，继续等待 summary |
| `summary` | 自然语言总结内容（在 final 之后推送） | 展示或追加到结果区域 |
| `error` | 发生错误 | 展示错误提示 |

> **重要**：`final` 事件只包含结构化数据（`markdown` 字段为空），自然语言总结内容通过 `summary` 事件在 `final` 之后异步推送。前端收到 `final` 后不应立即关闭 SSE 连接，需继续等待 `summary` 事件。


## 3. 接口一：发起推荐 /recommend

### 请求

```
POST /api/v1/recommend
Content-Type: application/json
```

### 请求体字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `thread_id` | string | ✅ | 会话唯一标识，由前端自行生成（推荐 UUID），每个新问题必须使用全新的 thread_id。<br><br>⚠️ **生命周期约束**：<br>• 同一 thread_id 只能用于"同一问题的 /recommend + /resume"流程，不可复用于新问题<br>• 服务重启后 thread_id 全部失效（TTLMemorySaver 进程内存），需重新生成<br>• 同一 thread_id 超过 1 天未活跃会被自动清理，再次使用会视为新会话 |
| `question` | string | ✅ | 用户自然语言问题，长度 1~500 字符 |
| `top_k_themes` | int | ❌ | 返回主题数量上限，默认 3，范围 1~10 |
| `top_k_templates` | int | ❌ | 返回模板数量上限，默认 5，范围 1~20 |
| `template_type` | string | ❌ | 模板类型过滤：INSIGHT / COMBINEDQUERY / 不传（全部） |
| `context` | object | ❌ | 多轮追问时传入，携带上一轮的关键信息（详见第7节） |

### 请求示例

```json
{
  "thread_id": "550e8400-e29b-41d4-a716-446655440000",
  "question": "我想分析南京分行的小微企业贷款不良率",
  "top_k_themes": 3,
  "top_k_templates": 5
}
```

### context 字段结构（追问时使用）

```json
{
  "thread_id": "新生成的-uuid-0002",
  "question": "那对公贷款呢？",
  "context": {
    "previous_question": "我想分析南京分行的小微企业贷款不良率",
    "previous_normalized_question": "分析南京分行2024年小微企业贷款不良率",
    "previous_filter_indicators": [
      { "alias": "二级账务机构名称", "value": "南京分行" },
      { "alias": "数据日期", "value": "2024年" }
    ],
    "previous_dimensions": ["小微企业贷款", "不良率"]
  }
}
```

---

## 4. 接口二：恢复执行 /resume

用户在确认界面完成选择后调用此接口，携带与首次请求相同的 thread_id。

### 请求

```
POST /api/v1/resume
Content-Type: application/json
```

### 请求体字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `thread_id` | string | ✅ | 与 /recommend 请求中完全相同的 thread_id |
| `confirmed_dimensions` | array\<string\> | ✅ | 用户勾选的分析维度列表，值为 search_term（来自 interrupt 事件中的 `dimension_options[].search_term`） |

### 请求示例

```json
{
  "thread_id": "550e8400-e29b-41d4-a716-446655440000",
  "confirmed_dimensions": ["小微企业贷款", "不良率"]
}
```

---

## 5. SSE 事件类型完整说明

### 5.1 stage_complete — 阶段完成

```json
{
  "event_type": "stage_complete",
  "stage": "aggregate_themes",
  "markdown": "│ ✅ **[1.1]** 候选主题聚合完成",
  "timestamp": 1718000000.123
}
```

**stage_complete 字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `stage` | string | 完成的阶段节点名称 |
| `markdown` | string \| null | 该阶段的进度文字，部分阶段有值（见下表），部分为 null（由 progress 事件覆盖） |
| `timestamp` | number | 事件时间戳 |

**stage 值对应关系**：

**哪些 stage 的 stage_complete.markdown 有值**：

| stage 值 | 含义 | markdown 值 |
|----------|------|-------------|
| `extract_phrases` | 词组提取完成 | null（由 progress 事件覆盖） |
| `classify_and_iterate` | 迭代精炼完成 | null（由 progress 事件覆盖） |
| `wait_for_confirmation` | 确认节点 | null（由 interrupt 事件覆盖） |
| `aggregate_themes` | 主题聚合完成 | `"│ ✅ [1.1] 候选主题聚合完成"` |
| `complete_indicators` | 指标补全完成 | `"│ ✅ [1.2] 全量指标补全完成"` |
| `judge_themes` | 主题裁决完成 | null（由 progress 事件覆盖） |
| `retrieve_templates` | 模板检索完成 | `"│ ✅ [2.1] 模板检索完成"` |
| `analyze_templates` | 模板可用性分析完成 | null（由 progress 事件覆盖） |
| `format_output` | 结果格式化完成 | `"\n✅ 所有阶段执行完毕，正在生成推荐结果..."` |

> **前端建议**：维护一个步骤列表，收到对应 stage 时将该步骤标记为"已完成"。若 markdown 非 null，可渲染该文字。

### 5.2 progress — 阶段内部进度

迭代精炼阶段（classify_and_iterate）的 progress 事件包含多种 step：

**step == "searching"**：第 N 轮开始搜索
```json
{
  "event_type": "progress",
  "markdown": "│ **[0.3] 第 1 轮迭代精炼**\n│   🔍 搜索词：`小微企业贷款`、`不良率`",
  "raw": {
    "stage": "classify_and_iterate",
    "step": "searching",
    "round": 1,
    "concepts": ["小微企业贷款", "不良率"]
  },
  "timestamp": 1718000000.456
}
```

**step == "converged"**：第 N 轮收敛判定完成
```json
{
  "event_type": "progress",
  "markdown": "│   ✅ 本轮收敛：`不良率`，剩余待精炼：1 个",
  "raw": {
    "stage": "classify_and_iterate",
    "step": "converged",
    "round": 1,
    "newly_converged": ["不良率"],
    "converged_count": 1,
    "pending_count": 1
  },
  "timestamp": 1718000000.789
}
```

**step == "evaluating"**：第 N 轮 LLM 精炼搜索词
```json
{
  "event_type": "progress",
  "markdown": "│   🤖 LLM 精炼第 1 轮搜索词...",
  "raw": {
    "stage": "classify_and_iterate",
    "step": "evaluating",
    "round": 1
  },
  "timestamp": 1718000001.012
}
```

**step == "completed"**：迭代精炼完成
```json
{
  "event_type": "progress",
  "markdown": "│ ✅ 迭代精炼完成，共 **2** 轮，**3** 个维度已收敛\n└─────────────────────────────────────────",
  "raw": {
    "stage": "classify_and_iterate",
    "step": "completed",
    "iterations": 2,
    "converged_count": 3,
    "low_confidence": false
  },
  "timestamp": 1718000002.345
}
```

> 低置信度出口时的 completed 事件：`low_confidence: true`，markdown 显示"部分维度未能收敛，进入低置信度流程"。

**progress 字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `markdown` | string | 服务端预渲染的 Markdown 格式进度文字，前端可直接渲染（追加到聊天气泡或日志区域），无需自行拼接 |
| `raw` | object | 原始节点数据，包含 stage、step 等；step == "completed" 时含 converged_count 和 low_confidence |
| `timestamp` | number | 事件时间戳 |

> **前端建议**：可直接将 markdown 字段的内容追加到进度展示区域，无需自行拼接进度文字。raw 字段可忽略或用于实现自定义进度条。

### 5.3 interrupt — ⚠️ 需要用户确认（最重要）

这是人机交互的核心事件。收到此事件后，前端必须停止等待，展示确认界面。

```json
{
  "event_type": "interrupt",
  "thread_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "waiting_confirmation",
  "pending_confirmation": {
    "message": "以下筛选条件已自动识别，请确认分析维度：",
    "filter_display": [
      {
        "alias": "二级账务机构名称",
        "value": "南京分行",
        "type": "机构筛选指标"
      },
      {
        "alias": "数据日期",
        "value": "2024年",
        "type": "时间筛选指标"
      }
    ],
    "dimension_options": [
      {
        "search_term": "小微企业贷款",
        "converged": true,
        "top_indicator_aliases": ["借据余额", "贷款本金", "贷款笔数", "五级分类", "不良贷款余额"],
        "top_indicators": [
          {
            "id": "INDICATOR.xxx",
            "alias": "借据余额",
            "similarity_score": 0.92
          }
        ]
      },
      {
        "search_term": "不良率",
        "converged": true,
        "top_indicator_aliases": ["不良贷款率", "逾期率", "关注类贷款占比"],
        "top_indicators": [...]
      }
    ],
    "normalized_question": "分析南京分行2024年小微企业贷款不良率"
  }
}
```

**pending_confirmation 字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `message` | string | 展示给用户的提示语 |
| `filter_display` | array | 已自动应用的筛选条件，仅展示，无需用户操作 |
| `filter_display[].alias` | string | 筛选指标名称，如"二级账务机构名称" |
| `filter_display[].value` | string | 筛选值，如"南京分行" |
| `filter_display[].type` | string | 类型：机构筛选指标 / 时间筛选指标 |
| `dimension_options` | array | 待用户确认的分析维度列表，支持多选 |
| `dimension_options[].search_term` | string | 维度标识，调用 /resume 时用此值 |
| `dimension_options[].converged` | bool | 是否高置信度收敛（true 表示匹配质量好） |
| `dimension_options[].top_indicator_aliases` | array\<string\> | 该维度关联的 Top 5 指标别名（从 top_indicators 提取），辅助用户理解 |
| `dimension_options[].top_indicators` | array\<object\> | 该维度关联的 Top 5 指标完整对象，含 id、alias、similarity_score |
| `normalized_question` | string | 预留字段，当前为空。规范化问题在用户确认维度后生成，可由用户在界面手动修改后传回 |

> **注意**：`top_indicator_aliases` 是 `top_indicators` 的 alias 提取，两者内容一一对应。

### 5.3.1 dimension_guidance — 维度勾选引导

当 `pending_confirmation` 中包含 `dimension_guidance` 字段时（多维度场景下会自动生成），前端应在维度选择界面上展示引导提示，帮助用户判断应优先勾选哪些维度。

```json
{
  "has_conflict": true,
  "can_select_all": false,
  "recommended_first": ["小微企业不良贷款率"],
  "conflict_analysis": "「小微企业不良贷款率」命中10个小微考核及不良统计主题，「贷款五级分类」命中19个信用卡/对公/个人信贷主题。加权Jaccard=0.00（<0.5），主题几乎无交集，存在严重主题交叉干扰",
  "selection_advice": "维度间主题无交集（加权 Jaccard < 0.5），同时勾选会导致主题交叉干扰。建议优先勾选核心维度：小微企业不良贷款率。",
  "dimension_analysis": [
    {
      "dimension": "小微企业不良贷款率",
      "matched_themes": [
        {"theme": "科创板块小微业务考核员工统计主题", "weight": 1.85},
        {"theme": "不良生成贷款统计日报_多维度主题", "weight": 1.62}
      ],
      "theme_count": 10,
      "independence_score": 0.9,
      "core_concept_score": 0.95,
      "recommendation": "优先"
    },
    {
      "dimension": "贷款五级分类",
      "matched_themes": [
        {"theme": "信用卡账户", "weight": 2.10},
        {"theme": "外呼营销业务效果（账户）", "weight": 1.45},
        {"theme": "对公贷款借据还款计划主题", "weight": 1.20}
      ],
      "theme_count": 19,
      "independence_score": 0.3,
      "core_concept_score": 0.7,
      "recommendation": "建议后选"
    }
  ]
}
```

**dimension_guidance 字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `has_conflict` | bool | 是否存在主题交叉冲突（**程序化计算**：与 can_select_all 互为反面，即 has_conflict = not can_select_all） |
| `can_select_all` | bool | 所有维度是否可以全部勾选（**程序化计算**：所有两两维度加权 Jaccard 均 >= 阈值） |
| `recommended_first` | array\<string\> | 建议优先勾选的核心维度（由 LLM 生成，取 `dimension_analysis` 中 `recommendation="优先"` 的维度） |
| `conflict_analysis` | string | 维度间主题冲突的专业分析（由 LLM 生成，可展示给高级用户参考），应包含加权 Jaccard 数值 |
| `selection_advice` | string | 面向用户的简洁勾选建议（**程序化计算**：与 can_select_all 严格对应，can_select_all=true 时建议全部勾选，否则提示存在冲突） |
| `dimension_analysis` | array | 各维度的详细分析 |
| `dimension_analysis[].dimension` | string | 分析维度名称（search_term） |
| `dimension_analysis[].matched_themes` | array\<object\> | 该维度命中 Neo4j 的真实主题列表（含别名和贡献权重），按权重降序排列 |
| `dimension_analysis[].matched_themes[].theme` | string | 主题别名 |
| `dimension_analysis[].matched_themes[].weight` | float | 该主题对该维度的贡献权重（= 映射到该 theme 的指标相似度之和），用于加权 Jaccard 计算 |
| `dimension_analysis[].theme_count` | int | 命中主题的总数量 |
| `dimension_analysis[].independence_score` | float | 独立性得分 0.0~1.0，越高表示越独立（与其他维度越不重叠） |
| `dimension_analysis[].core_concept_score` | float | 核心概念得分 0.0~1.0，越高表示越能代表用户的核心分析意图 |
| `dimension_analysis[].recommendation` | string | 建议等级：优先 / 可选 / 建议后选 |

**前端展示建议**：

- `can_select_all == true` 时，可在引导卡片中展示"所有维度主题高度重叠，可以全部勾选"
- `can_select_all == false` 时，在维度选择区顶部展示引导卡片：
  - 高亮 `recommended_first` 中的维度（推荐优先勾选）
  - 展示 `selection_advice` 作为提示文案
  - `conflict_analysis` 可折叠展示（面向高级用户）
- 单维度场景下 `dimension_guidance` 不会出现（无需引导）

> **实现说明**：`dimension_guidance` 中 `has_conflict`、`can_select_all`、`selection_advice` 由**程序化计算**得出（确保三字段严格一致），`recommended_first`、`conflict_analysis`、`dimension_analysis` 由 LLM 生成。LLM 生成失败时不阻塞流程（返回 null）。前端应做好 `dimension_guidance` 字段可能不存在的容错处理。

### 5.4 interrupt（低置信度类型）

当迭代达到最大轮次（5 轮）后，仍有概念 Top-1 相似度 < 0.80，推送低置信度 interrupt。

**与正常 interrupt 的区别**：低置信度 interrupt 的 `pending_confirmation` 不仅包含低置信度提示，还包含维度选择数据，前端需同时展示：
1. 低置信度警告和换词建议
2. 收敛/未收敛维度列表，供用户自选

```json
{
  "event_type": "interrupt",
  "thread_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "low_confidence",
  "pending_confirmation": {
    "type": "low_confidence",
    "message": "「涉农标识」无法精确匹配到指标，请尝试换词描述",
    "suggestions": [
      {
        "concept": "涉农标识",
        "reason": "该词过于口语化，系统无法精确匹配",
        "alternatives": ["农户贷款标志", "涉农贷款借据标志", "三农贷款"]
      }
    ],
    "action_required": "请选择要进入分析的维度（可多选），然后点击继续；或修改问题后重新提交",
    "filter_display": [
      {
        "alias": "二级账务机构名称",
        "value": "南京分行",
        "type": "机构筛选指标"
      }
    ],
    "dimension_options": [
      {
        "search_term": "小微企业贷款",
        "converged": true,
        "top_indicator_aliases": ["借据余额", "贷款本金", "贷款笔数", "五级分类", "不良贷款余额"],
        "top_indicators": [
          {
            "id": "INDICATOR.xxx",
            "alias": "借据余额",
            "similarity_score": 0.92
          }
        ]
      },
      {
        "search_term": "涉农标识",
        "converged": false,
        "top_indicator_aliases": ["农户贷款标志", "涉农贷款标志"],
        "top_indicators": [
          {
            "id": "INDICATOR.yyy",
            "alias": "农户贷款标志",
            "similarity_score": 0.35
          }
        ]
      }
    ]  }
}
```

> **重要**：低置信度 interrupt 后，前端应提供维度选择界面，用户可：
> 1. **自选维度继续**：勾选想要分析的维度（收敛或未收敛均可），点击继续 → 调用 `/resume`
> 2. **重新提问**：修改问题描述后重新调用 `/recommend`（生成新的 thread_id）

**低置信度 pending_confirmation 完整字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 固定为 `"low_confidence"` |
| `message` | string | 面向用户的友好提示信息 |
| `suggestions` | array | 换词建议列表，每项含 concept、reason、alternatives |
| `action_required` | string | 告知用户下一步操作指引 |
| `filter_display` | array | 已识别的筛选条件（仅展示） |
| `dimension_options` | array | 分析维度列表，含 converged 标记，用户可自选 |
| `dimension_options[].search_term` | string | 维度标识 |
| `dimension_options[].converged` | bool | true=高置信度已收敛，false=未收敛（匹配质量低） |
| `normalized_question` | string | 规范化问题，用户可修改后传回 |
| `dimension_guidance` | object | 维度勾选引导（多维度时自动生成），结构同上 5.3.1 节。`has_conflict==true` 时，前端应在维度选择区顶部展示引导卡片 |

### 5.5 final — 最终结果

流程全部完成后推送，携带完整推荐结果：

```json
{
  "event_type": "final",
  "data": {
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "normalized_question": "分析南京分行2024年小微企业贷款不良率",
    "is_low_confidence": false,
    "conversation_round": 1,
    "execution_time_ms": 3240.5,
    "iteration_rounds": 2,
    "filter_indicators": [
      {
        "indicator_id": "",
        "alias": "二级账务机构名称",
        "value": "南京分行",
        "type": "机构筛选指标"
      }
    ],
    "analysis_dimensions": [
      {
        "search_term": "小微企业贷款",
        "converged": true,
        "indicators": [
          {
            "id": "INDICATOR.xxx",
            "alias": "借据余额",
            "description": "贷款借据的当前余额",
            "theme_id": "THEME.xxx",
            "theme_alias": "小微企业贷款",
            "similarity_score": 0.92
          }
        ]
      }
    ],
    "recommended_themes": [
      {
        "theme_id": "THEME.xxx",
        "theme_alias": "小微企业贷款",
        "theme_level": 4,
        "theme_path": "自主分析 > 资产板块 > 小微贷款 > 小微企业贷款",
        "is_supported": true,
        "support_reason": "主题业务领域与用户需求高度匹配，包含完整的贷款风险分析指标",
        "selected_filter_indicators": [
          {
            "indicator_id": "INDICATOR.yyy",
            "alias": "二级账务机构名称",
            "type": "机构筛选指标",
            "reason": "用于筛选南京分行数据"
          }
        ],
        "selected_analysis_indicators": [
          {
            "indicator_id": "INDICATOR.zzz",
            "alias": "不良贷款率",
            "type": "",
            "reason": "核心分析指标，直接反映贷款不良情况"
          }
        ]
      }
    ],
    "candidate_themes_from_aggregate": [
      {
        "theme_id": "THEME.xxx",
        "theme_alias": "小微企业贷款",
        "theme_level": 4,
        "theme_path": "自主分析 > 资产板块 > 小微贷款 > 小微企业贷款",
        "frequency": 12,
        "weighted_frequency": 8.5,
        "matched_indicator_ids": ["INDICATOR.aaa", "INDICATOR.bbb"]
      }
    ],
    "navigation_path_detail": [
      {
        "sector_id": "SECTOR.xxx",
        "sector_alias": "资产板块",
        "sector_path": "自主分析 > 资产板块",
        "total_themes": 52,
        "selected_themes": [
          {
            "theme_id": "THEME.xxx",
            "theme_alias": "小微企业贷款",
            "theme_path": "自主分析 > 资产板块 > 小微贷款 > 小微企业贷款"
          }
        ]
      }
    ],
    "recommended_templates": [
      {
        "template_id": "TEMPLATE.INSIGHT.I0b2bcd1ade17b002",
        "template_alias": "小微企业贷款风险监控表",
        "template_description": "监控小微企业贷款的风险指标，包含不良率、逾期率等核心指标",
        "theme_id": "THEME.xxx",
        "theme_alias": "小微企业贷款",
        "usage_count": 256,
        "coverage_ratio": 1.0,
        "coverage_detail": {
          "covered_indicator_aliases": ["不良贷款率", "逾期率", "关注类贷款占比"],
          "missing_indicator_aliases": [],
          "matched_count": 3,
          "total_user_indicators": 3
        },
        "theme_has_qualified_templates": true,
        "theme_fallback_reason": "",
        "usability": {
          "template_id": "TEMPLATE.INSIGHT.I0b2bcd1ade17b002",
          "is_supported": true,
          "support_reason": "模板完整覆盖用户所需的所有分析指标，可直接使用"
        }
      }
    ],
    "template_search_detail": [
      {
        "theme_id": "THEME.xxx",
        "theme_alias": "小微企业贷款",
        "theme_path": "自主分析 > 资产板块 > 小微贷款 > 小微企业贷款",
        "is_supported": true,
        "matched_indicator_aliases": ["不良贷款率", "逾期率", "关注类贷款占比"],
        "has_qualified_templates": true,
        "fallback_reason": "",
        "all_template_count": 5,
        "templates": [
          {
            "template_id": "TEMPLATE.INSIGHT.I0b2bcd1ade17b002",
            "template_alias": "小微企业贷款风险监控表",
            "template_description": "监控小微企业贷款的风险指标",
            "usage_count": 256,
            "coverage_ratio": 1.0,
            "covered_indicator_aliases": ["不良贷款率", "逾期率", "关注类贷款占比"],
            "missing_indicator_aliases": [],
            "matched_count": 3,
            "total_user_indicators": 3,
            "is_supported": true,
            "usability_reason": "模板完整覆盖用户所需的所有分析指标，可直接使用"
          }
        ]
      }
    ],
    "markdown": ""
  },
  "timestamp": 1718000012.789
}
```

> **重要**：`final` 事件的 `markdown` 字段**永远为空字符串**，自然语言总结内容通过后续的 `summary` 事件推送。前端不应依赖 `final.data.markdown` 来渲染结果。

**final 关键字段说明**：

| 字段路径 | 说明 |
|----------|------|
| `analysis_dimensions[].indicators` | 包含全量搜索结果（最多 VECTOR_SEARCH_TOP_K = 50 条）。追问时，前端只需取 `analysis_dimensions[].search_term` 构造 `context.previous_dimensions`，无需传递 indicators 数据。 |
| `recommended_themes[].theme_path` | 主题在层级中的完整路径，如"自主分析 > 资产板块 > 小微贷款 > 小微企业贷款" |
| `recommended_themes[].is_supported` | true 为推荐主题，false 为不推荐（仍会返回，需标注） |
| `candidate_themes_from_aggregate` | 聚合路径候选主题列表，含 frequency、weighted_frequency、matched_indicator_ids |
| `navigation_path_detail` | 层级导航路径详情，每个板块含 selected_themes（筛选出的主题） |
| `recommended_templates[].coverage_ratio` | 覆盖率 0.0~1.0，建议展示为百分比 |
| `recommended_templates[].coverage_detail` | 覆盖率详情，含 covered_indicator_aliases（已覆盖指标）、missing_indicator_aliases（缺失指标）、matched_count、total_user_indicators |
| `recommended_templates[].theme_has_qualified_templates` | 该模板所属主题是否有达标模板（覆盖率>=阈值）。false 表示为降级推荐，需在界面上标注 |
| `recommended_templates[].theme_fallback_reason` | 降级原因（仅当 theme_has_qualified_templates=false 时有值） |
| `recommended_templates[].usability.is_supported` | LLM 判定该模板是否可用（true=推荐使用，false=不推荐） |
| `recommended_templates[].usability.support_reason` | LLM 给出的可用性判定理由 |
| `template_search_detail` | 每个主题的模板检索汇总，含 has_qualified_templates、fallback_reason、all_template_count、matched_indicator_aliases，以及 `templates` 子列表（该主题下所有被 LLM 评估过的模板详情） |
| `template_search_detail[].templates` | 该主题下被 LLM 评估过的模板列表，每项含 coverage_ratio、covered/missing_indicator_aliases、is_supported、usability_reason |
| `markdown` | **永远为空字符串**。自然语言总结通过 `summary` 事件推送 |

### 5.6 summary — 自然语言总结（在 final 之后推送）

`final` 事件之后，服务端会继续推送 `summary` 事件，包含自然语言形式的总结内容：

```json
{
  "event_type": "summary",
  "content": "根据您的问题「我想分析南京分行的小微企业贷款风险」，我为您分析了相关需求。规范化后的分析需求为：分析南京分行2024年小微企业贷款不良率。自动识别的筛选条件：二级账务机构名称为「南京分行」， 数据日期为「2024年」。确认的分析维度包括：「小微企业贷款」（关联指标：借据余额、贷款本金、贷款笔数）、「不良率」（关联指标：不良贷款率、逾期率、关注类贷款占比）。关于主题推荐：首选推荐「小微企业贷款」主题...",
  "timestamp": 1718000013.500
}
```

**summary 字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | string | 自然语言形式的完整总结内容，可直接展示给用户 |



### 5.6 error — 错误

当批量 LLM 调用（主题裁决、模板分析）中任意一个失败，整批立即终止，通过 SSE error 事件告知前端。

**重要**：error 事件统一返回用户友好提示，前端应引导用户重新提问（生成新 thread_id），而非展示原始异常信息。

```json
{
  "event_type": "error",
  "message": "底层 LLM 服务调用失败，请重新提问",
  "timestamp": 1718000005.000
}
```

**触发场景**：

| 触发点 | 说明 |
|--------|------|
| `judge_themes` 节点 | 任意主题的 LLM 裁决失败，或整批超过 `LLM_BATCH_TIMEOUT_SECONDS`（默认 310s） |
| `analyze_templates` 节点 | 任意模板的 LLM 可用性分析失败，或整批超过 `LLM_BATCH_TIMEOUT_SECONDS`（默认 310s） |
| `invoke_structured` 重试耗尽 | 单次 LLM 调用经全部重试后仍失败 |

**前端处理建议**：
1. 收到 error 事件后，切换到 error 状态
2. 展示错误提示："底层 LLM 服务调用失败，请重新提问"
3. 提示用户重新提问，**使用新的 thread_id**（原 thread_id 仍可用于 resume，但建议重新开始）
4. 可实现自动重试，但建议先展示错误让用户决定

---

## 6. 人机交互环节详解

### 6.1 完整交互流程

```
1. 前端收到 interrupt 事件
         ↓
2. 停止进度展示，渲染确认界面
         ↓
3. 用户操作：
   ├── 勾选/取消分析维度（multiSelect）
   ├── 确认或修改规范化问题描述（可编辑文本框）
   └── 点击"确认并继续"
         ↓
4. 前端调用 POST /resume
         ↓
5. 继续监听新的 SSE 流
         ↓
6. 收到 final 事件，展示结果
```

### 6.2 确认界面应包含的元素

根据 interrupt 事件中的 `pending_confirmation` 数据渲染以下 UI：

#### ① 筛选条件展示区（只读，不可操作）

展示 `filter_display` 中的内容，告知用户哪些条件已自动识别：

```
┌─────────────────────────────────────────┐
│ 筛选条件（已自动应用，无需操作）          │
├─────────────────────────────────────────┤
│ 🏦 机构：二级账务机构名称 = "南京分行"   │
│ 📅 时间：数据日期 = "2024年"            │
└─────────────────────────────────────────┘
```

#### ② 分析维度确认区（多选，默认全选）

展示 `dimension_options` 中的内容，用户可取消不需要的维度：

```
┌─────────────────────────────────────────┐
│ 请确认分析维度（可多选）                  │
├─────────────────────────────────────────┤
│ ☑ 小微企业贷款                          │
│   关联指标：借据余额、贷款本金、贷款笔数... │
│                                         │
│ ☑ 不良率                               │
│   关联指标：不良贷款率、逾期率、关注类占比  │
└─────────────────────────────────────────┘
```

> 默认全选，用户只需取消不想要的维度。`converged: false` 的维度可以用不同样式标注（如灰色），提示匹配置信度较低。

#### ③ 问题确认区（可编辑）

此处展示 `confirmed_question`（用户可在界面修改），用于传回 `/resume`。初始可显示原始问题或留空，确认后此处显示 LLM 生成的规范化描述：

```
┌─────────────────────────────────────────┐
│ 确认分析需求描述（可修改）                │
├─────────────────────────────────────────┤
│ [分析南京分行2024年小微企业贷款不良率___] │
└─────────────────────────────────────────┘
```

#### ④ 操作按钮

```
[ 取消 ]    [ 确认并继续 → ]
```

- **取消**：关闭确认界面，回到初始状态（不调用任何接口）
- **确认并继续**：调用 /resume

### 6.3 /resume 的参数如何构造

用户点击"确认并继续"后，从界面状态收集数据：

```javascript
// 从界面收集用户选择
const confirmedDimensions = dimensionOptions
  .filter(opt => opt.isChecked)         // 用户勾选的
  .map(opt => opt.search_term)          // 取 search_term 值

const confirmedQuestion = questionInput.value  // 用户确认/修改的问题

// 构造请求
fetch('/api/v1/resume', {
  method: 'POST',
  body: JSON.stringify({
    thread_id: currentThreadId,          // 与 /recommend 相同的 thread_id
    confirmed_dimensions: confirmedDimensions,
    confirmed_question: confirmedQuestion,
  })
})
```

### 6.4 低置信度时的界面处理

收到 `status: "low_confidence"` 的 interrupt 事件时，用户有两个选择：

**选项一：继续执行（调用 /resume）**

用户可以选择使用当前已识别的维度继续执行：
- 前端调用 `/resume` 接口，传入用户确认的维度
- 流程继续执行主题推荐和模板推荐
- 适用于用户认为当前维度已足够好的场景

**选项二：修改问题重新提问（调用 /recommend）**

展示换词引导界面，让用户修改问题：

```
┌─────────────────────────────────────────┐
│ ⚠️ 部分概念无法精确匹配                  │
├─────────────────────────────────────────┤
│ 「涉农标识」无法精确匹配到指标            │
│ 原因：该词过于口语化                     │
│                                         │
│ 建议换词：                              │
│   • 农户贷款标志                         │
│   • 涉农贷款借据标志                     │
│   • 三农贷款                             │
├─────────────────────────────────────────┤
│ [ 继续使用当前维度 ]  [ 修改问题重新提交 ] │
└─────────────────────────────────────────┘
```

用户点击"修改问题重新提交"后，生成新的 thread_id，重新调用 /recommend。

---

## 7. 多轮对话（追问）

### 7.1 核心规则

| 场景 | thread_id | 调用接口 |
|------|-----------|----------|
| 全新问题 | 生成新的 | /recommend |
| 同一问题的维度确认 | 与 /recommend 相同 | /resume |
| 追问（上下文相关） | 生成新的 | /recommend（携带 context） |
| 低置信度后重试 | 生成新的 | /recommend |

### 7.2 前端需要维护的状态

```javascript
// 前端需要持久化的会话状态（可存 sessionStorage）
const sessionState = {
  currentThreadId: null,           // 当前 thread_id
  lastQuestion: "",                // 上一轮原始问题
  lastNormalizedQuestion: "",      // 上一轮规范化问题
  lastFilterIndicators: [],        // 上一轮筛选条件
  lastDimensions: [],              // 上一轮确认的分析维度
}
```

### 7.3 追问时如何构造 context

```javascript
// 用户提交第二个问题时
async function askFollowUp(newQuestion) {
  const newThreadId = generateUUID()   // 生成全新 thread_id

  const request = {
    thread_id: newThreadId,
    question: newQuestion,
    context: {
      previous_question: sessionState.lastQuestion,
      previous_normalized_question: sessionState.lastNormalizedQuestion,
      previous_filter_indicators: sessionState.lastFilterIndicators,
      previous_dimensions: sessionState.lastDimensions,
    }
  }

  // 调用 /recommend
  await startRecommend(request)
}

// 每次收到 final 事件后，更新 sessionState
function onFinalEvent(data) {
  // 注意：final.data 中不包含原始问题，原始问题由前端在发起请求时自行缓存
  sessionState.lastNormalizedQuestion = data.normalized_question
  sessionState.lastFilterIndicators = data.filter_indicators.map(f => ({
    alias: f.alias,
    value: f.value,
  }))
  sessionState.lastDimensions = data.analysis_dimensions.map(d => d.search_term)
}
```

---

## 8. 完整交互时序图

```
前端                                    后端
  │                                        │
  │  ① 用户输入问题，生成 thread_id         │
  │                                        │
  │  POST /recommend                       │
  │  { thread_id, question, ... }          │
  │ ──────────────────────────────────>   │
  │                                        │ extract_phrases
  │  <── SSE: progress (进度)              │ classify_and_iterate
  │  <── SSE: stage_complete               │
  │  <── SSE: progress (搜索第1轮)         │
  │  <── SSE: progress (搜索第2轮)         │
  │  <── SSE: stage_complete               │
  │                                        │ wait_for_confirmation
  │  <── SSE: interrupt ───────────────────┤ ← 流程暂停
  │  {                                     │
  │    event_type: "interrupt",             │
  │    pending_confirmation: {             │
  │      filter_display: [...],            │
  │      dimension_options: [...],        │
  │      normalized_question: "..."        │
  │    }                                   │
  │  }                                     │
  │                                        │
  │  ② 前端展示确认界面                    │
  │     用户勾选维度，确认问题描述          │
  │     点击"确认并继续"                   │
  │                                        │
  │  POST /resume                          │
  │  {                                     │
  │    thread_id,          ← 同一个        │
  │    confirmed_dimensions: [...],        │
  │    confirmed_question: "..."           │
  │  }                                     │
  │ ──────────────────────────────────>   │
  │                                        │ wait_for_confirmation 继续
  │  <── SSE: stage_complete               │ aggregate_themes
  │  <── SSE: stage_complete               │ complete_indicators
  │  <── SSE: progress (裁决中)           │ judge_themes
  │  <── SSE: stage_complete               │ retrieve_templates
  │  <── SSE: stage_complete               │ analyze_templates
  │  <── SSE: stage_complete               │ format_output
  │                                        │
  │  <── SSE: final ───────────────────────┤ ← 流程完成
  │  {                                     │
  │    event_type: "final",                │
  │    data: {                             │
  │      recommended_themes: [...],        │
  │      recommended_templates: [...],     │
  │      markdown: "..."                   │
  │    }                                   │
  │  }                                     │
  │                                        │
  │  ③ 前端展示推荐结果                    │
  │                                        │
  │  ④ 用户追问第二个问题                  │
  │     生成新 thread_id                   │
  │     携带上一轮 context                 │
  │                                        │
  │  POST /recommend (新 thread_id)        │
  │  { thread_id: "新ID", context: {...} } │
  │ ──────────────────────────────────>   │
  │                                        │ (重复上述流程)
```

---

## 9. 前端状态机设计

前端需要维护以下状态，根据收到的事件进行转换：

```
[idle]
  │ 用户提交问题
  ↓
[loading] ── 收到 stage_complete / progress ──> [loading]（更新进度）
  │
  ├── 收到 interrupt (normal) ──────────────> [waiting_confirmation]
  │                                               │ 用户点击确认
  │                                               ↓
  │                                           [resuming] ── 收到 stage_complete ──> [resuming]
  │                                               │ 收到 final
  │                                               ↓
  │                                           [completed]
  │
  ├── 收到 interrupt (low_confidence) ──────> [low_confidence]
  │                                               │ 用户修改问题重新提交
  │                                               ↓
  │                                           [loading]（新 thread_id）
  │
  ├── 收到 final ───────────────────────────> [completed]
  │
  └── 收到 error ───────────────────────────> [error]
                                                  │ 用户重试
                                                  ↓
                                              [idle]
```

---

## 10. 错误处理

### 10.1 常见错误场景

| 场景 | 表现 | 前端处理建议 |
|------|------|-------------|
| 问题为空或过长 | HTTP 422，请求被拒绝 | 前端输入校验，提示用户 |
| thread_id 已被使用过（非 resume） | error 事件，状态冲突 | 生成新 thread_id 重试 |
| 使用错误的 thread_id 调用 /resume | error 事件，找不到会话 | 提示用户重新提问 |
| thread_id 超过 1 天未活跃 | 会话被自动清理（TTL） | 视为新会话，重新发起流程 |
| 向量搜索服务超时 | error 事件 | 提示"服务繁忙，请稍后重试" |
| 主题裁决 LLM 调用失败 | error 事件，"底层 LLM 服务调用失败，请重新提问" | 提示用户重新提问（使用新 thread_id） |
| 模板分析 LLM 调用失败 | error 事件，"底层 LLM 服务调用失败，请重新提问" | 提示用户重新提问（使用新 thread_id） |
| 批量 LLM 任务超时 | error 事件，"底层 LLM 服务调用失败，请重新提问" | 提示用户重新提问（使用新 thread_id） |
| 所有主题均不支持 | final 事件，recommended_themes 全为 is_supported: false | 展示"未找到匹配主题"提示 |
| 无达标模板（降级推荐） | final 事件，has_qualified_templates: false | 展示降级提示标注 |
| 并发超限（429） | HTTP 429 Too Many Requests | 提示"系统繁忙，请稍后重试"（详见[并发控制说明](#11-并发控制说明)） |

### 10.2 SSE 连接中断处理

```javascript
// 建议设置超时和重连逻辑
const TIMEOUT_MS = 120000  // 2分钟超时

const timer = setTimeout(() => {
  reader.cancel()
  showError("请求超时，请重试")
}, TIMEOUT_MS)

// 收到 final 或 error 后清除计时器
clearTimeout(timer)
```

---

## 11. 并发控制说明

### 11.1 机制概述

服务内置基于 `asyncio.Semaphore` 的并发控制机制，防止服务因过多并发请求而过载。

| 参数 | 默认值 | 说明 | 配置方式 |
|------|--------|------|----------|
| `MAX_CONCURRENT_REQUESTS` | 10 | 最大并发请求数 | 环境变量 `.env` |
| `CONCURRENT_TIMEOUT_SECONDS` | 5.0 | 等待信号量的超时时间（秒） | 环境变量 `.env` |
| `LLM_BATCH_TIMEOUT_SECONDS` | 310 | 批量 LLM 任务的超时时间（秒）。主题裁决或模板分析任一任务超时时，整批立即终止并返回 error | 环境变量 `.env` |

### 11.2 并发超限处理

当并发请求数达到上限时，服务会返回 **HTTP 429 Too Many Requests**：

**快速拒绝场景**（当前并发已满）：

```json
{
  "detail": {
    "error": "too_many_requests",
    "message": "当前并发已达上限 10，请稍后重试",
    "current_concurrency": 10,
    "max_concurrency": 10
  }
}
```

**超时场景**（等待信号量超时）：

```json
{
  "detail": {
    "error": "timeout_waiting",
    "message": "等待超过 5.0s，请稍后重试",
    "current_concurrency": 10,
    "max_concurrency": 10
  }
}
```

### 11.3 前端处理建议

收到 429 响应时：

1. **展示友好提示**："系统繁忙，请稍后重试"
2. **自动重试**：可实现指数退避重试（Exponential Backoff）
3. **降级处理**：提示用户稍后再试，或提供排队等待机制

```typescript
// 示例：带退避的请求函数
async function requestWithBackoff(url: string, body: object, maxRetries = 3): Promise<Response> {
  for (let i = 0; i < maxRetries; i++) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (response.status === 429) {
      const delay = Math.pow(2, i) * 1000; // 1s, 2s, 4s
      console.warn(`并发超限，${delay}ms 后重试...`);
      await new Promise(r => setTimeout(r, delay));
      continue;
    }

    return response;
  }
  throw new Error('请求失败，请稍后重试');
}
```

### 11.4 监控并发状态

通过 `/health` 接口可获取当前并发状态：

```bash
curl http://localhost:8000/health
```

**响应示例**：

```json
{
  "status": "healthy",
  "version": "1.0.0",
  "services": {
    "neo4j": true
  },
  "concurrency": {
    "current": 3,
    "max": 10,
    "available": 7
  }
}
```

**字段说明**：

| 字段 | 说明 |
|------|------|
| `concurrency.current` | 当前正在处理的请求数 |
| `concurrency.max` | 最大并发数上限 |
| `concurrency.available` | 可用槽位数（max - current）|

---

## 12. thread_id 生命周期速查


```
用户提交新问题
    │
    ├─ 生成新 thread_id ──────────────────────────────────────────────┐
    │                                                                  │
    │  POST /recommend { thread_id: "新ID", question: "..." }         │
    │       ↓                                                          │
    │  收到 interrupt                                                  │
    │       ↓                                                          │
    │  POST /resume { thread_id: "同一个ID", confirmed_dimensions: [] }│
    │       ↓                                                          │
    │  收到 final  ← thread_id 使命结束 ──────────────────────────────┘
    │
    └─ 用户追问 → 生成新 thread_id → 重复上述流程

⚠️ 注意：超过 1 天未活跃的 thread_id 会被自动清理，
   下次使用时视为新会话（推荐通过 context 机制传递上下文）。
```

---

## 附录：模板可用性说明

模板可用性由 `usability.is_supported`（bool）和 `usability.support_reason`（string）两个字段共同描述，由 LLM 根据模板指标覆盖情况和用户需求判定。

| `is_supported` | 含义 | 展示标记 |
|----------------|------|---------|
| `true` | 模板指标覆盖满足用户需求，推荐使用 | ✅ |
| `false` | 模板指标缺口较大或不匹配用户需求，不推荐 | ⚠️ |

`support_reason` 字段提供判定理由的自然语言描述，前端可展示给用户参考。

覆盖率详情可通过 `coverage_detail` 字段获取：
- `covered_indicator_aliases`：模板已覆盖的用户指标别名列表
- `missing_indicator_aliases`：模板缺失的用户指标别名列表
- `matched_count`：已覆盖指标数量
- `total_user_indicators`：用户所需指标总数

---

## 附录：TTL Memory 管理

### TTLMemorySaver 机制

服务内置基于 `TTLMemorySaver` 的自动内存管理：

| 参数 | 值 | 说明 |
|------|----|------|
| TTL | 86400 秒（1天） | thread 超过此时间未活跃则标记为过期 |
| 清理间隔 | 600 秒（10分钟） | 后台任务周期性执行清理 |
| 线程安全 | 是 | 使用 `Lock` 保护时间戳字典 |

**清理范围**：
- `storage`：thread 的 checkpoint 快照
- `writes`：thread 的写操作记录
- `_timestamps`：thread 最后活跃时间

**监控接口**：

```
GET /health/memory
```

**响应示例**：

```json
{
  "status": "ok",
  "ttl_seconds": 86400,
  "total_threads": 15,
  "active_threads": 12,
  "expired_threads": 3
}
```

### 前端需要注意的事项

1. **thread_id 不要长期复用**：同一 thread_id 超过 1 天未使用会被自动清理，下次请求会视为新会话（对话历史丢失）
2. **建议**：追问时使用 `context` 机制传递上下文，而不是依赖 session 恢复
3. **监控**：可通过 `/health/memory` 接口监控服务内存状态，若 `total_threads` 持续增长可考虑缩短 TTL

## 附录：覆盖率说明

- **达标模板**：覆盖率 >= 阈值（默认 20%，可通过 `TEMPLATE_COVERAGE_THRESHOLD` 配置）的模板，属于达标推荐
- **非达标模板**：覆盖率低于阈值的模板仍会返回，但前端应根据 `theme_has_qualified_templates` 和 `usability.is_supported` 判断展示方式
- **覆盖率详情**：每个模板的 `coverage_detail` 字段提供精确的覆盖/缺失指标明细，前端可据此展示具体差距
