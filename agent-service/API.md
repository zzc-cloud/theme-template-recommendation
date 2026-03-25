# 主题模板推荐服务 API 对接文档

> **版本**：v1.0
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
- [完整调用示例](#11-完整调用示例)

---

## 1. 接口总览

| 接口 | 方法 | 说明 | 何时调用 |
|------|------|------|----------|
| `/api/v1/recommend` | POST | 发起推荐，建立 SSE 流 | 用户提交新问题时 |
| `/api/v1/resume` | POST | 恢复执行，建立 SSE 流 | 用户完成维度确认后 |
| `/api/v1/health` | GET | 健康检查 | 服务探活 |

> **重要**：两个推荐接口均返回 SSE 流，不是普通 JSON 响应。前端必须使用流式读取方式处理。

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
| `stage_complete` | 某个处理阶段完成 | 更新进度条/步骤状态 |
| `custom` | 阶段内部进度详情 | 更新进度描述文字 |
| `interrupt` | 需要用户确认 | 停止等待，展示确认界面 |
| `final` | 流程全部完成，携带最终结果 | 展示推荐结果 |
| `error` | 发生错误 | 展示错误提示 |

---

## 3. 接口一：发起推荐 /recommend

### 请求

```
POST /api/v1/recommend
Content-Type: application/json
```

### 请求体字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `thread_id` | string | ✅ | 会话唯一标识，由前端自行生成（推荐 UUID），每个新问题必须使用全新的 thread_id |
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
| `confirmed_question` | string | ❌ | 用户确认或修改后的问题描述，不传则使用服务端生成的规范化问题 |

### 请求示例

```json
{
  "thread_id": "550e8400-e29b-41d4-a716-446655440000",
  "confirmed_dimensions": ["小微企业贷款", "不良率"],
  "confirmed_question": "分析南京分行2024年小微企业贷款不良率"
}
```

---

## 5. SSE 事件类型完整说明

### 5.1 stage_complete — 阶段完成

```json
{
  "event_type": "stage_complete",
  "stage": "classify_and_iterate",
  "timestamp": 1718000000.123
}
```

**stage 值对应关系**：

| stage 值 | 含义 |
|----------|------|
| `extract_phrases` | 词组提取完成 |
| `classify_and_iterate` | 迭代精炼完成 |
| `wait_for_confirmation` | 确认节点（resume 后恢复时出现） |
| `aggregate_themes` | 主题聚合完成 |
| `complete_indicators` | 指标补全完成 |
| `judge_themes` | 主题裁决完成 |
| `retrieve_templates` | 模板检索完成 |
| `analyze_templates` | 模板可用性分析完成 |
| `format_output` | 结果格式化完成 |

> **前端建议**：维护一个步骤列表，收到对应 stage 时将该步骤标记为"已完成"。

### 5.2 custom — 阶段内部进度

```json
{
  "event_type": "custom",
  "data": {
    "stage": "classify_and_iterate",
    "step": "searching",
    "status": "in_progress",
    "round": 2,
    "concepts": ["小微企业贷款", "不良率"]
  },
  "timestamp": 1718000000.456
}
```

> **前端建议**：可用于展示"正在搜索第2轮..."等细粒度进度描述，非必须处理。

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
| `dimension_options[].top_indicator_aliases` | array\<string\> | 该维度关联的 Top 5 指标名称，辅助用户理解 |
| `normalized_question` | string | 服务端生成的规范化问题，用户可修改后传回 |

### 5.4 interrupt（低置信度类型）

当某些概念无法精确匹配时，推送低置信度 interrupt：

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
    "action_required": "请修改问题描述后重新提交（使用新的 thread_id）"
  }
}
```

> **重要**：低置信度时，前端应引导用户修改问题，然后重新调用 /recommend（生成新的 thread_id），而不是调用 /resume。

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
    "recommended_templates": [
      {
        "template_id": "TEMPLATE.INSIGHT.I0b2bcd1ade17b002",
        "template_alias": "小微企业贷款风险监控表",
        "template_description": "监控小微企业贷款的风险指标，包含不良率、逾期率等核心指标",
        "theme_alias": "小微企业贷款",
        "usage_count": 256,
        "coverage_ratio": 1.0,
        "has_qualified_templates": true,
        "fallback_reason": "",
        "usability": {
          "template_id": "TEMPLATE.INSIGHT.I0b2bcd1ade17b002",
          "overall_usability": "可直接使用",
          "usability_summary": "模板完整覆盖用户所需的所有分析指标，可直接使用",
          "missing_indicator_analysis": []
        }
      }
    ],
    "markdown": "# 主题模板推荐结果\n\n**问题**：..."
  },
  "timestamp": 1718000012.789
}
```

