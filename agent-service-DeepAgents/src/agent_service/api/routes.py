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
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException
from langgraph.types import Command
from sse_starlette.sse import EventSourceResponse

from ..config import CONCURRENT_TIMEOUT_SECONDS, MAX_CONCURRENT_REQUESTS
from ..deepagents.agent_factory import get_agent
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


async def _stream_agent(input_data: Any, thread_id: str) -> AsyncIterator[dict[str, str]]:
    """执行一次 DeepAgents 流式调用并转换为 SSE event。

    这里处理三类输出：
    - interrupt chunk：保存 pending 标记，原样返回 DeepAgents raw interrupt 后结束本次 SSE。
    - 普通文本 chunk：原样透传，前端可展示也可忽略。
    - 未知 chunk：JSON 序列化后兜底透传。

    函数不解释用户确认语义；resume 后如何继续由 Skill 决定。
    """
    agent = get_agent()
    config = {"configurable": {"thread_id": thread_id}}

    async for chunk in agent.astream(input_data, config=config, stream_mode="updates"):
        interrupt = _find_interrupt(chunk)
        if interrupt:
            _pending_interrupts[thread_id] = interrupt
            yield _json_event(interrupt)
            return

        text = _extract_text_from_chunk(chunk)
        if text:
            _pending_interrupts.pop(thread_id, None)
            yield {"event": "message", "data": text}
        elif chunk is not None:
            # 兜底透传未知 chunk，避免吞掉 DeepAgents 未来版本的可展示输出。
            yield {"event": "message", "data": json.dumps(chunk, ensure_ascii=False, default=str)}


@router.post("/recommend")
async def recommend_stream(req: RecommendRequest):
    """统一推荐流式接口。

    同一个端点承担“首次输入”和“维度确认后的继续执行”：无 pending interrupt
    时构造首轮 messages；命中 pending 时把本次 user_input 作为自然语言回复交给
    `Command(resume=...)`。这样前端只需要维护同一个 thread_id 和同一个接口。
    """
    await _acquire_semaphore()

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        try:
            if req.thread_id in _pending_interrupts:
                input_data = Command(resume=build_resume_payload_from_text(req.user_input))
            else:
                # DeepAgents/LangGraph 入口需要 graph state dict：{"messages": list[MessageLike]}。
                # build_recommend_messages() 只产出消息列表；不要把它改回 dict，
                # 否则这里会形成 {"messages": {"messages": [...]}} 的双层嵌套。
                input_data = {"messages": build_recommend_messages(req)}
            async for event in _stream_agent(input_data, req.thread_id):
                yield event
        except Exception as exc:
            _pending_interrupts.pop(req.thread_id, None)
            logger.exception("推荐执行失败: %s", exc)
            yield _error_event("EXECUTION_ERROR", str(exc))
        finally:
            get_semaphore().release()

    return EventSourceResponse(event_generator())
