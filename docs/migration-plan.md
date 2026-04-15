# 魔数师主题模板推荐系统 — 整改方案与迁移计划

> **文档版本**：v1.0
> **编制日期**：2026-04-13
> **涉及提交**：5829be2（主 Skill 优化 + MCP 服务器）、7eb1cb9（子 Skill 新增）

---

## 一、整改方案汇总

### (a) 魔数师主题模板推荐主 Skill 优化点

#### 提交 5829be2 — 主 Skill 迭代精炼流程重构

| 优化项 | 原设计 | 新设计 | 影响范围 |
|--------|--------|--------|----------|
| **意图锚定机制** | 无约束，搜索词可自由修正 | 新增核心原则：修正词必须保留原始语义单元，偏离度 > 50% 触发警告并放弃修正 | 阶段 0.3 |
| **语义单元定义** | 字面 token 拆分（如"结算客户"→拆成"结算"+"客户"） | 专有业务术语不可拆分（如"结算客户"是一个独立语义单元） | 阶段 0.3 |
| **虚假收敛检测** | 无反向验证，Top-1 >= 0.80 即认为收敛 | 两步验证：初始收敛 + **反向语义验证**，防止高相似度但语义偏离的虚假收敛 | 阶段 0.3 |
| **剔除后重评估** | 发现虚假收敛后直接跳过 | 发现虚假收敛后，将该 Top-1 加入排除列表，重新评估剩余结果的收敛性（支持多级排除） | 阶段 0.3 |
| **超时出口逻辑** | 超时后简单退出发散 | 收敛率 >= 50% 且核心概念已收敛 → 允许未收敛概念进低置信度；收敛率 < 50% → 向用户说明质量不足 | 阶段 0.3 |
| **收敛率评估** | 无收敛率概念 | 引入收敛率 = 已收敛概念数 / 原始概念总数，作为迭代是否继续的判定依据 | 阶段 0.3 |
| **Jaccard 勾选引导增强** | 仅凭指标别名猜测主题 | 通过 Neo4j 批量查询每个维度的真实 theme_id，计算**加权 Jaccard 相似度** | 阶段 0.4 |
| **权重衰减规则** | 无差异化权重 | 相似度 >= 0.80 → 全权重；>= 0.50 → 半权重；< 0.50 → 极低权重或排除 | 阶段 0.4 |

#### 提交 7eb1cb9 — 子 Skill 新增

| 模块 | 核心内容 |
|------|----------|
| **双路径并行探查** | 路径A（统计聚合）+ 路径B（层级导航）并行执行，合并去重后共同进入后续裁决 |
| **相似度加权聚合** | 聚合 THEME 时按相似度加权：weighted_frequency = Σ(indicator_similarity_score)，按加权频次降序排序 |
| **动态阈值选择** | 所有加权频次 >= 0.6 的 THEME 作为候选主题；无 >= 0.6 时兜底返回最高者 |
| **指标去重规则** | 同一 indicator_id 在不同维度重复时，取最大相似度计入加权和 |
| **批量层级导航** | 调用 `get_sector_themes()` 一次性获取板块下所有深度的主题，无需逐层探索 |
| **两步 LLM 裁决** | 同一次 LLM 调用中完成：主题可用性判断 + 指标精筛 |
| **模板降级策略** | 无覆盖率 >= 80% 达标模板时，降级推荐覆盖率最高 + 热度最高的模板（最多 2 个） |
| **缺口分析 Prompt** | 对每个模板分析缺失指标的重要程度（核心/辅助/可忽略）、影响说明与补充建议 |

---

### (b) 主题定位子 Skill（theme-template-selection）的优化点

