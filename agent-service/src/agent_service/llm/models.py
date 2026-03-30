"""
LLM 响应 Pydantic 模型
使用 with_structured_output() 替代手动 JSON 解析
"""

from pydantic import BaseModel, Field
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════
# 阶段 0：需求澄清
# ═══════════════════════════════════════════════════════════════════════

class PhraseExtraction(BaseModel):
    """阶段 0.1：词组提取结果"""
    phrases: list[str] = Field(
        description="从用户问题中提取的业务相关词组列表"
    )


class PhraseClassification(BaseModel):
    """阶段 0.2：词组分类结果"""
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
    """阶段 0.3：迭代精炼结果"""
    new_concepts: list[str] = Field(
        description="下一轮搜索词列表，数量应与未收敛概念数量一致或更少"
    )
    reasoning: str = Field(
        default="",
        description="诊断说明，用于调试和日志"
    )


class NormalizedQuestionResult(BaseModel):
    """阶段 0.3：规范化问题结果"""
    normalized_question: str = Field(
        description="规范化后的需求描述，不超过 100 字"
    )


class LowConfidenceResult(BaseModel):
    """低置信度处理结果"""
    analysis: str = Field(description="每个概念无法匹配的原因分析")
    suggestions: list[dict] = Field(
        default_factory=list,
        description="换词建议列表"
    )
    user_message: str = Field(description="面向用户的友好提示信息")


class DimensionAnalysisItem(BaseModel):
    """维度分析条目"""
    dimension: str = Field(description="分析维度名称")
    primary_theme: str = Field(description="该维度主要命中的主题")
    independence_score: float = Field(description="独立性得分 0.0-1.0，越高越独立")
    core_concept_score: float = Field(description="核心概念得分 0.0-1.0，越高越代表用户核心意图")
    recommendation: str = Field(description="建议：优先/可选/建议后选")


class DimensionSelectionGuidance(BaseModel):
    """阶段 0.4：分析维度勾选引导结果"""
    has_conflict: bool = Field(description="是否存在主题交叉冲突")
    recommended_first: list[str] = Field(description="建议优先勾选的核心维度列表")
    conflict_analysis: str = Field(description="维度间主题冲突分析")
    selection_advice: str = Field(description="面向用户的勾选建议（1-2句话）")
    dimension_analysis: list[DimensionAnalysisItem] = Field(
        default_factory=list,
        description="各维度的详细分析"
    )


# ═══════════════════════════════════════════════════════════════════════
# 阶段 1：主题定位与裁决
# ═══════════════════════════════════════════════════════════════════════

class SelectedIndicatorLLM(BaseModel):
    """LLM 裁决选中的指标"""
    indicator_id: str = Field(description="指标ID")
    alias: str = Field(description="指标别名")
    type: str = Field(default="", description="指标类型")
    reason: str = Field(default="", description="选取原因")


class ThemeJudgment(BaseModel):
    """阶段 1.3：主题裁决结果"""
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


# ═══════════════════════════════════════════════════════════════════════
# 阶段 2：模板推荐
# ═══════════════════════════════════════════════════════════════════════

class MissingIndicatorAnalysis(BaseModel):
    """缺失指标分析"""
    indicator_alias: str = Field(description="缺失指标别名")
    importance: str = Field(description="重要程度：核心/辅助/可忽略")
    impact: str = Field(description="缺失影响")
    supplement_suggestion: str = Field(description="补充建议")


class TemplateUsability(BaseModel):
    """阶段 2.2：模板可用性分析"""
    template_id: str = Field(description="模板ID")
    overall_usability: str = Field(
        description="整体可用性：可直接使用/补充后可用/缺口较大建议谨慎"
    )
    usability_summary: str = Field(description="可用性一句话说明")
    missing_indicator_analysis: list[MissingIndicatorAnalysis] = Field(
        default_factory=list,
        description="缺失指标分析列表"
    )
