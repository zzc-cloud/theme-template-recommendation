
import json
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
    IterationRefinementResult,
    NormalizedQuestionResult,
    LowConfidenceResult,
    ThemeJudgment,
    TemplateUsability,
    DimensionSelectionGuidance,
    HierarchyNavigationResult,
    SectorFilterResult,
)

logger = logging.getLogger(__name__)


class LLMCallError(RuntimeError):
    pass


def _fix_malformed_json(raw_str: str) -> str:
    if not raw_str:
        return raw_str

    s = raw_str.strip()

    if s.startswith("```"):
        lines = s.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()

    max_iterations = 5
    for _ in range(max_iterations):
        if s.startswith('{"{') and s.endswith('"}'):
            inner = s[2:-2]
            if inner.startswith('"') and inner.endswith('"'):
                inner = inner[1:-1]
            s = inner.replace('\\"', '"')
            continue
        if s.startswith('"{"') and s.endswith('}"'):
            s = s[1:-1]
            continue
        break

    return s


def _try_parse_json_with_fix(raw_str: str, model: Type[BaseModel]) -> Optional[BaseModel]:
    try:
        data = json.loads(raw_str)
        return model.model_validate(data)
    except (json.JSONDecodeError, Exception) as e:
        logger.debug(f"[JSON解析] 首次解析失败: {e}")

    try:
        fixed_str = _fix_malformed_json(raw_str)
        if fixed_str != raw_str:
            logger.info(f"[JSON修复] 原始: {raw_str[:100]}... → 修复后: {fixed_str[:100]}...")
            data = json.loads(fixed_str)
            return model.model_validate(data)
    except (json.JSONDecodeError, Exception) as e:
        logger.debug(f"[JSON解析] 修复后解析仍失败: {e}")

    return None


def _log_parsed_result(model_name: str, parsed: BaseModel) -> None:
    if model_name == "HierarchyNavigationResult":
        count = len(parsed.selected_themes)
        if count == 0:
            logger.warning(f"[层级导航LLM返回] selected_themes=0，请检查 Prompt 或 LLM 判断逻辑")
        else:
            theme_names = [t.theme_alias for t in parsed.selected_themes[:3]]
            logger.info(f"[层级导航LLM返回] selected_themes={count}，前3个: {theme_names}")


_llm_client: Optional[ChatOpenAI] = None


def get_llm_client() -> ChatOpenAI:
    global _llm_client
    if _llm_client is None:
        _llm_client = ChatOpenAI(
            model=config.LLM_MODEL,
            api_key=config.SILICONFLOW_LLM_API_KEY,
            base_url=config.SILICONFLOW_BASE_URL,
            temperature=config.LLM_TEMPERATURE,
            max_tokens=config.LLM_MAX_TOKENS,
            streaming=False,
        )
    return _llm_client


class LLMErrorType(Enum):
    RATE_LIMIT   = "rate_limit"
    TIMEOUT      = "timeout"
    SERVER_ERROR = "server_error"
    AUTH_ERROR   = "auth_error"
    SCHEMA_ERROR = "schema_error"
    UNKNOWN      = "unknown"


def _classify_error(e: Exception) -> LLMErrorType:
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
    delay = min(base_delay * (2 ** attempt), max_delay)
    jitter = delay * 0.2 * (0.5 - random.random())
    return max(0, delay + jitter)


def _invoke_with_timeout(
    model: Type[BaseModel],
    system_prompt: str,
    user_prompt: str,
    timeout: float,
) -> BaseModel:
    def _do_invoke():
        client = get_llm_client()
        structured_client = client.with_structured_output(model, include_raw=True)
        messages = _build_messages(system_prompt, user_prompt)
        result = structured_client.invoke(messages)

        if result.get("parsed") is not None:
            parsed = result["parsed"]
            _log_parsed_result(model.__name__, parsed)
            return parsed

        raw = result.get("raw")
        parsing_error = result.get("parsing_error")
        if raw is not None and parsing_error is not None:
            raw_content = raw.content if hasattr(raw, 'content') else str(raw)
            logger.warning(f"[JSON解析失败] 尝试修复: {raw_content[:200]}...")

            parsed = _try_parse_json_with_fix(raw_content, model)
            if parsed is not None:
                logger.info(f"[JSON修复成功] {model.__name__}")
                _log_parsed_result(model.__name__, parsed)
                return parsed

        if parsing_error:
            raw_content = raw.content if hasattr(raw, 'content') else str(raw)
            logger.error(
                f"[JSON解析+修复均失败] {model.__name__}，"
                f"原始响应({len(raw_content)}字符): {raw_content[:500]}"
            )
            raise parsing_error

        parsed = result.get("parsed")
        if parsed is not None:
            _log_parsed_result(model.__name__, parsed)
        return parsed

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_do_invoke)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            future.cancel()
            raise TimeoutError(f"LLM调用超时（>{timeout}s）[{model.__name__}]")