| 优化点 | 说明 |
|--------|------|
| **子 Skill 独立化** | 从主 Skill 中拆出主题定位和模板推荐逻辑，作为独立子 Skill，被主 Skill 委派调用 |
| **相似度加权机制** | 聚合 THEME 时按指标相似度加权，避免低质量指标主导主题排序 |
| **动态阈值兜底** | 0.6 加权和阈值 + 最低兜底策略，确保总有结果 |
| **指标别名去重** | 筛选/分析指标按别名去重，同别名只保留一个，避免重复展示 |
| **双路径合并** | 统计聚合 + 层级导航各自生成候选主题后合并去重，保留全部上下文 |
| **批量板块探索** | 一次获取板块下所有主题，减少工具调用次数 |
| **模板降级推荐** | 覆盖率 < 80% 时明确告知降级，并说明降级原因和推荐依据 |
| **可用性等级标记** | 可直接使用(✅) / 补充后可用(🔧) / 缺口较大建议谨慎(⚠️) 三级标记 |

---

### (c) 新建的 MCP 工具集

#### theme-ontology（MCP 服务器 — Neo4j 数据源）

共 **12 个工具**，分为 5 类：

**层级导航工具（阶段 1.2）**

| 工具 | 功能 | 核心 Cypher |
|------|------|-------------|
| `get_sectors_from_root` | 获取"自主分析"下的所有板块（SECTOR） | MATCH (root:CATEGORY {alias: '自主分析'})-[:HAS_CHILD]->(s:SECTOR) |
| `get_sector_themes` | 批量获取指定板块下所有深度的 THEME（含完整路径，一次查询） | MATCH (sector)-[:HAS_CHILD*]->(theme:THEME) |
| `get_children_of_node` | 获取任意节点的直接子节点（支持类型过滤 + 同级 THEMEs） | MATCH (parent)-[:HAS_CHILD]->(child) |
| `get_path_to_theme` | 获取从根节点到主题的完整路径（含同级主题对比） | MATCH path = (entry)-[:HAS_CHILD*]->(theme) |

**主题聚合与指标补全工具（阶段 1.1/1.3）**

| 工具 | 功能 |
|------|------|
| `aggregate_themes_from_indicators` | 从指标列表聚合候选主题（按频次排序，含完整路径） |
| `batch_get_indicator_themes` | 批量获取指标的主题归属（用于 Jaccard 勾选引导，一次查询） |
| `get_theme_filter_indicators` | 获取主题下全量筛选指标（时间 + 机构，按别名去重） |
| `get_theme_analysis_indicators` | 获取主题下全量分析指标（= 全量 - 筛选指标） |
| `get_theme_full_path` | 获取主题的完整导航路径（用于展示） |

**语义增强工具（阶段 1）**

| 工具 | 功能 |
|------|------|
| `get_indicator_field_mapping` | 指标字段映射 |
| `get_table_terms` | 表字段术语描述 |
| `get_indicator_full_path` | 指标完整路径（含 THEME） |

**模板推荐工具（阶段 2）**

| 工具 | 功能 |
|------|------|
| `get_theme_templates_with_coverage` | 获取主题模板 + 覆盖率计算（含全量指标用于 LLM 缺口分析） |

#### theme-vector（MCP 服务器 — Chroma + SiliconFlow 数据源）

| 工具 | 功能 |
|------|------|
| `search_indicators_by_vector` | 基于向量化语义匹配搜索魔数师指标（阶段 0 核心工具） |
| `get_vector_stats` | 获取向量库统计信息 |

---

## 二、Agent Service 项目迁移与改造步骤

> **架构说明**：本项目不使用 MCP 协议，直接在 agent-service 项目中实现 Neo4j/Chroma 的 Python 调用接口。
> MCP 服务器（theme-ontology/theme-vector）作为参考实现，其中的 Cypher 查询逻辑和业务规则可直接迁移到 agent-service 的 `tools/` 目录中。

### (a) 特性完整迁移流程

#### 第一阶段：复用 MCP 服务器中的业务逻辑（预计 0.5 天）

**目标**：将 MCP 服务器中的 Cypher 查询逻辑和业务规则迁移到 agent-service

MCP 服务器（`mcp-server/theme_ontology_server.py`）已实现了所有需要的查询逻辑，迁移工作是将这些逻辑整合到 agent-service 现有架构中。

**迁移清单**：

