"""API 边界模型。

这些 Pydantic 模型定义 HTTP/SSE 边界可接受和可输出的结构。业务过程中的
主题、指标、模板明细由 Skill 和工具组织；API 层只校验请求字段、错误和
健康检查响应。最终推荐结果由 Skill 直接输出 Markdown 文本，路由层不再
强制转换为展示 JSON。
"""

from typing import Literal

from pydantic import BaseModel, Field


class RecommendRequest(BaseModel):
    """`/api/v1/recommend` 的输入。

    `thread_id` 是 DeepAgents checkpointer 恢复暂停点的 key；它不是业务
    session_store。首次请求和维度确认后的自然语言回复都使用这个模型。
    """

    user_input: str = Field(..., min_length=1, max_length=500, description="用户自然语言输入，可为首次问题、确认回复或补充说明")
    thread_id: str = Field(..., description="请求唯一标识，用于 checkpointer 恢复")


class ErrorPayload(BaseModel):
    """推荐失败后的稳定错误契约。"""

    status: Literal["error"] = "error"
    code: str = "UNKNOWN"
    message: str = "未知错误"


class HealthResponse(BaseModel):
    """`GET /health` 返回的依赖与并发状态。"""

    status: str
    version: str = "1.0.0"
    services: dict[str, bool] = Field(default_factory=dict)
    concurrency: dict[str, int] = Field(default_factory=dict)
