
import asyncio
import json
import logging
import time
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from langgraph.types import Command
from sse_starlette.sse import EventSourceResponse

from ..graph import graph as agent_graph
from ..graph.graph import get_checkpointer
from ..config import MAX_CONCURRENT_REQUESTS, CONCURRENT_TIMEOUT_SECONDS
from .schemas import (
    ConversationContext,
    FilterIndicatorResponse,
    IndicatorMatchResponse,
    RecommendRequest,
    RecommendResponse,
    RecommendedThemeResponse,
    RecommendedTemplateResponse,
    ResumeRequest,
    SelectedIndicatorResponse,
    SyncErrorInfo,
    SyncInterruptInfo,
    SyncResponse,
    TemplateUsabilityResponse,
    TemplateCoverageDetail,
    TemplateSearchDetailResponse,
    TemplateSearchDetailTemplateItem,
    CandidateThemeResponse,
    SectorNavigationResponse,
    NavigationThemeResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["recommend"])


_semaphore: asyncio.Semaphore | None = None


def init_semaphore():
    global _semaphore
    _semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    logger.info(f"[Semaphore] 并发上限设置为: {MAX_CONCURRENT_REQUESTS}")


def get_semaphore() -> asyncio.Semaphore:
    if _semaphore is None:
        raise RuntimeError("Semaphore 未初始化，请检查 lifespan 配置")
    return _semaphore


def get_current_concurrency() -> int:
    if _semaphore is None:
        return 0
    return MAX_CONCURRENT_REQUESTS - _semaphore._value


STAGE_COMPLETE_TEXT = {
    "extract_phrases": None,
    "classify_and_iterate": None,
    "wait_for_confirmation": None,
    "aggregate_themes": None,
    "navigate_hierarchy": "│ ✅ **[1.2]** 层级导航探查完成",
    "merge_themes": "│ ✅ **[1.3]** 双路径主题合并完成",
    "complete_indicators": "│ ✅ **[1.4]** 全量指标补全完成",
    "judge_themes": None,
    "retrieve_templates": "│ ✅ **[2.1]** 模板检索完成",
    "analyze_templates": None,
    "format_output": "\n✅ **所有阶段执行完毕，正在生成推荐结果...**",
}


