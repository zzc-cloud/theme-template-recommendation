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
   - 获取指标完��业务路径
   - 语义增强（术语描述）

→ [阶段 2] 主题聚合
   - THEME.DS_SSA_RISK.对公贷款借据: 15 个指标
   - THEME.DS_SSA_RISK.对公贷款客户风险: 8 个指标

→ [阶段 3] 模板推荐
   ✅ 对公贷款五级分类分析 (覆盖率 100%)
   ✅ 对公贷款逾期统计 (覆盖率 80%)
```

## MCP 工具

### theme-ontology（Neo4j - 8 个工具）
- `search_terms_by_keyword` - 搜索业务术语
- `get_tables_by_term` - 术语→关联表
- `get_indicator_full_path` - 指标完整路径
- `get_indicator_field_mapping` - 指标字段映射
- `get_table_terms` - 表字段术语描述
- `batch_get_indicators_themes` - 批量提取 THEME
- `get_theme_templates_with_coverage` - 主题模板+覆盖率
- `get_template_indicators` - 模板包含的指标

### theme-resources（MySQL - 3 个工具）
- `find_indicators_by_table` - 表→关联指标
- `get_indicator_field_mapping_mysql` - 指标→字段映射
- `get_table_columns_bigmeta` - 表字段详情

## 项目来源

本项目从 [Smart Query](../smart-query) 项目中独立出来，专注于主题模板推荐功能。
