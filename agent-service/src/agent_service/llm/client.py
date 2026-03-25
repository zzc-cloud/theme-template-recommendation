"""
LLM 客户端
使用 SiliconFlow 的 OpenAI 兼容格式 API
支持结构化输出 (with_structured_output) 替代手动 JSON 解析
"""

import logging
from typing import Any, Optional, Type

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from .. import config
from .models import (
    PhraseExtraction,
    PhraseClassification,
    IterationEvaluation,
    LowConfidenceResult,
    ThemeJudgment,
    TemplateUsability,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 全局 LLM 客户端（延迟初始化）
# ─────────────────────────────────────────────
_llm_client: Optional[ChatOpenAI] = None


def get_llm_client() -> ChatOpenAI:
    """获取 LLM 客户端（单例）"""
    global _llm_client
    if _llm_client is None:
        _llm_client = ChatOpenAI(
            model=config.LLM_MODEL,
            api_key=config.SILICONFLOW_API_KEY,
            base_url=config.SILICONFLOW_BASE_URL,
            temperature=config.LLM_TEMPERATURE,
            max_tokens=config.LLM_MAX_TOKENS,
            streaming=False,
        )
    return _llm_client


# ─────────────────────────────────────────────
# 结构化输出调用（核心改进）
# ─────────────────────────────────────────────

def _build_messages(system_prompt: str, user_prompt: str) -> list:
    """构建消息列表"""
    return [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]


def invoke_structured(
    model: Type[BaseModel],
    system_prompt: str,
    user_prompt: str,
) -> BaseModel:
    """
    调用 LLM 并强制返回结构化数据（Pydantic 模型）

    Args:
        model: Pydantic 模型类
        system_prompt: 系统提示词
        user_prompt: 用户提示词

    Returns:
        Pydantic 模型实例（类型安全，无需解析）

    Raises:
        RuntimeError: 当 LLM 调用或结构化输出失败时
    """
    client = get_llm_client()
    structured_client = client.with_structured_output(model)
    messages = _build_messages(system_prompt, user_prompt)

    try:
        result = structured_client.invoke(messages)
        return result
    except Exception as e:
        logger.error(f"结构化输出调用失败: {model.__name__}, error: {e}")
        raise RuntimeError(f"结构化输出调用失败 [{model.__name__}]: {e}")


# ─────────────────────────────────────────────
# 各阶段专用调用函数
# ─────────────────────────────────────────────

def _build_history_str(conversation_history: list) -> str:
    """将 conversation_history 转换为 LLM 可读的历史摘要字符串"""
    if not conversation_history:
        return ""

    lines = ["【上一轮对话背景（供参考，如当前问题有追问语境请继承）】"]
    for round_data in conversation_history:
        if round_data.get("user_question"):
            lines.append(f"上一轮问题：{round_data['user_question']}")
        if round_data.get("normalized_question"):
            lines.append(f"规范化后：{round_data['normalized_question']}")
        if round_data.get("filter_indicators"):
            filters = "、".join(
                f"{f['alias']}={f['value']}"
                for f in round_data["filter_indicators"]
                if f.get("alias") and f.get("value")
            )
            if filters:
                lines.append(f"筛选条件：{filters}")
        if round_data.get("analysis_dimensions"):
            dims = "、".join(
                d["search_term"]
                for d in round_data["analysis_dimensions"]
                if d.get("search_term")
            )
            if dims:
                lines.append(f"分析维度：{dims}")

    return "\n".join(lines) + "\n"  # 末尾加换行，与后续内容隔开


def extract_phrases(
    user_question: str,
    conversation_history: list = None,
) -> PhraseExtraction:
    """阶段 0.1：提取词组"""
    from . import prompts as llm_prompts

    system_prompt = "你是一个专业的银行数据分析助手，擅长提取用户问题中的关键业务词组。重要：返回的 JSON 必须包含名为 \"phrases\" 的键，值必须是字符串数组。不要使用任何 markdown 代码块包裹。"
    user_prompt = llm_prompts.PHRASE_EXTRACTION_PROMPT.format(
        user_question=user_question,
        conversation_history=_build_history_str(conversation_history or []),
    )
    return invoke_structured(PhraseExtraction, system_prompt, user_prompt)


def classify_phrases(user_question: str, phrases: list[str]) -> PhraseClassification:
    """阶段 0.2：分类词组"""
    from . import prompts as llm_prompts

    system_prompt = "你是一个专业的银行数据分析助手，擅长对词组进行语义分类。重要：直接输出 JSON，不要使用任何 markdown 代码块（如 ```json）包裹。"
    user_prompt = llm_prompts.PHRASE_CLASSIFICATION_PROMPT.format(
        user_question=user_question,
        phrases="\n".join(f"- {p}" for p in phrases),
    )
    return invoke_structured(PhraseClassification, system_prompt, user_prompt)


def evaluate_iteration(
    user_question: str,
    round_num: int,
    max_rounds: int,
    search_results_str: str,
) -> IterationEvaluation:
    """阶段 0.3：迭代评估"""
    from . import prompts as llm_prompts

    system_prompt = "你是一个专业的银行数据分析专家，正在精确定位分析指标。重要：直接输出 JSON，不要使用任何 markdown 代码块（如 ```json）包裹。"
    user_prompt = llm_prompts.ITERATION_EVALUATION_PROMPT.format(
        user_question=user_question,
        round=round_num,
        max_rounds=max_rounds,
        search_results_str=search_results_str,
    )
    return invoke_structured(IterationEvaluation, system_prompt, user_prompt)


def handle_low_confidence(
    user_question: str,
    low_confidence_concepts: list[str],
    search_results_str: str,
) -> LowConfidenceResult:
    """低置信度处理"""
    from . import prompts as llm_prompts

    system_prompt = "你是一个专业的银行数据分析专家。重要：直接输出 JSON，不要使用任何 markdown 代码块（如 ```json）包裹。"
    user_prompt = llm_prompts.LOW_CONFIDENCE_PROMPT.format(
        user_question=user_question,
        low_confidence_concepts=", ".join(low_confidence_concepts),
        search_results_str=search_results_str,
    )
    return invoke_structured(LowConfidenceResult, system_prompt, user_prompt)


def judge_theme(
    user_question: str,
    analysis_dimensions_str: str,
    theme_alias: str,
    theme_path: str,
    filter_indicators_str: str,
    analysis_indicators_str: str,
) -> ThemeJudgment:
    """阶段 1.3：裁决主题"""
    from . import prompts as llm_prompts

    system_prompt = "你是一个专业的银行数据分析专家，擅长判断主题对用户需求的支撑能力。重要：直接输出 JSON，不要使用任何 markdown 代码块（如 ```json）包裹。"
    user_prompt = llm_prompts.THEME_JUDGMENT_PROMPT.format(
        user_question=user_question,
        analysis_dimensions=analysis_dimensions_str,
        theme_name=theme_alias,
        theme_path=theme_path,
        filter_indicators_str=filter_indicators_str,
        analysis_indicators_str=analysis_indicators_str,
    )
    return invoke_structured(ThemeJudgment, system_prompt, user_prompt)


def analyze_template_usability(
    user_question: str,
    analysis_dimensions_str: str,
    template_alias: str,
    template_description: str,
    coverage_ratio: str,
    all_template_indicators_str: str,
    missing_indicators_str: str,
) -> TemplateUsability:
    """阶段 2.2：分析模板可用性"""
    from . import prompts as llm_prompts

    system_prompt = "你是一个专业的银行数据分析专家，擅长评估模板对用户需求的可用性。重要：直接输出 JSON，不要使用任何 markdown 代码块（如 ```json）包裹。"
    user_prompt = llm_prompts.TEMPLATE_USABILITY_PROMPT.format(
        user_question=user_question,
        analysis_dimensions=analysis_dimensions_str,
        template_alias=template_alias,
        template_description=template_description,
        coverage_ratio=coverage_ratio,
        all_template_indicators_str=all_template_indicators_str,
        missing_indicators_str=missing_indicators_str,
    )
    return invoke_structured(TemplateUsability, system_prompt, user_prompt)


# ─────────────────────────────────────────────
# 兼容性别名（用于过渡期）
# ─────────────────────────────────────────────

def invoke_llm_json(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """
    [���容] 旧接口，内部不再使用
    保留是为了避免其他模块直接调用时报错
    """
    raise NotImplementedError(
        "invoke_llm_json 已废弃，请使用新的结构化调用函数"
    )