def translate_event_to_markdown(data: dict) -> str | None:
    stage = data.get("stage", "")
    step = data.get("step", "")
    status = data.get("status", "")

    if stage == "extract_phrases":
        if status == "in_progress":
            return "┌─────────────────────────────────────────\n│ **[0.1] 词组提取** 开始执行...\n└─────────────────────────────────────────"
        if status == "done":
            count = data.get("phrases_count", 0)
            return f"│ ✅ 词组提取完成，共提取 **{count}** 个词组\n└─────────────────────────────────────────"

    if stage == "classify_and_iterate":
        if step == "classifying":
            return "\n┌─────────────────────────────────────────\n│ **[0.2] 词组分类** 正在执行..."
        if step == "searching":
            round_num = data.get("round", 1)
            concepts = data.get("concepts", [])
            concepts_str = "、".join(f"`{c}`" for c in concepts)
            return f"│ **[0.3] 第 {round_num} 轮迭代精炼**\n│   🔍 搜索词：{concepts_str}"
        if step == "evaluating":
            round_num = data.get("round", 1)
            return f"│   🤖 LLM 精炼第 {round_num} 轮搜索词..."
        if step == "converged":
            round_num = data.get("round", 1)
            newly_converged = data.get("newly_converged", [])
            pending_count = data.get("pending_count", 0)
            if newly_converged:
                conv_str = "、".join(f"`{c}`" for c in newly_converged)
                return f"│   ✅ 本轮收敛：{conv_str}，剩余待精炼：{pending_count} 个"
            else:
                return f"│   🔄 本轮暂无收敛，继续精炼..."
        if step == "completed":
            iterations = data.get("iterations", 1)
            converged_count = data.get("converged_count", 0)
            is_low_confidence = data.get("low_confidence", False)
            if is_low_confidence:
                return f"│ ⚠️ 迭代精炼结束（共 **{iterations}** 轮），部分维度未能收敛，进入低置信度流程\n└─────────────────────────────────────────"
            else:
                return f"│ ✅ 迭代精炼完成，共 **{iterations}** 轮，**{converged_count}** 个维度已收敛\n└─────────────────────────────────────────"

    if stage == "wait_for_confirmation":
        if step == "waiting_confirmation":
            return "\n┌─────────────────────────────────────────\n│ **[0.4] 等待用户确认分析维度** ⏸\n└─────────────────────────────────────────"
        if step == "low_confidence":
            return "\n┌─────────────────────────────────────────\n│ ⚠️ **低置信度** 无法精确匹配，等待用户修改描述\n└─────────────────────────────────────────"

    if stage == "navigate_hierarchy":
        if step == "fetching_sectors":
            return "\n┌─────────────────────────────────────────\n│ **[1.2] 层级导航** 正在获取板块列表..."
        if step == "sectors_loaded":
            count = data.get("sector_count", 0)
            return f"│   📂 已加载 {count} 个板块，开始筛选相关板块..."
        if step == "filtering_sectors":
            return "│   🤖 LLM 正在判断相关板块..."
        if step == "sectors_filtered":
            count = data.get("selected_sector_count", 0)
            return f"│   ✅ 筛选完成，选中 {count} 个相关板块"
        if step == "fetching_sector_themes":
            sector = data.get("sector_alias", "")
            return f"│   📂 正在加载「{sector}」板块下的所有主题..."
        if step == "sector_filtered":
            sector = data.get("sector_alias", "")
            selected = data.get("selected_count", 0)
            total = data.get("total_themes", 0)
            return f"│   ✅ 「{sector}」筛选完成，从 {total} 个主题中选中 **{selected}** 个"
        if step == "batch_start":
            batch_idx = data.get("batch_idx", 0)
            total_batches = data.get("total_batches", 0)
            sectors = data.get("sectors_in_batch", [])
            return f"│   🔄 批次 {batch_idx}/{total_batches} 开始处理：{', '.join(sectors)}"
        if step == "batch_completed":
            batch_idx = data.get("batch_idx", 0)
            total_batches = data.get("total_batches", 0)
            succeeded = data.get("succeeded_sectors", 0)
            failed = data.get("failed_sectors", 0)
            selected = data.get("selected_in_batch", 0)
            fail_info = f"，失败 {failed} 个" if failed > 0 else ""
            return f"│   ✅ 批次 {batch_idx}/{total_batches} 完成：成功 {succeeded} 个{fail_info}，选中 {selected} 个主题"
        if step == "completed":
            count = data.get("selected_count", 0)
            return f"│   ✅ 层级导航完成，共筛选出 **{count}** 个候选主题\n└─────────────────────────────────────────"

    if stage == "aggregate_themes":
        if step == "aggregating":
            return "\n┌─────────────────────────────────────────\n│ **[1.1] 候选主题聚合** 正在统计匹配指标..."
        if step == "completed":
            count = data.get("theme_count", 0)
            return f"│   ✅ 候选主题聚合完成，共 **{count}** 个候选主题\n└─────────────────────────────────────────"

    if stage == "merge_themes":
        if step == "merging":
            agg = data.get("aggregate_count", 0)
            nav = data.get("navigation_count", 0)
            return f"\n┌─────────────────────────────────────────\n│ **[1.3] 双路径合并** 聚合路径 {agg} 个 + 层级导航 {nav} 个，正在去重合并..."
        if step == "completed":
            count = data.get("merged_count", 0)
            return f"│   ✅ 合并完成，共 **{count}** 个候选主题\n└─────────────────────────────────────────"

    if stage == "judge_themes":
        if step == "judging":
            count = data.get("theme_count", 0)
            return f"\n┌─────────────────────────────────────────\n│ **[1.3] 主题裁决** 正在评估 **{count}** 个候选主题..."
        if step == "batch_progress":
            batch = data.get("batch", 1)
            total = data.get("total_batches", 1)
            processed = data.get("processed", 0)
            return f"│   📦 批次 {batch}/{total} 处理中...（已完成 {processed} 个）"
        if step == "completed":
            return "│ ✅ 主题裁决完成\n└─────────────────────────────────────────"

    if stage == "analyze_templates":
        if step == "analyzing":
            count = data.get("template_count", 0)
            return f"\n┌─────────────────────────────────────────\n│ **[2.2] 模板可用性分析** 共 **{count}** 个模板"
        if step == "batch_progress":
            batch = data.get("batch", 1)
            total = data.get("total_batches", 1)
            processed = data.get("processed", 0)
            return f"│   📦 批次 {batch}/{total} 处理中...（已完成 {processed} 个）"
        if step == "analyzing_template":
            idx = data.get("template_index", "")
            alias = data.get("template_alias", "")
            return f"│   📄 分析模板 {idx}：**{alias}**..."
        if step == "completed":
            return "│ ✅ 模板分析完成\n└─────────────────────────────────────────"

    if stage == "format_output":
        if step == "generating":
            return "\n┌─────────────────────────────────────────\n│ **[3] 生成推荐结果报告**..."
        if step == "completed":
            return "│ ✅ 推荐结果生成完成"

    return None