| MCP 服务器文件 | 迁移到 agent-service | 说明 |
|---------------|---------------------|------|
| `theme_ontology_server.py` | `tools/theme_tools.py` | Neo4j 查询工具（主题聚合、层级导航、指标补全、模板推荐） |
| `theme_ontology_server.py` | `tools/ontology_tools.py`（新增） | 语义增强工具（指标字段映射、表字段术语） |
| `theme_vector_server.py` | `tools/vector_search.py` | 向量搜索工具（参考实现，直接复用） |

**步骤 1.1**：整合主题本体工具

将 `theme_ontology_server.py` 中的所有工具函数迁移到 agent-service 的 `tools/` 目录：

```
agent-service/src/agent_service/tools/
├── __init__.py
├── theme_tools.py      ← 扩展现有文件，新增以下函数
│   ├── aggregate_themes_from_indicators()     # 主题聚合
│   ├── batch_get_indicator_themes()           # 批量指标主题映射
│   ├── get_theme_filter_indicators()          # 筛选指标
│   ├── get_theme_analysis_indicators()         # 分析指标
│   ├── get_theme_full_path()                   # 主题完整路径
│   ├── get_sectors_from_root()                 # 获取板块列表
│   ├── get_sector_themes()                     # 批量获取板块主题
│   ├── get_children_of_node()                 # 节点子节点
│   ├── get_path_to_theme()                     # 主题导航路径
│   └── get_theme_templates_with_coverage()     # 模板推荐
├── ontology_tools.py    ← 新增文件（语义增强）
│   ├── get_indicator_field_mapping()           # 指标字段映射
│   ├── get_table_terms()                       # 表字段术语
│   └── get_indicator_full_path()              # 指标完整路径
└── vector_search.py     ← 扩展现有文件
    ├── search_indicators_by_vector()           # 向量语义搜索
    └── get_vector_stats()                      # 向量库统计
```

**步骤 1.2**：确认依赖已具备

agent-service 的 `requirements.txt` 应已包含：

```bash
neo4j>=5.0.0      # Neo4j 驱动
chromadb>=0.4.0   # 向量数据库
requests>=2.31.0  # HTTP 请求（向量化 API）
python-dotenv     # 环境变量
```

如缺少则需添加。

#### 第二阶段：节点函数重构（预计 3 天）

**目标**：将各阶段函数适配新工具签名，并引入新增特性

| 步骤 | 节点函数 | 核心改动 |
|------|----------|---------|
| 2.1 | `classify_and_iterate`（阶段 0.2-0.3） | 意图锚定机制、两步收敛验证、语义单元提取、虚假收敛多级排除、超时收敛率评估 |
| 2.2 | `wait_for_confirmation`（阶段 0.4） | 批量 `batch_get_indicator_themes` 调用、加权 Jaccard 相似度计算、权重衰减规则 |
| 2.3 | `aggregate_themes`（阶段 1.1） | 相似度加权聚合、指标去重（取最大相似度）、动态阈值 >= 0.6 + 兜底 |
| 2.4 | 新增 `navigate_hierarchy`（阶段 1.2） | 双路径合并：统计聚合 + 层级导航并行，合并去重 |
| 2.5 | `judge_themes`（阶段 1.3） | 全量指标获取 + 两步 LLM 裁决（主题可用性 + 指标精筛） |
| 2.6 | `retrieve_templates`（阶段 2.1） | 覆盖率 >= 80% 达标模板 + 降级推荐（覆盖率最高 + 热度最高） |
| 2.7 | `analyze_templates`（阶段 2.2） | 缺口分析（核心/辅助/可忽略）、可用性等级（✅/🔧/⚠️） |

涉及文件：
- `agent-service/src/agent_service/graph/nodes.py` — 节点函数重构 + 新增
- `agent-service/src/agent_service/llm/prompts.py` — 新增/更新 Prompt 模板
- `agent-service/src/agent_service/llm/models.py` — 新增 Pydantic 响应模型

#### 第三阶段：Skill 文档迁移（预计 0.5 天）

```bash
# 复制 Skill 文档到 agent-service 项目
cp theme-template-recommendation/.claude/skills/theme-template-recommendation/SKILL.md \
   agent-service/docs/
cp theme-template-recommendation/.claude/skills/theme-template-selection/SKILL.md \
   agent-service/docs/
```

