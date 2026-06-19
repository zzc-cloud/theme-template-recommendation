"""API 请求到 DeepAgents 输入的适配层。

API 层使用 Pydantic DTO；DeepAgents 使用 messages 与 `Command(resume=...)`。
本文件只负责这两种形态之间的最小转换，不做业务判断、不读取 Skill，也不解析
最终结果。这样首次请求和确认后的自然语言回复都能保持同一套简单协议。
"""

from typing import Any

from langchain_core.messages import HumanMessage

from ..api.schemas import RecommendRequest


def build_recommend_messages(req: RecommendRequest) -> list[HumanMessage]:
    """构造 /recommend 首次运行输入。

    DeepAgents 的 SkillsMiddleware 只负责把可用 Skill 的 metadata 注入 system prompt：
    它告诉模型“有哪些 Skill 可用、完整说明在哪里”，但不负责替当前请求选择
    Skill，也不负责承载 HTTP 请求参数。

    本函数只做 API 到 messages 的最小适配：把 /recommend 的结构化请求字段转成
    模型可读的用户消息列表。是否需要读取并执行某个 Skill，由模型根据已加载的 Skill
    metadata 和用户输入自行判断，保持协议层通用、简洁。

    注意：这里必须只返回 list[HumanMessage]，不要再包一层 {"messages": ...}。
    DeepAgents/LangGraph 的 graph state 由 routes.py 统一组装为
    {"messages": build_recommend_messages(req)}。如果两边都包装 messages，会变成
    {"messages": {"messages": [...]}}，底层 reducer 会把内层 dict 当作单条消息解析，
    进而触发 MESSAGE_COERCION_FAILURE；如果这里直接把裸 list 传给 graph，又会触发
    Expected dict。保持“本函数产出消息列表、路由产出 graph state dict”的分工，
    可以避免这些输入契约错乱导致的下游序列化异常（包括曾出现的 str.model_dump）。

    thread_id 同时也会被 routes.py 放入 LangGraph config，供 checkpointer 关联本次运行。
    """
    return [
        HumanMessage(
            content=(
                f"thread_id: {req.thread_id}\n"
                f"用户输入: {req.user_input}"
            )
        )
    ]



def build_resume_payload_from_text(user_text: str) -> dict[str, Any]:
    """构造统一 /recommend pending 分支的 HITL resume 输入。

    API 层不解析用户是否确认、修改或拒绝，只把用户原始自然语言回复交给
    DeepAgents；具体语义由 Skill 恢复后判断。
    """
    return {
        "decisions": [
            {
                "type": "respond",
                "message": user_text,
            }
        ]
    }

