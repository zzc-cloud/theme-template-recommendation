# Skill Runtime 方案和 Agent Service 方案对比分析

> 本文档对 Skill Runtime 方案和 Agent Service 方案进行系统性对比，重点分析在企业级
> Agent 落地场景下的架构选型决策框架。两种方案均符合 Harness Engineering 的核心原则，
> 但在 Harness 谱系中处于不同位置：Skill Runtime 实现的是 Harness 的**自主性维度**，
> Agent Service 实现的是 Harness 的**可靠性维度**。

---

## 一、方案概述

### 1.1 Skill Runtime 方案

Skill Runtime 是用 Python 自建的轻量级 Agent Framework，复现了 Claude Code Runtime
的两个核心机制：**Skill 加载**和 **Task 子会话隔离**。

它直接装载自然语言编写的 Markdown Skill 定义，独立部署并对外暴露 API，无需依赖
Claude Code 环境。其本质是一个**完整的 Harness 实现**：Skill 定义是 system prompt
注入，task() 是 Orchestration Logic，Context Folding 对抗 Context Rot，Validation
Gates 提供确定性约束，GC Agent 实现自我修复循环。

```
外部系统 → FastAPI → Skill Runtime → Skill(SKILL.md) → Tools → 外部服务
                          │
                          ├── task() → 子 Agent Loop（隔离上下文）
                          │               └── Sub-Skill → Tools
                          │
                          ├── Context Folder（对抗 Context Rot）
                          ├── Drift Detector（对抗 Model Drift）
                          ├── Validation Gates（确定性约束）
                          └── GC Agent（自我修复循环）
```

**两个核心机制**：

**机制一：Skill 加载**

将 SKILL.md 的自然语言内容作为 system prompt 注入 Claude SDK，驱动 Agent Loop 执行。
Harness Engineering 的 Memory File 标准（AGENTS.md 模式）与此天然对齐：

```python
def load_skill(skill_name: str) -> str:
    # 方式一：从文件加载
    return Path(f".claude/skills/{skill_name}/SKILL.md").read_text()

    # 方式二：从本体加载（推荐，单一来源）
    return neo4j.query("""
        MATCH (s:SKILL {id: $skill_name})
        RETURN s.system_prompt
    """, skill_name=skill_name)
```

**机制二：Task 子会话隔离**

`task()` 创建全新的独立子会话，只传入必要上下文，执行完毕只返回精简结果，不污染父
会话。这是 Harness Engineering 中 **Progressive Disclosure** 的完整实现——每个子会话
只加载它需要的 Skill 和工具，父会话不被子任务的中间过程污染：

```python
def task(skill_name: str, context: dict, instruction: str) -> str:
    skill_prompt = load_skill(skill_name)
    contract = load_contract_from_ontology(skill_name)  # 加载输出契约

    # 全新会话，零历史污染
    response = client.messages.create(
        model="claude-opus-4-5",
        system=skill_prompt,
        tools=load_tools_for_skill(skill_name),
        messages=[{
            "role": "user",
            "content": f"{instruction}\n\n上下文：{json.dumps(context)}"
        }]
    )

    # 契约验证：不通过则重试，不是静默失败
    result = extract_result(response)
    validated = contract.validate(result)

    # 记录持久化制品（Durable Artifact）
    artifact_store.save(DurableArtifact(
        skill_name=skill_name,
        input_context=context,
        output_result=validated,
        contract_validated=True,
        ...
    ))

    return validated
```

**完整 Skill Runtime 运行时**：

```python
class SkillRuntime:
    """用 Python 实现的轻量级 Agent Framework（完整 Harness 实现）"""

    def run(self, skill_name: str, user_input: str) -> str:
        skill_prompt = load_skill(skill_name)
        tools = load_tools_for_skill(skill_name)
        tools.append(self._make_task_tool())

        messages = [{"role": "user", "content": user_input}]

        # Agent Loop
        while True:
            response = client.messages.create(
                model="claude-opus-4-5",
                system=skill_prompt,
                tools=tools,
                messages=messages
            )

            # Drift 检测：对抗长时任务中的逻辑漂移
            drift = self.drift_detector.check(self.loop_state)
            if DriftSignal.REPETITION_LOOP in drift:
                messages = self.context_folder.fold_if_needed(messages)
                messages.append(self._inject_goal_reminder(user_input))
            if DriftSignal.ITERATION_OVERFLOW in drift:
                return self._interrupt(reason="iteration_overflow")

            if response.stop_reason == "end_turn":
                return response.content

            if response.stop_reason == "tool_use":
                results = []
                for tool_call in get_tool_calls(response):
                    if tool_call.name == "task":
                        # task() 创建独立子会话，父会话只收到精简结果
                        result = self.task_with_evaluation(
                            skill_name=tool_call.input["skill"],
                            context=tool_call.input["context"],
                            instruction=tool_call.input["instruction"]
                        )
                    else:
                        result = execute_tool(tool_call)
                    results.append(result)

                # Context Folding：防止消息历史膨胀
                messages = self.context_folder.fold_if_needed(messages)
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": results})
```

**核心特点**：
- **声明式**：流程通过 SKILL.md 中的自然语言描述定义，LLM 自主决定执行路径
- **完整 Harness**：在自主性基础上，叠加确定性约束（契约验证、验证门控）、
  持久化制品、Model Drift 对抗、GC Agent 自我修复
- **独立部署**：Python 进程，Docker 容器化，对外暴露 REST + SSE API
- **上下文隔离**：`task()` 将复杂子任务委派给独立子会话，父会话上下文不膨胀
- **本体驱动**：Skill 定义、输出契约、验证门控均从本体动态加载，单一来源

