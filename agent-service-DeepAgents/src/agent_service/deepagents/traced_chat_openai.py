"""OpenAI-compatible ChatOpenAI 的 LLM trace 采集包装器。

本模块是 LLM 输入/输出 trace 的 provider 边界采集点：
- 输入：记录 LangChain 已经完成工具绑定、参数合并后的最终 request payload。
- 输出：记录 OpenAI-compatible provider 直接返回、尚未转换成 LangChain `ChatResult` 的 response payload。

注意：当前服务通过 `langchain_openai.ChatOpenAI` 访问 OpenAI-compatible endpoint，
因此 trace 保存的是 OpenAI-compatible wire shape，例如 `messages/tools/tool_choice`、
`choices/message/tool_calls/usage` 等字段；它不是 Anthropic 原生 Messages API shape，
也不应按 Anthropic SDK 的 `content` block 结构理解。
"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import (
    _construct_lc_result_from_responses_api,
    _handle_openai_api_error,
    _handle_openai_bad_request,
    _is_pydantic_class,
    openai,
    run_in_executor,
)

from .llm_trace import _safe_json, _token_usage, save_llm_input_payload, save_llm_output_payload


class TracedChatOpenAI(ChatOpenAI):
    """在 provider 调用边界记录最终请求 payload 与 provider 原始响应。

    这里刻意不使用 LangChain callback 的 `on_chat_model_start/on_llm_end`
    来记录 input/output，因为 callback 拿到的是 LangChain 的中间对象：
    - input 侧不是最终提交给 provider 的完整 payload；
    - output 侧是 `LLMResult` / `ChatGeneration` wrapper。

    本类只在两个最靠近 provider 的位置采集：
    - `_get_request_payload()` 返回后：最终请求 payload 已经构造完成。
    - `raw_response.parse()` 返回后：provider 响应尚未转成 LangChain `ChatResult`。
    """

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """记录最终提交给 OpenAI-compatible provider 的请求 payload。

        必须先调用 `super()`：LangChain 会在这里把 messages、tools、tool_choice、
        response_format、max_tokens/max_completion_tokens 等运行时参数合并成最终
        provider payload。trace 记录的就是这个 dict，避免只看到 callback messages。

        `source` 标识采集点，`provider` 标识当前是 OpenAI-compatible 适配层，
        `api_shape` 标识 payload 可能走 Chat Completions 或 Responses API 形态。
        `_safe_json(payload)` 只保证可 JSON 化，不能重命名、裁剪或重新解释 provider 字段。
        """
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        save_llm_input_payload(
            model_name=str(payload.get("model")) if payload.get("model") else None,
            payload={
                # source/provider/api_shape 用于前端或排障时识别该事件的采集边界。
                "source": "ChatOpenAI._get_request_payload",
                "provider": "openai-compatible",
                "api_shape": "chat_completions_or_responses",
                # `_safe_json` 只做可 JSON 化转换，不改写 OpenAI-compatible 字段语义。
                "payload": _safe_json(payload),
            },
        )
        return payload

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """同步调用路径：复制 LangChain 原逻辑，并在 provider 响应处插入 trace。

        这里保留 LangChain 的三个分支：
        1. `response_format`：走 beta chat.completions parse；
        2. Responses API：走 `root_client.responses`；
        3. Chat Completions：走 `client.with_raw_response.create`。

        每个分支都先拿到 provider response，再交回 LangChain 构造 `ChatResult`。
        trace 必须在 `raw_response.parse()` 后立即保存：此时已经拿到 provider 的
        可序列化响应对象，但还没有进入 `_create_chat_result()` /
        `_construct_lc_result_from_responses_api()` 的 LangChain wrapper 转换，
        因而最能代表排障所需的 wire-level output。
        """
        self._ensure_sync_client_available()
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        generation_info = None
        raw_response = None
        try:
            if "response_format" in payload:
                # OpenAI parse helper 不接受 stream 字段；保持 LangChain 原行为。
                payload.pop("stream")
                raw_response = self.root_client.chat.completions.with_raw_response.parse(**payload)
                response = raw_response.parse()
            elif self._use_responses_api(payload):
                original_schema_obj = kwargs.get("response_format")
                if original_schema_obj and _is_pydantic_class(original_schema_obj):
                    raw_response = self.root_client.responses.with_raw_response.parse(**payload)
                else:
                    raw_response = self.root_client.responses.with_raw_response.create(**payload)
                response = raw_response.parse()
                self._save_provider_output(response)
                if self.include_response_headers:
                    generation_info = {"headers": dict(raw_response.headers)}
                return _construct_lc_result_from_responses_api(
                    response,
                    schema=original_schema_obj,
                    metadata=generation_info,
                    output_version=self.output_version,
                )
            else:
                raw_response = self.client.with_raw_response.create(**payload)
                response = raw_response.parse()
        except openai.BadRequestError as e:
            _handle_openai_bad_request(e)
        except openai.APIError as e:
            _handle_openai_api_error(e)
        except Exception as e:
            # 保留 LangChain 原有行为：把底层 HTTP response 挂到异常上，便于上层排障。
            if raw_response is not None and hasattr(raw_response, "http_response"):
                e.response = raw_response.http_response  # type: ignore[attr-defined]
            raise e
        self._save_provider_output(response)
        if (
            self.include_response_headers
            and raw_response is not None
            and hasattr(raw_response, "headers")
        ):
            generation_info = {"headers": dict(raw_response.headers)}
        return self._create_chat_result(response, generation_info)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """异步调用路径：与 `_generate()` 对称，采集 provider 原始响应。

        同步和异步路径必须保持同一采集边界：都记录最终 provider request payload，
        都在 `raw_response.parse()` 后、LangChain 结果封装前记录 provider response。
        这样排查流式/非流式或 async/sync 差异时，不会因为执行路径不同而看到不同语义的 trace。
        """
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        generation_info = None
        raw_response = None
        try:
            if "response_format" in payload:
                payload.pop("stream")
                raw_response = await self.root_async_client.chat.completions.with_raw_response.parse(**payload)
                response = raw_response.parse()
            elif self._use_responses_api(payload):
                original_schema_obj = kwargs.get("response_format")
                if original_schema_obj and _is_pydantic_class(original_schema_obj):
                    raw_response = await self.root_async_client.responses.with_raw_response.parse(**payload)
                else:
                    raw_response = await self.root_async_client.responses.with_raw_response.create(**payload)
                response = raw_response.parse()
                self._save_provider_output(response)
                if self.include_response_headers:
                    generation_info = {"headers": dict(raw_response.headers)}
                return _construct_lc_result_from_responses_api(
                    response,
                    schema=original_schema_obj,
                    metadata=generation_info,
                    output_version=self.output_version,
                )
            else:
                raw_response = await self.async_client.with_raw_response.create(**payload)
                response = raw_response.parse()
        except openai.BadRequestError as e:
            _handle_openai_bad_request(e)
        except openai.APIError as e:
            _handle_openai_api_error(e)
        except Exception as e:
            if raw_response is not None and hasattr(raw_response, "http_response"):
                e.response = raw_response.http_response  # type: ignore[attr-defined]
            raise e
        self._save_provider_output(response)
        if (
            self.include_response_headers
            and raw_response is not None
            and hasattr(raw_response, "headers")
        ):
            generation_info = {"headers": dict(raw_response.headers)}
        return await run_in_executor(None, self._create_chat_result, response, generation_info)

    def _save_provider_output(self, response: Any) -> None:
        """保存 OpenAI-compatible provider 的直接返回内容。

        `response` 是 `raw_response.parse()` 的结果；此时还没有被 LangChain 转换成
        `ChatResult`、`LLMResult`、`AIMessage` 或 `ChatGeneration`。因此这里写入的
        `llm_output.payload` 应保持 provider 原始字段，如 `choices`、`message.tool_calls`、
        `usage` 等；不要再包成 `source/payload` 外壳，也不要替换成 LangChain summary。
        """
        payload = _safe_json(response)
        save_llm_output_payload(
            model_name=_provider_model_name(payload),
            token_usage=_token_usage(payload),
            payload=payload if isinstance(payload, dict) else {"raw": payload},
        )


def _provider_model_name(payload: Any) -> str | None:
    """从不同 provider/OpenAI-compatible 返回结构中提取模型名。

    常规 OpenAI-compatible 响应会在顶层返回 `model`；少数 provider shim 或测试对象
    可能使用 `model_name`，旧 LangChain wrapper 则可能放在 `llm_output.model_name`。
    这里仅做兼容提取，不改变 `llm_output.payload` 本身。
    """
    if isinstance(payload, dict):
        model = payload.get("model") or payload.get("model_name")
        if model:
            return str(model)
        # 兼容少数 LangChain 或 provider shim 仍把模型名放在 llm_output 的情况。
        llm_output = payload.get("llm_output")
        if isinstance(llm_output, dict) and llm_output.get("model_name"):
            return str(llm_output["model_name"])
    return None
