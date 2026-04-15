# Agent Service 执行流程详解

基于真实输入 `「分析南京分行的存款情况」`，逐环节展示调用代码 → 效果 → 代码样例。

---

## 一、输入与入口

**HTTP 请求**（路由：[routes.py:286](agent-service/src/agent_service/api/routes.py#L286)）：

```json
POST /api/v1/recommend
{
  "question": "分析南京分行的存款情况",
  "thread_id": "req-uuid-001",
  "top_k_themes": 3,
  "top_k_templates": 5
}
```

**内部状态初始化**（[routes.py:367](agent-service/src/agent_service/api/routes.py#L367)）：

```python
initial_state = {
    "user_question": "分析南京分行的存款情况",
    "top_k_themes": 3,
    "top_k_templates": 5,
    "extracted_phrases": [],
    "filter_indicators": [],
    "analysis_dimensions": [],
    "normalized_question": "",
    "search_results": {},
    "iteration_round": 0,
    "is_low_confidence": False,
    "conversation_history": [],
    "candidate_themes": [],
    "recommended_themes": [],
    "recommended_templates": [],
    "final_output": {},
}
```

LangGraph 按 [graph.py:84-96](agent-service/src/agent_service/graph/graph.py#L84) 定义的固定边顺序执行：

```
START → extract_phrases → classify_and_iterate → wait_for_confirmation
      → aggregate_themes → navigate_hierarchy → merge_themes
      → complete_indicators → judge_themes
      → retrieve_templates → analyze_templates
      → format_output → generate_summary → END
```

---

## 二、阶段 0：需求澄清

### 节点 0.1：extract_phrases — 词组提取

**调用代码**（[nodes.py:179](agent-service/src/agent_service/graph/nodes.py#L179)）：

```python
def extract_phrases(state: AgentState) -> dict:
    user_question = state["user_question"]
    result = llm_client.extract_phrases(
        user_question, conversation_history=state.get("conversation_history", [])
    )
    phrases = result.phrases if result.phrases else []
    return {"extracted_phrases": phrases}
```

**效果**：从用户问题中提取业务相关词组。

**LLM 调用**（[client.py:464](agent-service/src/agent_service/llm/client.py#L464)）：

```python
# Prompt 模板：PHRASE_EXTRACTION_PROMPT
def extract_phrases(user_question: str, conversation_history=None):
    user_prompt = PHRASE_EXTRACTION_PROMPT.format(
        user_question=user_question,
        conversation_history=_build_history_str(conversation_history or []),
    )
    # 内部：invoke_structured(PhraseExtraction, system_prompt, user_prompt)
    # 使用 with_structured_output() 强制返回 Pydantic 模型
    return invoke_structured(PhraseExtraction, system_prompt, user_prompt)
```

**输入 Prompt**（[prompts.py:79](agent-service/src/agent_service/llm/prompts.py#L79)）：
```
【当前用户问题】
分析南京分行的存款情况

【提取要求】
- 仅提取与业务分析相关的词组
- 忽略语气词、连接词、标点符号
- 不要重复，不要归纳总结
```

**输出效果**：
```
extracted_phrases = ["南京分行", "存款情况"]
```

---

### 节点 0.2-0.3：classify_and_iterate — 词组分类 + 迭代精炼

**第一部分：词组分类**（[nodes.py:200](agent-service/src/agent_service/graph/nodes.py#L200)）

```python
# 0.2 分类
classification = llm_client.classify_phrases(user_question, phrases)
# 返回：PhraseClassification(filter_phrases=["南京分行"], analysis_concepts=["存款情况"])

filter_phrases = ["南京分行"]  # 机构名 → 筛选词
analysis_concepts = ["存款情况"]  # 分析意图 → 分析概念

# 筛选指标映射（规则匹配）
filter_indicators = [
    _map_filter_phrase("南京分行")
    # → {"indicator_id": "INDICATOR.二级账务机构名称", "value": "南京分行", "alias": "二级账务机构名称", "type": "机构筛选指标"}
]
```

**第二部分：迭代精炼循环**（[nodes.py:226](agent-service/src/agent_service/graph/nodes.py#L226)）

```python
pending_concepts: dict = {"存款情况": []}
converged_dimensions: dict = {}

while iteration_round < config.MAX_ITERATION_ROUNDS:
    # Step 1：向量搜索（并行）
    round_search_results = _search_concepts_parallel(
        current_concepts=["存款情况"], top_k=20
    )
    # → {"存款情况": [IndicatorMatch(id="INDICATOR.存款余额", alias="存款余额", similarity=0.92), ...]}

    # Step 2：收敛判定（代码客观判定，阈值 0.80）
    top1_score = 0.92
    if top1_score >= config.CONVERGENCE_SIMILARITY_THRESHOLD:  # 0.92 >= 0.80 → 收敛
        converged_dimensions["存款情况"] = indicators
        del pending_concepts["存款情况"]
        # → 不再进入 Step 3 迭代精炼，正常出口

    # Step 3（跳过）：pending_concepts 已为空，跳过 LLM 精炼
    break
```

**向量搜索工具**（[vector_search.py:136](agent-service/src/agent_service/tools/vector_search.py#L136)）：

```python
def search_indicators_by_vector(query: str, top_k: int = 20) -> dict:
    # 1. 调用 SiliconFlow Embedding API 获取查询向量
    query_vector = get_embedding(query)  # → list[float, 4096维]

    # 2. Chroma 向量库语义搜索
    collection = _get_chroma_collection()
    results = collection.query(
        query_embeddings=[query_vector],
        n_results=top_k,
        include=["metadatas", "documents", "distances"],
    )

    # 3. 余弦距离 → 相似度
    for indicator_id, metadata, document, distance in zip(...):
        similarity = max(0.0, 1.0 - distance)
        indicators.append({
            "id": indicator_id,
            "alias": metadata["alias"],
            "similarity_score": round(similarity, 4),
            ...
        })
    return {"success": True, "indicators": indicators}
```

**迭代收敛输出**（State 更新）：

```python
{
    "extracted_phrases": ["南京分行", "存款情况"],
    "filter_indicators": [
        {"indicator_id": "INDICATOR.二级账务机构名称", "value": "南京分行", "alias": "二级账务机构名称", "type": "机构筛选指标"}
    ],
    "analysis_dimensions": [{
        "search_term": "存款情况",
        "converged": True,
        "indicators": [
            {"id": "INDICATOR.存款余额", "alias": "存款余额", "similarity_score": 0.92},
            {"id": "INDICATOR.对公存款余额", "alias": "对公存款余额", "similarity_score": 0.89},
            {"id": "INDICATOR.个人存款余额", "alias": "个人存款余额", "similarity_score": 0.87},
            ...
        ]
    }],
    "is_low_confidence": False,
    "iteration_round": 1,
    "pending_confirmation": {
        "filter_display": [{"alias": "二级账务机构名称", "value": "南京分行", "type": "机构筛选指标"}],
        "dimension_options": [{
            "search_term": "存款情况",
            "converged": True,
            "top_indicator_aliases": ["存款余额", "对公存款余额", "个人存款余额"],
            "top_indicators": [...]
        }],
        "dimension_guidance": {...}  # Jaccard 勾选引导
    }
}
```

---

### 节点 0.4：wait_for_confirmation — 等待用户确认

**调用代码**（[nodes.py:908](agent-service/src/agent_service/graph/nodes.py#L908)）：

```python
def wait_for_confirmation(state: AgentState) -> dict:
    # LangGraph interrupt() 暂停执行，序列化 pending_confirmation 推送 SSE
    interrupt_data = state.get("pending_confirmation")
    user_input = interrupt(interrupt_data)  # 等待前端确认

    # 用户确认后，从 user_input 中提取：
    #   - confirmed_dimensions: 用户勾选的维度列表
    #   - confirmed_question: 用户可能修改后的规范化问题

    # 生成 normalized_question
    norm_result = llm_client.generate_normalized_question(
        user_question=state["user_question"],
        filter_phrases_str="二级账务机构名称 = 南京分行",
        converged_concepts_str="「存款情况」（关联指标：存款余额、对公存款余额、个人存款余额）",
    )
    final_normalized_question = norm_result.normalized_question
    # → "分析南京分行存款余额情况（含对公存款和个人存款）"

    return {
        "analysis_dimensions": filtered_dimensions,  # 仅保留用户确认的维度
        "normalized_question": final_normalized_question,
        "user_confirmation": {"confirmed_dimensions": [...], "confirmed_question": "..."},
        "pending_confirmation": None,
    }
```

**效果**：SSE 推送 `interrupt` 事件，前端展示维度选择界面。

**API 返回**（[routes.py:427](agent-service/src/agent_service/api/routes.py#L427)）：

```json
{"event_type": "interrupt", "status": "waiting_confirmation",
 "pending_confirmation": {
     "filter_display": [{"alias": "二级账务机构名称", "value": "南京分行", ...}],
     "dimension_options": [{"search_term": "存款情况", "converged": true, ...}],
     "dimension_guidance": {...}
 }}
```

用户在前端勾选维度后，调用 `/resume` 恢复执行。

---

## 三、阶段 1：主题发现

### 节点 1.1：aggregate_themes — 聚合候选主题（路径A：统计聚合）

**调用代码**（[nodes.py:666](agent-service/src/agent_service/graph/nodes.py#L666)）：

```python
def aggregate_themes(state: AgentState) -> dict:
    # 从 analysis_dimensions 中提取指标 ID → 最大相似度映射（去重）
    indicator_max_sim = {}
    for dim in state["analysis_dimensions"]:
        for ind in dim["indicators"]:
            ind_id = ind["id"]
            sim_score = ind["similarity_score"]
            if ind_id not in indicator_max_sim or sim_score > indicator_max_sim[ind_id]:
                indicator_max_sim[ind_id] = sim_score

    matched_indicators = list(indicator_max_sim.keys())
    # → ["INDICATOR.存款余额", "INDICATOR.对公存款余额", "INDICATOR.个人存款余额"]

    result = theme_tools.aggregate_themes_from_indicators(matched_indicators, top_k=3)
```

**Neo4j 查询**（[theme_tools.py:99](agent-service/src/agent_service/tools/theme_tools.py#L99)）：

```cypher
MATCH path = (entry)-[:HAS_CHILD*]->(indicator)
WHERE entry.alias = '自主分析'
  AND indicator.id IN $indicator_ids
WITH indicator_id, [node in nodes(path) WHERE labels(node)[0] = 'THEME'] as themes
UNWIND themes as theme
WITH indicator_id, theme
RETURN theme.id as theme_id, theme.alias as theme_alias,
       collect(indicator_id) as matched_indicator_ids
```

**效果**：

```python
candidate_themes = [
    {
        "theme_id": "THEME.对公存款",
        "theme_alias": "对公存款",
        "theme_path": "自主分析 > 负债板块 > 对公存款",
        "frequency": 12,  # 12 个指标命中
        "matched_indicator_ids": ["INDICATOR.存款余额", "INDICATOR.对公存款余额", ...],
        "weighted_frequency": round(sum([0.92, 0.89]), 4)  # 1.81
    },
    {
        "theme_id": "THEME.个人存款",
        "theme_alias": "个人存款",
        "theme_path": "自主分析 > 负债板块 > 个人存款",
        "frequency": 8,
        "weighted_frequency": round(0.87, 4)
    },
    {
        "theme_id": "THEME.全量存款",
        "theme_alias": "全量存款",
        "theme_path": "自主分析 > 负债板块 > 全量存款",
        "frequency": 5,
        "weighted_frequency": round(0.90, 4)
    }
]
```

---

### 节点 1.2：navigate_hierarchy — 双路径探查（路径B：层级导航）

**调用代码**（[nodes.py:447](agent-service/src/agent_service/graph/nodes.py#L447)）：

```python
def navigate_hierarchy(state: AgentState) -> dict:
    # Step 1: 获取所有板块
    sectors_result = theme_tools.get_sectors_from_root()
    # → {"success": true, "sectors": [{"id": "SECTOR.负债板块", "alias": "负债板块", ...}, ...]}

    # Step 2: LLM 筛选相关板块（减少后续搜索范围）
    sector_filter_result = llm_client.filter_sectors_by_question(
        user_question=state["user_question"],
        sector_list_str="...\n- sector_id: SECTOR.负债板块 | 板块: 负债板块 | 路径: 自主分析 > 负债板块\n..."
    )
    selected_sectors = sector_filter_result.selected_sectors
    # → [SectorSelection(sector_id="SECTOR.负债板块", sector_alias="负债板块", ...)]

    # Step 3: 对每个选中板块 → 获取全量主题 → LLM 筛选
    for sector in selected_sectors:
        sector_themes_result = theme_tools.get_sector_themes(sector.sector_id, top_k=500)
        # → {"success": true, "themes": [{"theme_id": "THEME.xxx", "theme_alias": "...", ...}, ...]}

        # LLM 筛选（分块，每块 100 个）
        for theme_block in _chunk_themes_by_size(sector_themes, chunk_size=100):
            block_result = llm_client.filter_themes_by_hierarchy(
                user_question=state["user_question"],
                analysis_dimensions_str="「存款情况」关联指标: 存款余额、对公存款余额、个人存款余额",
                theme_list_str="...\n- 主题: 对公存款 | 路径: 自主分析 > 负债���块 > 对公存款\n...",
            )
            # → HierarchyNavigationResult(selected_themes=[...])
```

**Neo4j 查询**（[theme_tools.py:678](agent-service/src/agent_service/tools/theme_tools.py#L678)）：

```cypher
MATCH path = (sector:SECTOR {id: $sector_id})-[:HAS_CHILD*]->(theme:THEME)
WITH sector, theme, nodes(path) as path_nodes
RETURN theme.id as theme_id, theme.alias as theme_alias,
       reduce(s = '', item IN [n IN non_ind_nodes | n.alias] |
              s + CASE WHEN s = '' THEN item ELSE ' > ' + item END) as full_path
```

**效果**：

```python
navigation_path_themes = [
    {"theme_id": "THEME.对公存款", "theme_alias": "对公存款", "full_path": "自主分析 > 负债板块 > 对公存款", ...},
    {"theme_id": "THEME.个人存款", "theme_alias": "个人存款", "full_path": "自主分析 > 负债板块 > 个人存款", ...},
    {"theme_id": "THEME.全量存款", "theme_alias": "全量存款", "full_path": "自主分析 > 负债板块 > 全量存款", ...},
]
```

---

### 节点 1.2.5：merge_themes — 候选主题合并去重

**调用代码**（[nodes.py:594](agent-service/src/agent_service/graph/nodes.py#L594)）：

```python
def merge_themes(state: AgentState) -> dict:
    # 聚合路径 (candidate_themes) 为主，层级导航路径 (navigation_path_themes) 补充
    # 去重后按 weighted_frequency 降序排列，取 top_k

    theme_map = {}
    for theme in state["candidate_themes"]:  # 聚合路径
        theme_map[theme["theme_id"]] = {..., "source": "aggregate"}

    for nav_theme in state["navigation_path_themes"]:  # 层级导航路径补充
        if nav_theme["theme_id"] not in theme_map:
            theme_map[nav_theme["theme_id"]] = {..., "source": "navigation"}

    merged = sorted(theme_map.values(), key=lambda x: x["weighted_frequency"], reverse=True)[:3]

    return {"candidate_themes": merged}
```

**效果**：两条路径的候选主题合并去重，按加权频次排序。

---

### 节点 1.3：complete_indicators — 全量指标补全

**调用代码**（[nodes.py:721](agent-service/src/agent_service/graph/nodes.py#L721)）：

```python
def complete_indicators(state: AgentState) -> dict:
    for theme in candidate_themes:
        theme_id = theme["theme_id"]

        # 获取筛选指标（时间 + 机构）
        filter_result = theme_tools.get_theme_filter_indicators(theme_id)
        theme["filter_indicators_detail"] = (
            filter_result.get("time_filter_indicators", [])
            + filter_result.get("org_filter_indicators", [])
        )

        # 获取分析指标（排除筛选指标）
        analysis_result = theme_tools.get_theme_analysis_indicators(theme_id)
        theme["analysis_indicators_detail"] = analysis_result.get("analysis_indicators", [])
```

**Neo4j 查询**（[theme_tools.py:296](agent-service/src/agent_service/tools/theme_tools.py#L296)）：

```cypher
MATCH (theme:THEME {id: $theme_id})
MATCH (theme)-[:HAS_CHILD*1..2]->(i:INDICATOR)
RETURN i.id, i.alias, i.description
```

**效果**：每个候选主题补全其下全量的筛选指标和分析指标，供 LLM 裁决使用。

---

### 节点 1.4：judge_themes — LLM 主题裁决（并行）

**调用代码**（[nodes.py:748](agent-service/src/agent_service/graph/nodes.py#L748)）：

```python
def judge_themes(state: AgentState) -> dict:
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_theme = {
            executor.submit(_judge_theme_parallel, theme, user_question, analysis_dimensions): theme
            for theme in candidate_themes
        }
        for future in as_completed(future_to_theme, timeout=310):
            result = future.result()
            recommended_themes.append({
                "theme_id": theme["theme_id"],
                "is_supported": result["judgment"].is_supported,
                "support_reason": result["judgment"].support_reason,
                "selected_filter_indicators": [...],
                "selected_analysis_indicators": [...],
            })
```

**LLM 调用**（[client.py:569](agent-service/src/agent_service/llm/client.py#L569)）：

```python
def judge_theme(user_question, analysis_dimensions_str, theme_alias, theme_path,
               filter_indicators_str, analysis_indicators_str) -> ThemeJudgment:
    user_prompt = THEME_JUDGMENT_PROMPT.format(
        user_question=user_question,
        analysis_dimensions=analysis_dimensions_str,
        theme_name=theme_alias,
        theme_path=theme_path,
        filter_indicators_str=filter_indicators_str,
        analysis_indicators_str=analysis_indicators_str,
    )
    return invoke_structured(ThemeJudgment, system_prompt, user_prompt)
```

**输入 Prompt**（[prompts.py:135](agent-service/src/agent_service/llm/prompts.py#L135)）：

```
【用户原始问题】
分析南京分行的存款情况

【用户确认的分析维度】
「存款情况」关联指标: 存款余额、对公存款余额、个人存款余额

【当前主题】
主题名称: 对公存款
主题路径: 自主分析 > 负债板块 > 对公存款

【裁决任务】
第一步：判断主题是否能够支撑用户需求
第二步：从指标中精准定位能覆盖用户需求的指标
```

**输出效果**（并行，3 个主题并发裁决）：

```python
recommended_themes = [
    {
        "theme_id": "THEME.对公存款",
        "theme_alias": "对公存款",
        "theme_path": "自主分析 > 负债板块 > 对公存款",
        "is_supported": True,
        "support_reason": "该主题直接覆盖用户存款分析需求，包含存款余额、对公存款余额等核心指标",
        "selected_filter_indicators": [
            {"indicator_id": "INDICATOR.二级账务机构名称", "alias": "二级账务机构名称", "type": "机构筛选指标", "reason": "匹配用户筛选条件南京分行"}
        ],
        "selected_analysis_indicators": [
            {"indicator_id": "INDICATOR.存款余额", "alias": "存款余额", "type": "分析指标", "reason": "核心存款指标"},
            {"indicator_id": "INDICATOR.对公存款余额", "alias": "对公存款余额", "type": "分析指标", "reason": "直接支撑对公存款分析"}
        ]
    },
    ...
]
```

---

## 四、阶段 2：模板推荐

### 节点 2.1：retrieve_templates — 模板检索（带覆盖率）

**调用代码**（[nodes.py:816](agent-service/src/agent_service/graph/nodes.py#L816)）：

```python
def retrieve_templates(state: AgentState) -> dict:
    for theme in state["recommended_themes"]:
        if not theme.get("is_supported"):
            continue

        # 收集 LLM 裁决后的指标别名（覆盖率基于别名匹配）
        matched_indicator_aliases = []
        for ind in theme.get("selected_filter_indicators", []):
            matched_indicator_aliases.append(ind["alias"])
        for ind in theme.get("selected_analysis_indicators", []):
            matched_indicator_aliases.append(ind["alias"])

        result = template_tools.get_theme_templates_with_coverage(
            theme_id=theme["theme_id"],
            matched_indicator_aliases=matched_indicator_aliases,
            top_k=5,
        )
```

**Neo4j 查询 + 覆盖率计算**（[template_tools.py:58](agent-service/src/agent_service/tools/template_tools.py#L58)）：

```cypher
MATCH (t) WHERE t.theme_id = $theme_id AND t.heat > 0
OPTIONAL MATCH (t)-[:CONTAINS]->(i:INDICATOR)
WITH t, collect({id: i.id, alias: i.alias}) as template_indicators

-- 覆盖率 = 交集别名数 / 用户指标别名总数
user_indicator_set = set(matched_indicator_aliases)
template_indicator_aliases = set(i.alias for i in template_indicators)
coverage_ratio = len(user_indicator_set & template_indicator_aliases) / len(user_indicator_set)
```

**效果**：

```python
recommended_templates = [
    {
        "template_id": "TEMPLATE.存款分析模板",
        "template_alias": "存款结构分析模板",
        "coverage_ratio": 0.85,  # 85% 覆盖率
        "has_qualified_templates": True,
        "all_template_indicators": [
            {"alias": "存款余额", ...},
            {"alias": "对公存款余额", ...},
            {"alias": "个人存款余额", ...},
        ],
        "missing_indicator_aliases": ["机构存款利率"]  # 缺失指标
    },
    ...
]
```

---

### 节点 2.2：analyze_templates — LLM 可用性分析（并行）

**调用代码**（[nodes.py:862](agent-service/src/agent_service/graph/nodes.py#L862)）：

```python
def analyze_templates(state: AgentState) -> dict:
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_idx = {
            executor.submit(_analyze_template_parallel, template, user_question, analysis_dimensions): i
            for i, template in enumerate(templates)
        }
        for future in as_completed(future_to_idx, timeout=310):
            result = future.result()
            templates[idx]["usability"] = result["usability"]
```

**LLM 调用**（[client.py:592](agent-service/src/agent_service/llm/client.py#L592)）：

```python
def analyze_template_usability(...) -> TemplateUsability:
    user_prompt = TEMPLATE_USABILITY_PROMPT.format(
        user_question=user_question,
        analysis_dimensions_str=dim_str,
        template_alias=template_alias,
        coverage_ratio=coverage_ratio,  # "85%"
        all_template_indicators_str=all_inds_str,
        missing_indicators_str=missing_inds_str,
    )
    return invoke_structured(TemplateUsability, system_prompt, user_prompt)
```

**输出效果**（[models.py:132](agent-service/src/agent_service/llm/models.py#L132)）：

```python
usability = {
    "template_id": "TEMPLATE.存款分析模板",
    "overall_usability": "可直接使用",
    "usability_summary": "该模板覆盖了用户核心分析需求，缺失指标为辅助性指标，不影响主要分析",
    "missing_indicator_analysis": [
        {
            "indicator_alias": "机构存款利率",
            "importance": "辅助",
            "impact": "缺少利率维度，但不影响存款余额结构分析",
            "supplement_suggestion": "可在模板外补充勾选该指标"
        }
    ]
}
```

---

## 五、完成节点

### 节点 3：format_output — 结构化输出

**调用代码**（[nodes.py:984](agent-service/src/agent_service/graph/nodes.py#L984)）：

```python
def format_output(state: AgentState) -> dict:
    final_output = {
        "user_question": state["user_question"],
        "normalized_question": state["normalized_question"],
        "filter_indicators": state["filter_indicators"],
        "analysis_dimensions": state["analysis_dimensions"],
        "recommended_themes": [...],
        "recommended_templates": [...],
        "iteration_info": {"rounds": state["iteration_round"], "log": state["iteration_log"]},
        "markdown": "",  # 为空，快速返回
    }
    return {"final_output": final_output, "conversation_history": history}
```

**SSE 输出**（[routes.py:474](agent-service/src/agent_service/api/routes.py#L474)）：

```json
{"event_type": "final", "data": {
    "normalized_question": "分析南京分行存款余额情况（含对公存款和个人存款）",
    "recommended_themes": [
        {"theme_id": "THEME.对公存款", "theme_alias": "对公存款", "is_supported": true, ...}
    ],
    "recommended_templates": [
        {"template_id": "TEMPLATE.xxx", "template_alias": "存款结构分析模板",
         "coverage_ratio": 0.85, "usability": {...}}
    ],
    "execution_time_ms": 4523.7
 }}
```

---

## 六、整体流程图

```
HTTP POST /api/v1/recommend
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ extract_phrases                                         │
│  调用 llm_client.extract_phrases()                      │
│  → PHRASE_EXTRACTION_PROMPT → PhraseExtraction          │
│  输出: ["南京分行", "存款情况"]                           │
└──────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ classify_and_iterate                                     │
│  ├─ classify_phrases() → 筛选词/分析概念分离               │
│  │    → ["南京分行"] / ["存款情况"]                        │
│  ├─ _map_filter_phrase() → 筛选指标映射                   │
│  │    → {"indicator_id": "...", "value": "南京分行", ...} │
│  └─ 迭代精炼循环（最多 5 轮）                              │
│       ├─ _search_concepts_parallel()                     │
│       │    → search_indicators_by_vector()               │
│       │    → Chroma + SiliconFlow Embedding             │
│       ├─ 收敛判定（Top-1 相似度 >= 0.80）                  │
│       └─ refine_concepts()（可选，仅未收敛时调用）          │
│  输出: analysis_dimensions + pending_confirmation        │
└──────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ wait_for_confirmation ── interrupt() ── [等待用户勾选]    │
│  输出: user_confirmation + normalized_question          │
└──────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ aggregate_themes（路径A：统计聚合）                       │
│  ├─ 构建 indicator_id → max_similarity 映射（去重）      │
│  └─ aggregate_themes_from_indicators()                   │
│       → Neo4j 批量查询 → 主题聚合                        │
│  输出: candidate_themes (按 weighted_frequency 排序)     │
└────���─────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ navigate_hierarchy（路径B：层级导航）                      │
│  ├─ get_sectors_from_root() → 所有板块                   │
│  ├─ filter_sectors_by_question() → LLM 筛选相关板块       │
│  └─ 对每个板块: get_sector_themes() → LLM 筛选主题        │
│       → Neo4j 批量查询 → 候选主题列表                    │
│  输出: navigation_path_themes                             │
└──────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ merge_themes ── 双路径结果合并去重 ── candidate_themes    │
└──────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ complete_indicators ── 为每个主题补全全量指标              │
│  ├─ get_theme_filter_indicators() → 筛选指标             │
│  └─ get_theme_analysis_indicators() → 分析指标           │
│  输出: candidate_themes（含 filter_indicators_detail /   │
│        analysis_indicators_detail）                      │
└──────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ judge_themes ── 并行 LLM 裁决 ── [ThreadPoolExecutor]    │
│  调用 judge_theme() × N（最多 3 并发）                    │
│  → THEME_JUDGMENT_PROMPT → ThemeJudgment                 │
│  输出: recommended_themes (含 is_supported + selected_*)  │
└──────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ retrieve_templates ── 模板检索（带覆盖率计算）            │
│  └─ get_theme_templates_with_coverage()                  │
│       → Neo4j 查询模板 + 覆盖率计算（别名匹配）           │
│       → 达标模板（>= 80%）或降级推荐                     │
│  输出: recommended_templates                              │
└────────────────���─────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ analyze_templates ── 并行 LLM 可用性分析 ── [并发5]       │
│  调用 analyze_template_usability() × N                    │
│  → TEMPLATE_USABILITY_PROMPT → TemplateUsability          │
│  输出: recommended_templates（含 usability）              │
└──────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ format_output ── 结构化输出快速返回                      │
│  └─ final_output → SSE final 事件                        │
└──────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────┐
│ generate_summary ── 自然语言总结（无 LLM 调用）            │
│  └─ 程序化构建文字总结 → SSE summary 事件                 │
└──────────────────────────────────────────────────────────┘
  │
  ▼
END
```

---

## 七、外部依赖

| 依赖 | 用途 | 配置来源 |
|------|------|---------|
| **SiliconFlow LLM API** | 所有 LLM 推理调用 | `config.SILICONFLOW_LLM_API_KEY` / `config.SILICONFLOW_BASE_URL` |
| **SiliconFlow Embedding API** | 查询向量生成 | `config.SILICONFLOW_EMBEDDING_API_KEY` / `config.SILICONFLOW_EMBEDDING_URL` |
| **Chroma 向量库** | 指标语义搜索 | `config.CHROMA_PATH` / `config.COLLECTION_NAME`（默认 `mcp-server/data/indicators_vector`） |
| **Neo4j 图数据库** | 主题本体 + 模板数据 | `config.NEO4J_URI` / `config.NEO4J_USER` / `config.NEO4J_PASSWORD` |

**依赖检查**：

- 向量库需提前执行 `python scripts/indicator_vectorizer.py --rebuild` 构建
- Neo4j 需预置主题（THEME）、模板（TEMPLATE）、指标（INDICATOR）本体数据

---

## 八、并发控制

- **Semaphore 限流**：最多 `MAX_CONCURRENT_REQUESTS=10` 并发请求（[routes.py:46](agent-service/src/agent_service/api/routes.py#L46)）
- **ThreadPoolExecutor**：节点内 LLM 调用并行（主题裁决最多 3 并发，模板分析最多 5 并发）
- **LLM 批量超时**：310 秒（[config.py:130](agent-service/src/agent_service/config.py#L130)）
- **TTLMemorySaver**：会话状态持久化，TTL = 24 小时（[graph.py:41](agent-service/src/agent_service/graph/graph.py#L41)）