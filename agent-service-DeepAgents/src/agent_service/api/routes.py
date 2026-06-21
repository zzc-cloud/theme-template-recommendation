"""推荐 API 路由。

本文件是 FastAPI 与 DeepAgents runtime 的协议边界，只负责：
1. 并发控制：所有请求进入 agent 前先获取全局 semaphore。
2. 输入分流：首次请求构造 messages；pending interrupt 请求构造 `Command(resume=...)`。
3. SSE 转发：把 DeepAgents 的流式 chunk 转成 EventSourceResponse 输出。
4. 结果契约：识别 raw interrupt、透传最终 Markdown 文本和 `ErrorPayload`。

业务流程不在这里编排；推荐步骤、确认语义和最终展示内容由 `skills/` 下的
Skill 通过已注册工具完成。
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from langgraph.types import Command
from sse_starlette.sse import EventSourceResponse

from ..config import CONCURRENT_TIMEOUT_SECONDS, MAX_CONCURRENT_REQUESTS
from ..deepagents.agent_factory import get_agent
from ..deepagents.llm_trace import LLMTraceCallback, llm_trace_context
from ..deepagents.llm_trace_store import list_trace_events_by_thread
from ..deepagents.skill_protocol import build_recommend_messages, build_resume_payload_from_text
from .schemas import ErrorPayload, RecommendRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["recommend"])

# 进程级并发信号量，由 main.py lifespan 启动时初始化。
_semaphore: asyncio.Semaphore | None = None

# thread_id -> DeepAgents raw interrupt。
# 这里只保存“下一次同 thread_id 的 /recommend 应该走 resume”的路由标记；
# 真正的暂停状态由 agent_factory.py 中的 MemorySaver checkpointer 保存。
_pending_interrupts: dict[str, dict[str, Any]] = {}


def init_semaphore() -> None:
    """初始化全局并发信号量。"""
    global _semaphore
    _semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)


def get_semaphore() -> asyncio.Semaphore:
    """获取全局并发信号量。"""
    if _semaphore is None:
        raise RuntimeError("Semaphore 未初始化")
    return _semaphore


def get_current_concurrency() -> int:
    """返回当前正在执行的请求数，用于 /health 和限流判断。"""
    if _semaphore is None:
        return 0
    return MAX_CONCURRENT_REQUESTS - _semaphore._value


async def _acquire_semaphore() -> None:
    """进入 agent 执行前获取并发名额。"""
    semaphore = get_semaphore()
    if semaphore.locked() and get_current_concurrency() >= MAX_CONCURRENT_REQUESTS:
        raise HTTPException(status_code=429, detail="当前并发已达上限，请稍后重试")
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=CONCURRENT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=429, detail="等待并发名额超时，请稍后重试") from exc


def _json_event(payload: dict[str, Any]) -> dict[str, str]:
    """把标准结果包装成 SSE message 事件。"""
    return {"event": "message", "data": json.dumps(payload, ensure_ascii=False)}


def _error_event(code: str, message: str) -> dict[str, str]:
    """构造 ErrorPayload SSE 事件。"""
    return _json_event(ErrorPayload(code=code, message=message).model_dump(mode="json"))


def _maybe_payload(obj: Any) -> dict[str, Any] | None:
    """把 DeepAgents/LangGraph 可能返回的对象统一视作 payload dict。

    interrupt 在不同版本中可能表现为 dict，也可能是带 `.value` 的对象；
    这里集中兼容，避免下游提取逻辑绑定某一个具体内部类型。
    """
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "value") and isinstance(obj.value, dict):
        return obj.value
    return None


def _find_interrupt(chunk: Any) -> dict[str, Any] | None:
    """从流式 chunk 中提取 DeepAgents HITL interrupt payload。"""
    payload = _maybe_payload(chunk)
    if not payload:
        return None
    interrupts = payload.get("__interrupt__")
    if not interrupts:
        return None
    interrupt_obj = interrupts[0] if isinstance(interrupts, (list, tuple)) else interrupts
    return _maybe_payload(interrupt_obj)



def _extract_text_from_chunk(chunk: Any) -> str | None:
    """从 DeepAgents 更新 chunk 中取最新 assistant 文本。"""
    payload = _maybe_payload(chunk)
    if payload is None:
        return None
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        last = messages[-1]
        content = getattr(last, "content", None)
        if content is None and isinstance(last, dict):
            content = last.get("content")
        if isinstance(content, str):
            return content
    return None


async def _thread_has_pending_interrupt(agent: Any, thread_id: str) -> bool:
    """以 LangGraph checkpoint 快照为准判断 thread 是否仍停在 interrupt 上。

    `_pending_interrupts` 只是本进程内的快速分流标记，可能因为最终输出 chunk
    形态变化而没有被及时清理；真正的暂停点是否仍存在，要看 checkpointer 中该
    `thread_id` 的最新 StateSnapshot。只有 snapshot 仍有 interrupts/next 时，
    才说明图还停在 HITL 暂停点，下一次输入才应该使用 `Command(resume=...)`。
    """
    snapshot = await agent.aget_state({"configurable": {"thread_id": thread_id}})
    return bool(getattr(snapshot, "interrupts", ()) or getattr(snapshot, "next", ()))


async def _build_agent_input(req: RecommendRequest, agent: Any) -> Any:
    """根据真实 checkpoint 状态选择普通新消息或 interrupt resume 输入。

    普通追问不要从 console 或 API 层拼接完整历史，只把本轮用户输入作为
    messages update 交给同一个 `thread_id`；LangGraph 会从 checkpoint 恢复旧
    state，并由 messages channel 的 reducer 合并本轮新消息。
    """
    if req.thread_id in _pending_interrupts:
        if await _thread_has_pending_interrupt(agent, req.thread_id):
            return Command(resume=build_resume_payload_from_text(req.user_input))
        # 内存标记残留但 checkpoint 已经结束时，本次输入是同 thread 的普通追问；
        # 清理残留标记，避免把新问题误当成对上一次 interrupt 的 resume。
        _pending_interrupts.pop(req.thread_id, None)

    # DeepAgents/LangGraph 入口需要 graph state dict：{"messages": list[MessageLike]}。
    # 这里传的是“本轮 state update”，不是完整对话历史；旧 messages、工具结果、摘要等
    # 都由同一个 thread_id 对应的 checkpoint 恢复并合并。
    # build_recommend_messages() 只产出消息列表；不要把它改回 dict，
    # 否则这里会形成 {"messages": {"messages": [...]}} 的双层嵌套。
    return {"messages": build_recommend_messages(req)}


async def _stream_agent(input_data: Any, thread_id: str) -> AsyncIterator[dict[str, str]]:
    """执行一次 DeepAgents 流式调用并转换为 SSE event。

    这里处理三类输出：
    - interrupt chunk：保存 pending 标记，原样返回 DeepAgents raw interrupt 后结束本次 SSE。
    - 普通文本 chunk：原样透传，前端可展示也可忽略。
    - 未知 chunk：JSON 序列化后兜底透传。

    函数不解释用户确认语义；resume 后如何继续由 Skill 决定。
    """
    agent = get_agent()
    # request_id 是单次 /recommend 调用维度：同一 thread_id 可能经历首次请求和确认后的多次继续执行，
    # 用 request_id 可以把这些 provider input/output trace 拆开排查。
    request_id = str(uuid.uuid4())
    # callback 只负责 error trace；input/output 由 TracedChatOpenAI 在 provider 边界保存，
    # 避免把 LangChain callback messages 或 LLMResult wrapper 误当作真实 provider payload。
    callback = LLMTraceCallback(
        user_id="local",
        conversation_id=thread_id,
        thread_id=thread_id,
        request_id=request_id,
    )
    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [callback],
        # metadata 会随 LangChain run 传播，便于日志/回调侧对齐请求维度；
        # 真正让 TracedChatOpenAI 取到这些维度的是下面的 llm_trace_context。
        "metadata": {
            "user_id": "local",
            "conversation_id": thread_id,
            "thread_id": thread_id,
            "request_id": request_id,
        },
        "tags": ["deepagents", "llm-trace"],
    }

    # 请求级 trace 作用域必须包住 agent.astream：模型 wrapper 通过 ContextVar 读取
    # thread_id/request_id，并在 provider request/response 边界写 llm_input/llm_output。
    with llm_trace_context(
        user_id="local",
        conversation_id=thread_id,
        thread_id=thread_id,
        request_id=request_id,
    ):
        async for chunk in agent.astream(input_data, config=config, stream_mode="updates"):
            interrupt = _find_interrupt(chunk)
            if interrupt:
                # 这里只保存“下一次同 thread_id 可能需要 resume”的分流提示；
                # 真正的暂停状态已经由 LangGraph checkpointer 写入 checkpoint。
                _pending_interrupts[thread_id] = interrupt
                yield _json_event(interrupt)
                return

            text = _extract_text_from_chunk(chunk)
            if text:
                # 一旦正常产出 assistant 文本，本次执行已经离开 interrupt 暂停点；
                # 清理内存提示，后续同 thread_id 的普通追问应作为 messages update 进入。
                _pending_interrupts.pop(thread_id, None)
                yield {"event": "message", "data": text}
            elif chunk is not None:
                # 兜底透传未知 chunk，避免吞掉 DeepAgents 未来版本的可展示输出。
                yield {"event": "message", "data": json.dumps(chunk, ensure_ascii=False, default=str)}


@router.get("/traces/{thread_id}")
async def list_traces(
    thread_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    event_type: str | None = None,
    request_id: str | None = None,
):
    """按 thread_id 查询 LLM trace 事件。

    这是排障接口，只读取 llm_trace_store 中已经落库的 input/output/error；
    event_type/request_id 过滤用于缩小同一会话中的单次 provider 调用范围。
    """
    try:
        events = list_trace_events_by_thread(
            thread_id=thread_id,
            limit=limit,
            event_type=event_type,
            request_id=request_id,
        )
    except Exception as exc:
        logger.exception("查询 LLM trace 失败: %s", exc)
        raise HTTPException(status_code=500, detail="查询 LLM trace 失败") from exc
    return {"thread_id": thread_id, "events": events}


@router.post("/recommend")
async def recommend_stream(req: RecommendRequest):
    """统一推荐流式接口。

    同一个端点承担“普通新输入”和“维度确认后的继续执行”：只有 checkpoint
    真实停在 pending interrupt 上时，才把本次 user_input 作为 `Command(resume=...)`；
    否则同一 thread_id 的后续追问只提交本轮 messages update。旧 messages、工具结果
    和 DeepAgents context management 状态都从 checkpointer 中按 thread_id 恢复并合并，
    API 层不维护完整对话历史。
    """
    await _acquire_semaphore()

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        try:
            agent = get_agent()
            input_data = await _build_agent_input(req, agent)
            async for event in _stream_agent(input_data, req.thread_id):
                yield event
        except Exception as exc:
            _pending_interrupts.pop(req.thread_id, None)
            logger.exception("推荐执行失败: %s", exc)
            yield _error_event("EXECUTION_ERROR", str(exc))
        finally:
            get_semaphore().release()

    return EventSourceResponse(event_generator())
