"""Skill 路由。

当前服务只面向“主题和模板推荐”这一条业务主路径，因此路由是一个很薄的显式选择层。
保留该文件的目的，是把“运行时加载哪个 Skill”的决策集中在一个位置，避免散落在
agent_factory.py 或 routes.py 中。
"""

# FilesystemBackend 的 root 是 agent-service-DeepAgents 根目录。
# 当前加载整个 skills 根路径，让 DeepAgents 看到主 Skill 及其可调用的子 Skill。
RECOMMENDATION_SKILL = "skills"


def select_recommendation_skill() -> str:
    """返回本次 DeepAgents 运行需要加载的 Skill 根路径。"""
    return RECOMMENDATION_SKILL
