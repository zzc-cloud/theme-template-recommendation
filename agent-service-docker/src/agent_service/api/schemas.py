
from typing import Literal, Optional

from pydantic import BaseModel, Field


class PreviousFilterIndicator(BaseModel):
    alias: str
    value: str


class ConversationContext(BaseModel):
    previous_question: str = Field(default="")
    previous_normalized_question: str = Field(default="")
    previous_filter_indicators: list[PreviousFilterIndicator] = Field(default_factory=list)
    previous_dimensions: list[str] = Field(default_factory=list)


class RecommendRequest(BaseModel):
    question: str = Field(
        ...,
        description="用户自然语言问题",
        examples=["我想分析南京分行的小微企业贷款风险"],
        min_length=1,
        max_length=500,
    )
    top_k_themes: int = Field(
        default=3,
        ge=1,
        le=10,
        description="返回的主题数量上限",
    )
    top_k_templates: int = Field(
        default=5,
        ge=1,
        le=20,
        description="每种类型返回的模板数量上限",
    )
    template_type: Optional[str] = Field(
        default=None,
        description="模板类型过滤：INSIGHT / COMBINEDQUERY / None（全部）",
    )
    thread_id: str = Field(
        ...,
        description="请求唯一标识（用于断点续传和会话恢复）",
    )
    context: Optional[ConversationContext] = Field(
        default=None,
        description="对话上下文（追问场景使用）",
    )


class ResumeRequest(BaseModel):
    thread_id: str = Field(..., description="线程 ID")
    confirmed_dimensions: list[str] = Field(..., description="用户确认的分析维度列表")
    confirmed_question: str = Field(default="", description="用户确认的规范化问题")


class FilterIndicatorResponse(BaseModel):
    indicator_id: str
    value: str
    alias: str
    type: str


class IndicatorMatchResponse(BaseModel):
    id: str
    alias: str
    description: str
    theme_id: str
    theme_alias: str
    similarity_score: float


class AnalysisDimensionResponse(BaseModel):
    search_term: str
    converged: bool
    indicators: list[IndicatorMatchResponse]


class SelectedIndicatorResponse(BaseModel):
    indicator_id: str
    alias: str
    description: str = ""
    type: str = ""
    reason: str = ""


class RecommendedThemeResponse(BaseModel):
    theme_id: str
    theme_alias: str
    theme_level: int
    theme_path: str = ""
    is_supported: bool
    support_reason: str = ""
    selected_filter_indicators: list[SelectedIndicatorResponse] = Field(default_factory=list)
    selected_analysis_indicators: list[SelectedIndicatorResponse] = Field(default_factory=list)


class NavigationThemeResponse(BaseModel):
    theme_id: str = ""
    theme_alias: str = ""
    theme_path: str = ""


class SectorNavigationResponse(BaseModel):
    sector_id: str = ""
    sector_alias: str = ""
    sector_path: str = ""
    total_themes: int = 0
    selected_themes: list[NavigationThemeResponse] = Field(default_factory=list)


class CandidateThemeResponse(BaseModel):
    theme_id: str = ""
    theme_alias: str = ""
    theme_level: int = 0
    theme_path: str = ""
    frequency: int = 0
    weighted_frequency: float = 0.0
    matched_indicator_ids: list[str] = Field(default_factory=list)


class TemplateUsabilityResponse(BaseModel):
    template_id: str = ""
    is_supported: bool = False
    support_reason: str = ""


class TemplateCoverageDetail(BaseModel):
    covered_indicator_aliases: list[str] = Field(
        default_factory=list,
        description="模板覆盖的用户指标别名列表",
    )
    missing_indicator_aliases: list[str] = Field(
        default_factory=list,
        description="模板缺失的用户指标别名列表",
    )
    matched_count: int = Field(default=0, description="模板覆盖的用户指标数量")
    total_user_indicators: int = Field(default=0, description="用户所需指标总数")


