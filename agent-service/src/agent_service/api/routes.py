"""
API 路由
"""

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
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["recommend"])


# ═════════════════════════════════════════════════════════════════
# 全局信号量（在 main.py lifespan 中初始化）
# ═════════════════════════════════════════════════════════════════
_semaphore: asyncio.Semaphore | None = None


def init_semaphore():
    """由 main.py lifespan 调用，初始化信号量"""
    global _semaphore
    _semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    logger.info(f"[Semaphore] 并发上限设置为: {MAX_CONCURRENT_REQUESTS}")


def get_semaphore() -> asyncio.Semaphore:
    if _semaphore is None:
        raise RuntimeError("Semaphore 未初始化，请检查 lifespan 配置")
    return _semaphore


def get_current_concurrency() -> int:
    """返回当前正在处理的请求数"""
    if _semaphore is None:
        return 0
    return MAX_CONCURRENT_REQUESTS - _semaphore._value


# ═════════════════════════════════════════════════════════════════
# 辅助函数
# ═════════════════════════════════════════════════════════════════════════════════════════

# 阶段完成时的 Markdown 文字映射
STAGE_COMPLETE_TEXT = {
    "extract_phrases": None,        # 已由 custom 事件覆盖
    "classify_and_iterate": None,   # 已由 custom 事件覆盖
    "wait_for_confirmation": None,  # interrupt 事件覆盖
    "aggregate_themes": "│ ✅ **[1.1]** 候选主题聚合完成",
    "complete_indicators": "│ ✅ **[1.2]** 全量指标补全完成",
    "judge_themes": None,           # 已由 custom 事件覆盖
    "retrieve_templates": "│ ✅ **[2.1]** 模板检索完成",
    "analyze_templates": None,      # 已由 custom 事件覆盖
    "format_output": "\n✅ **所有阶段执行完毕，正在生成推荐结果...**",
}


def translate_event_to_markdown(data: dict) -> str | None:
    """将节点事件翻译为人类可读的 Markdown 进度文字"""
    stage = data.get("stage", "")
    step = data.get("step", "")
    status = data.get("status", "")

    # ── 阶段 0.1 词组提取 ──
    if stage == "extract_phrases":
        if status == "in_progress":
            return "┌─────────────────────────────────────────\n│ **[0.1] 词组提取** 开始执行...\n└─────────────────────────────────────────"
        if status == "done":
            count = data.get("phrases_count", 0)
            return f"│ ✅ 词组提取完成，共提取 **{count}** 个词组\n└─────────────────────────────────────────"

    # ── 阶段 0.2/0.3 分类与迭代 ──
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

    # ── 阶段 0.4 等待确认 ──
    if stage == "wait_for_confirmation":
        if step == "waiting_confirmation":
            return "\n┌─────────────────────────────────────────\n│ **[0.4] 等待用户确认分析维度** ⏸\n└─────────────────────────────────────────"
        if step == "low_confidence":
            return "\n┌─────────────────────────────────────────\n│ ⚠️ **低置信度** 无法精确匹配，等待用户修改描述\n└─────────────────────────────────────────"

    # ── 阶段 1.3 主题裁决 ──
    if stage == "judge_themes":
        if step == "judging":
            count = data.get("theme_count", 0)
            return f"\n┌─────────────────────────────────────────\n│ **[1.3] 主题裁决** 正在评估 **{count}** 个候选主题..."
        if step == "completed":
            return "│ ✅ 主题裁决完成\n└─────────────────────────────────────────"

    # ── 阶段 2.2 模板分析 ──
    if stage == "analyze_templates":
        if step == "analyzing":
            count = data.get("template_count", 0)
            return f"\n┌─────────────────────────────────────────\n│ **[2.2] 模板可用性分析** 共 **{count}** 个模板"
        if step == "analyzing_template":
            idx = data.get("template_index", "")
            alias = data.get("template_alias", "")
            return f"│   📄 分析模板 {idx}：**{alias}**..."
        if step == "completed":
            return "│ ✅ 模板分析完成\n└─────────────────────────────────────────"

    # ── 阶段 3 输出格式化 ──
    if stage == "format_output":
        if step == "generating":
            return "\n┌─────────────────────────────────────────\n│ **[3] 生成推荐结果报告**..."
        if step == "completed":
            # 完整的 Markdown 推荐报告已在 data["markdown"] 中，由 SSE 路由直接透传
            return "│ ✅ 推荐结果生成完成"

    return None  # 不需要翻译的事件


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
        conversation_round=final_output.get("conversation_round", 1),
        markdown=final_output.get("markdown", ""),
    )