def _build_messages(system_prompt: str, user_prompt: str) -> list:
    return [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]


def invoke_structured(
    model: Type[BaseModel],
    system_prompt: str,
    user_prompt: str,
) -> BaseModel:
    last_error = None
    timeout = config.LLM_CALL_TIMEOUT_SECONDS

    for attempt in range(4):
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

    raise LLMCallError(f"LLM调用失败（已重试）[{model.__name__}]: {last_error}")


def _invoke_text_with_timeout(
    system_prompt: str,
    user_prompt: str,
    timeout: float,
) -> str:
    def _do_invoke():
        client = get_llm_client()
        messages = _build_messages(system_prompt, user_prompt)
        result = client.invoke(messages)
        return result.content

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_do_invoke)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            future.cancel()
            raise TimeoutError(f"LLM文本调用超时（>{timeout}s）")


def invoke_text(
    system_prompt: str,
    user_prompt: str,
    timeout: Optional[float] = None,
) -> str:
    last_error = None
    if timeout is None:
        timeout = config.LLM_CALL_TIMEOUT_SECONDS

    for attempt in range(4):
        try:
            result = _invoke_text_with_timeout(system_prompt, user_prompt, timeout)
            if attempt > 0:
                logger.info(f"[重试成功] 文本生成 第 {attempt + 1} 次尝试成功")
            return result

        except Exception as e:
            last_error = e
            error_type = _classify_error(e)
            cfg = _get_retry_config(error_type)

            if attempt >= cfg["max_retries"]:
                logger.error(
                    f"[重试耗尽] 文本生成 错误类型={error_type.value}, "
                    f"共尝试 {attempt + 1} 次"
                )
                break

            sleep_time = _compute_delay(
                cfg["base_delay"], attempt, cfg["max_delay"]
            )
            logger.warning(
                f"[重试] 文本生成 第 {attempt + 1} 次失败 "
                f"error_type={error_type.value}, "
                f"{sleep_time:.1f}s 后重试..."
            )
            time.sleep(sleep_time)

    raise LLMCallError(f"LLM文本调用失败（已重试）: {last_error}")


def _build_history_str(conversation_history: list) -> str:
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
    from . import prompts as llm_prompts

    system_prompt = "你是一个专业的银行数据分析助手，擅长提取用户问题中的关键业务词组。重要：返回的 JSON 必须包含名为 \"phrases\" 的键，值必须是字符串数组。不要使用任何 markdown 代码块包裹。"
    user_prompt = llm_prompts.PHRASE_EXTRACTION_PROMPT.format(
        user_question=user_question,
        conversation_history=_build_history_str(conversation_history or []),
    )
    return invoke_structured(PhraseExtraction, system_prompt, user_prompt)


def classify_phrases(user_question: str, phrases: list[str]) -> PhraseClassification:
    from . import prompts as llm_prompts

    system_prompt = "你是一个专业的银行数据分析助手，擅长对词组进行语义分类。重要：直接输出 JSON，不要使用任何 markdown 代码块（如 ```json）包裹。"
    user_prompt = llm_prompts.PHRASE_CLASSIFICATION_PROMPT.format(
        user_question=user_question,
        phrases="\n".join(f"- {p}" for p in phrases),
    )
    return invoke_structured(PhraseClassification, system_prompt, user_prompt)


def refine_concepts(
    user_question: str,
    round_num: int,
    max_rounds: int,
    pending_search_results_str: str,
    converged_concepts_str: str,
) -> IterationRefinementResult:
    from . import prompts as llm_prompts

    system_prompt = "你是一个专业的银行数据分析专家，正在精确定位分析指标。重要：直接输出 JSON，不要使用任何 markdown 代码块（如 ```json）包裹。"
    user_prompt = llm_prompts.ITERATION_REFINEMENT_PROMPT.format(
        user_question=user_question,
        round=round_num,
        max_rounds=max_rounds,
        pending_search_results_str=pending_search_results_str,
        converged_concepts_str=converged_concepts_str,
    )
    return invoke_structured(IterationRefinementResult, system_prompt, user_prompt)


