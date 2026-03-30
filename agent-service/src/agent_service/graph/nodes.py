"""
LangGraph 节点函数
每个阶段对应一个节点函数
使用结构化输出 (with_structured_output) 替代手动 JSON 解析
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.types import interrupt

from .. import config
from ..llm import client as llm_client
from ..llm import prompts as llm_prompts
from ..llm import LLMCallError
from ..tools import template_tools, theme_tools, vector_search
from .state import (
    AgentState,
    FilterIndicator,
    UserConfirmation,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# 筛选指标规则映射
# ═══════════════════════════════════════════════════════════════════════

FILTER_INDICATOR_RULES = [
    {
        "keywords": ["分行", "支行", "机构", "网点"],
        "indicator_id": "INDICATOR.二级账务机构名称",
        "alias": "二级账务机构名称",
        "type": "机构筛选指标",
    },
    {
        "keywords": ["年", "月", "日", "季度", "今年", "上月", "上季", "本月", "本季", "去年"],
        "indicator_id": "INDICATOR.数据日期",
        "alias": "数据日期",
        "type": "时间筛选指标",
    },
]


def _map_filter_phrase(phrase: str) -> dict:
    """将筛选词按规则映射到魔数师指标"""
    for rule in FILTER_INDICATOR_RULES:
        if any(kw in phrase for kw in rule["keywords"]):
            return {
                "indicator_id": rule["indicator_id"],
                "value": phrase,
                "alias": rule["alias"],
                "type": rule["type"],
            }
    # 兜底：无法匹配规则时保留原始词，indicator_id 为空
    return {
        "indicator_id": "",
        "value": phrase,
        "alias": phrase,
        "type": "未知筛选指标",
    }


# ═══════════════════════════════════════════════════════════════════════
# 并行执行辅助函数
# ═══════════════════════════════════════════════════════════════════════

def _search_concepts_parallel(concepts: list[str], top_k: int) -> dict[str, list]:
    """并行向量搜索"""
    if not concepts:
        return {}

    results = {}
    max_workers = min(len(concepts), 5)  # 最多5个并行

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_concept = {
            executor.submit(vector_search.search_indicators_by_vector, concept, top_k): concept
            for concept in concepts
        }

        for future in as_completed(future_to_concept):
            concept = future_to_concept[future]
            try:
                result = future.result()
                if result.get("success"):
                    results[concept] = result.get("indicators", [])
                else:
                    results[concept] = []
            except Exception as e:
                logger.warning(f"向量搜索失败 [{concept}]: {e}")
                results[concept] = []

    return results


def _judge_theme_parallel(
    theme: dict,
    user_question: str,
    analysis_dimensions: list,
) -> dict | None:
    """并行主题裁决辅助函数"""
    theme_id = theme["theme_id"]
    theme_alias = theme["theme_alias"]
    theme_path = theme.get("theme_path", f"自主分析 > {theme_alias}")
    filter_inds = theme.get("filter_indicators_detail", [])
    analysis_inds = theme.get("analysis_indicators_detail", [])

    # 构建字符串
    dim_str = _build_analysis_dimensions_str(analysis_dimensions)
    filter_str = _build_filter_indicators_str(filter_inds)
    analysis_str = _build_analysis_indicators_str(analysis_inds)

    try:
        judgment = llm_client.judge_theme(
            user_question=user_question,
            analysis_dimensions_str=dim_str,
            theme_alias=theme_alias,
            theme_path=theme_path,
            filter_indicators_str=filter_str,
            analysis_indicators_str=analysis_str,
        )
        return {
            "theme": theme,
            "judgment": judgment,
        }
    except Exception as e:
        logger.error(f"主题裁决 LLM 调用失败 [{theme_alias}]: {e}")
        raise LLMCallError(f"LLM调用失败") from e


def _analyze_template_parallel(
    template: dict,
    user_question: str,
    analysis_dimensions: list,
) -> dict:
    """并行模板分析辅助函数"""
    template_id = template.get("template_id", "")
    template_alias = template.get("template_alias", "")
    template_description = template.get("template_description", "")
    coverage_ratio = f"{template.get('coverage_ratio', 0) * 100:.0f}%"
    all_template_inds = template.get("all_template_indicators", [])
    all_inds_str = _build_template_indicators_str(all_template_inds)

    missing_aliases = template.get("missing_indicator_aliases", [])
    missing_inds_str = "（无需补充）"
    if missing_aliases:
        missing_inds_str = "\n".join(f"- {alias}" for alias in missing_aliases[:10])

    dim_str = _build_analysis_dimensions_str(analysis_dimensions)

    try:
        usability = llm_client.analyze_template_usability(
            user_question=user_question,
            analysis_dimensions_str=dim_str,
            template_alias=template_alias,
            template_description=template_description,
            coverage_ratio=coverage_ratio,
            all_template_indicators_str=all_inds_str,
            missing_indicators_str=missing_inds_str,
        )
        return {
            "template": template,
            "usability": usability.model_dump(),
        }
    except Exception as e:
        logger.error(f"模板可用性分析 LLM 调用失败 [{template_alias}]: {e}")
        raise LLMCallError(f"LLM调用失败") from e


# ═══════════════════════════════════════════════════════════════════════
# 阶段 0 节点
# ═══════════════════════════════════════════════════════════════════════

def extract_phrases(state: AgentState) -> dict:
    """阶段 0.1：提取原始词组"""
    user_question = state["user_question"]
    writer = get_stream_writer()
    writer({"stage": "extract_phrases", "step": "extracting_phrases", "status": "in_progress"})

    conversation_history = state.get("conversation_history", [])
    result = llm_client.extract_phrases(user_question, conversation_history=conversation_history)
    phrases = result.phrases if result.phrases else []

    writer({"stage": "extract_phrases", "step": "extracting_phrases", "status": "done", "phrases_count": len(phrases)})
    return {"extracted_phrases": phrases}


def classify_and_iterate(state: AgentState) -> dict:
    """阶段 0.2-0.3：词组分类 + 迭代精炼（重构版）"""
    user_question = state["user_question"]
    phrases = state.get("extracted_phrases", [])
    writer = get_stream_writer()
    writer({"stage": "classify_and_iterate", "step": "classifying", "status": "in_progress"})

    # ── 0.2 分类 ──
    try:
        classification = llm_client.classify_phrases(user_question, phrases)
    except Exception as e:
        logger.warning(f"分类失败，使用默认分类: {e}")
        classification = None

    if classification:
        filter_phrases = classification.filter_phrases
        analysis_concepts = classification.analysis_concepts
    else:
        filter_phrases = []
        analysis_concepts = phrases

    # 构建筛选指标（使用规则映射）
    filter_indicators: list[FilterIndicator] = []
    for pf in filter_phrases:
        filter_indicators.append(_map_filter_phrase(pf))

    # ── 0.3 迭代精炼（重构版）──
    # 数据结构初始化
    pending_concepts: dict[str, list] = {c: [] for c in analysis_concepts}
    converged_dimensions: dict[str, list] = {}
    iteration_log: list[dict] = []
    iteration_round = 0

    # 主循环
    while iteration_round < config.MAX_ITERATION_ROUNDS:
        # Step 1：搜索（仅对 pending_concepts 搜索）
        if not pending_concepts:
            break

        iteration_round += 1
        current_concepts = list(pending_concepts.keys())
        writer({
            "stage": "classify_and_iterate",
            "step": "searching",
            "round": iteration_round,
            "concepts": current_concepts,
        })

        round_search_results = _search_concepts_parallel(
            current_concepts, top_k=config.VECTOR_SEARCH_TOP_K
        )
        # 覆盖更新 pending_concepts 的 value（不累积，每轮重新搜索）
        for concept, indicators in round_search_results.items():
            pending_concepts[concept] = indicators

        # Step 2：收敛判定（代码客观判定，不再依赖 LLM）
        newly_converged: list[str] = []
        for concept in list(pending_concepts.keys()):
            indicators = pending_concepts[concept]
            top1_score = indicators[0]["similarity_score"] if indicators else 0.0
            if top1_score >= config.CONVERGENCE_SIMILARITY_THRESHOLD:
                # 收敛：移入 converged_dimensions
                converged_dimensions[concept] = indicators
                del pending_concepts[concept]
                newly_converged.append(concept)

        writer({
            "stage": "classify_and_iterate",
            "step": "converged",
            "round": iteration_round,
            "newly_converged": newly_converged,
            "converged_count": len(converged_dimensions),
            "pending_count": len(pending_concepts),
        })

        # Step 3：结束判定
        if not pending_concepts:
            break  # 正常收敛出口
        if iteration_round >= config.MAX_ITERATION_ROUNDS:
            break  # 超时强制出口

        # Step 4：生成下一轮搜索词（仅对 pending_concepts 生成）
        writer({
            "stage": "classify_and_iterate",
            "step": "evaluating",
            "round": iteration_round,
        })

        pending_str = _build_pending_search_results_str(pending_concepts)
        converged_str = _build_converged_concepts_str(converged_dimensions)

        try:
            refinement = llm_client.refine_concepts(
                user_question=user_question,
                round_num=iteration_round,
                max_rounds=config.MAX_ITERATION_ROUNDS,
                pending_search_results_str=pending_str,
                converged_concepts_str=converged_str,
            )
            new_concepts = refinement.new_concepts
        except Exception as e:
            logger.warning(f"迭代精炼失败，使用原搜索词继续: {e}")
            new_concepts = list(pending_concepts.keys())

        # 如果新词与原词完全相同，无法进一步优化，提前退出
        if set(new_concepts) == set(pending_concepts.keys()):
            logger.info("LLM 无法进一步优化搜索词，提前退出迭代")
            break

        # 替换 pending_concepts，value 清空等下轮搜索填充
        pending_concepts = {c: [] for c in new_concepts}

        iteration_log.append({
            "round": iteration_round,
            "pending_concepts": list(current_concepts),
            "newly_converged": newly_converged,
            "refinement": refinement.model_dump() if refinement else None,
        })

    # ── 迭代结束后的处理 ──
    # Step A：生成规范化问题（迭代结束后调用一次）
    filter_str = _build_filter_phrases_str(filter_indicators)
    converged_str = _build_converged_concepts_str(converged_dimensions)

    try:
        norm_result = llm_client.generate_normalized_question(
            user_question=user_question,
            filter_phrases_str=filter_str,
            converged_concepts_str=converged_str,
        )
        normalized_question = norm_result.normalized_question
    except Exception as e:
        logger.warning(f"规范化问题生成失败，使用原文: {e}")
        normalized_question = user_question

    # Step B：出口判断
    is_low_confidence = bool(pending_concepts)

    # Step C：构建 analysis_dimensions
    analysis_dimensions = []
    for concept, indicators in converged_dimensions.items():
        analysis_dimensions.append({
            "search_term": concept,
            "converged": True,
            "indicators": indicators,
        })
    for concept, indicators in pending_concepts.items():
        analysis_dimensions.append({
            "search_term": concept,
            "converged": False,
            "indicators": indicators,
        })

    # Step D：低置信度出口的额外处理
    low_confidence_message = ""
    low_confidence_suggestions: list = []
    if is_low_confidence:
        low_conf_concepts = list(pending_concepts.keys())
        pending_str = _build_pending_search_results_str(pending_concepts)
        try:
            low_conf_result = llm_client.handle_low_confidence(
                user_question=user_question,
                low_confidence_concepts=low_conf_concepts,
                search_results_str=pending_str,
            )
            low_confidence_message = low_conf_result.user_message
            low_confidence_suggestions = low_conf_result.suggestions
        except Exception as e:
            logger.warning(f"低置信度处理失败: {e}")
            low_confidence_message = "以下分析概念无法精确匹配，可能需要更清晰的描述："

    # Step E：推送完成事件
    writer({
        "stage": "classify_and_iterate",
        "step": "completed",
        "iterations": iteration_round,
        "converged_count": len(converged_dimensions),
        "low_confidence": is_low_confidence,
    })

    # Step E2：生成维度勾选引导（基于 Jaccard 的主题交叉检测）
    dimension_guidance = _generate_dimension_guidance(
        user_question=user_question,
        analysis_dimensions=analysis_dimensions,
    )

    # Step F：构建返回值
    if is_low_confidence:
        # 低置信度出口：构建 pending_confirmation，前端展示收敛/未收敛维度让用户自选
        filter_display = [
            {"alias": f.get("alias", ""), "value": f.get("value", ""), "type": f.get("type", "")}
            for f in filter_indicators
        ]
        dimension_options = [
            {
                "search_term": d["search_term"],
                "converged": d["converged"],
                "top_indicator_aliases": [i["alias"] for i in d["indicators"][:5]],
                "top_indicators": d["indicators"][:5],
            }
            for d in analysis_dimensions
        ]
        pending_confirmation = {
            "filter_display": filter_display,
            "dimension_options": dimension_options,
            "normalized_question": normalized_question,
            "message": "以下筛选条件已自动识别，请选择要分析的维度（已标注收敛状态）：",
        }
        return {
            "filter_indicators": filter_indicators,
            "search_results": {**converged_dimensions, **pending_concepts},
            "iteration_round": iteration_round,
            "iteration_log": iteration_log,
            "analysis_dimensions": analysis_dimensions,
            "normalized_question": normalized_question,
            "is_low_confidence": True,
            "low_confidence_message": low_confidence_message,
            "low_confidence_suggestions": low_confidence_suggestions,
            "pending_confirmation": pending_confirmation,
            "user_confirmation": None,
            "dimension_guidance": dimension_guidance,
        }
    else:
        # 正常收敛出口：构建待确认数据
        filter_display = [
            {"alias": f.get("alias", ""), "value": f.get("value", ""), "type": f.get("type", "")}
            for f in filter_indicators
        ]
        dimension_options = [
            {
                "search_term": d["search_term"],
                "converged": d["converged"],
                "top_indicator_aliases": [i["alias"] for i in d["indicators"][:5]],
                "top_indicators": d["indicators"][:5],
            }
            for d in analysis_dimensions
        ]
        pending_confirmation = {
            "filter_display": filter_display,
            "dimension_options": dimension_options,
            "normalized_question": normalized_question,
            "message": "以下筛选条件已自动识别，请确认分析维度：",
        }
        return {
            "filter_indicators": filter_indicators,
            "search_results": converged_dimensions,
            "iteration_round": iteration_round,
            "iteration_log": iteration_log,
            "analysis_dimensions": analysis_dimensions,
            "normalized_question": normalized_question,
            "is_low_confidence": False,
            "pending_confirmation": pending_confirmation,
            "user_confirmation": None,
            "low_confidence_message": "",
            "low_confidence_suggestions": [],
            "dimension_guidance": dimension_guidance,
        }


# ═══════════════════════════════════════════════════════════════════════
# 阶段 1 节点
# ═══════════════════════════════════════════════════════════════════════

def aggregate_themes(state: AgentState) -> dict:
    """阶段 1.1：聚合候选主题"""
    matched_indicators = []
    for dim in state.get("analysis_dimensions", []):
        for ind in dim.get("indicators", []):
            if ind["id"] and ind["id"] not in matched_indicators:
                matched_indicators.append(ind["id"])

    if not matched_indicators:
        return {"candidate_themes": []}

    result = theme_tools.aggregate_themes_from_indicators(
        matched_indicators, top_k=state.get("top_k_themes", 3)
    )

    if result.get("success"):
        return {"candidate_themes": result.get("candidate_themes", [])}
    else:
        return {
            "candidate_themes": [],
            "error": result.get("error", "主题聚合失败"),
        }


def complete_indicators(state: AgentState) -> dict:
    """阶段 1.2：指标补全 - 为每个主题补全全量指标"""
    candidate_themes = state.get("candidate_themes", [])

    for theme in candidate_themes:
        theme_id = theme["theme_id"]

        # 获取筛选指标
        filter_result = theme_tools.get_theme_filter_indicators(theme_id)
        theme["filter_indicators_detail"] = []
        if filter_result.get("success"):
            theme["filter_indicators_detail"] = (
                filter_result.get("time_filter_indicators", [])
                + filter_result.get("org_filter_indicators", [])
            )

        # 获取分析指标
        analysis_result = theme_tools.get_theme_analysis_indicators(theme_id)
        theme["analysis_indicators_detail"] = []
        if analysis_result.get("success"):
            theme["analysis_indicators_detail"] = analysis_result.get(
                "analysis_indicators", []
            )

    return {"candidate_themes": candidate_themes}


def judge_themes(state: AgentState) -> dict:
    """阶段 1.3：LLM 裁决 - 判断主题可用性 + 指标精筛（并行化优化版））"""
    user_question = state["user_question"]
    analysis_dimensions = state.get("analysis_dimensions", [])
    candidate_themes = state.get("candidate_themes", [])
    writer = get_stream_writer()

    writer({"stage": "judge_themes", "step": "judging", "status": "in_progress", "theme_count": len(candidate_themes)})

    if not candidate_themes:
        writer({"stage": "judge_themes", "step": "completed", "status": "done"})
        return {"recommended_themes": []}

    recommended_themes = []
    max_workers = min(len(candidate_themes), 3)  # 最多3个并行

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_theme = {
            executor.submit(
                _judge_theme_parallel, theme, user_question, analysis_dimensions
            ): theme
            for theme in candidate_themes
        }

        try:
            for future in as_completed(future_to_theme, timeout=config.LLM_BATCH_TIMEOUT_SECONDS):
                result = future.result()  # 失败直接抛出，不捕获
                theme = future_to_theme[future]
                judgment = result.get("judgment")
                recommended_themes.append({
                    "theme_id": theme["theme_id"],
                    "theme_alias": theme["theme_alias"],
                    "theme_level": theme.get("theme_level", 0),
                    "theme_path": theme.get("theme_path", f"自主分析 > {theme['theme_alias']}"),
                    "is_supported": judgment.is_supported,
                    "support_reason": judgment.support_reason,
                    "selected_filter_indicators": [
                        {
                            "indicator_id": si.indicator_id,
                            "alias": si.alias,
                            "type": si.type,
                            "reason": si.reason,
                        }
                        for si in judgment.selected_filter_indicators
                    ],
                    "selected_analysis_indicators": [
                        {
                            "indicator_id": si.indicator_id,
                            "alias": si.alias,
                            "type": si.type,
                            "reason": si.reason,
                            "description": si.reason,
                        }
                        for si in judgment.selected_analysis_indicators
                    ],
                    "unsupported_dimensions": judgment.unsupported_dimensions,
                })
        except FuturesTimeoutError:
            raise LLMCallError("LLM调用失败：主题裁决批次超时")

    writer({"stage": "judge_themes", "step": "completed", "status": "done"})
    return {"recommended_themes": recommended_themes}


# ═══════════════════════════════════════════════════════════════════════
# 阶段 2 节点
# ═══════════════════════════════════════════════════════════════════════

def retrieve_templates(state: AgentState) -> dict:
    """阶段 2.1：检索模板（带覆盖率计算）"""
    recommended_themes = state.get("recommended_themes", [])
    top_k = state.get("top_k_templates", 5)

    all_templates = []

    for theme in recommended_themes:
        if not theme.get("is_supported"):
            continue

        theme_id = theme["theme_id"]

        # 收集 LLM 裁决后的指标别名（覆盖率基于别名匹配）
        matched_indicator_aliases = []

        for ind in theme.get("selected_filter_indicators", []):
            if ind.get("alias"):
                matched_indicator_aliases.append(ind["alias"])

        for ind in theme.get("selected_analysis_indicators", []):
            if ind.get("alias"):
                matched_indicator_aliases.append(ind["alias"])

        if not matched_indicator_aliases:
            continue

        result = template_tools.get_theme_templates_with_coverage(
            theme_id=theme_id,
            matched_indicator_aliases=matched_indicator_aliases,
            top_k=top_k,
        )

        if result.get("success"):
            templates = result.get("matched_templates", [])
            for t in templates:
                t["theme_id"] = theme_id
                t["theme_alias"] = theme["theme_alias"]
                t["has_qualified_templates"] = result.get("has_qualified_templates", False)
                t["fallback_reason"] = result.get("fallback_reason", "")

            all_templates.extend(templates)

    return {"recommended_templates": all_templates}


def analyze_templates(state: AgentState) -> dict:
    """阶段 2.2：LLM 可用性与缺口分析（并行化优化版）"""
    user_question = state["user_question"]
    analysis_dimensions = state.get("analysis_dimensions", [])
    templates = state.get("recommended_templates", [])
    writer = get_stream_writer()

    writer({"stage": "analyze_templates", "step": "analyzing", "status": "in_progress", "template_count": len(templates)})

    if not templates:
        writer({"stage": "analyze_templates", "step": "completed", "status": "done"})
        return {"recommended_templates": []}

    max_workers = min(len(templates), 5)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(
                _analyze_template_parallel, template, user_question, analysis_dimensions
            ): i
            for i, template in enumerate(templates)
        }

        try:
            for future in as_completed(future_to_idx, timeout=config.LLM_BATCH_TIMEOUT_SECONDS):
                idx = future_to_idx[future]
                result = future.result()  # 失败直接抛出，不捕获
                templates[idx]["usability"] = result["usability"]
                writer({
                    "stage": "analyze_templates",
                    "step": "analyzing_template",
                    "status": "in_progress",
                    "template_index": idx + 1,
                    "template_alias": templates[idx].get("template_alias", ""),
                })
        except FuturesTimeoutError:
            raise LLMCallError("LLM调用失败：模板分析批次超时")

    writer({"stage": "analyze_templates", "step": "completed", "status": "done"})
    return {"recommended_templates": templates}


# ═══════════════════════════════════════════════════════════════════════
# 用户交互节点
# ═══════════════════════════════════════════════════════════════════════

def wait_for_confirmation(state: AgentState) -> dict:
    """等待用户确认分析维度"""
    writer = get_stream_writer()

    if state.get("is_low_confidence"):
        # 低置信度中断：展示维度选择界面（含收敛标记），让用户自选
        writer({"stage": "wait_for_confirmation", "step": "low_confidence", "status": "interrupted"})
        pending_conf = state.get("pending_confirmation", {})
        # 合并低置信度提示和维度确认数据
        interrupt_data = {
            "type": "low_confidence",
            "message": state.get("low_confidence_message", ""),
            "suggestions": state.get("low_confidence_suggestions", []),
            # 维度选择相关字段（前端据此渲染维度勾选界面）
            "filter_display": pending_conf.get("filter_display", []),
            "dimension_options": pending_conf.get("dimension_options", []),
            "normalized_question": pending_conf.get("normalized_question", ""),
            "action_required": "请选择要进入分析的维度（可多选），然后点击继续；或修改问题后重新提交",
        }
        user_input = interrupt(interrupt_data)

        # 读取用户实际选择的维度（用户可能只勾选收敛维度）
        confirmed_dimensions = user_input.get("confirmed_dimensions", [])
        confirmed_question = user_input.get(
            "confirmed_question", state.get("normalized_question", state.get("user_question", ""))
        )
    else:
        # 正常确认流程
        writer({"stage": "wait_for_confirmation", "step": "waiting_confirmation", "status": "in_progress"})
        user_input = interrupt(state.get("pending_confirmation"))

        confirmed_dimensions = user_input.get("confirmed_dimensions", [])
        confirmed_question = user_input.get(
            "confirmed_question", state.get("normalized_question", "")
        )

    # 两种情况统一处理：过滤 analysis_dimensions，只保留用户确认的维度
    filtered_dimensions = [
        d for d in state.get("analysis_dimensions", [])
        if d.get("search_term") in confirmed_dimensions
    ]

    user_confirmation: UserConfirmation = {
        "confirmed_dimensions": confirmed_dimensions,
        "confirmed_question": confirmed_question,
    }

    return {
        "analysis_dimensions": filtered_dimensions,
        "normalized_question": confirmed_question,
        "pending_confirmation": None,
        "user_confirmation": user_confirmation,
    }


# ═══════════════════════════════════════════════════════════════════════
# 完成节点
# ═══════════════════════════════════════════════════════════════════════

def format_output(state: AgentState) -> dict:
    """整理最终输出（仅结构化数据，markdown 为空，快速返回）"""
    writer = get_stream_writer()
    writer({"stage": "format_output", "step": "generating", "status": "in_progress"})

    # 追加本轮对话到历史
    history = list(state.get("conversation_history", []))
    history.append({
        "round": len(history) + 1,
        "user_question": state["user_question"],
        "normalized_question": state.get("normalized_question", ""),
        "filter_indicators": state.get("filter_indicators", []),
        "analysis_dimensions": [
            {  # 只保留 top3 指标摘要
                "search_term": d["search_term"],
                "converged": d["converged"],
                "indicators": d["indicators"][:3],
            }
            for d in state.get("analysis_dimensions", [])
        ],
    })

    # 构建 final_output（仅结构化数据，markdown 为空）
    final_output = {
        "user_question": state["user_question"],
        "normalized_question": state.get("normalized_question", ""),
        "filter_indicators": state.get("filter_indicators", []),
        "analysis_dimensions": state.get("analysis_dimensions", []),
        "is_low_confidence": state.get("is_low_confidence", False),
        "conversation_round": len(history),
        "recommended_themes": [
            {
                "theme_id": t["theme_id"],
                "theme_alias": t["theme_alias"],
                "theme_level": t["theme_level"],
                "is_supported": t["is_supported"],
                "support_reason": t["support_reason"],
                "selected_filter_indicators": t["selected_filter_indicators"],
                "selected_analysis_indicators": t["selected_analysis_indicators"],
            }
            for t in state.get("recommended_themes", [])
        ],
        "recommended_templates": [
            {
                "template_id": t["template_id"],
                "template_alias": t["template_alias"],
                "template_description": t.get("template_description", ""),
                "theme_alias": t.get("theme_alias", ""),
                "usage_count": t.get("usage_count", 0),
                "coverage_ratio": t.get("coverage_ratio", 0),
                "has_qualified_templates": t.get("has_qualified_templates", False),
                "fallback_reason": t.get("fallback_reason", ""),
                "usability": t.get("usability", {}),
            }
            for t in state.get("recommended_templates", [])
        ],
        "iteration_info": {
            "rounds": state.get("iteration_round", 0),
            "log": state.get("iteration_log", []),
        },
        "markdown": "",  # 为空，快速返回
    }

    # 立即推送 final 事件（不等待 LLM）
    writer({
        "stage": "format_output",
        "step": "completed",
        "status": "done",
        "final": final_output
    })

    return {
        "final_output": final_output,
        "conversation_history": history,
    }


# ═══════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════
# 分析维度勾选引导
# ═══════════════════════════════════════════════════════════════════════


def _compute_jaccard_similarity(set_a: set, set_b: set) -> float:
    """计算两个集合的 Jaccard 相似度"""
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _generate_dimension_guidance(
    user_question: str,
    analysis_dimensions: list[dict],
) -> dict | None:
    """阶段 0.4：生成分析维度勾选引导（基于 Neo4j theme_id 的 Jaccard 检测）

    1. 收集所有维度的指标 ID
    2. 批量查询 Neo4j 获取每个指标的 theme_id 集合
    3. 计算维度两两之间的 Jaccard 相似度
    4. 将主题信息注入 Prompt，调用 LLM 生成最终引导
    """

    if not analysis_dimensions or len(analysis_dimensions) < 2:
        return None

    # Step 1: 收集所有维度的指标 ID（取 Top-20 以内，避免过多）
    dim_indicator_map: dict[str, list[dict]] = {}  # dim_name -> indicators
    all_indicator_ids: list[str] = []
    for dim in analysis_dimensions:
        dim_name = dim.get("search_term", dim.get("dimension", ""))
        indicators = dim.get("indicators", [])
        dim_indicator_map[dim_name] = indicators
        for ind in indicators[:20]:
            if ind.get("id"):
                all_indicator_ids.append(ind["id"])

    if not all_indicator_ids:
        return None

    # Step 2: 批量查询 Neo4j 获取 theme_id
    theme_mapping = theme_tools.batch_get_indicator_themes(all_indicator_ids)

    # Step 3: 构建每个维度的主题集合
    dim_themes: dict[str, list[dict]] = {}  # dim_name -> [{"id": theme_id, "alias": theme_alias}]
    for dim_name, indicators in dim_indicator_map.items():
        theme_list: list[dict] = []
        seen_theme_ids: set[str] = set()
        for ind in indicators[:20]:
            ind_id = ind.get("id")
            if ind_id and ind_id in theme_mapping:
                for theme in theme_mapping[ind_id]:
                    if theme["id"] not in seen_theme_ids:
                        seen_theme_ids.add(theme["id"])
                        theme_list.append(theme)
        dim_themes[dim_name] = theme_list

    # Step 4: 计算 Jaccard 相似度矩阵
    dim_names = list(dim_themes.keys())
    jaccard_matrix: dict[str, dict[str, float]] = {}
    for i, dim_a in enumerate(dim_names):
        jaccard_matrix[dim_a] = {}
        for j, dim_b in enumerate(dim_names):
            if i == j:
                jaccard_matrix[dim_a][dim_b] = 1.0
            else:
                themes_a = {t["id"] for t in dim_themes[dim_a]}
                themes_b = {t["id"] for t in dim_themes[dim_b]}
                jaccard_matrix[dim_a][dim_b] = _compute_jaccard_similarity(themes_a, themes_b)

    # Step 5: 构建分析维度字符串（含真实 theme 信息）
    analysis_dimensions_str_parts = []
    for dim in analysis_dimensions:
        dim_name = dim.get("search_term", dim.get("dimension", ""))
        indicators = dim.get("indicators", [])
        themes = dim_themes.get(dim_name, [])

        # Top-5 指标
        top_inds = indicators[:5]
        ind_lines = []
        for ind in top_inds:
            alias = ind.get("alias", "")
            score = ind.get("similarity_score", 0)
            desc = ind.get("description", "")
            ind_lines.append(f"  - {alias}（相似度: {score:.2f}）描述: {desc}")

        # 命中的主题列表
        theme_lines = []
        for t in themes:
            theme_lines.append(f"  - [{t['id']}] {t['alias']}")

        part = f"""分析维度：「{dim_name}」
  命中主题数: {len(themes)}
  命中主题列表:
{chr(10).join(theme_lines) if theme_lines else "  （无主题信息）"}
  关联指标 Top-5:
{chr(10).join(ind_lines)}"""
        analysis_dimensions_str_parts.append(part)

    analysis_dimensions_str = "\n\n".join(analysis_dimensions_str_parts)

    # Step 6: 构建 dimensions_str（旧版兼容，提供 Jaccard 矩阵摘要）
    dimensions_str_parts = []
    for i, dim_a in enumerate(dim_names):
        row = [f"{dim_a} vs {dim_b}: Jaccard={jaccard_matrix[dim_a][dim_b]:.2f}"
               for j, dim_b in enumerate(dim_names) if i < j]
        dimensions_str_parts.extend(row)

    dimensions_str = "\n".join(dimensions_str_parts) if dimensions_str_parts else "（仅一个维度）"

    # Step 7: 调用 LLM 生成引导
    try:
        guidance = llm_client.generate_dimension_selection_guidance(
            user_question=user_question,
            dimensions_str=dimensions_str,
            analysis_dimensions_str=analysis_dimensions_str,
        )
        # 转换为 dict（兼容直接返回）
        result = {
            "has_conflict": guidance.has_conflict,
            "recommended_first": guidance.recommended_first,
            "conflict_analysis": guidance.conflict_analysis,
            "selection_advice": guidance.selection_advice,
            "dimension_analysis": [
                {
                    "dimension": item.dimension,
                    "matched_themes": [t["alias"] for t in dim_themes.get(item.dimension, [])],
                    "theme_count": len(dim_themes.get(item.dimension, [])),
                    "independence_score": item.independence_score,
                    "core_concept_score": item.core_concept_score,
                    "recommendation": item.recommendation,
                }
                for item in guidance.dimension_analysis
            ],
            # 附加调试信息（供后续记录）
            "_jaccard_matrix": jaccard_matrix,
            "_dim_themes": dim_themes,
        }
        logger.info(f"维度勾选引导生成完成: has_conflict={result['has_conflict']}, "
                    f"推荐优先={result['recommended_first']}")
        return result
    except Exception as e:
        logger.warning(f"维度勾选引导 LLM 调用失败: {e}，跳过引导")
        return None


def _build_search_results_str(search_results: dict[str, list]) -> str:
    """构建搜索结果字符串（保留，用于低置信度等场景）"""
    lines = []
    for concept, indicators in search_results.items():
        lines.append(f"分析概念：「{concept}」")
        if not indicators:
            lines.append("  （无匹配结果）")
        else:
            for ind in indicators[:5]:
                score = ind.get("similarity_score", 0)
                alias = ind.get("alias", "")
                desc = ind.get("description", "")
                lines.append(f"  - {alias}（相似度: {score:.2f}）描述: {desc}")
        lines.append("")
    return "\n".join(lines)


def _build_pending_search_results_str(pending_concepts: dict[str, list]) -> str:
    """构建未收敛概念的搜索结果字符串（用于 LLM 精炼输入）"""
    lines = []
    for concept, indicators in pending_concepts.items():
        top1_score = indicators[0]["similarity_score"] if indicators else 0.0
        lines.append(f"分析概念：「{concept}」（Top-1 相似度: {top1_score:.2f}）")
        if not indicators:
            lines.append("  （无匹配结果）")
        else:
            for ind in indicators[:5]:
                score = ind.get("similarity_score", 0)
                alias = ind.get("alias", "")
                desc = ind.get("description", "")
                lines.append(f"  - {alias}（相似度: {score:.2f}）描述: {desc}")
        lines.append("")
    return "\n".join(lines)


def _build_converged_concepts_str(converged_dimensions: dict[str, list]) -> str:
    """构建已收敛概念的列表字符串（用于 LLM 参考）"""
    if not converged_dimensions:
        return "（暂无已收敛概念）"
    lines = []
    for concept in converged_dimensions.keys():
        lines.append(f"- {concept}")
    return "\n".join(lines)


def _build_filter_phrases_str(filter_indicators: list) -> str:
    """构建筛选条件的描述字符串（用于规范化问题生成）"""
    if not filter_indicators:
        return "（无筛选条件）"
    lines = []
    for f in filter_indicators:
        alias = f.get("alias", "")
        value = f.get("value", "")
        lines.append(f"- {alias} = {value}")
    return "\n".join(lines)


def _build_analysis_dimensions_str(analysis_dimensions: list) -> str:
    """构建分析维度字符串"""
    return "\n".join(
        f"- 「{d['search_term']}」关联指标: {[i['alias'] for i in d['indicators'][:5]]}"
        for d in analysis_dimensions
    )


def _build_filter_indicators_str(filter_inds: list) -> str:
    """构建筛选指标字符串"""
    if not filter_inds:
        return "（无）"
    return "\n".join(
        f"- {ind.get('alias', '')}（类型: {'时间筛选指标' if '数据日期' in ind.get('alias', '') else '机构筛选指标'}）"
        for ind in filter_inds
    )


def _build_analysis_indicators_str(analysis_inds: list) -> str:
    """构建分析指标字符串"""
    if not analysis_inds:
        return "（无）"
    return "\n".join(
        f"- {ind.get('alias', '')}"
        for ind in analysis_inds[:50]
    )


# ═══════════════════════════════════════════════════════════════════════
# 独立的自然语言总结生成（异步）
# ═══════════════════════════════════════════════════════════════════════

def generate_summary(state: AgentState) -> dict:
    """基于 final 结构化数据生成详细文字总结（不调用 LLM）"""
    writer = get_stream_writer()

    # 获取数据
    user_question = state.get("user_question", "")
    normalized = state.get("normalized_question", user_question)
    themes = state.get("recommended_themes", [])
    templates = state.get("recommended_templates", [])
    dimensions = state.get("analysis_dimensions", [])
    filters = state.get("filter_indicators", [])

    # 直接构建文字总结
    parts = []

    # 1. 需求概括
    parts.append(f"根据您的问题「{user_question}」，我为您分析了相关需求。")
    if normalized and normalized != user_question:
        parts.append(f"规范化后的分析需求为：{normalized}。")

    # 2. 筛选条件
    if filters:
        filter_strs = [f"{f.get('alias', '')}为「{f.get('value', '')}」" for f in filters]
        parts.append(f"自动识别的筛选条件：{', '.join(filter_strs)}。")

    # 3. 分析维度
    if dimensions:
        dim_parts = []
        for d in dimensions:
            search_term = d.get('search_term', '')
            indicators = d.get('indicators', [])
            if indicators:
                top_inds = [i.get('alias', '') for i in indicators[:3]]
                dim_parts.append(f"「{search_term}」（关联指标：{', '.join(top_inds)}）")
            else:
                dim_parts.append(f"「{search_term}」")
        parts.append(f"确认的分析维度包括：{'、'.join(dim_parts)}。")

    # 4. 推荐主题
    if themes:
        parts.append("关于主题推荐：")
        for i, t in enumerate(themes):
            theme_name = t.get('theme_alias', '')
            theme_path = t.get('theme_path', '')
            is_supported = t.get('is_supported', False)
            reason = t.get('support_reason', '')

            if i == 0:
                parts.append(f"首选推荐「{theme_name}」主题")
            else:
                status = "推荐" if is_supported else "作为备选"
                parts.append(f"同时{status}「{theme_name}」主题")

            if theme_path:
                parts.append(f"，位于{theme_path}")

            if reason:
                parts.append(f"。推荐理由：{reason}")

            # 选中指标
            filter_inds = t.get('selected_filter_indicators', [])
            analysis_inds = t.get('selected_analysis_indicators', [])

            if filter_inds:
                aliases = [ind.get('alias', '') for ind in filter_inds]
                parts.append(f"。该主题包含筛选指标：{', '.join(aliases)}")

            if analysis_inds:
                aliases = [ind.get('alias', '') for ind in analysis_inds[:5]]
                parts.append(f"；分析指标：{', '.join(aliases)}")

            parts.append("。")

    # 5. 推荐模板
    if templates:
        parts.append("关于模板推荐：")
        for t in templates:
            template_name = t.get('template_alias', '')
            coverage = t.get('coverage_ratio', 0) * 100
            usage = t.get('usage_count', 0)
            usability = t.get('usability', {})
            usability_summary = usability.get('usability_summary', '')

            parts.append(f"「{template_name}」模板（覆盖率 {coverage:.0f}%，使用 {usage} 次）")

            if usability_summary:
                parts.append(f"，{usability_summary}")

            # 缺口说明
            missing = usability.get('missing_indicator_analysis', [])
            if missing:
                missing_strs = []
                for m in missing[:2]:
                    alias = m.get('indicator_alias', '')
                    importance = m.get('importance', '')
                    if importance:
                        missing_strs.append(f"{alias}（{importance}）")
                    else:
                        missing_strs.append(alias)
                parts.append(f"。缺失指标：{', '.join(missing_strs)}")

            parts.append("。")
    else:
        parts.append("暂未找到匹配度较高的模板，您可以直接使用推荐的主题进行手动配置。")

    # 6. 下一步建议
    if themes:
        parts.append(f"建议您优先使用「{themes[0].get('theme_alias', '')}」主题进行分析，")
        if templates:
            parts.append(f"或直接使用「{templates[0].get('template_alias', '')}」模板快速开始。")
        else:
            parts.append("在主题中勾选需要的指标后开始分析。")

    summary = "".join(parts)
    writer({"stage": "summary", "content": summary})
    return {}


# ═══════════════════════════════════════════════════════════════════════
# LLM Markdown 生成辅助函数
# ═══════════════════════════════════════════════════════════════════════

def _build_filter_indicators_for_prompt(filter_indicators: list) -> str:
    """构建筛选条件的提示词字符串"""
    if not filter_indicators:
        return "（无自动识别的筛选条件）"

    lines = []
    for f in filter_indicators:
        alias = f.get("alias", "")
        value = f.get("value", "")
        lines.append(f"- {alias} = \"{value}\"")
    return "\n".join(lines)


def _build_analysis_dimensions_for_prompt(dimensions: list) -> str:
    """构建分析维度的提示词字符串"""
    if not dimensions:
        return "（无分析维度）"

    lines = []
    for d in dimensions:
        search_term = d.get("search_term", "")
        indicators = d.get("indicators", [])
        top_aliases = [i.get("alias", "") for i in indicators[:5]]
        lines.append(f"- 「{search_term}」")
        if top_aliases:
            lines.append(f"  关联指标：{'、'.join(top_aliases)}")
    return "\n".join(lines)


def _build_themes_for_prompt(themes: list) -> str:
    """构建推荐主题的提示词字符串（简洁版）"""
    if not themes:
        return "无"

    lines = []
    for i, t in enumerate(themes[:2]):  # 只取前2个
        theme_name = t.get('theme_alias', '')
        theme_path = t.get('theme_path', '')
        reason = t.get('support_reason', '')

        # 只保留核心信息
        lines.append(f"{i+1}. {theme_name}")
        if theme_path:
            lines.append(f"   路径: {theme_path}")
        if reason and len(reason) < 100:
            lines.append(f"   理由: {reason}")

    return "\n".join(lines)


def _build_templates_for_prompt(templates: list) -> str:
    """构建推荐模板的提示词字符串（简洁版）"""
    if not templates:
        return "无"
    lines = []
    for t in templates[:2]:  # 只取前2个
        coverage = t.get("coverage_ratio", 0)
        usage = t.get("usage_count", 0)
        summary = t.get("usability", {}).get("usability_summary", "")
        lines.append(f"- {t.get('template_alias', '')}（热度{usage}, 覆盖率{coverage*100:.0f}%）")
        if summary:
            lines.append(f"  {summary}")
    return "\n".join(lines)


def _fallback_markdown_output(state: AgentState) -> str:
    """LLM 失败时的兜底 Markdown 模板（简化版）"""
    user_question = state.get("user_question", "")
    normalized_question = state.get("normalized_question", user_question)

    lines = [
        "# 主题 & 模板推荐",
        "",
        f"**用户问题**：{user_question}",
        "",
        f"**规范化需求**：{normalized_question}",
        "",
    ]

    # 筛选条件
    filter_inds = state.get("filter_indicators", [])
    if filter_inds:
        lines.append("## 筛选条件")
        for f in filter_inds:
            lines.append(f"- {f.get('alias', '')} = \"{f.get('value', '')}\"")
        lines.append("")

    # 推荐主题
    themes = state.get("recommended_themes", [])
    if themes:
        lines.append("## 推荐主题")
        for t in themes:
            if t.get("is_supported"):
                lines.append(f"- **{t.get('theme_alias', '')}**")
                lines.append(f"  路径：{t.get('theme_path', '')}")
        lines.append("")

    # 推荐模板
    templates = state.get("recommended_templates", [])
    if templates:
        lines.append("## 推荐模板")
        for t in templates:
            coverage = t.get("coverage_ratio", 0)
            lines.append(f"- **{t.get('template_alias', '')}** (覆盖率: {coverage * 100:.0f}%)")
        lines.append("")

    return "\n".join(lines)


# 可用性 emoji 映射（保留，可能在其他地方使用）
USABILITY_EMOJI = {
    "可直接使用": "✅",
    "补充后可用": "🔧",
    "缺口较大建议谨慎": "⚠️",
}


def _build_template_indicators_str(template_inds: list) -> str:
    """构建模板指标字符串"""
    if not template_inds:
        return "（无）"
    return "\n".join(
        f"- {ind.get('alias', '')}：{ind.get('description', '')}"
        for ind in template_inds
    )
