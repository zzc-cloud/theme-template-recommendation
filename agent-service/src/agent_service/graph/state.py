"""
LangGraph State 定义
包含所有阶段流转的数据结构
"""

from typing import Annotated, Literal, TypedDict

from langgraph.graph import add_messages


# ─────────────────────────────────────────────
# 搜索结果条目
# ─────────────────────────────────────────────

class IndicatorMatch(TypedDict):
    """匹配到的指标"""
    id: str
    alias: str
    description: str
    theme_id: str
    theme_alias: str
    similarity_score: float


class AnalysisDimension(TypedDict):
    """分析维度"""
    search_term: str
    converged: bool
    deviation_warning: bool  # 新增：偏离度警告
    indicators: list[IndicatorMatch]


class FilterIndicator(TypedDict):
    """筛选指标"""
    indicator_id: str
    value: str  # 筛选值
    alias: str
    type: str   # 机构筛选指标 / 时间筛选指标


class SelectedIndicator(TypedDict):
    """选中的指标"""
    indicator_id: str
    alias: str
    description: str
    type: str
    reason: str


class ThemeCandidate(TypedDict):
    """候选主题"""
    theme_id: str
    theme_alias: str
    theme_level: int
    theme_path: str
    frequency: int
    weighted_frequency: float  # 新增：相似度加权频次
    matched_indicator_ids: list[str]


class NavigationPathTheme(TypedDict):
    """层级导航候选主题"""
    theme_id: str
    theme_alias: str
    theme_level: int
    depth: int
    parent_alias: str
    parent_type: str
    full_path: str
    sector_id: str
    sector_alias: str


class SectorThemeInfo(TypedDict):
    """板块及在该板块下筛选到的主题"""
    sector_id: str
    sector_alias: str
    sector_path: str
    total_themes: int       # 该板块下全量主题数
    selected_themes: list[NavigationPathTheme]  # LLM 筛选出的候选主题


class RecommendedTheme(TypedDict):
    """推荐主题（含裁决结果）"""
    theme_id: str
    theme_alias: str
    theme_level: int
    theme_path: str
    is_supported: bool
    support_reason: str
    selected_filter_indicators: list[SelectedIndicator]
    selected_analysis_indicators: list[SelectedIndicator]
    unsupported_dimensions: list[str]


class TemplateUsability(TypedDict):
    """模板可用性分析结果"""
    template_id: str
    is_supported: bool
    support_reason: str


class RecommendedTemplate(TypedDict):
    """推荐模板"""
    template_id: str
    template_alias: str
    template_description: str
    usage_count: int
    coverage_ratio: float
    has_qualified_templates: bool
    fallback_reason: str
    usability: TemplateUsability


class IterationRecord(TypedDict):
    """迭代记录"""
    round: int
    search_results: dict
    corrections: list


# ─────────────────────────────────────────────
# 对话历史
# ─────────────────────────────────────────────

class ConversationRound(TypedDict):
    """对话轮次记录"""
    round: int
    user_question: str
    normalized_question: str
    filter_indicators: list
    analysis_dimensions: list


class UserConfirmation(TypedDict):
    """用户确认结果"""
    confirmed_dimensions: list[str]
    confirmed_question: str


class DimensionGuidance(TypedDict):
    """分析维度勾选引导"""
    has_conflict: bool
    recommended_first: list[str]
    conflict_analysis: str
    selection_advice: str
    dimension_analysis: list


# ─────────────────────────────────────────────
# Agent State
# ─────────────────────────────────────────────

class AgentState(TypedDict):
    """LangGraph Agent 的状态"""

    # ── 输入 ──
    user_question: str                         # 用户原始问题

    # ── 阶段 0 产物 ──
    extracted_phrases: list[str]               # 提取的原始词组
    filter_indicators: list[FilterIndicator]   # 筛选指标（自动应用）
    analysis_dimensions: list[AnalysisDimension]  # 分析维度
    normalized_question: str                    # 规范化问题描述
    search_results: dict[str, list]           # 搜索结果（概念→指标列表）
    iteration_round: int                       # 当前迭代轮次
    iteration_log: list[IterationRecord]      # 迭代日志
    is_low_confidence: bool                    # 是否进入低置信度流程

    # ── 用户交互状态 ──
    low_confidence_message: str                # 低置信度提示信息
    low_confidence_suggestions: list           # 低置信度换词建议
    dimension_guidance: DimensionGuidance | None  # 维度勾选引导
    pending_confirmation: dict | None         # 待用户确认的交互数据
    user_confirmation: UserConfirmation | None # 用户确认结果
    conversation_history: list[ConversationRound]  # 对话历史

    # ── 阶段 1 产物 ──
    candidate_themes: list[ThemeCandidate]     # 候选主题
    navigation_path_themes: list[NavigationPathTheme]  # 层级导航候选主题（汇总）
    navigation_path_detail: list[SectorThemeInfo]      # 新增：每个板块的导航详情
    recommended_themes: list[RecommendedTheme] # 推荐主题（含裁决）
    convergence_rate: float                  # 新增：收敛率 = 已收敛概念数 / 原始概念总数

    # ── 阶段 2 产物 ──
    recommended_templates: list[RecommendedTemplate]  # 推荐模板
    template_search_detail: list[dict]  # 每个主题的模板检索详情（含覆盖率）

    # ── 元数据 ──
    top_k_themes: int                         # 请求的 top_k_themes
    top_k_templates: int                     # 请求的 top_k_templates

    # ── 输出 ──
    final_output: dict                        # 最终输出（格式化后）
    execution_time_ms: float                   # 总执行时间
    error: str | None                         # 错误信息