# ═════════════════════════════════════════════════════════════════
# 路由
# ═════════════════════════════════════════════════════════════════

@router.post("/recommend")
async def recommend_stream(req: RecommendRequest):
    """
    流式推荐接口（SSE）

    返回 Server-Sent Events 流，实时推送推理过程
    """
    request_id = req.thread_id
    start_time = time.time()

    semaphore = get_semaphore()
    current = get_current_concurrency()

    # 快速拒绝：已满载时直接返回 429，不等待
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

    # 等待获取信号量（带超时）
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

    # ── 获取信号量成功，处理请求 ──────────────────────────────
    logger.info(
        f"[Semaphore] 获取成功，当前并发: "
        f"{get_current_concurrency()}/{MAX_CONCURRENT_REQUESTS} "
        f"thread_id={request_id}"
    )

    logger.info(f"[{request_id}] 开始流式处理请求: {req.question}")

    async def event_generator() -> AsyncIterator[dict]:
        """SSE 事件生成器"""
        try:
            agent = agent_graph.get_agent()

            # 如果有 context，构建初始 conversation_history
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
                "generate_summary",
            ]

            final_result = None
            summary_content = None
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

                    # 检测 interrupt 状态
                    if "__interrupt__" in updates:
                        interrupt_data = updates["__interrupt__"]
                        # LangGraph interrupt 返回的是 Interrupt 对象列表，不是 dict
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
                        return  # 停止 SSE 流，等待 /resume

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
                    # 自定义事件（来自节点内的 get_stream_writer()）
                    raw_data = chunk.get("data", {})
                    stage = raw_data.get("stage", "")

                    if stage == "summary":
                        # 独立的自然语言总结事件
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
                        # format_output 节点推送的 final 事件（快速返回）
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
                        # 其他进度事件
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
            # ✅ 无论正常结束还是异常，都释放信号量
            semaphore.release()
            logger.info(
                f"[Semaphore] 已释放，当前并发: "
                f"{get_current_concurrency()}/{MAX_CONCURRENT_REQUESTS} "
                f"thread_id={request_id}"
            )

    return EventSourceResponse(event_generator())


@router.post("/resume")
async def resume_stream(req: ResumeRequest):
    """
    恢复中断的流式处理（SSE）

    当 wait_for_confirmation 节点触发 interrupt 后，
    前端确认分析维度，通过此接口恢复执行
    """
    request_id = req.thread_id
    start_time = time.time()

    logger.info(f"[{request_id}] 恢复流式处理请求")

    node_order = [
        "extract_phrases",
        "classify_and_iterate",
        "wait_for_confirmation",
        "aggregate_themes",
        "complete_indicators",
        "judge_themes",
        "retrieve_templates",
        "analyze_templates",
        "format_output",
        "generate_summary",
    ]

    async def event_generator() -> AsyncIterator[dict]:
        """SSE 事件生成器"""
        agent = agent_graph.get_agent()
        config = {"configurable": {"thread_id": request_id}}

        # 先检查 checkpoint 是否存在
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

        # 构造 Command，将用户确认结果注入 interrupt
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

                    # 检测 interrupt 状态
                    if "__interrupt__" in updates:
                        interrupt_data = updates["__interrupt__"]
                        # LangGraph interrupt 返回的是 Interrupt 对象列表，不是 dict
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
                        return  # 再次中断，等待下次 /resume

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
                    # 自定义事件（来自节点内的 get_stream_writer()）
                    raw_data = chunk.get("data", {})
                    stage = raw_data.get("stage", "")

                    if stage == "summary":
                        # 独立的自然语言总结事件
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
                        # format_output 节点推送的 final 事件（快速返回）
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
                        # 其他进度事件
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