---

### (b) 针对新工具的项目适配

#### B.1 工具函数签名对照（MCP → Python）

将 MCP 工具的函数签名迁移为 Python 函数，参数和返回值完全对齐：

| MCP 工具函数 | 迁移为 Python 函数 | 文件位置 |
|-------------|-------------------|---------|
| `aggregate_themes_from_indicators` | `aggregate_themes_from_indicators()` | `tools/theme_tools.py` |
| `batch_get_indicator_themes` | `batch_get_indicator_themes()` | `tools/theme_tools.py` |
| `get_theme_filter_indicators` | `get_theme_filter_indicators()` | `tools/theme_tools.py` |
| `get_theme_analysis_indicators` | `get_theme_analysis_indicators()` | `tools/theme_tools.py` |
| `get_theme_full_path` | `get_theme_full_path()` | `tools/theme_tools.py` |
| `get_sectors_from_root` | `get_sectors_from_root()` | `tools/theme_tools.py` |
| `get_sector_themes` | `get_sector_themes()` | `tools/theme_tools.py` |
| `get_children_of_node` | `get_children_of_node()` | `tools/theme_tools.py` |
| `get_path_to_theme` | `get_path_to_theme()` | `tools/theme_tools.py` |
| `get_theme_templates_with_coverage` | `get_theme_templates_with_coverage()` | `tools/theme_tools.py` |
| `search_indicators_by_vector` | `search_indicators_by_vector()` | `tools/vector_search.py` |

#### B.2 API 兼容性

SSE 流式输出格式无需修改，但以下事件内容需适配：

| 事件类型 | 需适配内容 |
|----------|-----------|
| `progress` | 展示意图锚定过程（偏离度警告、虚假收敛拦截） |
| `stage_complete` | 展示收敛率评估结果（阶段 0.3 完成后） |
| `interrupt` | 增加维度冲突分析（Jaccard 矩阵内容） |
| `final` | 主题数据增加 weighted_frequency；模板增加可用性等级标记 |

---

### (c) 整体架构调整说明

#### C.1 架构演进方向

```
旧架构：                              新架构：
┌──────────────────┐                  ┌────────────────────────┐
│  agent-service   │                  │   agent-service         │
│                  │                  │                         │
│  graph/         │                  │   graph/               │
│   nodes.py      │──────适应──────▶ │    nodes.py            │
│                  │   新工具签名     │                         │
│  tools/          │                  │   tools/                │
│   直接 Cypher    │──────迁移──────▶ │    Python 调用接口      │
│   直接 Chroma    │   MCP 业务逻辑   │    (复用 MCP Cypher)    │
│                  │                  │         │               │
│                  │                  │         ▼               │
│                  │                  │   Neo4j + Chroma        │
└──────────────────┘                  └────────────────────────┘
```

#### C.2 关键架构变化

| 变化维度 | 旧方案 | 新方案 | 优势 |
|----------|--------|--------|------|
| **数据访问** | 分散的 Cypher 查询 | 统一工具函数封装（参考 MCP 实现） | 逻辑集中、签名清晰 |
| **主题聚合** | 简单频次统计 | 相似度加权频次 | 排序更准确 |
| **收敛验证** | 单步判定 | 两步验证（防虚假收敛） | 结果更可靠 |
| **勾选引导** | 指标别名猜测 | 加权 Jaccard + Neo4j 真实主题 | 引导更精准 |
| **层级探查** | 逐层调用 | 批量获取 + LLM 筛选 | 减少调用次数 |
| **模板推荐** | 仅返回模板 | 模板 + 覆盖率 + 缺口分析 | 可用性更明确 |

#### C.3 依赖关系图