**final 关键字段说明**：

| 字段路径 | 说明 |
|----------|------|
| `recommended_themes[].is_supported` | true 为推荐主题，false 为不推荐（仍会返回，需标注） |
| `recommended_templates[].coverage_ratio` | 覆盖率 0.0~1.0，建议展示为百分比 |
| `recommended_templates[].has_qualified_templates` | false 表示为降级推荐，需在界面上标注 |
| `recommended_templates[].usability.overall_usability` | 可直接使用 / 补充后可用 / 缺口较大建议谨慎 |
| `markdown` | 服务端生成的完整 Markdown 格式结果，可直接渲染 |

### 5.6 error — 错误

```json
{
  "event_type": "error",
  "message": "向量搜索服务连接超时",
  "timestamp": 1718000005.000
}
```

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

展示 `normalized_question`，用户可修改：

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

收到 `status: "low_confidence"` 的 interrupt 事件时，展示换词引导界面：

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
│ [ 修改问题重新提交 ]                     │
└─────────────────────────────────────────┘
```

用户点击"修改问题重新提交"后，生成新的 thread_id，重新调用 /recommend，不调用 /resume。

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
  sessionState.lastQuestion = data.question  // 原始问题
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
  │  <── SSE: custom (进度)                │ classify_and_iterate
  │  <── SSE: stage_complete               │
  │  <── SSE: custom (搜索第1轮)           │
  │  <── SSE: custom (搜索第2轮)           │
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
  │  <── SSE: custom (裁决中)             │ judge_themes
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
[loading] ── 收到 stage_complete / custom ──> [loading]（更新进度）
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
| 向量搜索服务超时 | error 事件 | 提示"服务繁忙，请稍后重试" |
| 所有主题均不支持 | final 事件，recommended_themes 全为 is_supported: false | 展示"未找到匹配主题"提示 |
| 无达标模板（降级推荐） | final 事件，has_qualified_templates: false | 展示降级提示标注 |

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

## 11. 完整调用示例

### TypeScript/JavaScript 完整示例

```typescript
class RecommendClient {
  private currentThreadId: string | null = null;
  private state: string = 'idle';
  // 用于追问的会话摘要
  private lastSession: {
    question: string;
    normalizedQuestion: string;
    filterIndicators: Array<{ alias: string; value: string }>;
    dimensions: string[];
  } | null = null;

  // ── 工具函数 ──
  private generateThreadId(): string {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
      const r = Math.random() * 16 | 0;
      return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
  }

  // ── 发起新问题 ──
  async ask(question: string, useContext: boolean = false): Promise<void> {
    this.currentThreadId = this.generateThreadId();
    this.state = 'loading';

    const body: Record<string, unknown> = {
      thread_id: this.currentThreadId,
      question: question,
      top_k_themes: 3,
      top_k_templates: 5,
    };

    // 追问时携带上一轮 context
    if (useContext && this.lastSession) {
      body.context = {
        previous_question: this.lastSession.question,
        previous_normalized_question: this.lastSession.normalizedQuestion,
        previous_filter_indicators: this.lastSession.filterIndicators,
        previous_dimensions: this.lastSession.dimensions,
      };
    }

    await this.streamRequest('/api/v1/recommend', body);
  }

  // ── 用户确认后恢复 ──
  async resume(confirmedDimensions: string[], confirmedQuestion: string): Promise<void> {
    this.state = 'resuming';

    await this.streamRequest('/api/v1/resume', {
      thread_id: this.currentThreadId,
      confirmed_dimensions: confirmedDimensions,
      confirmed_question: confirmedQuestion,
    });
  }

  // ── 通用 SSE 流处理 ──
  private async streamRequest(url: string, body: Record<string, unknown>): Promise<void> {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      this.state = 'error';
      this.onError(`HTTP ${response.status}`);
      return;
    }

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const TIMEOUT_MS = 120000;

    const timer = setTimeout(() => {
      reader.cancel();
      this.state = 'error';
      this.onError('请求超时，请重试');
    }, TIMEOUT_MS);

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const event = JSON.parse(line.slice(6));
              this.handleEvent(event);

