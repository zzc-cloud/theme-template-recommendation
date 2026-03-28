# Theme Template Recommendation

魔数师主题和模板推荐系统，基于用户自然语言问题推荐合适的 THEME（主题）和 TEMPLATE（模板）。

## 功能特性

- **需求澄清**：将用户问题映射到标准术语，支持用户联动确认
- **主题推荐**：基于匹配指标聚合 THEME，推荐合适的业务主题
- **指标推荐**：推荐主题下可勾选的核心指标
- **模板推荐**：推荐可直接使用的透视分析/万能查询模板，支持覆盖率计算和 LLM 精细化分析

## 快速开始

### 1. 配置环境变量

```bash
cd mcp-server
cp .env.example .env
# 编辑 .env 文件，填入数据库连接信息
```

### 2. 创建 Python 虚拟环境

```bash
cd mcp-server
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. 在 Claude Code 中使用

在 Claude Code 中打开本项目，然后使用以下 Skill：

- `theme-template-recommendation`：主 Skill，执行完整的推荐流程
- `requirement-clarification`：阶段 0 需求澄清

## 使用示例

```
用户问题: "我想分析对公贷款的风险"

→ [阶段 0] 需求澄清
   - "对公贷款" → 对公贷款 (CORP_LOAN)
   - "风险" → 五级分类 (FIVE_CLS_CD) [用户确认]
   - 关联指标: 15 个

→ [阶段 1] 指标信息获取
   - 获取指标完整业务路径
   - 语义增强（术语描述）

→ [阶段 2] 主题聚合
   - THEME.DS_SSA_RISK.对公贷款借据: 15 个指标
   - THEME.DS_SSA_RISK.对公贷款客户风险: 8 个指标

→ [阶段 3] 模板推荐
   ✅ 对公贷款五级分类分析 (覆盖率 100%)
   ✅ 对公贷款逾期统计 (覆盖率 80%)
```

## 数据来源

本项目依赖两个数据源：

| 数据源 | 用途 | 说明 |
|--------|------|------|
| **Chroma** | 向量语义搜索 | 存储魔数师指标的向量化数据，用于语义匹配 |
| **Neo4j** | 本体关系存储 | 存储 THEME、TEMPLATE、INDICATOR、TERM 的关系图 |

## MCP 工具

### 数据源对应关系

| 阶段 | 工具 | 数据来源 | 功能 |
|------|------|----------|------|
| **阶段 0** | `search_indicators_by_vector` | **Chroma** | 向量化语义搜索魔数师指标 |
| **阶段 1** | `aggregate_themes_from_indicators` | **Neo4j** | 从指标列表聚合候选主题（按频次排序） |
| **阶段 1** | `get_theme_filter_indicators` | **Neo4j** | 获取主题下全量筛选指标（时间+机构） |
| **阶段 1** | `get_theme_analysis_indicators` | **Neo4j** | 获取主题下全量分析指标 |
| **阶段 2** | `get_theme_templates_with_coverage` | **Neo4j** | 获取主题模板 + 覆盖率计算 |
| **阶段 2** | `get_template_indicators` | **Neo4j** | 获取模板包含的所有指标 |

### theme-vector（Chroma）

- `search_indicators_by_vector` - 向量化语义搜索魔数师指标

### theme-ontology（Neo4j）

- `aggregate_themes_from_indicators` - 从指标列表聚合候选主题（按频次排序）
- `get_theme_full_path` - 获取主题从"自主分析"到该主题的完整路径
- `get_theme_filter_indicators` - 获取主题下全量筛选指标（时间+机构）
- `get_theme_analysis_indicators` - 获取主题下全量分析指标
- `get_indicator_field_mapping` - 指标字段映射（语义增强）
- `get_table_terms` - 表字段术语描述（语义增强）
- `get_indicator_full_path` - 指标完整路径
- `get_theme_templates_with_coverage` - 主题模板+覆盖率
- `get_template_indicators` - 模板包含的指标

## 项目来源

本项目从 [Smart Query](../smart-query) 项目中独立出来，专注于主题模板推荐功能。
