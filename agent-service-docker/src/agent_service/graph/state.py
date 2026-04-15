
from typing import Annotated, Literal, TypedDict

from langgraph.graph import add_messages


class IndicatorMatch(TypedDict):
    id: str
    alias: str
    description: str
    theme_id: str
    theme_alias: str
    similarity_score: float


class AnalysisDimension(TypedDict):
    search_term: str
    converged: bool
    deviation_warning: bool
    indicators: list[IndicatorMatch]


class FilterIndicator(TypedDict):
    indicator_id: str
    value: str
    alias: str
    type: str


class SelectedIndicator(TypedDict):
    indicator_id: str
    alias: str
    description: str
    type: str
    reason: str


class ThemeCandidate(TypedDict):
    theme_id: str
    theme_alias: str
    theme_level: int
    theme_path: str
    frequency: int
    weighted_frequency: float
    matched_indicator_ids: list[str]


class NavigationPathTheme(TypedDict):
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
    sector_id: str
    sector_alias: str
    sector_path: str
    total_themes: int
    selected_themes: list[NavigationPathTheme]


class RecommendedTheme(TypedDict):
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
    template_id: str
    is_supported: bool
    support_reason: str


class RecommendedTemplate(TypedDict):
    template_id: str
    template_alias: str
    template_description: str
    usage_count: int
    coverage_ratio: float
    has_qualified_templates: bool
    fallback_reason: str
    usability: TemplateUsability


class IterationRecord(TypedDict):
    round: int
    search_results: dict
    corrections: list


class ConversationRound(TypedDict):
    round: int
    user_question: str
    normalized_question: str
    filter_indicators: list
    analysis_dimensions: list


class UserConfirmation(TypedDict):
    confirmed_dimensions: list[str]
    confirmed_question: str


class DimensionGuidance(TypedDict):
    has_conflict: bool
    recommended_first: list[str]
    conflict_analysis: str
    selection_advice: str
    dimension_analysis: list


class AgentState(TypedDict):

    user_question: str

    extracted_phrases: list[str]
    filter_indicators: list[FilterIndicator]
    analysis_dimensions: list[AnalysisDimension]
    normalized_question: str
    search_results: dict[str, list]
    iteration_round: int
    iteration_log: list[IterationRecord]
    is_low_confidence: bool

    low_confidence_message: str
    low_confidence_suggestions: list
    dimension_guidance: DimensionGuidance | None
    pending_confirmation: dict | None
    user_confirmation: UserConfirmation | None
    conversation_history: list[ConversationRound]

    candidate_themes: list[ThemeCandidate]
    navigation_path_themes: list[NavigationPathTheme]
    navigation_path_detail: list[SectorThemeInfo]
    recommended_themes: list[RecommendedTheme]
    convergence_rate: float

    recommended_templates: list[RecommendedTemplate]
    template_search_detail: list[dict]

    top_k_themes: int
    top_k_templates: int

    final_output: dict
    execution_time_ms: float
    error: str | None
