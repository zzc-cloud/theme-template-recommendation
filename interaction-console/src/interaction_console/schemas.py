"""前后端共享的数据模型。

这些模型定义 interaction-console 对外暴露的稳定事件 envelope。后端负责生成
ConsoleEvent，前端时间线只依赖这些字段进行展示；上游 DeepAgents 的原始结构则
保存在 raw 中，作为调试和兼容兜底。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

# 前端 EVENT_TYPE_CONFIG 与 renderEventBody() 依赖这组事件类型；新增类型时需要同步更新前端和 README。
EventType = Literal[
    "skill_loaded",
    "middleware",
    "user_message",
    "assistant_message",
    "tool_use",
    "tool_result",
    "interrupt",
    "error",
    "raw",
    "done",
]


class AgentInfo(BaseModel):
    """浏览器 Agent 列表使用的展示与调用信息。"""

    id: str
    name: str
    description: str
    agent_type: str
    upstream_url: str


class ChatStreamRequest(BaseModel):
    """前端发起一次流式对话时提交的请求体。"""

    agent_id: str
    thread_id: str
    user_input: str


class ConsoleEvent(BaseModel):
    """标准化后的单个时间线事件。

    payload 是前端稳定消费的展示字段；raw 保留上游原始 chunk，便于排查解析规则，
    不应被当作稳定业务协议使用。
    """

    type: EventType
    thread_id: str
    agent_id: str
    seq: int
    timestamp: str
    payload: dict[str, Any] = Field(default_factory=dict)
    raw: Any | None = None
