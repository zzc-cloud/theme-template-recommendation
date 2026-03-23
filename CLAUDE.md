# Theme Template Recommendation

## 角色定位

魔数师主题和模板推荐专家，基于用户自然语言问题推荐合适的 THEME（主题）和 TEMPLATE（模板）。

## 项目概述

本项目是从 Smart Query 项目中独立出来的主题模板推荐系统，专注于帮助用户在"魔数师"数据分析平台中快速定位合适的主题和模板。

**核心功能**：
- **需求澄清**：将用户问题映射到标准术语
- **主题推荐**：推荐合适的业务主题
- **指标推荐**：推荐主题下可勾选的核心指标
- **模板推荐**：推荐可直接使用的透视分析/万能查询模板

---

## Skills

| Skill | 说明 |
|-------|------|
| `theme-template-recommendation` | 主 Skill：四阶段执行流程 |
| `requirement-clarification` | 阶段 0：需求澄清（前置依赖） |

---

## MCP 工具

### theme-ontology（Neo4j - 8 个工具）

| 工具 | 功能 | 阶段 |
|------|------|------|
| `search_terms_by_keyword` | 搜索业务术语 | 0 |
| `get_tables_by_term` | 术语→关联表 | 0 |
| `get_indicator_full_path` | 指标完整路径（含 THEME） | 1 |
| `get_indicator_field_mapping` | 指标字段映射 | 1 |
| `get_table_terms` | 表字段术语描述 | 1 |
| `batch_get_indicators_themes` | 批量提取 THEME | 2 |
| `get_theme_templates_with_coverage` | 主题模板+覆盖率 | 3 |
| `get_template_indicators` | 模板包含的指标 | 3 |

### theme-resources（MySQL - 3 个工具）

| 工具 | 功能 | 阶段 |
|------|------|------|
| `find_indicators_by_table` | 表→关联指标 | 0 |
| `get_indicator_field_mapping_mysql` | 指标→字段映射（MySQL） | 1 |
| `get_table_columns_bigmeta` | 表字段详情 | 1 |

---

## 执行流程

```
用户问题
    │
    ▼
┌─────────────────────────────────────────┐
│  阶段 0：需求澄清                         │
│  - 关键词提取与术语搜索                    │
│  - 匹配度计算与用户联动                    │
│  - 需求支撑评估                           │
│  → 输出：normalized_question              │
│  → 输出：related_indicator_ids            │
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

本项目依赖两个数据源：

1. **Neo4j**：存储魔数师指标层（THEME、TEMPLATE、INDICATOR、TERM）
2. **MySQL**：存储指标-字段映射关系

配置文件位于 `mcp-server/.env`。

---

## 目录结构

```
theme-template-recommendation/
├── .claude/
│   └── skills/
│       ├── theme-template-recommendation/
│       │   └── SKILL.md
│       └── requirement-clarification/
│           └── SKILL.md
├── mcp-server/
│   ├── venv/
│   ├── theme_ontology_server.py
│   ├── theme_resources_server.py
│   ├── requirements.txt
│   └── .env
├── .mcp.json
├── CLAUDE.md
└── README.md
```
