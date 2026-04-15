"""
API 数据模型
Pydantic 请求/响应模型
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
# 请求模型
# ─────────────────────────────────────────────

class PreviousFilterIndicator(BaseModel):
    """历史筛选指标"""
    alias: str
    value: str


class ConversationContext(BaseModel):
    """对话上下文（用于追问场景）"""
    previous_question: str = Field(default="")
    previous_normalized_question: str = Field(default="")
    previous_filter_indicators: list[PreviousFilterIndicator] = Field(default_factory=list)
    previous_dimensions: list[str] = Field(default_factory=list)


class RecommendRequest(BaseModel):
    """推荐请求"""
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
    """恢复请求"""
    thread_id: str = Field(..., description="线程 ID")
    confirmed_dimensions: list[str] = Field(..., description="用户确认的分析维度列表")
    confirmed_question: str = Field(default="", description="用户确认的规范化问题")


# ─────────────────────────────────────────────
# 响应模型
# ─────────────────────────────────────────────

class FilterIndicatorResponse(BaseModel):
    """筛选指标"""
    indicator_id: str
    value: str
    alias: str
    type: str  # 机构筛选指标 / 时间筛选指标


class IndicatorMatchResponse(BaseModel):
    """匹配指标"""
    id: str
    alias: str
    description: str
    theme_id: str
    theme_alias: str
    similarity_score: float


class AnalysisDimensionResponse(BaseModel):
    """分析维度"""
    search_term: str
    converged: bool
    indicators: list[IndicatorMatchResponse]


class SelectedIndicatorResponse(BaseModel):
    """选中的指标"""
    indicator_id: str
    alias: str
    description: str = ""
    type: str = ""
    reason: str = ""


class RecommendedThemeResponse(BaseModel):
    """推荐主题"""
    theme_id: str
    theme_alias: str
    theme_level: int
    theme_path: str = ""
    is_supported: bool
    support_reason: str = ""
    selected_filter_indicators: list[SelectedIndicatorResponse] = Field(default_factory=list)
    selected_analysis_indicators: list[SelectedIndicatorResponse] = Field(default_factory=list)


class NavigationThemeResponse(BaseModel):
    """层级导航中筛选出的候选主题"""
    theme_id: str = ""
    theme_alias: str = ""
    theme_path: str = ""


class SectorNavigationResponse(BaseModel):
    """板块导航结果"""
    sector_id: str = ""
    sector_alias: str = ""
    sector_path: str = ""
    total_themes: int = 0
    selected_themes: list[NavigationThemeResponse] = Field(default_factory=list)


class CandidateThemeResponse(BaseModel):
    """聚合路径候选主题"""
    theme_id: str = ""
    theme_alias: str = ""
    theme_level: int = 0
    theme_path: str = ""
    frequency: int = 0
    weighted_frequency: float = 0.0
    matched_indicator_ids: list[str] = Field(default_factory=list)


class TemplateUsabilityResponse(BaseModel):
    """模板可用性"""
    template_id: str = ""
    is_supported: bool = False
    support_reason: str = ""


class TemplateCoverageDetail(BaseModel):
    """模板覆盖率详情（用于展示每个模板的匹配情况）"""
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
    """推荐模板"""
    template_id: str = ""
    template_alias: str = ""
    template_description: str = ""
    theme_id: str = ""
    theme_alias: str = ""
    usage_count: int = 0
    coverage_ratio: float = 0.0  # 0.0 ~ 1.0
    # 覆盖率详情
    coverage_detail: TemplateCoverageDetail = Field(default_factory=TemplateCoverageDetail)
    # 该主题是否达标（has_qualified_templates 改为 per-theme 字段）
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
    """template_search_detail 中每个主题下的模板条目"""
    template_id: str = ""
    template_alias: str = ""
    template_description: str = ""
    usage_count: int = 0
    coverage_ratio: float = 0.0
    covered_indicator_aliases: list[str] = Field(default_factory=list)
    missing_indicator_aliases: list[str] = Field(default_factory=list)
    matched_count: int = 0
    total_user_indicators: int = 0
    # LLM 评估结果
    is_supported: bool = False
    usability_reason: str = ""


class TemplateSearchDetailResponse(BaseModel):
    """主题模板检索详情（每个主题的模板覆盖率汇总）"""
    theme_id: str = ""
    theme_alias: str = ""
    theme_path: str = ""
    is_supported: bool = False
    matched_indicator_aliases: list[str] = Field(default_factory=list)
    has_qualified_templates: bool = False
    fallback_reason: str = ""
    all_template_count: int = 0
    # 该主题下被 LLM 评估过的所有模板
    templates: list[TemplateSearchDetailTemplateItem] = Field(default_factory=list)


class RecommendResponse(BaseModel):
    """推荐响应"""
    request_id: str = Field(description="请求唯一标识")
    normalized_question: str
    filter_indicators: list[FilterIndicatorResponse] = Field(default_factory=list)
    analysis_dimensions: list[AnalysisDimensionResponse] = Field(default_factory=list)
    is_low_confidence: bool = False
    # 双路径探查结果
    candidate_themes_from_aggregate: list[CandidateThemeResponse] = Field(
        default_factory=list,
        description="聚合路径候选主题",
    )
    navigation_path_detail: list[SectorNavigationResponse] = Field(
        default_factory=list,
        description="层级导航路径详情（每个板块及筛选出的主题）",
    )
    # 推荐结果
    recommended_themes: list[RecommendedThemeResponse] = Field(default_factory=list)
    recommended_templates: list[RecommendedTemplateResponse] = Field(
        default_factory=list,
        description="检索到的模板列表（含每个模板的覆盖率详情）",
    )
    # 主题模板检索详情（每个主题的汇总）
    template_search_detail: list[TemplateSearchDetailResponse] = Field(
        default_factory=list,
        description="每个主题的模板检索汇总，含覆盖率详情",
    )
    execution_time_ms: float
    iteration_rounds: int = 0
    conversation_round: int = Field(default=1, description="当前对话轮次")
    error: Optional[str] = None
    markdown: str = Field(default="", description="Markdown 格式的人类可读输出")


# ─────────────────────────────────────────────
# 健康检查
# ─────────────────────────────────────────────

class SyncResponse(BaseModel):
    """
    非流式同步响应

    所有非 SSE 的直接调用（CLI、脚本）使用此统一响应格式。

    status 枚举：
    - completed: 全流程执行完毕，data 中包含完整推荐结果
    - interrupted: 执行到中途被 interrupt（通常是等待用户确认分析维度），
                   interrupt 字段包含挂起状态，调用方需要引导用户确认后调用 resume-sync
    - error: 执行过程中发生异常，error 字段包含错误信息
    """
    status: Literal["completed", "interrupted", "error"] = Field(
        description="执行状态"
    )
    request_id: str = Field(description="请求唯一标识")
    execution_time_ms: float = Field(default=0.0, description="服务端执行耗时（毫秒）")

    # completed 时有
    data: Optional[RecommendResponse] = Field(
        default=None,
        description="完整的推荐结果（status=completed 时有）",
    )

    # interrupted 时有
    interrupt: Optional["SyncInterruptInfo"] = Field(
        default=None,
        description="中断信息（status=interrupted 时有）",
    )

    # error 时有
    error: Optional["SyncErrorInfo"] = Field(
        default=None,
        description="错误信息（status=error 时有）",
    )


class SyncInterruptInfo(BaseModel):
    """中断信息（等待用户确认）"""
    thread_id: str
    status: str  # "waiting_confirmation" | "low_confidence"
    pending_confirmation: dict = Field(
        default_factory=dict,
        description="待确认的分析维度信息",
    )
    message: str = Field(
        default="请调用 resume-sync 接口确认分析维度后继续执行",
        description="提示信息",
    )


class SyncErrorInfo(BaseModel):
    """错误信息"""
    code: str = Field(default="UNKNOWN", description="错误码")
    message: str = Field(default="未知错误", description="错误描述")


# ─────────────────────────────────────────────
# 健康检查
# ─────────────────────────────────────────────

class HealthResponse(BaseModel):
    """健康检查响应"""
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