### 1.2 Agent Service 方案

Agent Service 是独立部署的服务进程，通过 LangGraph StateGraph 实现工作流显式编排。
其本质是一个**确定性优先的 Harness 实现**：StateGraph 节点边界即架构边界，
TypedDict State 提供强类型约束，LangGraph 内置重试、中断恢复和持久化。

```
外部系统 → FastAPI → LangGraph(StateGraph) → Tools → 外部服务
```

**核心特点**：
- **命令式**：流程通过 Python 代码显式定义，每个节点、每条边都由开发者枚举
- **确定性约束**：StateGraph 节点边界即架构边界，`with_structured_output()` 强制
  输出结构，行为完全可预期
- **独立服务**：Docker 容器化部署
- **状态机编排**：StateGraph 管理状态流转，State 在节点间内存传递
- **API 暴露**：REST + SSE 流式输出

---

## 二、Harness Engineering 视角下的架构对比

> **核心框架**：根据 Harness Engineering 的定义，**Agent = Model + Harness**。
> Harness 是围绕模型的所有非模型部分——代码、配置、执行逻辑——用来把模型的智能
> 转化为可用的工作能力。两种方案都是合法的 Harness 实现，但在 Harness 谱系中
> 处于不同位置。

### 2.1 本质差异：控制权在哪里

这是两种方案最根本的分歧，也是它们在 Harness 谱系中位置不同的根本原因：

```
Skill Runtime（自主性维度）：
  开发者定义"有哪些工具"、"Skill 的目标"、"输出契约"、"验证门控"
  LLM 自主决定"执行哪些步骤、以什么顺序、是否委派子任务"
  Harness 提供约束基础设施，但不预设执行路径
  → 控制权在 LLM，Harness 保障边界

Agent Service（可靠性维度）：
  开发者定义"每一个节点"、"每一条边"、"每一个分支条件"
  LLM 只在节点内部做局部推理
  Harness 完全控制执行路径，LLM 是受约束的局部推理器
  → 控制权在代码，LLM 在 Harness 内执行
```

### 2.2 Harness 组件符合度矩阵

| Harness 组件 | Skill Runtime | Agent Service | 说明 |
|-------------|:---:|:---:|------|
| **System Prompt / Skill 注入** | ✅ | ✅ | 两者均支持，Skill Runtime 更动态 |
| **Progressive Disclosure（Context Rot）** | ✅✅ | ✅ | task() + Context Folding 是更完整的实现 |
| **子 Agent 动态生成与委派** | ✅✅ | ⚠️ | Skill Runtime 更符合 Harness 精神 |
| **确定性输出约束** | ✅ | ✅✅ | 迭代后通过契约层弥合，但 Agent Service 仍更强 |
| **持久化制品（Durable Artifacts）** | ✅ | ✅ | 迭代后两者相当 |
| **中断恢复** | ⚠️ | ✅✅ | Agent Service 内置，Skill Runtime 需自行实现 |
| **Model Drift 对抗** | ✅✅ | ⚠️ | Skill Runtime 原生优势（Drift Detector + Folding）|
| **GC Agent / 自我修复** | ✅✅ | ⚠️ | Skill Runtime 原生优势（GAN 式 Evaluator）|
| **验证门控** | ✅ | ✅ | 两者相当（本体定义 vs 代码定义）|
| **跨 Runtime 可移植性** | ✅✅ | ❌ | Skill Runtime 独有（HARNESS.md 可移植）|
| **Memory File 模式** | ✅✅ | ⚠️ | Skill Runtime 天然对齐 AGENTS.md 标准 |
| **固定结构执行 Trace** | ⚠️ | ✅✅ | Agent Service 天然结构化 |
| **流程步骤可预期性** | ❌ | ✅✅ | Agent Service 的根本优势，无法被弥合 |

### 2.3 执行模型对比

| 维度 | Skill Runtime | Agent Service |
|------|--------------|---------------|
| **编排机制** | Agent Loop + task() 子会话 | LangGraph StateGraph |
| **流程定义方式** | 自然语言（SKILL.md） | Python 代码（显式节点和边） |
| **执行路径** | LLM 动态决策 | 代码静态枚举 |
| **状态管理** | 会话级消息历史 + Context Folding | TypedDict State + TTLMemorySaver |
| **上下文隔离** | task() 子会话（精确传递上下文） | StateGraph 节点（共享 State） |
| **确定性约束** | 输出契约 + 验证门控（本体定义） | StateGraph 边界 + structured_output |
| **自我修复** | GAN 式 Evaluator Agent | 节点级重试 |
| **中断恢复** | 需自行实现 | `interrupt()` + `/resume` API |
| **单次执行内并发** | asyncio 并发 task() 调用 | 原生并行（ThreadPoolExecutor） |
| **多会话并行** | 支持（多 HTTP 请求并发） | 支持（多 HTTP 请求并发） |

### 2.4 多智能体协作机制

#### Skill Runtime：Agent Loop + task() 委派