class RecommendedTemplateResponse(BaseModel):
    template_id: str = ""
    template_alias: str = ""
    template_description: str = ""
    theme_id: str = ""
    theme_alias: str = ""
    usage_count: int = 0
    coverage_ratio: float = 0.0
    coverage_detail: TemplateCoverageDetail = Field(default_factory=TemplateCoverageDetail)
    theme_has_qualified_templates: bool = Field(
        default=False,
        description="该模板所属主题是否有达标模板（覆盖率>=阈值）",
    )
    theme_fallback_reason: str = Field(
        default="",
        description="该模板所属主题的降级原因",
    )
    usability: TemplateUsabilityResponse = Field(default_factory=TemplateUsabilityResponse)


class TemplateSearchDetailTemplateItem(BaseModel):
    template_id: str = ""
    template_alias: str = ""
    template_description: str = ""
    usage_count: int = 0
    coverage_ratio: float = 0.0
    covered_indicator_aliases: list[str] = Field(default_factory=list)
    missing_indicator_aliases: list[str] = Field(default_factory=list)
    matched_count: int = 0
    total_user_indicators: int = 0
    is_supported: bool = False
    usability_reason: str = ""


class TemplateSearchDetailResponse(BaseModel):
    theme_id: str = ""
    theme_alias: str = ""
    theme_path: str = ""
    is_supported: bool = False
    matched_indicator_aliases: list[str] = Field(default_factory=list)
    has_qualified_templates: bool = False
    fallback_reason: str = ""
    all_template_count: int = 0
    templates: list[TemplateSearchDetailTemplateItem] = Field(default_factory=list)


class RecommendResponse(BaseModel):
    request_id: str = Field(description="请求唯一标识")
    normalized_question: str
    filter_indicators: list[FilterIndicatorResponse] = Field(default_factory=list)
    analysis_dimensions: list[AnalysisDimensionResponse] = Field(default_factory=list)
    is_low_confidence: bool = False
    candidate_themes_from_aggregate: list[CandidateThemeResponse] = Field(
        default_factory=list,
        description="聚合路径候选主题",
    )
    navigation_path_detail: list[SectorNavigationResponse] = Field(
        default_factory=list,
        description="层级导航路径详情（每个板块及筛选出的主题）",
    )
    recommended_themes: list[RecommendedThemeResponse] = Field(default_factory=list)
    recommended_templates: list[RecommendedTemplateResponse] = Field(
        default_factory=list,
        description="检索到的模板列表（含每个模板的覆盖率详情）",
    )
    template_search_detail: list[TemplateSearchDetailResponse] = Field(
        default_factory=list,
        description="每个主题的模板检索汇总，含覆盖率详情",
    )
    execution_time_ms: float
    iteration_rounds: int = 0
    conversation_round: int = Field(default=1, description="当前对话轮次")
    error: Optional[str] = None
    markdown: str = Field(default="", description="Markdown 格式的人类可读输出")


class SyncResponse(BaseModel):
    status: Literal["completed", "interrupted", "error"] = Field(
        description="执行状态"
    )
    request_id: str = Field(description="请求唯一标识")
    execution_time_ms: float = Field(default=0.0, description="服务端执行耗时（毫秒）")

    data: Optional[RecommendResponse] = Field(
        default=None,
        description="完整的推荐结果（status=completed 时有）",
    )

    interrupt: Optional["SyncInterruptInfo"] = Field(
        default=None,
        description="中断信息（status=interrupted 时有）",
    )

    error: Optional["SyncErrorInfo"] = Field(
        default=None,
        description="错误信息（status=error 时有）",
    )


class SyncInterruptInfo(BaseModel):
    thread_id: str
    status: str
    pending_confirmation: dict = Field(
        default_factory=dict,
        description="待确认的分析维度信息",
    )
    message: str = Field(
        default="请调用 resume-sync 接口确认分析维度后继续执行",
        description="提示信息",
    )


class SyncErrorInfo(BaseModel):
    code: str = Field(default="UNKNOWN", description="错误码")
    message: str = Field(default="未知错误", description="错误描述")


class HealthResponse(BaseModel):
    status: str
    version: str = "1.0.0"
    services: dict[str, bool] = Field(
        default_factory=dict,
        description="各服务状态",
    )
    concurrency: dict[str, int] = Field(
        default_factory=dict,
        description="并发状态：current/max/available",
    )