              // 收到 final 或 error 后清除计时器
              if (event.event_type === 'final' || event.event_type === 'error') {
                clearTimeout(timer);
              }
            } catch (e) {
              console.warn('解析 SSE 数据失败:', line);
            }
          }
        }
      }
    } finally {
      clearTimeout(timer);
    }
  }

  // ── 事件分发 ──
  private handleEvent(event: Record<string, unknown>): void {
    switch (event.event_type) {
      case 'stage_complete':
        this.onStageComplete(event.stage as string);
        break;

      case 'custom':
        this.onProgress(event.data as Record<string, unknown>);
        break;

      case 'interrupt': {
        const interruptEvent = event as {
          event_type: string;
          status: string;
          pending_confirmation: Record<string, unknown>;
        };
        if (interruptEvent.status === 'low_confidence') {
          // 低置信度：引导用户修改问题
          this.state = 'low_confidence';
          this.onLowConfidence(interruptEvent.pending_confirmation);
        } else {
          // 正常确认：展示维度确认界面
          this.state = 'waiting_confirmation';
          this.onInterrupt(interruptEvent.pending_confirmation);
        }
        break;
      }

      case 'final': {
        const finalEvent = event as {
          data: {
            question?: string;
            normalized_question: string;
            filter_indicators: Array<{ alias: string; value: string }>;
            analysis_dimensions: Array<{ search_term: string }>;
          };
        };
        this.state = 'completed';
        // 保存本轮会话摘要，供下次追问使用
        this.lastSession = {
          question: finalEvent.data.question ?? '',
          normalizedQuestion: finalEvent.data.normalized_question,
          filterIndicators: finalEvent.data.filter_indicators.map(f => ({
            alias: f.alias,
            value: f.value,
          })),
          dimensions: finalEvent.data.analysis_dimensions.map(d => d.search_term),
        };
        this.onFinal(finalEvent.data);
        break;
      }

      case 'error':
        this.state = 'error';
        this.onError((event as { message: string }).message);
        break;
    }
  }

  // ── 以下方法由业务层实现 ──
  onStageComplete(stage: string): void {
    // 更新进度条
    console.log(`阶段完成: ${stage}`);
  }

  onProgress(data: Record<string, unknown>): void {
    // 更新进度描述
    console.log('进度:', data);
  }

  onInterrupt(pending: Record<string, unknown>): void {
    // 展示确认界面
    console.log('需要确认:', pending);
  }

  onLowConfidence(data: Record<string, unknown>): void {
    // 展示换词建议
    console.log('低置信度:', data);
  }

  onFinal(result: Record<string, unknown>): void {
    // 展示推荐结果
    console.log('最终结果:', result);
  }

  onError(message: string): void {
    // 展示错误
    console.error('错误:', message);
  }
}

// ── 使用示例 ──
const client = new RecommendClient();

// 覆写回调
client.onInterrupt = (pending) => {
  showConfirmationModal({
    filterDisplay: (pending as { filter_display: unknown[] }).filter_display,
    dimensionOptions: (pending as { dimension_options: unknown[] }).dimension_options,
    normalizedQuestion: (pending as { normalized_question: string }).normalized_question,
    onConfirm: (selectedDimensions: string[], question: string) => {
      client.resume(selectedDimensions, question);
    }
  });
};

client.onFinal = (result) => {
  renderRecommendResult(result);
};

// 第一个问题
client.ask('分析南京分行小微企业贷款不良率');

// 用户追问（携带上下文）
client.ask('那对公贷款呢？', true);
```

### thread_id 生命周期速查

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
```

---

## 附录：模板可用性等级说明

| 可用性等级 | 含义 | 展示标记 |
|-----------|------|---------|
| 可直接使用 | 缺失指标均为辅助或可忽略级别，不影响核心分析 | ✅ |
| 补充后可用 | 缺失部分辅助指标，在模板基础上补充后可满足需求 | 🔧 |
| 缺口较大建议谨慎 | 缺失核心指标，需较多调整才能满足需求 | ⚠️ |

---

## 附录：覆盖率说明

- **达标模板**：覆盖率 >= 80% 的模板，可直接推荐
- **降级推荐**：无达标模板时，返回覆盖率最高和热度最高的模板（最多 2 个），需在界面上标注"参考推荐"
