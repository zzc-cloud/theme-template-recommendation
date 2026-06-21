---
name: theme-template-recommendation
description: 基于用户自然语言表述的数据分析需求，推荐相关 THEME 与可直接使用的 TEMPLATE。
---

# 魔数师主题和模板推荐

## 输入

用户消息会提供：`thread_id`、`用户输入`。

## 必须遵守

1. 只能使用 tool_registry.py 中的工具：`search_indicators_by_vector`、`batch_get_indicator_themes`、`aggregate_themes_from_indicators`、`get_sectors_from_root`、`get_sector_themes`、`get_theme_filter_indicators`、`get_theme_analysis_indicators`、`get_theme_templates_with_coverage`、`AskUserQuestion_tools`。
2. 阶段 0 完成后必须调用 `AskUserQuestion_tools`，该工具参数用于构造前端确认页面的 interrupt payload；DeepAgents 会在此处暂停，调用方读取该 payload 并渲染确认页面。


## 阶段 0：需求澄清

输出：`[主题和模板推荐] 开始执行`

1. 从用户输入中拆分：
   - 筛选值：机构、时间、地区等，自动映射为筛选指标，不需要用户确认。
   - 分析概念：业务实体、分析口径、风险、增长、占比、分布、趋势等，需要收敛为可确认分析意图。
2. 精炼分析概念搜索词：保留原始业务语义，必要时替换为银行业标准术语；统计意图优先保留业务实体词；无法改进时保留原词并标记低置信度。
3. 对每个分析概念调用 `search_indicators_by_vector(query, top_k=20)`。
4. 结合搜索结果收敛候选分析意图；低置信度结果保留为候选并说明原因。
5. 对候选指标 ID 调用 `batch_get_indicator_themes`，判断多个维度是否主题方向一致。
6. 调用 `AskUserQuestion_tools` 让用户确认分析意图和筛选条件。
   - 该工具参数按 `Section` / `Option` 结构组织，用于生成前端确认页面的 interrupt payload。
   - `interrupt_type` 固定为 `dimension_and_filters_confirmation`。
   - `thread_id` 必须使用当前请求线程 ID。
   - `sections` 至少包含两个区块：
     - `candidate_filters`：用户问题中识别出的筛选条件，`options[*].label` 使用用户可读文案，`value` 使用稳定回传标识，如 `org=南京分行`、`date=2025`。
     - `candidate_dimensions`：需要用户确认的分析意图，`options[*].label` 使用分析意图名称，`description` 必须说明业务用途和关联指标，`value` 使用稳定回传标识，如 `dim=small_micro_loan_risk_balance`。
   - 两个区块的 `select_mode` 均为 `multiple`，`allow_freeform` 均为 `true`，并提供明确的 `freeform_hint`。


## 用户确认回复后：按回复意图分支

收到用户对 `AskUserQuestion_tools` 的自然语言回复后，按回复意图继续处理。

1. 明确确认：使用 `candidate_filters` 和 `candidate_dimensions` 中用户认可的选项，整理 `user_question`、`normalized_question`、`analysis_dimensions`、`filter_indicators`、`matched_indicators`，进入下方阶段 1 和阶段 2。
2. 修改、再看看、增删维度、不明确或否认：根据用户回复重新澄清，必要时重新搜索指标，并再次调用 `AskUserQuestion_tools`。

确认后整理字段：
- `user_question`：用户原始问题。
- `normalized_question`：用户确认后的规范化问题。
- `analysis_dimensions`：用户认可的分析维度，以及每个维度关联的匹配指标。
- `filter_indicators`：自动应用或用户确认的筛选指标。
- `matched_indicators`：用于主题定位的指标 ID 列表。

## 阶段 1：主题定位与指标补全

输出：`[主题定位与模板推荐] 开始执行`

1. 统计聚合路径：调用 `aggregate_themes_from_indicators(matched_indicators)`，按指标归属聚合候选主题并输出。
2. 层级导航路径：调用 `get_sectors_from_root()`，选择相关板块后调用 `get_sector_themes(sector_id)`，基于主题名称和完整路径筛选候选主题并输出。
3. 合并候选主题：按 `theme_id` 去重，保留统计聚合和层级导航两条路径的候选主题并输出。
4. 指标补全：对每个候选主题调用 `get_theme_filter_indicators(theme_id)` 和 `get_theme_analysis_indicators(theme_id)`。
5. 主题裁决：判断主题业务领域、分析指标、筛选指标是否支撑用户需求并输出裁决结果；不要求主题预置聚合指标，无预聚合指标不能直接判定不支持。

## 阶段 2：模板推荐

仅对可支持主题调用：

```json
get_theme_templates_with_coverage(
  theme_id="...",
  matched_indicator_aliases=["精筛后的筛选指标和分析指标别名"]
)
```

处理规则：
- 有达标模板：按覆盖率和热度输出。
- 无达标模板：说明降级原因，推荐覆盖率最高和热度最高的参考模板。
- 对缺失核心指标说明影响与补充建议。

## 最终输出

最后一条消息必须直接输出面向用户的 Markdown 文本，不要包装成 JSON，不要使用 Markdown 代码块包裹整体结果。

最终 Markdown 必须包含以下三个语义区块：

1. `推荐主题`：推荐主题及可勾选指标，内容必须包含：主题名称、主题路径、支持理由、建议勾选的筛选指标和分析指标。
2. `推荐模板`：模板名称、ID、热度、覆盖率、可用性和缺口说明。
3. `使用建议`：在魔数师平台的下一步使用建议。

推荐使用二级或三级标题、列表和表格组织内容，确保人类用户可以直接阅读和复制。
