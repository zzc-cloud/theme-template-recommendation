"""可用 Agent 注册表。

第一版只内置一个 DeepAgents 主题模板推荐 Agent。前端通过 `/api/agents` 获取这里的
结果，因此后续扩展更多 Agent 时应优先改本文件，而不是在前端硬编码列表。
"""

from .config import get_settings
from .schemas import AgentInfo

AGENT_ID = "theme-template-recommendation-deepagents"


def list_agents() -> list[AgentInfo]:
    """返回当前控制台支持的 Agent 列表。"""
    settings = get_settings()
    return [
        AgentInfo(
            id=AGENT_ID,
            name="魔数师主题模板推荐 Agent",
            description="调用 agent-service-DeepAgents 的主题模板推荐能力",
            agent_type="DeepAgents",
            upstream_url=settings.deepagents_recommend_url,
        )
    ]


def get_agent(agent_id: str) -> AgentInfo | None:
    """按 ID 查找 Agent；未知 ID 由 API 层转换为 404。"""
    return next((agent for agent in list_agents() if agent.id == agent_id), None)
