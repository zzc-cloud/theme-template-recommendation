"""
API 路由
"""

import json
import logging
import time
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from ..graph import graph as agent_graph
from ..graph.graph import get_checkpointer
from .schemas import (
    FilterIndicatorResponse,
    IndicatorMatchResponse,
    RecommendRequest,
    RecommendResponse,
    RecommendedThemeResponse,
    RecommendedTemplateResponse,
    SelectedIndicatorResponse,
    StreamEvent,
    TemplateUsabilityResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["recommend"])


# ═════════════════════════════════════════════════════════════════
# 辅助函数
# ═════════════════════════════════════════════════════════════════

def _build_response(
    final_output: dict,
    execution_time_ms: float,
    request_id: str,
) -> RecommendResponse:
    """从 Agent 输出构建 API 响应"""
    # 筛选指标
    filter_indicators = [
        FilterIndicatorResponse(
            indicator_id=fi.get("indicator_id", ""),
            value=fi.get("value", ""),
            alias=fi.get("alias", ""),
            type=fi.get("type", ""),
        )
        for fi in final_output.get("filter_indicators", [])
    ]

    # 分析维度
    analysis_dimensions = [
        {
            "search_term": d["search_term"],
            "converged": d["converged"],
            "indicators": [
                IndicatorMatchResponse(
                    id=i["id"],
                    alias=i["alias"],
                    description=i.get("description", ""),
                    theme_id=i.get("theme_id", ""),
                    theme_alias=i.get("theme_alias", ""),
                    similarity_score=i.get("similarity_score", 0.0),
                )
                for i in d.get("indicators", [])
            ],
        }
        for d in final_output.get("analysis_dimensions", [])
    ]

    # 推荐主题
    recommended_themes = []
    for t in final_output.get("recommended_themes", []):
        recommended_themes.append(
            RecommendedThemeResponse(
                theme_id=t["theme_id"],
                theme_alias=t["theme_alias"],
                theme_level=t.get("theme_level", 0),
                is_supported=t.get("is_supported", False),
                support_reason=t.get("support_reason", ""),
                selected_filter_indicators=[
                    SelectedIndicatorResponse(
                        indicator_id=si.get("indicator_id", ""),
                        alias=si.get("alias", ""),
                        description=si.get("description", ""),
                        type=si.get("type", ""),
                        reason=si.get("reason", ""),
                    )
                    for si in t.get("selected_filter_indicators", [])
                ],
                selected_analysis_indicators=[
                    SelectedIndicatorResponse(
                        indicator_id=si.get("indicator_id", ""),
                        alias=si.get("alias", ""),
                        description=si.get("description", ""),
                        type=si.get("type", ""),
                        reason=si.get("reason", ""),
                    )
                    for si in t.get("selected_analysis_indicators", [])
                ],
            )
        )

    # 推荐模板
    recommended_templates = []
    for t in final_output.get("recommended_templates", []):
        usability_data = t.get("usability", {})
        recommended_templates.append(
            RecommendedTemplateResponse(
                template_id=t["template_id"],
                template_alias=t["template_alias"],
                template_description=t.get("template_description", ""),
                theme_alias=t.get("theme_alias", ""),
                usage_count=t.get("usage_count", 0),
                coverage_ratio=t.get("coverage_ratio", 0.0),
                has_qualified_templates=t.get("has_qualified_templates", False),
                fallback_reason=t.get("fallback_reason", ""),
                usability=TemplateUsabilityResponse(
                    template_id=usability_data.get("template_id", t["template_id"]),
                    overall_usability=usability_data.get("overall_usability", ""),
                    usability_summary=usability_data.get("usability_summary", ""),
                    missing_indicator_analysis=[
                        {
                            "indicator_alias": m.get("indicator_alias", ""),
                            "importance": m.get("importance", ""),
                            "impact": m.get("impact", ""),
                            "supplement_suggestion": m.get("supplement_suggestion", ""),
                        }
                        for m in usability_data.get("missing_indicator_analysis", [])
                    ],
                ),
            )
        )

    return RecommendResponse(
        request_id=request_id,
        normalized_question=final_output.get("normalized_question", ""),
        filter_indicators=filter_indicators,
        analysis_dimensions=analysis_dimensions,
        is_low_confidence=final_output.get("is_low_confidence", False),
        recommended_themes=recommended_themes,
        recommended_templates=recommended_templates,
        execution_time_ms=execution_time_ms,
        iteration_rounds=final_output.get("iteration_info", {}).get("rounds", 0),
    )


# ═════════════════════════════════════════════════════════════════
# 路由
# ═════════════════════════════════════════════════════════════════

