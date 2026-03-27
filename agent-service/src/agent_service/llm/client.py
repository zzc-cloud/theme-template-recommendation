"""
LLM 客户端
使用 SiliconFlow 的 OpenAI 兼容格式 API
支持结构化输出 (with_structured_output) 替代手动 JSON 解析
内置按错误类型差异化重试机制
"""

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
    IterationEvaluation,
    LowConfidenceResult,
    ThemeJudgment,
    TemplateUsability,
)

logger = logging.getLogger(__name__)


class LLMCallError(RuntimeError):
    """底层 LLM 调用失败（含重试耗尽），统一向上抛出"""
    pass


# ─────────────────────────────────────────────
# JSON 修复工具
# ─────────────────────────────────────────────
def _fix_malformed_json(raw_str: str) -> str:
    """
    尝试修复 LLM 返回的格式错误 JSON

    常见问题：
    1. 双重编码：'{"{ "key": "value" }"}' → '{ "key": "value" }'
    2. 多余的引号包裹：'"{"key": "value"}"' → '{"key": "value"}'
    3. Markdown 代码块：'```json\\n{...}\\n```' → '{...}'
    """
    if not raw_str:
        return raw_str

    s = raw_str.strip()

    # 1. 移除 markdown 代码块包裹
    if s.startswith("```"):
        lines = s.split("\n")
        # 移除首行（```json 或 ```）
        if lines[0].startswith("```"):
            lines = lines[1:]
        # 移除尾行（```）
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()

    # 2. 递归解包多层引号包裹
    # 处理双重编码和单层引号包裹的混合情况
    max_iterations = 5  # 防止无限循环
    for _ in range(max_iterations):
        # 双重编码：'{"{...}"}' 模式
        if s.startswith('{"{') and s.endswith('"}'):
            inner = s[2:-2]  # 移除 {" 和 "}
            if inner.startswith('"') and inner.endswith('"'):
                inner = inner[1:-1]
            s = inner.replace('\\"', '"')
            continue
        # 单层引号包裹：'"{...}"' 模式
        if s.startswith('"{"') and s.endswith('}"'):
            s = s[1:-1]
            continue
        # 不再需要处理，退出循环
        break

    return s


def _try_parse_json_with_fix(raw_str: str, model: Type[BaseModel]) -> Optional[BaseModel]:
    """
    尝试解析 JSON，失败时自动修复后重试

    Args:
        raw_str: LLM 返回的原始字符串
        model: 目标 Pydantic 模型

    Returns:
        解析成功的模型实例，或 None（解析失败）
    """
    # 第一次尝试：直接解析
    try:
        data = json.loads(raw_str)
        return model.model_validate(data)
    except (json.JSONDecodeError, Exception) as e:
        logger.debug(f"[JSON解析] 首次解析失败: {e}")

    # 第二次尝试：修复后解析
    try:
        fixed_str = _fix_malformed_json(raw_str)
        if fixed_str != raw_str:
            logger.info(f"[JSON修复] 原始: {raw_str[:100]}... → 修复后: {fixed_str[:100]}...")
            data = json.loads(fixed_str)
            return model.model_validate(data)
    except (json.JSONDecodeError, Exception) as e:
        logger.debug(f"[JSON解析] 修复后解析仍失败: {e}")

    return None


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
            api_key=config.SILICONFLOW_LLM_API_KEY,
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
    """
    带超时的单次调用（同步）

    使用 include_raw=True 获取原始响应，以便在解析失败时尝试修复 JSON
    """
    def _do_invoke():
        client = get_llm_client()
        # 使用 include_raw=True 来获取原始响应
        structured_client = client.with_structured_output(model, include_raw=True)
        messages = _build_messages(system_prompt, user_prompt)
        result = structured_client.invoke(messages)

        # 如果解析成功，直接返回
        if result.get("parsed") is not None:
            return result["parsed"]

        # 如果解析失败但有原始响应，尝试修复 JSON
        raw = result.get("raw")
        parsing_error = result.get("parsing_error")
        if raw is not None and parsing_error is not None:
            raw_content = raw.content if hasattr(raw, 'content') else str(raw)
            logger.warning(f"[JSON解析失败] 尝试修复: {raw_content[:200]}...")

            # 尝试修复并重新解析
            parsed = _try_parse_json_with_fix(raw_content, model)
            if parsed is not None:
                logger.info(f"[JSON修复成功] {model.__name__}")
                return parsed

        # 如果修复也失败，抛出原始错误
        if parsing_error:
            raise parsing_error

        # 兜底：返回解析结果（可能是 None）
        return result.get("parsed")

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
        LLMCallError: 当 LLM 调用失败且重试耗尽时
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

    raise LLMCallError(f"LLM调用失败（已重试）[{model.__name__}]: {last_error}")


# ─────────────────────────────────────────────
# 纯文本输出调用（用于 Markdown 生成等场景）
# ─────────────────────────────────────────────

def _invoke_text_with_timeout(
    system_prompt: str,
    user_prompt: str,
    timeout: float,
) -> str:
    """
    带超时的单次文本调用（同步）

    与 _invoke_with_timeout 不同，这里直接返回文本内容，不做结构化解析。
    """
    def _do_invoke():
        client = get_llm_client()
        messages = _build_messages(system_prompt, user_prompt)
        result = client.invoke(messages)
        # AIMessage 的 content 属性包含文本内容
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
    """
    调用 LLM 并返回纯文本（非结构化输出）

    复用现有的：
    - 错误分类与重试机制（_classify_error, _get_retry_config）
    - 超时控制（ThreadPoolExecutor）
    - 指数退避策略（_compute_delay）

    Args:
        system_prompt: 系统提示词
        user_prompt: 用户提示词
        timeout: 可选超时时间（秒），默认使用 config.LLM_CALL_TIMEOUT_SECONDS

    Returns:
        str: LLM 生成的文本内容

    Raises:
        LLMCallError: 当 LLM 调用失败且重试耗尽时
    """
    last_error = None
    if timeout is None:
        timeout = config.LLM_CALL_TIMEOUT_SECONDS

    for attempt in range(4):  # 最多尝试 4 次（1次正常 + 3次重试）
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