```
┌──────────────────────────────────────────────────────────────┐
│  Skill Runtime：主 Agent Loop                                │
│  system: SKILL.md（自然语言流程定义）                         │
│  LLM 自主决定何时委派、委派给谁                               │
│  Drift Detector 监控执行状态，Context Folder 管理上下文       │
└──────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         │（LLM 决定并发时，asyncio.gather 并行执行）│
         ▼                    ▼                    ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ task(           │ │ task(           │ │ task(           │
│  "vector-search"│ │  "ontology-     │ │  "template-     │
│  skill,         │ │   query-skill", │ │   recommend-    │
│  context={      │ │  context={      │ │   skill",       │
│   keywords}     │ │   indicators}   │ │  context={      │
│ )               │ │ )               │ │   themes}       │
├─────────────────┤ ├─────────────────┤ ├─────────────────┤
│ 独立子会话       │ │ 独立子会话       │ │ 独立子会话       │
│ 精确上下文传入   │ │ 精确上下文传入   │ │ 精确上下文传入   │
│ 契约验证输出     │ │ 契约验证输出     │ │ 契约验证输出     │
│ 记录持久化制品   │ │ 记录持久化制品   │ │ 记录持久化制品   │
│ 只返回精简结果   │ │ 只返回精简结果   │ │ 只返回精简结果   │
│ 父会话不膨胀     │ │ 父会话不膨胀     │ │ 父会话不膨胀     │
└─────────────────┘ └─────────────────┘ └─────────────────┘
         │                    │                    │
         └────────────────────┼────────────────────┘
                              ▼
                  ┌─────────────────────┐
                  │  GC Agent（可选）    │
                  │  评估三个子任务的    │
                  │  输出一致性         │
                  └─────────────────────┘
```

**上下文隔离效果**：

```
没有 task() 隔离时（Single-Agent Loop）：
  第1轮：用户问题                        → ~500 tokens
  第4轮：+ 向量搜索返回 200 个候选指标    → ~8000 tokens
  第6轮：+ 本体查询返回复杂图结构         → ~20000 tokens
  第8轮：模型开始"遗忘"第1轮的用户意图   → Model Drift ⚠️

有 task() 隔离后：
  父会话第1轮：用户问题                  → ~500 tokens
  父会话第2轮：task("vector-search")
               子会话内部跑完所有迭代
               → 只返回 {"converged_indicators": [...]}  → ~200 tokens
  父会话第3轮：task("ontology-query")
               → 只返回 {"matched_themes": [...]}        → ~200 tokens
  父会话第4轮：task("template-recommend")
               → 只返回 {"recommended_templates": [...]} → ~200 tokens
  父会话始终 < 2000 tokens，上下文干净 ✅
  Context Folder 在超过阈值时自动折叠历史 ✅
```

#### Agent Service：StateGraph + 内存传递

```
┌─────────────────────────────────────────────────────────────┐
│  StateGraph: Agent 工作流（开发者显式定义每个节点和边）        │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐ │
│  │extract  │───→│classify │───→│aggregate│───→│judge    │ │
│  │_phrases │    │_iterate │    │_themes  │    │_themes  │ │
│  └─────────┘    └─────────┘    └─────────┘    └─────────┘ │
└─────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  Theme Tools   │ │  Vector Search  │ │ Template Tools  │
│  (Neo4j)       │ │  (Chroma)       │ │  (Neo4j)        │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

**协作特点**：
- 各节点通过 State 直接在内存中传递数据（全量共享，无隔离）
- 流程步骤、分支条件由开发者在代码中完整枚举
- 支持并行节点（ThreadPoolExecutor）
- 内置错误处理和重试机制

---

## 三、Harness 核心机制详解

### 3.1 Context Rot 对抗机制

Context Rot（上下文腐化）是 Harness Engineering 的核心挑战之一：随着上下文窗口填满，
模型推理质量下降。两种方案的应对策略根本不同：

| 维度 | Skill Runtime | Agent Service |
|------|--------------|---------------|
| **根本策略** | 动态隔离（task() + Context Folding） | 静态约束（State 字段固定） |
| **父子会话隔离** | ✅ task() 精确传递，子会话不污染父会话 | 不适用 |
| **上下文折叠** | ✅ 超过阈值自动压缩历史为结构化摘要 | 不适用 |
| **上下文膨胀风险** | 有（靠 task() 边界 + Folding 控制） | 无（State 字段固定不增长） |
| **Progressive Disclosure** | ✅ 每个 task() 只加载对应 Skill 的工具 | ❌ 节点启动时全量加载 |

**Context Folding 实现**：

```python
class ContextFolder:
    FOLD_THRESHOLD = 8000  # tokens

    def fold_if_needed(self, messages: list) -> list:
        if count_tokens(messages) < self.FOLD_THRESHOLD:
            return messages

        # 用独立 task() 生成结构化摘要（本身也是隔离的子会话）
        folded_summary = self.task(
            "context-fold-skill",
            context={"messages": messages},
            instruction="提取关键决策和已确认的中间结果"
        )

        return [
            messages[0],  # system prompt 永远保留
            {"role": "user", "content": f"[历史摘要]\n{folded_summary}"},
            *messages[-4:]  # 最近 4 轮完整保留
        ]
```

### 3.2 Model Drift 对抗机制

Model Drift（模型漂移）是 Harness Engineering 在生产环境的核心挑战：在数百次工具调用
后，逻辑一致性和指令遵循能力退化。这是 Skill Runtime 相对于 Agent Service 的**原生
优势**——Agent Service 因为流程固定，天然不存在 Drift 问题；Skill Runtime 需要主动
对抗：

```python
class DriftDetector:
    def check(self, loop_state: LoopState) -> list[DriftSignal]:
        signals = []

        # 信号1：工具调用重复率（陷入循环）
        if self._repetition_rate(loop_state.recent_tool_calls) > 0.4:
            signals.append(DriftSignal.REPETITION_LOOP)

        # 信号2：契约验证失败率（输出质量下降）
        if loop_state.contract_failure_rate > 0.3:
            signals.append(DriftSignal.OUTPUT_DEGRADATION)

        # 信号3：迭代轮次超限（无法收敛）
        if loop_state.iteration_count > loop_state.skill_max_iterations:
            signals.append(DriftSignal.ITERATION_OVERFLOW)

        return signals