```
agent-service (主项目)
│
├── graph/nodes.py
│   └── 调用 tools/ 中的函数
│
├── tools/
│   ├── theme_tools.py          ← 复用 MCP 的 Cypher 逻辑
│   │   └── 直接连接 Neo4j
│   ├── ontology_tools.py       ← 新增（语义增强）
│   │   └── 直接连接 Neo4j
│   └── vector_search.py        ← 复用 MCP 的向量搜索逻辑
│       └── 直接连接 Chroma + SiliconFlow API
│
└── llm/
    ├── client.py (复用)
    ├── models.py (新增/更新 Pydantic 模型)
    └── prompts.py (新增/更新 Prompt 模板)
```

---

## 三、实施优先级与建议

### 优先级排序

| 优先级 | 任务 | 理由 |
|--------|------|------|
| P0 | tools/ 目录工具函数整合（复用 MCP Cypher 逻辑） | 其他所有改动依赖此基础设施 |
| P0 | 阶段 0.3 迭代精炼重构（意图锚定） | 核心质量改进，防止虚假收敛 |
| P0 | 阶段 1.1 主题聚合加权机制 | 影响后续所有阶段的输入质量 |
| P1 | 阶段 0.4 加权 Jaccard 引导 | 提升用户勾选准确性 |
| P1 | 阶段 1.2 双路径并行探查 | 扩展主题发现能力 |
| P1 | 阶段 2 模板降级 + 缺口分析 | 提升模板推荐可用性 |
| P2 | Skill 文档迁移 | 文档固化，非关键路径 |

### 风险点与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 虚假收敛误判 | LLM 语义判断不稳定 | 设置多级排除机制，允许人工干预 |
| 加权 Jaccard 阈值 0.5 | 可能在某些场景下过于严格 | 阈值可配置化，支持调参 |
| 收敛率阈值 50% | 低收敛率场景下可能提前放弃 | 设置最低兜底，确保总有输出 |

### 测试建议

1. **回归测试**：用已有测试用例验证旧功能未被破坏
2. **意图锚定专项测试**：构造易触发虚假收敛的边界案例
3. **加权 Jaccard 测试**：验证不同相似度分布下的排序正确性
4. **降级策略测试**：验证无达标模板时的降级推荐质量
5. **端到端测试**：用真实用户问题走完整个流程

---

*文档由 Claude Code 生成 | 2026-04-13*

```
改动点：
1. 新增层级导航路径：从 get_sectors_from_root 开始
2. 批量调用 get_sector_themes 获取板块下所有主题
3. LLM 在批量结果中筛选候选主题
4. 两条路径候选主题合并去重
```

涉及文件：`agent-service/src/agent_service/graph/nodes.py` 新增节点函数：
- `navigate_hierarchy` — 层级导航路径
- `merge_candidate_themes` — 合并去重

**步骤 3.5**：重构 `judge_themes`（阶段 1.3）

```
改动点：
1. 从 get_theme_filter_indicators 和 get_theme_analysis_indicators 获取全量指标
2. 两步裁决在同一次 LLM 调用中完成
3. 输出结构增加 selected_filter_indicators 和 selected_analysis_indicators
```

**步骤 3.6**：重构 `retrieve_templates`（阶段 2.1）

```
改动点：
1. 调用 get_theme_templates_with_coverage（带降级策略）
2. 覆盖率 >= 80% 的达标模板按覆盖率降序返回
3. 无达标模板时返回覆盖率最高 + 热度最高的模板（最多 2 个）
```

**步骤 3.7**：重构 `analyze_templates`（阶段 2.2）

```
改动点：
1. 利用模板的全量指标信息（all_template_indicators）进行缺口分析
2. 对每个缺失指标判定重要程度（核心/辅助/可忽略）
3. 输出可用性等级：可直接使用 / 补充后可用 / 缺口较大建议谨慎
```

#### 第四阶段：Skill 文档迁移（预计 0.5 天）

**目标**：将 Skill 逻辑固化到项目文档中

```bash
# 复制 Skill 文档到 agent-service 项目
cp theme-template-recommendation/.claude/skills/theme-template-recommendation/SKILL.md \
   agent-service/docs/SKILL.md
cp theme-template-recommendation/.claude/skills/theme-template-selection/SKILL.md \
   agent-service/docs/SKILL-sub-theme-selection.md
```

