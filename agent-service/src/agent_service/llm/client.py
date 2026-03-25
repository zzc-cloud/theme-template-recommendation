"""
LLM 客户端
使用 SiliconFlow 的 OpenAI 兼容格式 API
支持结构化输出 (with_structured_output) 替代手动 JSON 解析
内置按错误类型差异化重试机制
"""

import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from enum import Enum
from typing import Optional, Type

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
# 错误分类
# ─────────────────────────────────────────────
class LLMErrorType(Enum):
    RATE_LIMIT   = "rate_limit"    # 429 限流 → 可重试，退避更长
    TIMEOUT      = "timeout"       # 超时 → 可重试
    SERVER_ERROR = "server_error"  # 5xx → 可重试
    AUTH_ERROR   = "auth_error"    # 401/403 → 不可重试
    SCHEMA_ERROR = "schema_error"  # 结构化输出格式错误 → 限1次
    UNKNOWN      = "unknown"       # 未知 → 限1次


def _classify_error(e: Exception) -> LLMErrorType:
    """将异常分类，决定重试策略"""
    msg = str(e).lower()
    if "429" in msg or "rate limit" in msg or "too many" in msg:
        return LLMErrorType.RATE_LIMIT
    if "timeout" in msg or "timed out" in msg or "timed-out" in msg:
        return LLMErrorType.TIMEOUT
    if any(c in msg for c in ["500", "502", "503", "504"]):
        return LLMErrorType.SERVER_ERROR
    if "401" in msg or "403" in msg or "unauthorized" in msg or "forbidden" in msg:
        return LLMErrorType.AUTH_ERROR
    if "validation" in msg or "schema" in msg or "parse" in msg or "json" in msg:
        return LLMErrorType.SCHEMA_ERROR
    return LLMErrorType.UNKNOWN


def _get_retry_config(error_type: LLMErrorType) -> dict:
    """从 config 读取指定错误类型的重试配置"""
    mapping = {
        LLMErrorType.RATE_LIMIT: {
            "max_retries": config.LLM_MAX_RETRIES_RATE_LIMIT,
            "base_delay":  config.LLM_BASE_DELAY_RATE_LIMIT,
            "max_delay":  config.LLM_MAX_DELAY_RATE_LIMIT,
        },
        LLMErrorType.TIMEOUT: {
            "max_retries": config.LLM_MAX_RETRIES_TIMEOUT,
            "base_delay":  config.LLM_BASE_DELAY_TIMEOUT,
            "max_delay":  config.LLM_MAX_DELAY_TIMEOUT,
        },
        LLMErrorType.SERVER_ERROR: {
            "max_retries": config.LLM_MAX_RETRIES_SERVER_ERROR,
            "base_delay":  config.LLM_BASE_DELAY_SERVER_ERROR,
            "max_delay":  config.LLM_MAX_DELAY_SERVER_ERROR,
        },
        LLMErrorType.SCHEMA_ERROR: {
            "max_retries": config.LLM_MAX_RETRIES_SCHEMA_ERROR,
            "base_delay":  config.LLM_BASE_DELAY_SCHEMA_ERROR,
            "max_delay":  config.LLM_MAX_DELAY_SCHEMA_ERROR,
        },
        LLMErrorType.AUTH_ERROR: {
            "max_retries": config.LLM_MAX_RETRIES_AUTH_ERROR,
            "base_delay":  config.LLM_BASE_DELAY_AUTH_ERROR,
            "max_delay":  config.LLM_MAX_DELAY_AUTH_ERROR,
        },
        LLMErrorType.UNKNOWN: {
            "max_retries": config.LLM_MAX_RETRIES_UNKNOWN,
            "base_delay":  config.LLM_BASE_DELAY_UNKNOWN,
            "max_delay":  config.LLM_MAX_DELAY_UNKNOWN,
        },
    }
    return mapping[error_type]


def _compute_delay(base_delay: float, attempt: int, max_delay: float) -> float:
    """计算退避延迟：指数退避 + jitter"""
    delay = min(base_delay * (2 ** attempt), max_delay)
    jitter = delay * 0.2 * (0.5 - random.random())
    return max(0, delay + jitter)


def _invoke_with_timeout(
    model: Type[BaseModel],
    system_prompt: str,
    user_prompt: str,
    timeout: float,
) -> BaseModel:
    """带超时的单次调用（同步）"""
    def _do_invoke():
        client = get_llm_client()
        structured_client = client.with_structured_output(model)
        messages = _build_messages(system_prompt, user_prompt)
        return structured_client.invoke(messages)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_do_invoke)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            future.cancel()
            raise TimeoutError(f"LLM调用超时（>{timeout}s）[{model.__name__}]")


# ─────────────────────────────────────────────
# 结构化输出调用（核心改进：带重试）
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

    内部实现：带重试 + 超时控制。
    - 指数退避 + jitter（避免惊群效应）
    - 按错误类型差异化重试策略
    - 超时通过 ThreadPoolExecutor 实现（默认 60 秒）

    Args:
        model: Pydantic 模型类
        system_prompt: 系统提示词
        user_prompt: 用户提示词

    Returns:
        Pydantic 模型实例

    Raises:
        RuntimeError: 当 LLM 调用失败且重试耗尽时
    """
    last_error = None
    timeout = config.LLM_CALL_TIMEOUT_SECONDS

    for attempt in range(4):  # 最多尝试 4 次（1次正常 + 3次重试）
        try:
            result = _invoke_with_timeout(model, system_prompt, user_prompt, timeout)
            if attempt > 0:
                logger.info(f"[重试成功] {model.__name__} 第 {attempt + 1} 次尝试成功")
            return result

        except Exception as e:
            last_error = e
            error_type = _classify_error(e)
            cfg = _get_retry_config(error_type)

            if attempt >= cfg["max_retries"]:
                logger.error(
                    f"[重试耗尽] {model.__name__} 错误类型={error_type.value}, "
                    f"共尝试 {attempt + 1} 次"
                )
                break

            sleep_time = _compute_delay(
                cfg["base_delay"], attempt, cfg["max_delay"]
            )
            logger.warning(
                f"[重试] {model.__name__} 第 {attempt + 1} 次失败 "
                f"error_type={error_type.value}, "
                f"{sleep_time:.1f}s 后重试..."
            )
            time.sleep(sleep_time)

    raise RuntimeError(f"LLM调用失败（已重试）[{model.__name__}]: {last_error}")


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

    return "\n".join(lines) + "\n"


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

def invoke_llm_json(system_prompt: str, user_prompt: str) -> dict:
    """
    [兼容] 旧接口，内部不再使用
    保留是为了避免其他模块直接调用时报错
    """
    raise NotImplementedError(
        "invoke_llm_json 已废弃，请使用新的结构化调用函数"
    )
