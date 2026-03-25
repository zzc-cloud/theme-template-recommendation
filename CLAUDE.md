# Theme Template Recommendation

## 角色定位

魔数师主题和模板推荐专家，基于用户自然语言问题推荐合适的 THEME（主题）和 TEMPLATE（模板）。

## 项目概述

本项目是从 Smart Query 项目中独立出来的主题模板推荐系统，专注于帮助用户在"魔数师"数据分析平台中快速定位合适的主题和模板。

**核心功能**：
- **需求澄清**：将用户问题通过向量化语义搜索直接映射到魔数师指标
- **主题推荐**：推荐合适的业务主题
- **指标推荐**：推荐主题下可勾选的核心指标
- **模板推荐**：推荐可直接使用的透视分析/万能查询模板

---

## Skills

| Skill | 说明 |
|-------|------|
| `theme-template-recommendation` | 主 Skill：四阶段执行流程（含需求澄清） |

---

## MCP 工具

### theme-vector（Chroma + SiliconFlow - 1 个工具）

| 工具 | 功能 | 阶段 |
|------|------|------|
| `search_indicators_by_vector` | **向量化语义搜索魔数师指标** | 0 |

### theme-ontology（Neo4j - 8 个工具）

| 工具 | 功能 | 阶段 |
|------|------|------|
| `aggregate_themes_from_indicators` | 从指标列表聚合候选主题（按频次排序） | 1 |
| `get_theme_filter_indicators` | 获取主题下全量筛选指标（时间+机构） | 1 |
| `get_theme_analysis_indicators` | 获取主题下全量分析指标 | 1 |
| `get_indicator_field_mapping` | 【语义增强】指标字段映射 | 1 |
| `get_table_terms` | 【语义增强】表字段术语描述 | 1 |
| `get_indicator_full_path` | 指标完整路径（含 THEME） | 1 |
| `get_theme_templates_with_coverage` | 主题模板+覆盖率 | 2 |
| `get_template_indicators` | 模板包含的指标 | 2 |

### theme-resources（MySQL - 3 个工具）

| 工具 | 功能 | 阶段 |
|------|------|------|
| `find_indicators_by_table` | 表→关联指标 | 0 |
| `get_indicator_field_mapping_mysql` | 指标→字段映射（MySQL） | 1 |
| `get_table_columns_bigmeta` | 表字段详情 | 1 |

### Chrome MCP（浏览器自动化）

当需要访问具体 URL、抓取页面内容或截图时，使用 MCP Chrome 浏览器。**不要尝试 WebFetch**（该工具不可用）。

**配置**：项目已配置 MCP Chrome，位于 [.mcp.json](.mcp.json)。

**可用工具**：

| 工具                        | 功能                           |
| --------------------------- | ------------------------------ |
| `mcp__chrome__get_windows_and_tabs` | 获取所有窗口和标签页           |
| `mcp__chrome__navigate`     | 导航到指定 URL                  |
| `mcp__chrome__get_visible_text` | 获取页面可见文本               |
| `mcp__chrome__click_element` | 点击页面元素                   |
| `mcp__chrome__fill_form_input` | 填写表单输入框                 |
| `mcp__chrome__submit_form`  | 提交表单                       |
| `mcp__chrome__take_screenshot` | 截取页面截图                   |
| `mcp__chrome__execute_javascript` | 执行 JavaScript               |

**典型工作流**：

1. **搜索 + 抓取**：先用 `WebSearch` 获取搜索结果 → 导航到目标 URL → 用 MCP Chrome 抓取页面内容或截图
2. **直接抓取**：导航到目标 URL → 获取页面文本或截图
3. **表单填写**：自动填写和提交表单

**注意**：使用 MCP Chrome 前请确保 Chrome 浏览器已打开。

---

## 执行流程

```
用户问题
    │
    ▼
┌─────────────────────────────────────────┐
│  阶段 0：需求澄清                         │
│  - 关键词提取                             │
│  - 向量化语义搜索（直接搜索指标）           │
│  - 用户确认映射结果                       │
│  - 问题改写                               │
│  → 输出：normalized_question              │
│  → 输出：confirmed_indicators           │
│  → 输出：filter_indicators              │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│  阶段 1：指标信息获取                      │
│  - 获取指标完整业务路径（含 THEME）        │
│  - 语义增强：获取字段描述                  │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│  阶段 2：主题聚合与决策                    │
│  - 从匹配指标中聚合 THEME                 │
│  - 选择 Top 3 作为推荐主题                │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│  阶段 3：模板推荐                          │
│  - 程序化初筛（覆盖率 >= 80%）            │
│  - LLM 精细化判定（用户主动触发）          │
└─────────────────────────────────────────┘
```

---

## 与 Smart Query 的区别

| 维度 | Smart Query | 主题模板推荐 |
|------|-------------|--------------|
| **目标** | 定位物理表和字段 | 推荐主题和模板 |
| **输出** | 主表 + 字段映射 | 主题 + 指标 + 模板 |
| **用户场景** | 需要 SQL 或了解数据存储 | 需要在魔数师平台拖拉拽分析 |
| **核心路径** | 指标/场景/术语 → 物理表 | 指标 → 主题 → 模板 |

---

## 数据库依赖

本项目依赖三个数据源：

1. **Chroma**：存储魔数师指标的向量化数据（用于语义搜索）
2. **Neo4j**：存储魔数师指标层（THEME、TEMPLATE、INDICATOR、TERM）
3. **MySQL**：存储指标-字段映射关系

配置文件位于 `mcp-server/.env`。

**前置准备**：首次使用需要运行 `python scripts/indicator_vectorizer.py --rebuild` 对指标进行向量化。

---

## 目录结构

```
theme-template-recommendation/
├── .claude/
│   └── skills/
│       └── theme-template-recommendation/
│           └── SKILL.md
├── mcp-server/
│   ├── venv/
│   ├── theme_ontology_server.py
│   ├── theme_resources_server.py
│   ├── theme_vector_server.py        # 向量搜索 MCP 服务器
│   ├── data/indicators_vector/       # Chroma 向量库存储目录
│   ├── scripts/
│   │   └── indicator_vectorizer.py   # 指标向量化脚本
│   ├── requirements.txt
│   └── .env
├── .mcp.json
├── CLAUDE.md
└── README.md
```