#### 第二阶段：节点函数重构（预计 3 天）

**目标**：将各阶段函数适配新工具签名，并引入新增特性

| 步骤 | 节点函数 | 核心改动 |
|------|----------|---------|
| 2.1 | `classify_and_iterate`（阶段 0.2-0.3） | 意图锚定机制、两步收敛验证、语义单元提取、虚假收敛多级排除、超时收敛率评估 |
| 2.2 | `wait_for_confirmation`（阶段 0.4） | 批量 `batch_get_indicator_themes` 调用、加权 Jaccard 相似度计算、权重衰减规则 |
| 2.3 | `aggregate_themes`（阶段 1.1） | 相似度加权聚合、指标去重（取最大相似度）、动态阈值 >= 0.6 + 兜底 |
| 2.4 | 新增 `navigate_hierarchy`（阶段 1.2） | 双路径合并：统计聚合 + 层级导航并行，合并去重 |
| 2.5 | `judge_themes`（阶段 1.3） | 全量指标获取 + 两步 LLM 裁决（主题可用性 + 指标精筛） |
| 2.6 | `retrieve_templates`（阶段 2.1） | 覆盖率 >= 80% 达标模板 + 降级推荐（覆盖率最高 + 热度最高） |
| 2.7 | `analyze_templates`（阶段 2.2） | 缺口分析（核心/辅助/可忽略）、可用性等级（✅/🔧/⚠️） |

涉及文件：
- `agent-service/src/agent_service/graph/nodes.py` — 节点函数重构 + 新增
- `agent-service/src/agent_service/llm/prompts.py` — 新增/更新 Prompt 模板
- `agent-service/src/agent_service/llm/models.py` — 新增 Pydantic 响应模型

#### 第三阶段：Skill 文档迁移（预计 0.5 天）

```bash
# 复制 Skill 文档到 agent-service 项目
cp theme-template-recommendation/.claude/skills/theme-template-recommendation/SKILL.md \
   agent-service/docs/
cp theme-template-recommendation/.claude/skills/theme-template-selection/SKILL.md \
   agent-service/docs/
```

---

### (b) 针对新工具的项目适配

#### B.1 工具函数签名对照（MCP → Python）

将 MCP 工具的函数签名迁移为 Python 函数，参数和返回值完全对齐：

| MCP 工具函数 | 迁移为 Python 函数 | 文件位置 |
|-------------|-------------------|---------|
| `aggregate_themes_from_indicators` | `aggregate_themes_from_indicators()` | `tools/theme_tools.py` |
| `batch_get_indicator_themes` | `batch_get_indicator_themes()` | `tools/theme_tools.py` |
| `get_theme_filter_indicators` | `get_theme_filter_indicators()` | `tools/theme_tools.py` |
| `get_theme_analysis_indicators` | `get_theme_analysis_indicators()` | `tools/theme_tools.py` |
| `get_theme_full_path` | `get_theme_full_path()` | `tools/theme_tools.py` |
| `get_sectors_from_root` | `get_sectors_from_root()` | `tools/theme_tools.py` |
| `get_sector_themes` | `get_sector_themes()` | `tools/theme_tools.py` |
| `get_children_of_node` | `get_children_of_node()` | `tools/theme_tools.py` |
| `get_path_to_theme` | `get_path_to_theme()` | `tools/theme_tools.py` |
| `get_theme_templates_with_coverage` | `get_theme_templates_with_coverage()` | `tools/theme_tools.py` |
| `search_indicators_by_vector` | `search_indicators_by_vector()` | `tools/vector_search.py` |

#### B.2 API 兼容性

SSE 流式输出格式无需修改，但以下事件内容需适配：

| 事件类型 | 需适配内容 |
|----------|-----------|
| `progress` | 展示意图锚定过程（偏离度警告、虚假收敛拦截） |
| `stage_complete` | 展示收敛率评估结果（阶段 0.3 完成后） |
| `interrupt` | 增加维度冲突分析（Jaccard 矩阵内容） |
| `final` | 主题数据增加 weighted_frequency；模板增加可用性等级标记 |

