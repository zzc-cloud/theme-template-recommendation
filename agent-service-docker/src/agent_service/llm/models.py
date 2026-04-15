
from pydantic import BaseModel, Field
from typing import Optional


class PhraseExtraction(BaseModel):
    phrases: list[str] = Field(
        description="从用户问题中提取的业务相关词组列表"
    )


class PhraseClassification(BaseModel):
    filter_phrases: list[str] = Field(
        description="筛选值词组：机构名称（分行/支行）、时间词、地区行政区划"
    )
    analysis_concepts: list[str] = Field(
        description="分析概念词组：业务术语、分析意图词"
    )
    reasoning: str = Field(
        description="分类依据简述"
    )


class IterationRefinementResult(BaseModel):
    new_concepts: list[str] = Field(
        description="下一轮搜索词列表，数量应与未收敛概念数量一致或更少"
    )
    reasoning: str = Field(
        default="",
        description="诊断说明，用于调试和日志"
    )
    deviation_warning: bool = Field(
        default=False,
        description="是否触发偏离度警告"
    )


class NormalizedQuestionResult(BaseModel):
    normalized_question: str = Field(
        description="规范化后的需求描述，不超过 100 字"
    )


class LowConfidenceResult(BaseModel):
    analysis: str = Field(description="每个概念无法匹配的原因分析")
    suggestions: list[dict] = Field(
        default_factory=list,
        description="换词建议列表"
    )
    user_message: str = Field(description="面向用户的友好提示信息")


class DimensionAnalysisItem(BaseModel):
    dimension: str = Field(description="分析维度名称")
    primary_theme: str = Field(description="该维度主要命中的主题（来自 Neo4j 权重最高的 theme）")
    independence_score: float = Field(description="独立性得分 0.0-1.0，越高越独立")
    core_concept_score: float = Field(description="核心概念得分 0.0-1.0，越高越代表用户核心意图")
    recommendation: str = Field(description="建议：优先/可选/建议后选")


class DimensionSelectionGuidance(BaseModel):
    recommended_first: list[str] = Field(description="建议优先勾选的核心维度列表")
    conflict_analysis: str = Field(description="维度间主题冲突分析（含加权 Jaccard 数值）")
    dimension_analysis: list[DimensionAnalysisItem] = Field(
        default_factory=list,
        description="各维度的详细分析"
    )


class SelectedIndicatorLLM(BaseModel):
    indicator_id: str = Field(description="指标ID")
    alias: str = Field(description="指标别名")
    type: str = Field(default="", description="指标类型")
    reason: str = Field(default="", description="选取原因")


class ThemeJudgment(BaseModel):
    theme_id: str = Field(description="主题ID")
    theme_name: str = Field(description="主题名称")
    is_supported: bool = Field(description="主题是否支撑用户需求")
    support_reason: str = Field(description="主题可用性判断理由")
    selected_filter_indicators: list[SelectedIndicatorLLM] = Field(
        default_factory=list,
        description="选中的筛选指标"
    )
    selected_analysis_indicators: list[SelectedIndicatorLLM] = Field(
        default_factory=list,
        description="选中的分析指标"
    )
    unsupported_dimensions: list[str] = Field(
        default_factory=list,
        description="无法覆盖的分析维度"
    )


class TemplateUsability(BaseModel):
    template_id: str = Field(description="模板ID")
    is_supported: bool = Field(description="模板是否支撑用户需求")
    support_reason: str = Field(description="判断理由")


class HierarchyNavigationTheme(BaseModel):
    theme_id: str = Field(description="主题ID")
    theme_alias: str = Field(description="主题名称")
    theme_path: str = Field(description="主题完整路径")
    reason: str = Field(default="", description="选择理由")


class HierarchyNavigationResult(BaseModel):
    selected_themes: list[HierarchyNavigationTheme] = Field(
        default_factory=list,
        description="选中的候选主题列表"
    )


class SectorSelection(BaseModel):
    sector_id: str = Field(description="板块ID")
    sector_alias: str = Field(description="板块名称")
    sector_path: str = Field(description="板块路径")
    reason: str = Field(default="", description="选择理由")


class SectorFilterResult(BaseModel):
    selected_sectors: list[SectorSelection] = Field(
        default_factory=list,
        description="选中的板块列表"
    )