def generate_normalized_question(
    user_question: str,
    filter_phrases_str: str,
    converged_concepts_str: str,
) -> NormalizedQuestionResult:
    from . import prompts as llm_prompts

    system_prompt = "你是一个专业的银行数据分析专家，擅长将口语化问题转换为标准分析语言。重要：直接输出 JSON，不要使用任何 markdown 代码块（如 ```json）包裹。"
    user_prompt = llm_prompts.NORMALIZED_QUESTION_PROMPT.format(
        user_question=user_question,
        filter_phrases_str=filter_phrases_str,
        converged_concepts_str=converged_concepts_str,
    )
    return invoke_structured(NormalizedQuestionResult, system_prompt, user_prompt)


def handle_low_confidence(
    user_question: str,
    low_confidence_concepts: list[str],
    search_results_str: str,
) -> LowConfidenceResult:
    from . import prompts as llm_prompts

    system_prompt = "你是一个专业的银行数据分析专家。重要：直接输出 JSON，不要使用任何 markdown 代码块（如 ```json）包裹。"
    user_prompt = llm_prompts.LOW_CONFIDENCE_PROMPT.format(
        user_question=user_question,
        low_confidence_concepts=", ".join(low_confidence_concepts),
        search_results_str=search_results_str,
    )
    return invoke_structured(LowConfidenceResult, system_prompt, user_prompt)


def generate_dimension_selection_guidance(
    user_question: str,
    dimensions_str: str,
    analysis_dimensions_str: str,
    jaccard_threshold: float | None = None,
) -> DimensionSelectionGuidance:
    from .. import config
    from . import prompts as llm_prompts

    if jaccard_threshold is None:
        jaccard_threshold = config.JACCARD_SIMILARITY_THRESHOLD

    system_prompt = "你是一个专业的银行数据分析专家，擅长分析维度间的独立性和推荐优先级。重要：直接输出 JSON，不要使用任何 markdown 代码块（如 ```json）包裹。"
    user_prompt = llm_prompts.DIMENSION_SELECTION_GUIDANCE_PROMPT.format(
        user_question=user_question,
        dimensions_str=dimensions_str,
        analysis_dimensions_str=analysis_dimensions_str,
        jaccard_threshold=jaccard_threshold,
    )
    return invoke_structured(DimensionSelectionGuidance, system_prompt, user_prompt)


def judge_theme(
    user_question: str,
    analysis_dimensions_str: str,
    theme_alias: str,
    theme_path: str,
    filter_indicators_str: str,
    analysis_indicators_str: str,
) -> ThemeJudgment:
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


def filter_sectors_by_question(
    user_question: str,
    sector_list_str: str,
) -> SectorFilterResult:
    from . import prompts as llm_prompts

    system_prompt = "你是一个专业的银行数据分析专家，擅长判断板块与用户需求的关联性。重要：直接输出 JSON，不要使用任何 markdown 代码块（如 ```json）包裹。"
    user_prompt = llm_prompts.SECTOR_FILTER_PROMPT.format(
        user_question=user_question,
        sector_list_str=sector_list_str,
    )
    return invoke_structured(SectorFilterResult, system_prompt, user_prompt)


def filter_themes_by_hierarchy(
    user_question: str,
    analysis_dimensions_str: str,
    theme_list_str: str,
) -> HierarchyNavigationResult:
    from . import prompts as llm_prompts

    system_prompt = "你是一个专业的银行数据分析专家，擅长从大量主题中筛选与用户需求相关的候选。重要：直接输出 JSON，不要使用任何 markdown 代码块（如 ```json）包裹。"
    user_prompt = llm_prompts.HIERARCHY_NAVIGATION_PROMPT.format(
        user_question=user_question,
        analysis_dimensions=analysis_dimensions_str,
        theme_list_str=theme_list_str,
    )
    return invoke_structured(HierarchyNavigationResult, system_prompt, user_prompt)


def invoke_llm_json(system_prompt: str, user_prompt: str) -> dict:
    raise NotImplementedError(
        "invoke_llm_json 已废弃，请使用新的结构化调用函数"
    )
