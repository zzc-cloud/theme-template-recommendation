"""
API 数据模型
Pydantic 请求/响应模型
"""

from typing import Optional

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
    is_supported: bool
    support_reason: str = ""
    selected_filter_indicators: list[SelectedIndicatorResponse] = Field(default_factory=list)
    selected_analysis_indicators: list[SelectedIndicatorResponse] = Field(default_factory=list)


class MissingIndicatorAnalysisResponse(BaseModel):
    """缺失指标分析"""
    indicator_alias: str
    importance: str  # 核心 / 辅助 / 可忽略
    impact: str
    supplement_suggestion: str


class TemplateUsabilityResponse(BaseModel):
    """模板可用性"""
    template_id: str = ""
    overall_usability: str  # 可直接使用 / 补充后可用 / 缺口较大建议谨慎
    usability_summary: str
    missing_indicator_analysis: list[MissingIndicatorAnalysisResponse] = Field(default_factory=list)


class RecommendedTemplateResponse(BaseModel):
    """推荐模板"""
    template_id: str
    template_alias: str
    template_description: str = ""
    theme_alias: str = ""
    usage_count: int
    coverage_ratio: float  # 0.0 ~ 1.0
    has_qualified_templates: bool
    fallback_reason: str = ""
    usability: TemplateUsabilityResponse = Field(default_factory=TemplateUsabilityResponse)


class RecommendResponse(BaseModel):
    """推荐响应"""
    request_id: str = Field(description="请求唯一标识")
    normalized_question: str
    filter_indicators: list[FilterIndicatorResponse] = Field(default_factory=list)
    analysis_dimensions: list[AnalysisDimensionResponse] = Field(default_factory=list)
    is_low_confidence: bool = False
    recommended_themes: list[RecommendedThemeResponse] = Field(default_factory=list)
    recommended_templates: list[RecommendedTemplateResponse] = Field(default_factory=list)
    execution_time_ms: float
    iteration_rounds: int = 0
    conversation_round: int = Field(default=1, description="当前对话轮次")
    error: Optional[str] = None
    markdown: str = Field(default="", description="Markdown 格式的人类可读输出")


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