def _build_response(
    final_output: dict,
    execution_time_ms: float,
    request_id: str,
) -> RecommendResponse:
    filter_indicators = [
        FilterIndicatorResponse(
            indicator_id=fi.get("indicator_id", ""),
            value=fi.get("value", ""),
            alias=fi.get("alias", ""),
            type=fi.get("type", ""),
        )
        for fi in final_output.get("filter_indicators", [])
    ]

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

    candidate_themes_from_aggregate = [
        CandidateThemeResponse(
            theme_id=t.get("theme_id", ""),
            theme_alias=t.get("theme_alias", ""),
            theme_level=t.get("theme_level", 0),
            theme_path=t.get("theme_path", ""),
            frequency=t.get("frequency", 0),
            weighted_frequency=t.get("weighted_frequency", 0.0),
            matched_indicator_ids=t.get("matched_indicator_ids", []),
        )
        for t in final_output.get("candidate_themes_from_aggregate", [])
    ]

    navigation_path_detail = [
        SectorNavigationResponse(
            sector_id=s.get("sector_id", ""),
            sector_alias=s.get("sector_alias", ""),
            sector_path=s.get("sector_path", ""),
            total_themes=s.get("total_themes", 0),
            selected_themes=[
                NavigationThemeResponse(
                    theme_id=t.get("theme_id", ""),
                    theme_alias=t.get("theme_alias", ""),
                    theme_path=t.get("theme_path", ""),
                )
                for t in s.get("selected_themes", [])
            ],
        )
        for s in final_output.get("navigation_path_detail", [])
    ]

    recommended_themes = []
    for t in final_output.get("recommended_themes", []):
        recommended_themes.append(
            RecommendedThemeResponse(
                theme_id=t["theme_id"],
                theme_alias=t["theme_alias"],
                theme_level=t.get("theme_level", 0),
                theme_path=t.get("theme_path", ""),
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

    recommended_templates = []
    for t in final_output.get("recommended_templates", []):
        usability_data = t.get("usability", {})
        recommended_templates.append(
            RecommendedTemplateResponse(
                template_id=t["template_id"],
                template_alias=t["template_alias"],
                template_description=t.get("template_description", ""),
                theme_id=t.get("theme_id", ""),
                theme_alias=t.get("theme_alias", ""),
                usage_count=t.get("usage_count", 0),
                coverage_ratio=t.get("coverage_ratio", 0.0),
                coverage_detail=TemplateCoverageDetail(
                    covered_indicator_aliases=t.get("covered_indicator_aliases", []),
                    missing_indicator_aliases=t.get("missing_indicator_aliases", []),
                    matched_count=t.get("matched_count", 0),
                    total_user_indicators=t.get("total_user_indicators", 0),
                ),
                theme_has_qualified_templates=t.get("theme_has_qualified_templates", False),
                theme_fallback_reason=t.get("theme_fallback_reason", ""),
                usability=TemplateUsabilityResponse(
                    template_id=usability_data.get("template_id", t["template_id"]),
                    is_supported=usability_data.get("is_supported", False),
                    support_reason=usability_data.get("support_reason", ""),
                ),
            )
        )

    template_search_detail = []
    for d in final_output.get("template_search_detail", []):
        template_items = [
            TemplateSearchDetailTemplateItem(
                template_id=t.get("template_id", ""),
                template_alias=t.get("template_alias", ""),
                template_description=t.get("template_description", ""),
                usage_count=t.get("usage_count", 0),
                coverage_ratio=t.get("coverage_ratio", 0.0),
                covered_indicator_aliases=t.get("covered_indicator_aliases", []),
                missing_indicator_aliases=t.get("missing_indicator_aliases", []),
                matched_count=t.get("matched_count", 0),
                total_user_indicators=t.get("total_user_indicators", 0),
                is_supported=t.get("is_supported", False),
                usability_reason=t.get("usability_reason", ""),
            )
            for t in d.get("templates", [])
        ]
        template_search_detail.append(
            TemplateSearchDetailResponse(
                theme_id=d.get("theme_id", ""),
                theme_alias=d.get("theme_alias", ""),
                theme_path=d.get("theme_path", ""),
                is_supported=d.get("is_supported", False),
                matched_indicator_aliases=d.get("matched_indicator_aliases", []),
                has_qualified_templates=d.get("has_qualified_templates", False),
                fallback_reason=d.get("fallback_reason", ""),
                all_template_count=d.get("all_template_count", 0),
                templates=template_items,
            )
        )

    return RecommendResponse(
        request_id=request_id,
        normalized_question=final_output.get("normalized_question", ""),
        filter_indicators=filter_indicators,
        analysis_dimensions=analysis_dimensions,
        is_low_confidence=final_output.get("is_low_confidence", False),
        candidate_themes_from_aggregate=candidate_themes_from_aggregate,
        navigation_path_detail=navigation_path_detail,
        recommended_themes=recommended_themes,
        recommended_templates=recommended_templates,
        template_search_detail=template_search_detail,
        execution_time_ms=execution_time_ms,
        iteration_rounds=final_output.get("iteration_info", {}).get("rounds", 0),
        conversation_round=final_output.get("conversation_round", 1),
        markdown=final_output.get("markdown", ""),
    )


@router.post("/recommend")
async def recommend_stream(req: RecommendRequest):
    request_id = req.thread_id
    start_time = time.time()

    semaphore = get_semaphore()
    current = get_current_concurrency()

    if semaphore.locked() and current >= MAX_CONCURRENT_REQUESTS:
        logger.warning(
            f"[Semaphore] 并发已满 {current}/{MAX_CONCURRENT_REQUESTS}，"
            f"拒绝请求 thread_id={request_id}"
        )
        raise HTTPException(
            status_code=429,
            detail={
                "error": "too_many_requests",
                "message": f"当前并发已达上限 {MAX_CONCURRENT_REQUESTS}，请稍后重试",
                "current_concurrency": current,
                "max_concurrency": MAX_CONCURRENT_REQUESTS,
            },
        )

    try:
        await asyncio.wait_for(
            semaphore.acquire(),
            timeout=CONCURRENT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"[Semaphore] 等待超时 {CONCURRENT_TIMEOUT_SECONDS}s，"
            f"拒绝请求 thread_id={request_id}"
        )
        raise HTTPException(
            status_code=429,
            detail={
                "error": "timeout_waiting",
                "message": f"等待超过 {CONCURRENT_TIMEOUT_SECONDS}s，请稍后重试",
                "current_concurrency": get_current_concurrency(),
                "max_concurrency": MAX_CONCURRENT_REQUESTS,
            },
        )

    logger.info(
        f"[Semaphore] 获取成功，当前并发: "
        f"{get_current_concurrency()}/{MAX_CONCURRENT_REQUESTS} "
        f"thread_id={request_id}"
    )

    logger.info(f"[{request_id}] 开始流式处理请求: {req.question}")

    async def event_generator() -> AsyncIterator[dict]:
        try:
            agent = agent_graph.get_agent()

            initial_history = []
            if req.context:
                initial_history.append({
                    "round": 1,
                    "user_question": req.context.previous_question,
                    "normalized_question": req.context.previous_normalized_question,
                    "filter_indicators": [
                        {"alias": f.alias, "value": f.value, "indicator_id": "", "type": ""}
                        for f in req.context.previous_filter_indicators
                    ],
                    "analysis_dimensions": [
                        {"search_term": d, "converged": True, "indicators": []}
                        for d in req.context.previous_dimensions
                    ],
                })

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
                "low_confidence_message": "",
                "low_confidence_suggestions": [],
                "dimension_guidance": None,
                "pending_confirmation": None,
                "user_confirmation": None,
                "conversation_history": initial_history,
                "candidate_themes": [],
                "navigation_path_themes": [],
                "navigation_path_detail": [],
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
                "navigate_hierarchy",
                "merge_themes",
                "complete_indicators",
                "judge_themes",
                "retrieve_templates",
                "analyze_templates",
                "format_output",
                "generate_summary",
            ]

            final_result = None
            summary_content = None
            async for chunk in agent.astream(
                initial_state,
                config={"configurable": {"thread_id": request_id}},
                stream_mode=["updates", "custom"],
                version="v2",
            ):
                chunk_type = chunk.get("type", "")

                if chunk_type == "updates":
                    updates = chunk.get("data", {})

                    if "__interrupt__" in updates:
                        interrupt_data = updates["__interrupt__"]
                        interrupt_obj = interrupt_data[0] if interrupt_data else None
                        pending = interrupt_obj.value if interrupt_obj else {}
                        yield {
                            "event": "message",
                            "data": json.dumps({
                                "event_type": "interrupt",
                                "thread_id": request_id,
                                "status": "low_confidence" if pending.get("type") == "low_confidence" else "waiting_confirmation",
                                "pending_confirmation": pending,
                                "timestamp": time.time(),
                            }, ensure_ascii=False),
                        }
                        return

                    for node_name, node_state in updates.items():
                        if node_name in node_order:
                            stage_text = STAGE_COMPLETE_TEXT.get(node_name)
                            yield {
                                "event": "message",
                                "data": json.dumps({
                                    "event_type": "stage_complete",
                                    "stage": node_name,
                                    "markdown": stage_text,
                                    "timestamp": time.time(),
                                }, ensure_ascii=False),
                            }
                            final_result = node_state if node_state else final_result

                elif chunk_type == "custom":
                    raw_data = chunk.get("data", {})
                    stage = raw_data.get("stage", "")

                    if stage == "summary":
                        summary_content = raw_data.get("content", "")
                        yield {
                            "event": "message",
                            "data": json.dumps({
                                "event_type": "summary",
                                "content": summary_content,
                                "timestamp": time.time(),
                            }, ensure_ascii=False),
                        }
                    elif stage == "format_output" and raw_data.get("final"):
                        final_output = raw_data["final"]
                        execution_time_ms = (time.time() - start_time) * 1000
                        response = _build_response(final_output, execution_time_ms, request_id)
                        yield {
                            "event": "message",
                            "data": json.dumps({
                                "event_type": "final",
                                "data": response.model_dump(mode="json"),
                                "timestamp": time.time(),
                            }, ensure_ascii=False),
                        }
                    else:
                        markdown_text = translate_event_to_markdown(raw_data)
                        if markdown_text:
                            yield {
                                "event": "message",
                                "data": json.dumps({
                                    "event_type": "progress",
                                    "markdown": markdown_text,
                                    "raw": raw_data,
                                    "timestamp": time.time(),
                                }, ensure_ascii=False),
                            }

        except Exception as e:
            logger.exception(f"[{request_id}] 流式处理失败: {e}")
            yield {
                "event": "error",
                "data": json.dumps({
                    "event_type": "error",
                    "message": "底层 LLM 服务调用失败，请重新提问",
                    "timestamp": time.time(),
                }, ensure_ascii=False),
            }
        finally:
            semaphore.release()
            logger.info(
                f"[Semaphore] 已释放，当前并发: "
                f"{get_current_concurrency()}/{MAX_CONCURRENT_REQUESTS} "
                f"thread_id={request_id}"
            )

    return EventSourceResponse(event_generator())


@router.post("/resume")
async def resume_stream(req: ResumeRequest):
    request_id = req.thread_id
    start_time = time.time()

    logger.info(f"[{request_id}] 恢复流式处理请求")

    node_order = [
        "extract_phrases",
        "classify_and_iterate",
        "wait_for_confirmation",
        "aggregate_themes",
        "navigate_hierarchy",
        "merge_themes",
        "complete_indicators",
        "judge_themes",
        "retrieve_templates",
        "analyze_templates",
        "format_output",
        "generate_summary",
    ]

    async def event_generator() -> AsyncIterator[dict]:
        agent = agent_graph.get_agent()
        config = {"configurable": {"thread_id": request_id}}

        checkpointer = get_checkpointer()
        checkpoint = checkpointer.get(config)

        if not checkpoint:
            logger.error(f"[{request_id}] Checkpoint 不存在，无法恢复")
            yield {
                "event": "error",
                "data": json.dumps({
                    "event_type": "error",
                    "message": "会话已过期或不存在，请重新发起请求",
                    "error_code": "CHECKPOINT_NOT_FOUND",
                    "timestamp": time.time(),
                }, ensure_ascii=False),
            }
            return

        resume_command = Command(resume={
            "confirmed_dimensions": req.confirmed_dimensions,
            "confirmed_question": req.confirmed_question,
        })

        try:
            final_result = None
            async for chunk in agent.astream(
                resume_command,
                config=config,
                stream_mode=["updates", "custom"],
                version="v2",
            ):
                chunk_type = chunk.get("type", "")

                if chunk_type == "updates":
                    updates = chunk.get("data", {})

                    if "__interrupt__" in updates:
                        interrupt_data = updates["__interrupt__"]
                        interrupt_obj = interrupt_data[0] if interrupt_data else None
                        pending = interrupt_obj.value if interrupt_obj else {}
                        yield {
                            "event": "message",
                            "data": json.dumps({
                                "event_type": "interrupt",
                                "thread_id": request_id,
                                "status": "low_confidence" if pending.get("type") == "low_confidence" else "waiting_confirmation",
                                "pending_confirmation": pending,
                                "timestamp": time.time(),
                            }, ensure_ascii=False),
                        }
                        return

                    for node_name, node_state in updates.items():
                        if node_name in node_order:
                            stage_text = STAGE_COMPLETE_TEXT.get(node_name)
                            yield {
                                "event": "message",
                                "data": json.dumps({
                                    "event_type": "stage_complete",
                                    "stage": node_name,
                                    "markdown": stage_text,
                                    "timestamp": time.time(),
                                }, ensure_ascii=False),
                            }
                            final_result = node_state if node_state else final_result

                elif chunk_type == "custom":
                    raw_data = chunk.get("data", {})
                    stage = raw_data.get("stage", "")

                    if stage == "summary":
                        summary_content = raw_data.get("content", "")
                        yield {
                            "event": "message",
                            "data": json.dumps({
                                "event_type": "summary",
                                "content": summary_content,
                                "timestamp": time.time(),
                            }, ensure_ascii=False),
                        }
                    elif stage == "format_output" and raw_data.get("final"):
                        final_output = raw_data["final"]
                        execution_time_ms = (time.time() - start_time) * 1000
                        response = _build_response(final_output, execution_time_ms, request_id)
                        yield {
                            "event": "message",
                            "data": json.dumps({
                                "event_type": "final",
                                "data": response.model_dump(mode="json"),
                                "timestamp": time.time(),
                            }, ensure_ascii=False),
                        }
                    else:
                        markdown_text = translate_event_to_markdown(raw_data)
                        if markdown_text:
                            yield {
                                "event": "message",
                                "data": json.dumps({
                                    "event_type": "progress",
                                    "markdown": markdown_text,
                                    "raw": raw_data,
                                    "timestamp": time.time(),
                                }, ensure_ascii=False),
                            }

        except Exception as e:
            logger.exception(f"[{request_id}] 恢复流式处理失败: {e}")
            yield {
                "event": "error",
                "data": json.dumps({
                    "event_type": "error",
                    "message": "底层 LLM 服务调用失败，请重新提问",
                    "timestamp": time.time(),
                }, ensure_ascii=False),
            }

    return EventSourceResponse(event_generator())


def _invoke_agent(
    request_id: str,
    question: str,
    top_k_themes: int,
    top_k_templates: int,
    context: ConversationContext | None,
    resume_command: Command | None,
) -> SyncResponse:
    import time
    start_time = time.time()

    initial_history = []
    if context:
        initial_history.append({
            "round": 1,
            "user_question": context.previous_question,
            "normalized_question": context.previous_normalized_question,
            "filter_indicators": [
                {"alias": f.alias, "value": f.value, "indicator_id": "", "type": ""}
                for f in context.previous_filter_indicators
            ],
            "analysis_dimensions": [
                {"search_term": d, "converged": True, "indicators": []}
                for d in context.previous_dimensions
            ],
        })

    initial_state = {
        "user_question": question,
        "top_k_themes": top_k_themes,
        "top_k_templates": top_k_templates,
        "extracted_phrases": [],
        "filter_indicators": [],
        "analysis_dimensions": [],
        "normalized_question": "",
        "search_results": {},
        "iteration_round": 0,
        "iteration_log": [],
        "is_low_confidence": False,
        "low_confidence_message": "",
        "low_confidence_suggestions": [],
        "pending_confirmation": None,
        "user_confirmation": None,
        "conversation_history": initial_history,
        "candidate_themes": [],
        "navigation_path_themes": [],
        "navigation_path_detail": [],
        "recommended_themes": [],
        "recommended_templates": [],
        "final_output": {},
        "execution_time_ms": 0.0,
        "error": None,
    }

    agent = agent_graph.get_agent()
    config = {"configurable": {"thread_id": request_id}}

    try:
        if resume_command:
            result = agent.invoke(resume_command, config=config)
        else:
            result = agent.invoke(initial_state, config=config)

        execution_time_ms = (time.time() - start_time) * 1000

        checkpointer = get_checkpointer()
        checkpoint = checkpointer.get(config)
        pending = None
        if checkpoint:
            pending = checkpoint.get("channel_values", {}).get("pending_confirmation")

        if pending:
            interrupt_info = SyncInterruptInfo(
                thread_id=request_id,
                status="waiting_confirmation",
                pending_confirmation=pending,
            )
            return SyncResponse(
                status="interrupted",
                request_id=request_id,
                execution_time_ms=execution_time_ms,
                interrupt=interrupt_info,
            )

        final_output = result.get("final_output", {}) if isinstance(result, dict) else {}
        response = _build_response(final_output, execution_time_ms, request_id)
        return SyncResponse(
            status="completed",
            request_id=request_id,
            execution_time_ms=execution_time_ms,
            data=response,
        )

    except Exception as e:
        logger.exception(f"[{request_id}] sync 端点执行失败: {e}")
        execution_time_ms = (time.time() - start_time) * 1000
        return SyncResponse(
            status="error",
            request_id=request_id,
            execution_time_ms=execution_time_ms,
            error=SyncErrorInfo(
                code="EXECUTION_ERROR",
                message=f"底层执行失败: {str(e)}",
            ),
        )


@router.post("/recommend-sync")
async def recommend_sync(req: RecommendRequest):
    semaphore = get_semaphore()
    current = get_current_concurrency()

    if semaphore.locked() and current >= MAX_CONCURRENT_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "too_many_requests",
                "message": f"当前并发已达上限 {MAX_CONCURRENT_REQUESTS}，请稍后重试",
                "current_concurrency": current,
                "max_concurrency": MAX_CONCURRENT_REQUESTS,
            },
        )

    try:
        await asyncio.wait_for(
            semaphore.acquire(),
            timeout=CONCURRENT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "timeout_waiting",
                "message": f"等待超过 {CONCURRENT_TIMEOUT_SECONDS}s，请稍后重试",
                "current_concurrency": get_current_concurrency(),
                "max_concurrency": MAX_CONCURRENT_REQUESTS,
            },
        )

    try:
        logger.info(f"[{req.thread_id}] recommend-sync 请求: {req.question}")
        response = _invoke_agent(
            request_id=req.thread_id,
            question=req.question,
            top_k_themes=req.top_k_themes,
            top_k_templates=req.top_k_templates,
            context=req.context,
            resume_command=None,
        )
        return response
    finally:
        semaphore.release()