---

### (c) 整体架构调整说明

#### C.1 架构演进方向

```
旧架构：                              新架构：
┌──────────────────┐                  ┌────────────────────────┐
│  agent-service   │                  │   agent-service         │
│                  │                  │                         │
│  graph/         │                  │   graph/               │
│   nodes.py      │──────适应──────▶ │    nodes.py            │
│                  │   新工具签名     │                         │
│  tools/          │                  │   tools/                │
│   直接 Cypher    │──────迁移──────▶ │    Python 调用接口      │
│   直接 Chroma    │   MCP 业务逻辑   │    (复用 MCP Cypher)    │
│                  │                  │         │               │
│                  │                  │         ▼               │
│                  │                  │   Neo4j + Chroma        │
└──────────────────┘                  └────────────────────────┘
```

#### C.2 关键架构变化

| 变化维度 | 旧方案 | 新方案 | 优势 |
|----------|--------|--------|------|
| **数据访问** | 分散的 Cypher 查询 | 统一工具函数封装（参考 MCP 实现） | 逻辑集中、签名清晰 |
| **主题聚合** | 简单频次统计 | 相似度加权频次 | 排序更准确 |
| **收敛验证** | 单步判定 | 两步验证（防虚假收敛） | 结果更可靠 |
| **勾选引导** | 指标别名猜测 | 加权 Jaccard + Neo4j 真实主题 | 引导更精准 |
| **层级探查** | 逐层调用 | 批量获取 + LLM 筛选 | 减少调用次数 |
| **模板推荐** | 仅返回模板 | 模板 + 覆盖率 + 缺口分析 | 可用性更明确 |

#### C.3 依赖关系图

```
agent-service (主项目)
│
├── graph/nodes.py
│   └── 调用 tools/ 中的函数
│
├── tools/
│   ├── theme_tools.py          ← 复用 MCP 的 Cypher 逻辑
│   │   └── 直接连接 Neo4j
│   ├── ontology_tools.py       ← 新增（语义增强）
│   │   └── 直接连接 Neo4j
│   └── vector_search.py        ← 复用 MCP 的向量搜索逻辑
│       └── 直接连接 Chroma + SiliconFlow API
│
└── llm/
    ├── client.py (复用)
    ├── models.py (新增/更新 Pydantic 模型)
    └── prompts.py (新增/更新 Prompt 模板)
```

---

## 三、实施优先级与建议

### 优先级排序

| 优先级 | 任务 | 理由 |
|--------|------|------|
| P0 | tools/ 目录工具函数整合（复用 MCP Cypher 逻辑） | 其他所有改动依赖此基础设施 |
| P0 | 阶段 0.3 迭代精炼重构（意图锚定） | 核心质量改进，防止虚假收敛 |
| P0 | 阶段 1.1 主题聚合加权机制 | 影响后续所有阶段的输入质量 |
| P1 | 阶段 0.4 加权 Jaccard 引导 | 提升用户勾选准确性 |
| P1 | 阶段 1.2 双路径并行探查 | 扩展主题发现能力 |
| P1 | 阶段 2 模板降级 + 缺口分析 | 提升模板推荐可用性 |
| P2 | Skill 文档迁移 | 文档固化，非关键路径 |

### 风险点与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 虚假收敛误判 | LLM 语义判断不稳定 | 设置多级排除机制，允许人工干预 |
| 加权 Jaccard 阈值 0.5 | 可能在某些场景下过于严格 | 阈值可配置化，支持调参 |
| 收敛率阈值 50% | 低收敛率场景下可能提前放弃 | 设置最低兜底，确保总有输出 |

### 测试建议

1. **回归测试**：用已有测试用例验证旧功能未被破坏
2. **意图锚定专项测试**：构造易触发虚假收敛的边界案例
3. **加权 Jaccard 测试**：验证不同相似度分布下的排序正确性
4. **降级策略测试**：验证无达标模板时的降级推荐质量
5. **端到端测试**：用真实用户问题走完整个流程

---

*文档由 Claude Code 生成 | 2026-04-13*