```

**处理策略**：

| Drift 信号 | 处理策略 |
|-----------|---------|
| REPETITION_LOOP | 触发 Context Folding + 重新注入目标 |
| OUTPUT_DEGRADATION | 触发 GAN 式 Evaluator 双重验证 |
| ITERATION_OVERFLOW | 触发中断，等待人工确认或降级处理 |

### 3.3 确定性约束机制

Harness Engineering 的核心原则之一：**架构约束由 Linter 强制，而非 Prompt 请求**。
两种方案实现这一原则的方式不同：

#### Skill Runtime：输出契约 + 验证门控（本体定义）

```python
# 输出契约：从本体加载，强制 task() 返回值结构
class TaskContract(BaseModel):
    output_schema: type[BaseModel]
    required_fields: list[str]

# 验证门控：从本体加载，在关键步骤强制验证
# 本体中定义：
# (s:SKILL)-[:HAS_GATE]->(g:ValidationGate {
#   after_step: 'vector_search',
#   validator: 'indicator_coverage_check',
#   threshold: 0.8,
#   on_fail: 'retry' | 'interrupt' | 'fallback'
# })

class ValidationGate:
    def check(self, step_name: str, result: dict) -> GateResult:
        gates = load_gates_from_ontology(self.skill_name, step_name)
        for gate in gates:
            if not gate.validator(result):
                return GateResult(gate.on_fail)
        return GateResult.PASS
```

#### Agent Service：StateGraph 边界 + structured_output（代码定义）

```python
class AgentState(TypedDict):
    # 强类型约束，字段固定
    extracted_phrases: list[str]
    recommended_themes: list[RecommendedTheme]
    ...

# with_structured_output() 强制 LLM 输出结构
node_chain = prompt | llm.with_structured_output(RecommendedTheme)
```

**关键差异**：Skill Runtime 的约束定义在本体中（声明式，改本体即生效），Agent Service
的约束定义在代码中（命令式，改代码需重新部署）。

### 3.4 GC Agent 与自我修复循环

Harness Engineering 的"Garbage Collection"机制——周期性运行的 Agent，检测不一致性
并自动修复。这是 Skill Runtime 的**原生优势**：

```python
class SkillRuntime:
    def task_with_evaluation(self, skill_name, context, instruction):
        """GAN 式双 Agent：Generator + Evaluator"""

        # Generator：执行 Skill
        result = self.task(skill_name, context, instruction)

        # Evaluator：独立子会话，零上下文污染
        evaluation = self.task(
            skill_name=f"{skill_name}-evaluator",
            context={
                "original_instruction": instruction,
                "result": result,
                "contract": load_contract(skill_name)
            },
            instruction="评估结果是否满足契约和原始指令要求"
        )

        if not evaluation["passed"] and self.retry_count < self.max_retries:
            # 将 Evaluator 的反馈注入重试
            return self.task_with_evaluation(
                skill_name, context,
                instruction + f"\n\n[上次问题]: {evaluation['feedback']}"
            )

        return result
```

**批量 GC Agent**（周期性运行）：

```python
# 扫描 DurableArtifacts，检测系统性问题
gc_agent = SkillRuntime()
gc_agent.run(
    "gc-consistency-skill",
    "扫描最近 24 小时的执行制品，检测契约违反的系统性模式，生成本体修复建议"
)
```

### 3.5 持久化制品（Durable Artifacts）

每次 task() 执行记录为可追溯的制品，支撑 GC Agent、时间旅行调试和 Drift 检测：

```python
class DurableArtifact(BaseModel):
    artifact_id: str          # uuid
    skill_name: str
    thread_id: str            # 父会话 ID
    task_depth: int           # 嵌套深度
    input_context: dict       # 传入的上下文
    output_result: dict       # 返回的精简结果
    contract_validated: bool  # 是否通过契约验证
    duration_ms: float
    llm_calls: int
    timestamp: datetime
```

---

## 四、信息交互与上下文管理

### 4.1 上下文管理策略对比

| 维度 | Skill Runtime | Agent Service |
|------|--------------|---------------|
| **父子会话隔离** | ✅ task() 精确传递，子会话结果精简返回 | 不适用（无父子会话概念） |
| **节点间数据传递** | 消息历史（靠 task() + Folding 控制膨胀） | TypedDict State（强类型，全量共享） |
| **上下文膨胀风险** | 有（需合理设计 task() 边界） | 无（State 字段固定，不随轮次增长） |
| **中间结果可追溯** | ✅ DurableArtifact 持久化制品 | ✅ 每个节点的 State 快照可记录 |
| **Context Rot 对抗** | ✅ task() 隔离 + Context Folding | ✅ State 字段固定（天然不膨胀） |

### 4.2 数据结构对比

#### Skill Runtime：消息历史 + task() 精简结果 + 持久化制品

父会话中流转的是精简的 task() 返回值（契约验证后）：

```python
# task("vector-search-skill") 的返回值（契约验证后）
{
    "converged_indicators": [
        {"id": "INDICATOR.001", "alias": "小微企业贷款余额", "score": 0.92}
    ],
    "filter_indicators": [
        {"indicator_id": "INDICATOR.xxx", "value": "南京分行", "type": "机构筛选"}
    ]
}

