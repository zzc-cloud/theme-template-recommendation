"""DeepAgents 工具白名单注册。

DeepAgents 会把这里返回的 Python 函数暴露给 Skill 调用。本文件是服务的工具
安全边界：只有经过白名单登记的能力才能被 Skill 使用，避免模型调用计划外工具
或绕过当前推荐流程。

工具分为两类：
- 数据工具：访问 Chroma/Neo4j，提供指标搜索、主题导航和模板覆盖率计算。
- 确认工具：`AskUserQuestion_tools`，由 DeepAgents `interrupt_on` 捕获为人机确认暂停点。
"""

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from ..tools.confirmation_tools import AskUserQuestion_tools
from ..tools.template_tools import get_theme_templates_with_coverage
from ..tools.theme_tools import (
    aggregate_themes_from_indicators,
    batch_get_indicator_themes,
    get_sector_themes,
    get_sectors_from_root,
    get_theme_analysis_indicators,
    get_theme_filter_indicators,
)
from ..tools.vector_search import search_indicators_by_vector

logger = logging.getLogger(__name__)

# 测试会校验该列表，确保对 DeepAgents 暴露的工具没有超过计划白名单。
# 顺序也作为人工审查和契约测试的稳定参照。
TOOL_NAMES = [
    "search_indicators_by_vector",
    "batch_get_indicator_themes",
    "aggregate_themes_from_indicators",
    "get_sectors_from_root",
    "get_sector_themes",
    "get_theme_filter_indicators",
    "get_theme_analysis_indicators",
    "get_theme_templates_with_coverage",
    "AskUserQuestion_tools",
]


def _safe_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """把工具异常包装成稳定返回值。

    工具失败时不让底层异常直接中断 agent 流式执行，而是返回统一错误结构，
    由 Skill 决定如何写入过程输出或最终 Markdown 文本。
    """

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            logger.exception("工具调用失败: %s", fn.__name__)
            # 返回稳定错误结构，让 Skill 能把失败原因写进过程输出或最终 Markdown 文本。
            return {"success": False, "error": str(exc), "tool": fn.__name__}

    return wrapper


def build_tool_registry() -> list[Callable[..., Any]]:
    """构建 DeepAgents tools 参数。

    顺序与 TOOL_NAMES 保持一致，便于测试和人工核对。
    """
    tools = [
        search_indicators_by_vector,
        batch_get_indicator_themes,
        aggregate_themes_from_indicators,
        get_sectors_from_root,
        get_sector_themes,
        get_theme_filter_indicators,
        get_theme_analysis_indicators,
        get_theme_templates_with_coverage,
        AskUserQuestion_tools,
    ]
    return [_safe_tool(tool) for tool in tools]
