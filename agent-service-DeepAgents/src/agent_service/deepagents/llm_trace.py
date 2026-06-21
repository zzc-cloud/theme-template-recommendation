"""LLM trace 请求上下文与 LangChain callback 错误事件。

本模块只负责三件事：
1. 通过 `ContextVar` 保存一次 `/recommend` 请求的 trace 关联信息。
2. 提供 `save_llm_input_payload` / `save_llm_output_payload` 给 provider wrapper 落库。
3. 保留 LangChain callback 中的错误采集。

重要边界：LLM input/output 的真实内容不在 callback 中采集，而是在
`traced_chat_openai.py` 的 provider 调用边界采集。callback 里的 start/end 事件拿到的是
LangChain 中间对象，不代表最终 provider request/response；只有错误事件仍适合从
callback 侧捕获，因为异常可能发生在请求构造、provider 调用或 LangChain 转换链路中。
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

from langchain_core.callbacks import BaseCallbackHandler

from .llm_trace_store import save_trace_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMTraceContext:
    """一次 API 请求内所有 LLM trace 事件共享的关联维度。

    - `thread_id`：业务线程 ID，用于跨首次请求和确认回复串联同一轮对话。
    - `request_id`：单次 `/recommend` 调用 ID，用于区分同一 thread 下多次请求。
    - `conversation_id`：当前实现与 `thread_id` 一致，保留为对话级查询维度。
    - `user_id`：当前服务化场景固定为 `local`，后续多用户时可替换。
    """

    user_id: str
    conversation_id: str
    thread_id: str
    request_id: str


# ContextVar 能穿透当前 async task 内的调用链，让 model wrapper 不必从函数参数层层传 ID。
# 这里不用全局变量或显式参数传递，是为了避免并发 `/recommend` 请求把 thread/request 维度串到彼此的 trace 中。
_trace_context: ContextVar[LLMTraceContext | None] = ContextVar("llm_trace_context", default=None)


@contextmanager
def llm_trace_context(*, user_id: str, conversation_id: str, thread_id: str, request_id: str) -> Iterator[None]:
    """设置一次请求范围内的 LLM trace 上下文。

    只有在这个 context manager 内执行的 provider wrapper 才会写 input/output trace；
    context 退出后自动 reset，避免不同请求之间串线。
    """
    token = _trace_context.set(LLMTraceContext(
        user_id=user_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        request_id=request_id,
    ))
    try:
        yield
    finally:
        _trace_context.reset(token)


def save_llm_input_payload(*, model_name: str | None, payload: dict[str, Any]) -> None:
    """保存 provider 边界的最终 request payload。

    调用方应传入 `TracedChatOpenAI._get_request_payload()` 得到的 OpenAI-compatible
    payload，而不是 LangChain callback messages。这样才能看到最终发送给 provider 的
    tools、tool_choice、response_format 和模型参数。
    """
    _save_context_event("llm_input", model_name=model_name, token_usage=None, payload=payload)


def save_llm_output_payload(*, model_name: str | None, token_usage: Any | None, payload: dict[str, Any]) -> None:
    """保存 provider 边界的直接 response payload。

    调用方应传入 `raw_response.parse()` 的结果；payload 顶层就是 provider 响应本身，
    不再额外包装为 LangChain `LLMResult` 或摘要结构。
    """
    _save_context_event("llm_output", model_name=model_name, token_usage=token_usage, payload=payload)


def _save_context_event(
    event_type: str,
    *,
    model_name: str | None,
    token_usage: Any | None,
    payload: dict[str, Any],
) -> None:
    """把当前请求上下文补齐成 `llm_trace_events` 表的一条事件。

    `run_id` / `parent_run_id` 当前固定为 None，因为 provider 边界采集点不稳定暴露
    LangChain run id；请求级关联使用 `thread_id + request_id`。trace 写入失败只记录日志，
    不影响推荐主流程。
    """
    context = _trace_context.get()
    if context is None:
        return
    event = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "user_id": context.user_id,
        "conversation_id": context.conversation_id,
        "thread_id": context.thread_id,
        "request_id": context.request_id,
        "run_id": None,
        "parent_run_id": None,
        "model_name": model_name,
        "token_usage": token_usage,
        "payload": payload,
    }
    try:
        save_trace_event(event)
    except Exception:
        logger.exception("保存 LLM %s trace 失败", event_type)


class LLMTraceCallback(BaseCallbackHandler):
    """LangChain callback handler，仅保留 LLM 错误事件采集。

    LLM input/output 不在这里保存：
    - `on_chat_model_start` 只能看到 LangChain messages，不是最终 provider payload。
    - `on_llm_end` 只能看到 LangChain `LLMResult` wrapper，不是 provider 原始响应。

    真正的 input/output trace 由 `TracedChatOpenAI` 在 provider 调用边界写入。
    """

    def __init__(self, *, user_id: str, conversation_id: str, thread_id: str, request_id: str) -> None:
        """绑定请求维度，供 callback 错误事件落库使用。"""
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.thread_id = thread_id
        self.request_id = request_id

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        """不保存 start 事件，避免把 callback messages 误当最终 prompt。"""
        return None

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        """不保存 end 事件，避免把 LangChain `LLMResult` wrapper 写入 output。"""
        return None

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        """保存 LangChain/Provider 调用过程中抛出的异常信息。

        错误事件只记录异常类型和消息，并保留 callback 提供的 run id，便于把 trace 与
        LangChain 调试日志对齐；它不承担 input/output payload 采集职责。
        """
        self._save(
            "llm_error",
            run_id=run_id,
            parent_run_id=parent_run_id,
            payload={"error": {"type": type(error).__name__, "message": str(error)}},
        )

    def _save(
        self,
        event_type: str,
        *,
        run_id: Any,
        parent_run_id: Any | None,
        payload: dict[str, Any],
        model_name: str | None = None,
        token_usage: Any | None = None,
    ) -> None:
        """保存 callback 事件，保留 LangChain run id 方便和调试日志对齐。"""
        event = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "thread_id": self.thread_id,
            "request_id": self.request_id,
            "run_id": str(run_id) if run_id is not None else None,
            "parent_run_id": str(parent_run_id) if parent_run_id is not None else None,
            "model_name": model_name,
            "token_usage": token_usage,
            "payload": payload,
        }
        try:
            save_trace_event(event)
        except Exception:
            logger.exception("保存 LLM trace 失败")


# 以下 helper 保留给 trace payload 兼容与测试使用。当前 output 主路径已改为 provider 原始响应，
# 但这些函数仍可用于安全序列化、token usage 提取，以及后续需要展示 LangChain 对象摘要时复用。
def _message_batches(messages: list[list[Any]]) -> list[list[dict[str, Any]]]:
    """把 LangChain message batch 转成可 JSON 化摘要。"""
    return [[_message_summary(message) for message in batch] for batch in messages]


def _message_summary(message: Any) -> dict[str, Any]:
    """提取消息摘要，兼容 LangChain message、dict 和 provider-like dict。

    该摘要用于旧测试和后续展示层复用，不代表当前 `llm_input.payload` 的主采集结构。
    """
    data = _safe_json(message)
    if not isinstance(data, dict):
        return {"role": type(message).__name__, "content": data}

    content = data.get("content")
    if not content:
        content = getattr(message, "content", None)
    if not content:
        content = data.get("text")
    summary = {
        "role": data.get("type") or data.get("role") or type(message).__name__,
        "content": content,
    }
    tool_calls = _tool_calls_from_message(data)
    if tool_calls:
        summary["tool_calls"] = tool_calls
    for key in ("name", "id", "tool_call_id", "response_metadata", "usage_metadata"):
        if data.get(key):
            summary[key] = data[key]
    return summary


def _llm_output_payload(response: Any) -> dict[str, Any]:
    """旧版 LangChain `LLMResult` 摘要函数，当前不作为 output trace 主路径。"""
    raw = _safe_json(response)
    generations = _generations(raw)
    primary = generations[0][0] if generations and generations[0] else {}
    message = primary.get("message") if isinstance(primary, dict) else None
    metadata = _llm_output_metadata(raw, primary)
    payload: dict[str, Any] = {
        "message": message,
        "text": primary.get("text") if isinstance(primary, dict) else None,
        "generations": generations,
        "metadata": metadata,
    }
    return payload


def _llm_output_metadata(raw: Any, primary: Any) -> dict[str, Any]:
    """从旧版 LangChain output wrapper 中抽取模型、token 和 finish reason。"""
    metadata: dict[str, Any] = {}
    llm_output = raw.get("llm_output") if isinstance(raw, dict) else None
    if isinstance(llm_output, dict):
        for key in ("id", "model_name", "model_provider", "system_fingerprint"):
            if key in llm_output:
                metadata[key] = llm_output[key]
        token_usage = llm_output.get("token_usage") or llm_output.get("usage")
        if token_usage is not None:
            metadata["token_usage"] = token_usage
    generation_info = primary.get("generation_info") if isinstance(primary, dict) else None
    if isinstance(generation_info, dict):
        if generation_info.get("finish_reason") is not None:
            metadata["finish_reason"] = generation_info["finish_reason"]
        if generation_info:
            metadata["generation_info"] = generation_info
    return metadata


def _generations(payload: Any) -> list[list[dict[str, Any]]]:
    """把 LangChain generations 二维数组转成 message/text 摘要。"""
    if not isinstance(payload, dict):
        return []
    generations = payload.get("generations") or []
    output = []
    for batch in generations:
        batch_output = []
        for generation in batch if isinstance(batch, list) else [batch]:
            batch_output.append(_generation_summary(generation))
        output.append(batch_output)
    return output


def _generation_summary(generation: Any) -> dict[str, Any]:
    """摘要单个 LangChain generation，尽量保留 message、text 和 generation_info。"""
    raw_text = getattr(generation, "text", None)
    data = _safe_json(generation)
    if not isinstance(data, dict):
        return {"text": data}
    generation_text = data.get("text") or raw_text
    message = data.get("message")
    if message is not None:
        message_summary = _message_summary(message)
        if not _content_text(message_summary.get("content")) and generation_text:
            message_summary["content"] = generation_text
        summary = {"message": message_summary}
        if generation_text:
            summary["text"] = generation_text
        if data.get("generation_info"):
            summary["generation_info"] = data["generation_info"]
        return summary
    summary: dict[str, Any] = {"text": generation_text}
    if data.get("generation_info"):
        summary["generation_info"] = data["generation_info"]
    return summary


def _tool_calls_from_message(data: dict[str, Any]) -> Any | None:
    """兼容直接 `tool_calls` 与 LangChain `additional_kwargs.tool_calls` 两种来源。"""
    tool_calls = data.get("tool_calls")
    additional_kwargs = data.get("additional_kwargs") or {}
    if not tool_calls and isinstance(additional_kwargs, dict):
        tool_calls = additional_kwargs.get("tool_calls")
    return [_tool_call_summary(call) for call in tool_calls] if isinstance(tool_calls, list) else tool_calls


def _tool_call_summary(call: Any) -> dict[str, Any]:
    """把 OpenAI function-call/tool-call 结构压缩为 id/name/args。"""
    data = _safe_json(call)
    if not isinstance(data, dict):
        return {"raw": data}
    function = data.get("function") or {}
    args = data.get("args") or data.get("arguments") or function.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            pass
    return {
        "id": data.get("id"),
        "name": data.get("name") or function.get("name"),
        "args": args,
    }


def _content_text(content: Any) -> str | None:
    """把 str/list content 尽量归并为可判断非空的文本。"""
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text).strip())
        return "\n".join(parts) or None
    return None


def _safe_json(value: Any) -> Any:
    """递归转换为可 JSON 序列化结构，优先使用 Pydantic/LangChain dump 方法。

    该函数只解决“能否写入 JSON 列”的问题；不能借此重塑 provider payload 语义，
    例如不能把 OpenAI-compatible response 改写成 LangChain summary。
    """
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _safe_json(value.model_dump(mode="json"))
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _safe_json(value.dict())
        except Exception:
            pass
    return str(value)


def _model_name(serialized: dict[str, Any] | None, payload: Any | None) -> str | None:
    """从 LangChain serialized 或旧版 payload 中兼容提取模型名。"""
    if serialized:
        kwargs = serialized.get("kwargs") or {}
        for key in ("model", "model_name"):
            if kwargs.get(key):
                return str(kwargs[key])
        if serialized.get("name"):
            return str(serialized["name"])
    if isinstance(payload, dict):
        llm_output = payload.get("llm_output") or {}
        if isinstance(llm_output, dict) and llm_output.get("model_name"):
            return str(llm_output["model_name"])
    return None


def _token_usage(payload: Any) -> Any | None:
    """从 provider 原始响应或旧版 LangChain wrapper 中提取 token usage。

    当前主路径优先读取 provider response 顶层 `usage`；保留 `llm_output` 兼容分支，
    仅是为了旧测试或非标准 provider shim，不表示 output trace 又回到 LangChain wrapper。
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("usage") is not None:
        return payload["usage"]
    if payload.get("token_usage") is not None:
        return payload["token_usage"]
    llm_output = payload.get("llm_output") or {}
    if isinstance(llm_output, dict):
        return llm_output.get("token_usage") or llm_output.get("usage")
    return None
