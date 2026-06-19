"""DeepAgents agent 工厂。

本文件是 DeepAgents 运行时的唯一创建入口，负责把四类运行时依赖组装成
一个可复用 agent：
1. LLM 模型：由 config 中的 SiliconFlow/OpenAI-compatible 配置创建。
2. 工具白名单：由 tool_registry.py 暴露给 Skill 调用。
3. 文件后端：FilesystemBackend 让 DeepAgents 从工程根目录加载 `skills/`。
4. 中断恢复：MemorySaver checkpointer + interrupt_on 支撑维度确认和同一
   `/recommend` 的继续执行。

这里不做业务编排，也不手写显式业务图；推荐流程由 Skill 文档和
DeepAgents runtime 驱动。
"""

from functools import lru_cache

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver

from .. import config
from .router import select_recommendation_skill
from .tool_registry import build_tool_registry

# 这是对所有服务化 Skill 生效的系统级运行约束。
# 具体业务步骤、工具调用顺序和展示内容仍写在 skills/theme-template-recommendation/SKILL.md 中。
SYSTEM_PROMPT = """你是魔数师主题和模板推荐服务。
必须按已加载 Skill 执行，不要调用未注册工具。
需要确认分析维度时，必须调用 AskUserQuestion_tools。
最终必须直接输出面向用户的 Markdown 文本，不要把最终结果包装成 JSON。
"""

# checkpointer 必须是进程级共享对象：
# /recommend 触发 AskUserQuestion_tools 后，DeepAgents 会把暂停状态写入这里；
# /recommend pending 分支再用同一个 thread_id 从这里找回暂停点继续执行。
checkpointer = MemorySaver()



def _build_model() -> ChatOpenAI:
    """按配置创建 OpenAI-compatible Chat 模型客户端。"""
    return ChatOpenAI(
        model=config.LLM_MODEL,
        api_key=config.SILICONFLOW_LLM_API_KEY,
        base_url=config.SILICONFLOW_BASE_URL,
        temperature=config.LLM_TEMPERATURE,
        max_tokens=config.LLM_MAX_TOKENS,
    )


@lru_cache(maxsize=1)
def get_agent():
    """创建并缓存 DeepAgents agent。

    缓存原因：agent 创建包含 Skill 加载、工具绑定和图编译，服务进程内复用即可。
    thread 级状态不放在 agent 对象里，而是由调用时传入的 thread_id + checkpointer 管理。
    """
    # backend root 指向 agent-service-DeepAgents 根目录。
    # skills 参数相对该 root 解析，运行时由 FilesystemBackend 加载 Skill，
    # 应用层不自行读取或解析 Skill 文件。
    backend = FilesystemBackend(root_dir=str(config.ROOT_DIR))

    return create_deep_agent(
        model=_build_model(),
        tools=build_tool_registry(),
        backend=backend,
        skills=[select_recommendation_skill()],
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
        # DeepAgents 在该工具调用点暂停，并把工具入参作为确认页面 interrupt payload 交给路由层透传。
        # 后续同 thread_id 的 Command(resume=...) 会从上面的 checkpointer 恢复执行。
        interrupt_on={"AskUserQuestion_tools": True},
    )