# ═════════════════════════════════════════════════════════════════
# 非流式同步端点（适合 CLI / 脚本调用）
# ═════════════════════════════════════════════════════════════════


def _invoke_agent(
    request_id: str,
    question: str,
    top_k_themes: int,
    top_k_templates: int,
    context: ConversationContext | None,
    resume_command: Command | None,
) -> SyncResponse:
    """
    封装 agent.invoke / agent.ainvoke 调用逻辑，统一处理 interrupt 和异常。

    返回 SyncResponse：
    - status=completed：执行完毕，data 中有 RecommendResponse
    - status=interrupted：被 interrupt，interrupt 中有 pending_confirmation
    - status=error：发生异常，error 中有错误信息
    """
    import time
    start_time = time.time()

    # 构造初始状态（与流式端点相同）
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
            # resume 场景
            result = agent.invoke(resume_command, config=config)
        else:
            # 首次执行场景
            result = agent.invoke(initial_state, config=config)

        execution_time_ms = (time.time() - start_time) * 1000

        # 检查是否被 interrupt（通过 checkpoint 判断）
        checkpointer = get_checkpointer()
        checkpoint = checkpointer.get(config)
        pending = None
        if checkpoint:
            pending = checkpoint.get("channel_values", {}).get("pending_confirmation")

        if pending:
            # 被 interrupt（等待用户确认）
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

        # 正常完成
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
    """
    非流式同步推荐接口

    与 /recommend（流式 SSE）功能完全相同，但直接返回完整结构化结果。
    适合 CLI 工具、脚本调用等不需要实时进度反馈的场景。

    **返回格式**：
    - status=completed：执行完毕，data 中有完整推荐结果
    - status=interrupted：需要用户确认分析维度，interrupt 中有 pending_confirmation
      → 调用方应引导用户确认后，调用 /resume-sync 继续
    - status=error：执行异常
    """
    semaphore = get_semaphore()
    current = get_current_concurrency()

    # 快速拒绝
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
    """
    非流式同步恢复接口

    与 /resume（流式 SSE）功能完全相同，但直接返回完整结构化结果。
    适合 CLI 工具、脚本调用等场景。

    **返回格式**：
    - status=completed：执行完毕，data 中有完整推荐结果
    - status=interrupted：再次被 interrupt（通常不应该在 resume 时发生）
    - status=error：执行异常
    """
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

        # 先检查 checkpoint 是否存在
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
            question="",  # resume 不需要 question
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
    """调试端点：查看指定 thread_id 的 checkpoint 状态"""
    import json

    checkpointer = get_checkpointer()
    config = {"configurable": {"thread_id": thread_id}}
    checkpoint = checkpointer.get(config)

    if not checkpoint:
        return {"exists": False, "thread_id": thread_id}

    # 返回完整 checkpoint 结构
    result = {
        "exists": True,
        "thread_id": thread_id,
        "checkpoint_keys": list(checkpoint.keys()),
    }

    # 检查 channel_values 中的关键字段
    channel_values = checkpoint.get("channel_values", {})
    result["channel_values_keys"] = list(channel_values.keys()) if channel_values else []
    result["pending_confirmation"] = channel_values.get("pending_confirmation")
    result["user_question"] = channel_values.get("user_question", "")[:100] if channel_values.get("user_question") else None

    # 检查 pending_sends（interrupt 信息可能在这里）
    result["pending_sends"] = checkpoint.get("pending_sends", [])
    result["pending_tasks"] = checkpoint.get("pending_tasks", [])

    return result
