"""FastAPI 网关入口，负责连接浏览器控制台与上游 DeepAgents 服务。

本模块只做交互编排：提供页面/API、代理上游 SSE、调用事件标准化器，
再把统一的 ConsoleEvent 事件流返回给前端。主题模板推荐业务逻辑仍由
上游 agent-service-DeepAgents 负责。
"""

import json

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .agent_registry import get_agent, list_agents
from .config import get_settings
from .event_normalizer import EventNormalizer
from .schemas import ChatStreamRequest
from .session_store import session_store
from .static import index, mount_static
from .upstream_client import stream_recommend

app = FastAPI(title="Interaction Console")
mount_static(app)
app.get("/")(index)


@app.get("/api/agents")
async def api_agents():
    """返回前端可选择的 Agent 列表。"""
    return [agent.model_dump() for agent in list_agents()]


@app.get("/api/sessions/{thread_id}/events")
async def api_session_events(thread_id: str):
    """返回当前进程内缓存的标准化事件，主要用于调试和刷新恢复。"""
    return session_store.list_events(thread_id)


@app.post("/api/chat/stream")
async def api_chat_stream(request: ChatStreamRequest):
    """代理上游推荐接口，并把上游 SSE 标准化后继续以 SSE 返回前端。

    同一个入口同时服务首次提问和 interrupt 后的继续执行；两种场景都依赖
    thread_id 让上游 DeepAgents 找回对应上下文。
    """
    agent = get_agent(request.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Unknown agent_id: {request.agent_id}")

    # 每次前端发起一条流式请求时创建新的 normalizer，seq 从当前线程已缓存事件后继续递增。
    normalizer = EventNormalizer(
        thread_id=request.thread_id,
        agent_id=request.agent_id,
        initial_seq=session_store.max_seq(request.thread_id),
    )

    async def event_stream():
        try:
            user_event = normalizer.user_message(request.user_input).model_dump()
            session_store.append(request.thread_id, user_event)
            yield _sse(user_event)

            async for line in stream_recommend(agent.upstream_url, request.thread_id, request.user_input):
                for event in normalizer.normalize_line(line):
                    event_dict = event.model_dump()
                    session_store.append(request.thread_id, event_dict)
                    yield _sse(event_dict)
            # 上游连接自然结束后补一个 done 事件，方便前端明确标记本轮流已关闭。
            done = normalizer.normalize({"status": "done"})[0].model_dump()
            session_store.append(request.thread_id, done)
            yield _sse(done)
        except httpx.HTTPStatusError as exc:
            # 上游返回 4xx/5xx 时，不让浏览器只看到连接断开，而是转成可展示的 error 事件。
            event = normalizer.normalize({
                "status": "error",
                "message": f"上游服务返回 HTTP {exc.response.status_code}",
                "body": exc.response.text,
            })[0].model_dump()
            session_store.append(request.thread_id, event)
            yield _sse(event)
        except Exception as exc:  # noqa: BLE001 - SSE 需要把异常转为 error 事件
            # 兜底异常同样进入事件流，保证前端时间线可观察失败原因。
            event = normalizer.normalize({"status": "error", "message": str(exc)})[0].model_dump()
            session_store.append(request.thread_id, event)
            yield _sse(event)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse(data: dict) -> str:
    """把事件字典编码为标准 SSE frame；保留中文便于浏览器详情中直接阅读。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def main() -> None:
    """本地开发启动入口。"""
    settings = get_settings()
    uvicorn.run("interaction_console.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
