"""
LangGraph 图构建
支持：
- Checkpointer（会话恢复、时间旅行调试）
- Streaming v2（节点内通过 get_stream_writer() 输出进度）
"""

import logging
from typing import Optional

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .nodes import (
    aggregate_themes,
    analyze_templates,
    classify_and_iterate,
    complete_indicators,
    extract_phrases,
    format_output,
    judge_themes,
    retrieve_templates,
)
from .state import AgentState

logger = logging.getLogger(__name__)

# ── 全局 Checkpointer ──
_checkpointer: Optional[InMemorySaver] = None


def get_checkpointer() -> InMemorySaver:
    """获取 Checkpointer（单例，进程内共享）"""
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = InMemorySaver()
    return _checkpointer


def build_agent_graph(
    checkpointer: Optional[InMemorySaver] = None,
) -> StateGraph:
    """
    构建主题模板推荐 Agent 的 LangGraph

    Args:
        checkpointer: 状态持久化器，默认使用 InMemorySaver

    流程：
    Stage 0: extract_phrases → classify_and_iterate
    Stage 1: aggregate_themes → complete_indicators → judge_themes
    Stage 2: retrieve_templates → analyze_templates
    Finish:  format_output
    """
    workflow = StateGraph(AgentState)

    # ── 添加节点 ──
    # Stage 0
    workflow.add_node("extract_phrases", extract_phrases)
    workflow.add_node("classify_and_iterate", classify_and_iterate)

    # Stage 1
    workflow.add_node("aggregate_themes", aggregate_themes)
    workflow.add_node("complete_indicators", complete_indicators)
    workflow.add_node("judge_themes", judge_themes)

    # Stage 2
    workflow.add_node("retrieve_templates", retrieve_templates)
    workflow.add_node("analyze_templates", analyze_templates)

    # Finish
    workflow.add_node("format_output", format_output)

    # ── 添加边 ──
    workflow.add_edge(START, "extract_phrases")
    workflow.add_edge("extract_phrases", "classify_and_iterate")
    workflow.add_edge("classify_and_iterate", "aggregate_themes")
    workflow.add_edge("aggregate_themes", "complete_indicators")
    workflow.add_edge("complete_indicators", "judge_themes")
    workflow.add_edge("judge_themes", "retrieve_templates")
    workflow.add_edge("retrieve_templates", "analyze_templates")
    workflow.add_edge("analyze_templates", "format_output")
    workflow.add_edge("format_output", END)

    # ── 编译（可选 Checkpointer） ──
    cp = checkpointer or get_checkpointer()
    return workflow.compile(checkpointer=cp)


# ── 全局单例 ──
_agent_graph = None


def get_agent() -> StateGraph:
    """获取编译后的 Agent（图的单例，带 Checkpointer）"""
    global _agent_graph
    if _agent_graph is None:
        _agent_graph = build_agent_graph()
        logger.info("LangGraph Agent 已编译（已启用 Checkpointer）")
    return _agent_graph


def reset_agent() -> None:
    """重置 Agent（用于重新加载配置等场景）"""
    global _agent_graph
    _agent_graph = None