# 同时记录 DurableArtifact 到制品存储（不进入父会话上下文）
```

#### Agent Service：强类型 State 全量流转

```python
class AgentState(TypedDict):
    user_question: str
    extracted_phrases: list[str]
    filter_indicators: list[FilterIndicator]
    analysis_dimensions: list[AnalysisDimension]
    normalized_question: str
    search_results: dict[str, list]
    candidate_themes: list[ThemeCandidate]
    recommended_themes: list[RecommendedTheme]
    recommended_templates: list[RecommendedTemplate]
```

每个节点读取 State 中它关心的字段，写入它负责的字段，全程强类型约束。

### 4.3 与外部系统的集成方式

两种方案都是独立部署的 Python 服务，都通过 FastAPI 对外暴露 REST + SSE API，集成方
式完全一致：

```python
# 两种方案的外部调用方式相同
response = requests.post(
    "http://agent-service:8000/recommend",
    json={"question": "分析南京分行的存款情况", "thread_id": "uuid"},
    stream=True
)

for line in response.iter_lines():
    event = json.loads(line)
    if event["type"] == "stage_complete":
        print(f"阶段完成: {event['stage']}")
    elif event["type"] == "final":
        print(f"结果: {event['data']}")
```

---

## 五、性能对比

### 5.1 执行性能

| 指标 | Skill Runtime | Agent Service |
|------|--------------|---------------|
| **服务启动** | 常驻进程，无冷启动 | 常驻进程，无冷启动 |
| **平均执行时间** | 相当（核心 LLM 推理开销相同） | 相当 |
| **并发处理能力** | 支持（asyncio + Semaphore 限流） | 支持（ThreadPoolExecutor + Semaphore） |
| **上下文增长控制** | task() 边界 + Context Folding | 无增长（State 字段固定） |
| **长任务支持** | DurableArtifact + 自行实现持久化 | TTLMemorySaver 持久化 |
| **LLM 调用次数** | 动态（LLM 自主决定轮次） | 可预期（节点数固定） |
| **GC Agent 开销** | 额外 LLM 调用（可配置是否启用） | 不适用 |

### 5.2 延迟来源对比

| 开销项 | Skill Runtime | Agent Service |
|--------|--------------|---------------|
| **Skill 加载** | 每次请求一次（本体查询，毫秒级） | 不适用 |
| **LLM 推理** | 主要开销（秒级） | 主要开销（秒级） |
| **task() 子会话启动** | 每次委派一次新 API 调用（秒级） | 不适用 |
| **契约验证** | 每次 task() 后执行（毫秒级） | with_structured_output（毫秒级） |
| **Context Folding** | 超过阈值时触发（一次额外 LLM 调用） | 不适用 |
| **GC Evaluator** | 高风险 Skill 启用（一次额外 LLM 调用） | 不适用 |
| **Artifact 持久化** | 每次 task() 后写入（毫秒级） | 不适用 |
| **State 更新** | 不适用 | 内存操作（微秒级） |
| **SSE 流式推送** | 毫秒级 | 毫秒级 |

> **注**：Skill Runtime 的 GC Evaluator 和 Context Folding 会引入额外的 LLM 调用开销。
> 建议按 Skill 的风险等级配置是否启用（`high_stakes: true` 才启用 Evaluator），
> 避免所有 task() 都走双重验证。

---

## 六、稳定性对比

### 6.1 可靠性机制

| 机制 | Skill Runtime | Agent Service |
|------|--------------|---------------|
| **重试机制** | ✅ GAN 式 Evaluator 驱动重试（语义重试） | ✅ 节点级差异化重试（规则重试） |
| **超时控制** | 需自行实现 | ✅ 节点级超时控制 |
| **并发控制** | 需自行实现（asyncio Semaphore） | ✅ Semaphore 限流（默认 10） |
| **LLM 结构化输出** | ✅ 输出契约（本体定义） | ✅ `with_structured_output()` 强约束 |
| **健康检查** | ✅ FastAPI /health | ✅ FastAPI /health |
| **执行步骤可预期性** | ❌ LLM 动态决策，步骤数不固定 | ✅ 节点数固定，行为可预期 |
| **Model Drift 对抗** | ✅ Drift Detector + Context Folding | 不适用（流程固定，无 Drift 问题） |
| **自我修复循环** | ✅ GC Agent（批量 + 实时） | ❌ 无内置 GC 机制 |

### 6.2 会话管理

| 维度 | Skill Runtime | Agent Service |
|------|--------------|---------------|
| **会话持久化** | ✅ DurableArtifact（制品级）+ 需自行实现会话级 | ✅ TTLMemorySaver（可配置 TTL） |
| **中断恢复** | 需自行实现 | ✅ `interrupt()` + `/resume` API |
| **时间旅行调试** | ⚠️ DurableArtifact 支持制品回溯；完整时间旅行需自行实现 | ⚠️ LangGraph Platform 原生支持；自托管需自行实现 |
| **多会话隔离** | ✅ thread_id 隔离（需自行实现） | ✅ thread_id 隔离（内置） |
| **task() 子会话状态** | 无状态（每次全新），制品持久化 | 不适用 |

### 6.3 监控与可观测性

#### Skill Runtime 的可观测性

执行路径由 LLM 动态决定，但 DurableArtifact 提供了结构化的制品追踪：

```python
# DurableArtifact 提供制品级追踪（结构固定）
artifact_trace = [
    {
        "artifact_id": "uuid-001",
        "skill": "vector-search-skill",
        "task_depth": 1,
        "contract_validated": True,
        "duration_ms": 2300,
        "llm_calls": 3
    },
    {
        "artifact_id": "uuid-002",
        "skill": "template-recommend-skill",
        "task_depth": 1,
        "contract_validated": True,
        "duration_ms": 1800,
        "llm_calls": 2
    }
]

