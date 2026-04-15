
import logging
from typing import Optional

from langgraph.graph import END, START, StateGraph

from ..utils.ttl_memory_saver import TTLMemorySaver

from .nodes import (
    aggregate_themes,
    analyze_templates,
    classify_and_iterate,
    complete_indicators,
    extract_phrases,
    format_output,
    generate_summary,
    judge_themes,
    merge_themes,
    navigate_hierarchy,
    retrieve_templates,
    wait_for_confirmation,
)
from .state import AgentState

logger = logging.getLogger(__name__)

_checkpointer: Optional[TTLMemorySaver] = None


def get_checkpointer() -> TTLMemorySaver:
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = TTLMemorySaver(ttl_seconds=86400)
    return _checkpointer


def build_agent_graph(
    checkpointer: Optional[TTLMemorySaver] = None,
) -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("extract_phrases", extract_phrases)
    workflow.add_node("classify_and_iterate", classify_and_iterate)
    workflow.add_node("wait_for_confirmation", wait_for_confirmation)

    workflow.add_node("aggregate_themes", aggregate_themes)
    workflow.add_node("navigate_hierarchy", navigate_hierarchy)
    workflow.add_node("merge_themes", merge_themes)
    workflow.add_node("complete_indicators", complete_indicators)
    workflow.add_node("judge_themes", judge_themes)

    workflow.add_node("retrieve_templates", retrieve_templates)
    workflow.add_node("analyze_templates", analyze_templates)

    workflow.add_node("format_output", format_output)
    workflow.add_node("generate_summary", generate_summary)

    workflow.add_edge(START, "extract_phrases")
    workflow.add_edge("extract_phrases", "classify_and_iterate")
    workflow.add_edge("classify_and_iterate", "wait_for_confirmation")
    workflow.add_edge("wait_for_confirmation", "aggregate_themes")
    workflow.add_edge("aggregate_themes", "navigate_hierarchy")
    workflow.add_edge("navigate_hierarchy", "merge_themes")
    workflow.add_edge("merge_themes", "complete_indicators")
    workflow.add_edge("complete_indicators", "judge_themes")
    workflow.add_edge("judge_themes", "retrieve_templates")
    workflow.add_edge("retrieve_templates", "analyze_templates")
    workflow.add_edge("analyze_templates", "format_output")
    workflow.add_edge("format_output", "generate_summary")
    workflow.add_edge("generate_summary", END)

    cp = checkpointer or get_checkpointer()
    return workflow.compile(checkpointer=cp)


_agent_graph = None


def get_agent() -> StateGraph:
    global _agent_graph
    if _agent_graph is None:
        _agent_graph = build_agent_graph()
        logger.info("LangGraph Agent 已编译（已启用 Checkpointer）")
    return _agent_graph


def reset_agent() -> None:
    global _agent_graph
    _agent_graph = None