@router.post("/resume-sync")
async def resume_sync(req: ResumeRequest):
    semaphore = get_semaphore()
    current = get_current_concurrency()

    if semaphore.locked() and current >= MAX_CONCURRENT_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "too_many_requests",
                "message": f"当前并发已达上限 {MAX_CONCURRENT_REQUESTS}，请稍后重试",
                "current_concurrency": current,
                "max_concurrency": MAX_CONCURRENT_REQUESTS,
            },
        )

    try:
        await asyncio.wait_for(
            semaphore.acquire(),
            timeout=CONCURRENT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "timeout_waiting",
                "message": f"等待超过 {CONCURRENT_TIMEOUT_SECONDS}s，请稍后重试",
                "current_concurrency": get_current_concurrency(),
                "max_concurrency": MAX_CONCURRENT_REQUESTS,
            },
        )

    try:
        logger.info(f"[{req.thread_id}] resume-sync 请求")

        checkpointer = get_checkpointer()
        checkpoint = checkpointer.get({"configurable": {"thread_id": req.thread_id}})
        if not checkpoint:
            return SyncResponse(
                status="error",
                request_id=req.thread_id,
                execution_time_ms=0.0,
                error=SyncErrorInfo(
                    code="CHECKPOINT_NOT_FOUND",
                    message="会话已过期或不存在，请重新发起请求",
                ),
            )

        resume_command = Command(resume={
            "confirmed_dimensions": req.confirmed_dimensions,
            "confirmed_question": req.confirmed_question,
        })

        response = _invoke_agent(
            request_id=req.thread_id,
            question="",
            top_k_themes=3,
            top_k_templates=5,
            context=None,
            resume_command=resume_command,
        )
        return response

    finally:
        semaphore.release()


@router.get("/debug/checkpoint/{thread_id}")
async def debug_checkpoint(thread_id: str):
    import json

    checkpointer = get_checkpointer()
    config = {"configurable": {"thread_id": thread_id}}
    checkpoint = checkpointer.get(config)

    if not checkpoint:
        return {"exists": False, "thread_id": thread_id}

    result = {
        "exists": True,
        "thread_id": thread_id,
        "checkpoint_keys": list(checkpoint.keys()),
    }

    channel_values = checkpoint.get("channel_values", {})
    result["channel_values_keys"] = list(channel_values.keys()) if channel_values else []
    result["pending_confirmation"] = channel_values.get("pending_confirmation")
    result["user_question"] = channel_values.get("user_question", "")[:100] if channel_values.get("user_question") else None

    result["pending_sends"] = checkpoint.get("pending_sends", [])
    result["pending_tasks"] = checkpoint.get("pending_tasks", [])

    return result