# 但父会话的执行路径仍是动态的，需主动埋点
def task_with_trace(skill_name, context, instruction):
    start = time.time()
    result = task(skill_name, context, instruction)
    # 制品已自动记录，这里补充父会话视角的 trace
    parent_trace.append({
        "skill": skill_name,
        "duration_ms": (time.time() - start) * 1000,
    })
    return result
```

- 制品级 trace 结构固定（DurableArtifact）
- 父会话执行路径不固定，需主动埋点
- task() 子会话的内部推理过程对父会话不可见

#### Agent Service 的可观测性

```python
# 节点执行记录（结构固定，天然可追溯）
execution_trace = [
    {
        "node": "extract_phrases",
        "input": {"user_question": "..."},
        "output": {"extracted_phrases": [...]},
        "duration_ms": 120,
        "llm_calls": 1
    },
    {
        "node": "classify_and_iterate",
        "duration_ms": 3500,
        "llm_calls": 5,
        "vector_search_calls": 10
    }
]

# SSE 事件流（结构固定）
events = [
    {"type": "progress", "stage": "extract_phrases", "status": "complete"},
    {"type": "stage_complete", "stage": "classify_iterate", "duration_ms": 3500},
    {"type": "interrupt", "reason": "user_confirmation"},
    {"type": "final", "data": {...}}
]
```

- 执行路径固定，trace 结构每次一致
- 每个节点的输入输出天然可记录
- SSE 事件流结构化，前端可精确展示进度

---

## 七、开发与维护成本对比

### 7.1 开发成本

| 维度 | Skill Runtime | Agent Service |
|------|--------------|---------------|
| **新增 Agent** | 写 SKILL.md + 在本体注册契约和门控 | 写 Python 节点代码 + 定义 State + 连接边 |
| **修改流程逻辑** | 修改 SKILL.md 即可（无需改代码） | 修改节点代码 + 可能调整 State 结构 |
| **新增工具** | 注册工具函数，Skill 自动使用 | 注册工具函数 + 在对应节点显式调用 |
| **调试流程问题** | 中（DurableArtifact 辅助，但路径不固定） | 易（每个节点输入输出可单独测试） |
| **上下文设计** | 需要仔细设计 task() 边界和契约 | 需要仔细设计 State 字段 |
| **框架自建成本** | 需要自建 Runtime（一次性投入） | 使用 LangGraph（已有框架） |

### 7.2 Skill Runtime 的自建成本清单

Skill Runtime 是一次性的框架投入，需要自行实现以下工程能力：

| 需要自行实现的能力 | 对应 Harness 组件 | 实现方式 |
|-----------------|-----------------|---------|
| task() 并行执行 | Orchestration Logic | `asyncio.gather()` |
| 子会话异常隔离 | Sandboxing | try/except + 重试逻辑 |
| 工具调用超时 | Deterministic Constraints | asyncio timeout |
| 嵌套 task() 深度控制 | Architectural Constraints | 递归深度限制 |
| 会话状态持久化 | Durable State | 自行实现或集成 Checkpointer |
| 中断恢复 | Human-in-the-loop | 自行实现 |
| Context Folding | Context Management | 独立 fold-skill |
| Drift Detector | Model Drift Guard | 信号检测 + 处理策略 |
| 输出契约验证 | Deterministic Constraints | Pydantic + 本体定义 |
| 验证门控 | Validation Gates | 本体定义 + 执行器 |
| GC Agent | Garbage Collection | 独立 gc-skill |
| DurableArtifact 存储 | Durable Artifacts | Neo4j 或文件系统 |

> **判断原则**：Skill Runtime 的自建成本是一次性的框架投入，且随着模型能力提升，
> 这个框架的价值会持续增大——因为更强的模型意味着 LLM 自主决策的可靠性更高，
> Skill Runtime 的动态规划优势会更加突出。如果你的场景中有大量流程边界模糊、
> 需要 LLM 自主决策的 Agent，这个投入是值得的。

### 7.3 长期维护成本

| 维度 | Skill Runtime | Agent Service |
|------|--------------|---------------|
| **流程变更** | 改 SKILL.md + 本体契约，无需改代码 | 改 Python 代码，需测试部署 |
| **新增 Agent 类型** | 写新 SKILL.md + 注册本体节点 | 写新 StateGraph |
| **框架升级** | 维护自建 Runtime（但逻辑在本体中，框架稳定） | 跟随 LangGraph 版本 |
| **团队知识门槛** | 理解 Agent Loop + Harness 机制 | 理解 LangGraph 概念 |
| **排查生产问题** | 中（DurableArtifact 辅助，但路径动态） | 易（路径固定，节点可复现） |
| **跨 Runtime 迁移** | ✅ HARNESS.md 可移植，换 Runtime 不丢逻辑 | ❌ 逻辑与 LangGraph 强耦合 |

---

## 八、架构决策框架

### 8.1 本质差异：一个判断问题

选型的核心只有一个判断：

> **你的 Agent 流程，是否可以被完整枚举？**

```
可以枚举：
  "一定先提取关键词，再向量搜索，再聚合主题，再推荐模板"
  "每个步骤的输入输出字段是固定的"
  → Agent Service（StateGraph 显式定义，行为可预期）