@router.post("/recommend", response_model=RecommendResponse)
async def recommend(req: RecommendRequest) -> RecommendResponse:
    """
    同步推荐接口

    接收用户问题，返回主题和模板推荐结果
    """
    request_id = str(uuid.uuid4())
    start_time = time.time()

    logger.info(f"[{request_id}] 开始处理请求: {req.question}")

    try:
        agent = agent_graph.get_agent()

        # 初始化状态
        initial_state = {
            "user_question": req.question,
            "top_k_themes": req.top_k_themes,
            "top_k_templates": req.top_k_templates,
            # 以下为默认值
            "extracted_phrases": [],
            "filter_indicators": [],
            "analysis_dimensions": [],
            "normalized_question": "",
            "search_results": {},
            "iteration_round": 0,
            "iteration_log": [],
            "is_low_confidence": False,
            "candidate_themes": [],
            "recommended_themes": [],
            "recommended_templates": [],
            "final_output": {},
            "execution_time_ms": 0.0,
            "error": None,
        }

        result = await agent.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": request_id}},
        )

        execution_time_ms = (time.time() - start_time) * 1000
        logger.info(
            f"[{request_id}] 处理完成，耗时: {execution_time_ms:.0f}ms"
        )

        if result.get("error"):
            logger.warning(f"[{request_id}] Agent 执行出错: {result['error']}")

        return _build_response(
            result.get("final_output", {}),
            execution_time_ms,
            request_id,
        )

    except Exception as e:
        execution_time_ms = (time.time() - start_time) * 1000
        logger.exception(f"[{request_id}] 请求处理失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recommend/stream")
async def recommend_stream(req: RecommendRequest):
    """
    流式推荐接口（SSE）

    返回 Server-Sent Events 流，实时推送推理过程
    """
    request_id = str(uuid.uuid4())
    start_time = time.time()

    logger.info(f"[{request_id}] 开始流式处理请求: {req.question}")

    async def event_generator() -> AsyncIterator[dict]:
        """SSE 事件生成器"""
        agent = agent_graph.get_agent()

        initial_state = {
            "user_question": req.question,
            "top_k_themes": req.top_k_themes,
            "top_k_templates": req.top_k_templates,
            "extracted_phrases": [],
            "filter_indicators": [],
            "analysis_dimensions": [],
            "normalized_question": "",
            "search_results": {},
            "iteration_round": 0,
            "iteration_log": [],
            "is_low_confidence": False,
            "candidate_themes": [],
            "recommended_themes": [],
            "recommended_templates": [],
            "final_output": {},
            "execution_time_ms": 0.0,
            "error": None,
        }

        node_order = [
            "extract_phrases",
            "classify_and_iterate",
            "aggregate_themes",
            "complete_indicators",
            "judge_themes",
            "retrieve_templates",
            "analyze_templates",
            "format_output",
        ]

        try:
            final_result = None
            # 使用 LangGraph v2 streaming 格式
            async for chunk in agent.astream(
                initial_state,
                config={"configurable": {"thread_id": request_id}},
                stream_mode=["updates", "custom"],
                version="v2",
            ):
                # v2 格式: {"type": "updates"|"custom", "data": ..., "ns": ...}
                chunk_type = chunk.get("type", "")

                if chunk_type == "updates":
                    # 节点状态更新事件
                    updates = chunk.get("data", {})
                    for node_name, node_state in updates.items():
                        if node_name in node_order:
                            yield {
                                "event": "message",
                                "data": json.dumps({
                                    "event_type": "stage_complete",
                                    "stage": node_name,
                                    "timestamp": time.time(),
                                }, ensure_ascii=False),
                            }
                            final_result = node_state if node_state else final_result

                elif chunk_type == "custom":
                    # 自定义事件（来自节点内的 get_stream_writer()）
                    yield {
                        "event": "message",
                        "data": json.dumps({
                            "event_type": "custom",
                            "data": chunk.get("data", {}),
                            "timestamp": time.time(),
                        }, ensure_ascii=False),
                    }

            execution_time_ms = (time.time() - start_time) * 1000

            # v2 streaming 只返回增量更新，从 Checkpointer 获取完整最终状态
            if final_result is None:
                try:
                    checkpointer = get_checkpointer()
                    checkpoint = checkpointer.get({"configurable": {"thread_id": request_id}})
                    if checkpoint and checkpoint.get("channel_values"):
                        final_result = checkpoint["channel_values"]
                except Exception as e:
                    logger.warning(f"无法从 Checkpointer 获取最终状态: {e}")

            response = _build_response(
                final_result.get("final_output", {}) if final_result else {},
                execution_time_ms,
                request_id,
            )

            yield {
                "event": "message",
                "data": json.dumps({
                    "event_type": "final",
                    "data": response.model_dump(mode="json"),
                    "timestamp": time.time(),
                }, ensure_ascii=False),
            }

        except Exception as e:
            logger.exception(f"[{request_id}] 流式处理失败: {e}")
            yield {
                "event": "error",
                "data": json.dumps({
                    "event_type": "error",
                    "message": str(e),
                    "timestamp": time.time(),
                }, ensure_ascii=False),
            }

    return EventSourceResponse(event_generator())
