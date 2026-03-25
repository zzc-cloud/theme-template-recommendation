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


class Correction(BaseModel):
    """搜索词修正"""
    original: str = Field(description="原搜索词")
    action: str = Field(description="修正动作：扩展/收缩/替换/合并")
    corrected: list[str] = Field(description="修正后搜索词列表")
    reason: str = Field(description="修正原因")


class IterationEvaluation(BaseModel):
    """阶段 0.3：迭代评估结果"""
    quality_pass: bool = Field(description="质量是否达标")
    converged: bool = Field(description="是否已收敛")
    coverage_assessment: str = Field(description="覆盖率评估：已覆盖XX，缺失XX")
    normalized_question: str = Field(description="规范化后的问题描述")
    corrections: list[Correction] = Field(
        default_factory=list,
        description="搜索词修正列表"
    )
    low_confidence_concepts: list[str] = Field(
        default_factory=list,
        description="无法收敛的分析概念列表"
    )


class LowConfidenceResult(BaseModel):
    """低置信度处理结果"""
    analysis: str = Field(description="每个概念无法匹配的原因分析")
    suggestions: list[dict] = Field(
        default_factory=list,
        description="换词建议列表"
    )
    user_message: str = Field(description="面向用户的友好提示信息")


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