不能枚举：
  "根据用户问题的复杂度，可能需要 2 轮也可能需要 8 轮向量搜索"
  "某些问题需要委派子任务，某些不需要"
  "流程边界模糊，需要 LLM 自主判断"
  → Skill Runtime（LLM 动态决策，Harness 保障边界）
```

### 8.2 五维决策矩阵

| 维度 | 评估问题 | 倾向 Skill Runtime | 倾向 Agent Service |
|------|---------|------------------|-------------------|
| **流程确定性** | 步骤是否固定可枚举？ | 步骤模糊，LLM 自主判断 | 步骤固定，分支可枚举 |
| **上下文复杂度** | 中间结果是否会大量膨胀？ | 是（需 task() + Folding） | 否（State 字段固定） |
| **容错要求** | 是否需要语义级重试还是规则级重试？ | 语义重试（GC Evaluator） | 规则重试（节点级差异化） |
| **可观测性要求** | 是否需要固定结构的执行路径 trace？ | 制品级 trace 可接受 | 需要固定路径 trace |
| **迭代模式** | 流程逻辑由谁修改、多久改一次？ | 业务人员高频修改 SKILL.md | 工程师按需修改代码 |

### 8.3 长期演化视角

随着模型能力持续提升，两种方案的相对优势会发生变化：

```
当前（2026）：
  Skill Runtime 的动态决策可靠性 < Agent Service 的确定性控制
  → 对可靠性要求高的场景，Agent Service 仍是更安全的选择

中期（模型能力提升后）：
  Skill Runtime 的动态决策可靠性 ≈ Agent Service 的确定性控制
  → 两者在可靠性上趋于相当，但 Skill Runtime 的灵活性优势凸显

长期（模型足够可靠后）：
  Agent Service 的 StateGraph 显式枚举反而成为不必要的约束
  → Skill Runtime 成为主流，Agent Service 退守"必须固定流程"的场景
```

**这意味着**：Skill Runtime 的自建框架投入，随时间增值；Agent Service 的 LangGraph
依赖，随模型能力提升而价值递减。

### 8.4 迁移触发条件

不要预设迁移时间表，而是定义**触发从 Skill Runtime 迁移到 Agent Service 的具体条件**：

| 触发条件 | 说明 |
|---------|------|
| **流程固化触发** | 经过探索期，执行步骤高度稳定，LLM 动态决策不再带来额外价值 |
| **可观测性触发** | 需要固定结构的执行路径 trace，用于审计合规（制品级 trace 不满足） |
| **可靠性触发** | 某步骤需要独立的规则级重试策略，GC Evaluator 的语义重试不满足 |
| **中断恢复触发** | 需要在特定步骤暂停等待人工确认，且需要从断点精确恢复 |
| **性能触发** | GC Evaluator 的额外 LLM 调用在高并发场景下成为瓶颈，且该步骤已可规则化 |

### 8.5 按场景选择

| 场景 | 推荐方案 | 理由 |
|------|---------|------|
| **流程边界模糊的新 Agent** | Skill Runtime | LLM 自主决策，快速验证 |
| **需要 LLM 自主规划步骤** | Skill Runtime | 步骤数动态，无法预先枚举 |
| **上下文会大量膨胀的场景** | Skill Runtime | task() + Folding 解决 Context Rot |
| **业务人员主导流程定义** | Skill Runtime | 改 SKILL.md 无需工程介入 |
| **需要跨 Runtime 可移植** | Skill Runtime | HARNESS.md 可移植 |
| **数据治理（规则管理）** | Skill Runtime | 模糊判断长期存在，LLM 自主决策是核心价值 |
| **流程步骤固定可枚举** | Agent Service | StateGraph 显式控制，行为可预期 |
| **需要固定路径执行 trace** | Agent Service | 节点输入输出天然可记录 |
| **需要中断等待人工确认** | Agent Service | `interrupt()` 原生支持 |
| **数据运维监控告警** | Agent Service | 实时性要求高，流程固定 |
| **数据开发（SQL 生成）** | Agent Service | 错误代价高，需规则化验证和重试 |

### 8.6 按数据管理子领域选择

```
┌──────────────────────────────────────────────────────────────────────┐
│                    数据管理领域 Agent 决策矩阵                         │
├──────────────┬──────────────────────┬────────────────────────────────┤
│   领域        │  探索期               │  生产期                        │
├──────────────┼──────────────────────┼────────────────────────────────┤
│  数据分析      │ Skill Runtime        │ Skill Runtime                  │
│  (主题模板推荐) │ (流程边界模糊，       │ (步骤仍动态，Harness 迭代后     │
│               │  LLM 自主规划)        │  可达生产级可靠性)              │
├──────────────┼──────────────────────┼────────────────────────────────┤
│  数据开发      │ Skill Runtime        │ Agent Service                  │
│  (SQL 生成等)  │ (快速验证流程)        │ (SQL 错误代价高，需规则化重试)  │
├──────────────┼──────────────────────┼────────────────────────────────┤
│  数据治理      │ Skill Runtime        │ Skill Runtime                  │
│  (规则管理)   │ (规则模糊，           │ (模糊判断长期存在，             │
│               │  需 LLM 自主判断)     │  LLM 自主决策是核心价值)        │
├──────────────┼──────────────────────┼────────────────────────────────┤
│  数据运维      │ Agent Service        │ Agent Service                  │
│  (监控告警)   │ (流程固定，           │ (实时性要求高，                 │
│               │  实时性要求高)        │  固定 trace 用于审计)           │
└──────────────┴──────────────────────┴────────────────────────────────┘
```

---

## 九、大本体背景下的决策考量

### 9.1 本体对两种方案的驱动方式

```
┌─────────────────────────────────────────────────────────────┐
│                        大综合本体                            │
│    (Neo4j: 覆盖所有领域的 AGENT/SKILL/TOOL/ONTOLOGY)         │
│                                                             │
│    ┌─────────────────────────────────────────────────┐      │
│    │ AGENT: 数据分析智能体                             │      │
│    │   ├── SKILL_DEF: system_prompt（自然语言定义）    │      │
│    │   ├── OUTPUT_CONTRACT: 输出契约（Pydantic Schema）│      │
│    │   ├── VALIDATION_GATES: 验证门控定义              │      │
│    │   ├── TOOLS: [向量搜索, 本体查询, 模板推荐]        │      │
│    │   ├── SUB_SKILLS: [vector-search, ontology-query]│      │
│    │   ├── STAGE: SKILL_RUNTIME / AGENT_SERVICE       │      │
│    │   └── MIGRATE_TRIGGER: [触发条件列表]             │      │
│    └─────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────┘
                              │
           ┌──────────────────┴──────────────────┐
           │                                     │
           ▼                                     ▼
┌─────────────────────────┐         ┌─────────────────────────┐
│    Skill Runtime 层     │         │    Agent Service 层     │
├─────────────────────────┤         ├─────────────────────────┤
│ 本体驱动方式:            │         │ 本体驱动方式:            │
│ • SKILL_DEF → system    │         │ • 工具列表和约束         │
│   prompt                │         │ • 节点内 Claude SDK      │
│ • OUTPUT_CONTRACT →     │         │   调用时注入本体知识      │
│   task() 契约验证        │         │ • State 字段定义参考     │
│ • VALIDATION_GATES →    │         │   本体概念结构           │
│   关键步骤门控           │         │                         │
│ • SUB_SKILLS → task()   │         │                         │
│   委派时加载子 Skill     │         │                         │
├─────────────────────────┤         ├─────────────────────────┤
│ 单一来源保障:            │         │ 单一来源保障:            │
│ 改本体 = Skill 定义、    │         │ 改本体工具定义           │
│ 契约、门控同步更新        │         │ = 所有实例行为同步更新    │
└─────────────────────────┘         └─────────────────────────┘
```

### 9.2 本体对两种方案的增强

| 能力 | Skill Runtime | Agent Service |
|------|--------------|---------------|
| **Skill 定义单一来源** | ✅ 从本体动态加载 system prompt | ✅ 节点内从本体加载 prompt |
| **输出契约单一来源** | ✅ task() 契约验证从本体加载 | ✅ 可从本体加载 Pydantic Schema |
| **验证门控单一来源** | ✅ 门控定义在本体，声明式生效 | ⚠️ 门控逻辑在代码中，需重新部署 |
| **工具动态加载** | ✅ 从本体加载工具列表，Skill 自动使用 | ✅ 节点启动时从本体加载 |
| **子 Skill 注册** | ✅ task() 委派时从本体查找子 Skill | 不适用（节点显式定义） |
| **架构状态记录** | ✅ 本体记录 STAGE、触发条件、制品统计 | ✅ 同左 |

### 9.3 本体作为架构演进的决策日志

```cypher
// 在本体中记录 Agent 的完整架构状态
MATCH (a:AGENT {id: 'theme-template-recommendation'})
SET a.stage = 'SKILL_RUNTIME',
    a.harness_layers = ['contract', 'validation_gate', 'drift_detector'],
    a.gc_agent_enabled = true,
    a.migrate_triggers = [],              // 尚未满足迁移条件
    a.next_stage = 'AGENT_SERVICE',       // 候选下一阶段
    a.last_reviewed = '2026-04-01'
```

### 9.4 最终推荐

> **在有大本体支撑的企业场景下，推荐以 Skill Runtime 起步并持续迭代 Harness 能力，
> 触发条件驱动迁移到 Agent Service，两者均以本体为单一来源：**
>
> - **Skill Runtime**：流程边界模糊、需要 LLM 自主规划的场景；通过七层 Harness 迭代
>   （契约验证、持久化制品、Context Folding、Drift 检测、验证门控、GC Agent、
>   HARNESS.md 可移植）达到生产级可靠性；以本体为单一来源，无逻辑漂移风险
> - **Agent Service**：流程步骤固化、需要固定路径 trace / 精确中断恢复的场景；
>   节点内从本体加载知识，保持领域一致性
> - **长期演化**：随模型能力提升，Skill Runtime 的动态决策可靠性持续增强，
>   Agent Service 的适用场景会逐步收窄至"必须固定流程"的场景
> - **迁移时机**：由触发条件驱动（见 8.4 节），而非固定时间表

---

## 十、总结

### 10.1 核心认知

> **两种方案都是符合 Harness Engineering 的，但实现的是 Harness 谱系的两端。**
>
> - **Skill Runtime**：Harness 的自主性维度。开发者定义"目标"、"工具"和"约束边界"，
>   LLM 决定"路径"。task() 解决上下文污染，Harness 七层机制保障可靠性。
>   随模型能力提升，价值持续增大。
>
> - **Agent Service**：Harness 的可靠性维度。开发者定义每一个节点、每一条边。
>   LLM 只在节点内部做局部推理，整体流程完全可预期。适合流程固定、审计要求高的场景。
>
> 选型的核心问题只有一个：**你的 Agent 流程，是否可以被完整枚举？**

