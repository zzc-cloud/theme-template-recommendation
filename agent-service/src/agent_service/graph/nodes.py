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

    missing_ids = template.get("missing_indicator_ids", [])
    missing_inds_str = "（无需补充）"
    if missing_ids:
        missing_inds_str = "\n".join(f"- {mid}" for mid in missing_ids[:10])

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
    """阶段 0.2-0.3：词组分类 + 迭代精炼"""
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

    # ── 0.3 迭代精炼 ──
    search_results: dict[str, list] = {}
    iteration_log = []
    iteration_round = 0
    current_concepts = list(analysis_concepts)

    while iteration_round < config.MAX_ITERATION_ROUNDS and current_concepts:
        iteration_round += 1

        round_search_results: dict[str, list] = {}

        writer({"stage": "classify_and_iterate", "step": "searching", "status": "in_progress", "round": iteration_round, "concepts": current_concepts})

        # 使用并行搜索辅助函数
        round_search_results = _search_concepts_parallel(
            current_concepts, top_k=config.VECTOR_SEARCH_TOP_K
        )

        # 合并到总结果（去重）
        for concept, indicators in round_search_results.items():
            if concept not in search_results:
                search_results[concept] = []
            existing_ids = {i["id"] for i in search_results[concept]}
            for ind in indicators:
                if ind["id"] not in existing_ids:
                    search_results[concept].append(ind)

        # 构建搜索结果字符串用于 LLM 评估
        search_results_str = _build_search_results_str(search_results)

        writer({"stage": "classify_and_iterate", "step": "evaluating", "status": "in_progress", "round": iteration_round})

        # LLM 评估（结构化输出）
        try:
            evaluation = llm_client.evaluate_iteration(
                user_question=user_question,
                round_num=iteration_round,
                max_rounds=config.MAX_ITERATION_ROUNDS,
                search_results_str=search_results_str,
            )
        except Exception as e:
            logger.warning(f"迭代评估失败: {e}")
            evaluation = None

        if evaluation:
            converged = evaluation.converged
            normalized_question = evaluation.normalized_question
            corrections = evaluation.corrections
            low_conf_concepts = evaluation.low_confidence_concepts
        else:
            converged = True
            normalized_question = user_question
            corrections = []
            low_conf_concepts = []

        iteration_log.append({
            "round": iteration_round,
            "search_results": dict(round_search_results),
            "evaluation": evaluation.model_dump() if evaluation else None,
        })

        if converged:
            break

        # 生成下一轮搜索词
        new_concepts = []
        for corr in corrections:
            new_concepts.extend(corr.corrected)

        if not new_concepts or set(new_concepts) == set(current_concepts):
            break

        current_concepts = new_concepts

    # 构建分析维度
    analysis_dimensions = []
    for concept, indicators in search_results.items():
        top1_score = indicators[0]["similarity_score"] if indicators else 0.0
        analysis_dimensions.append({
            "search_term": concept,
            "converged": top1_score >= config.CONVERGENCE_SIMILARITY_THRESHOLD,
            "indicators": indicators,
        })

    # 低置信度检查
    low_confidence = any(
        d["indicators"] and d["indicators"][0]["similarity_score"] < config.LOW_CONFIDENCE_THRESHOLD
        for d in analysis_dimensions
        if d["indicators"]
    )
    is_low_confidence = low_confidence and iteration_round >= config.MAX_ITERATION_ROUNDS

    # 规范化问题
    if evaluation and evaluation.normalized_question:
        norm_question = evaluation.normalized_question
    else:
        norm_question = user_question

    writer({"stage": "classify_and_iterate", "step": "completed", "status": "done", "iterations": iteration_round})

    # ── 出口判断：低置信度 vs 正常收敛 ──
    if is_low_confidence:
        # 低置信度出口
        low_conf_concepts = [
            d["search_term"]
            for d in analysis_dimensions
            if d["indicators"] and d["indicators"][0]["similarity_score"] < config.LOW_CONFIDENCE_THRESHOLD
        ]
        search_results_str = _build_search_results_str(search_results)
        try:
            low_conf_result = llm_client.handle_low_confidence(
                user_question=user_question,
                low_confidence_concepts=low_conf_concepts,
                search_results_str=search_results_str,
            )
            low_confidence_message = low_conf_result.user_message
            low_confidence_suggestions = low_conf_result.suggestions
        except Exception as e:
            logger.warning(f"低置信度处理失败: {e}")
            low_confidence_message = "以下分析概念无法精确匹配，可能需要更清晰的描述："
            low_confidence_suggestions = []

        return {
            "filter_indicators": filter_indicators,
            "search_results": search_results,
            "iteration_round": iteration_round,
            "iteration_log": iteration_log,
            "analysis_dimensions": analysis_dimensions,
            "normalized_question": norm_question,
            "is_low_confidence": is_low_confidence,
            "low_confidence_message": low_confidence_message,
            "low_confidence_suggestions": low_confidence_suggestions,
            "pending_confirmation": None,
            "user_confirmation": None,
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
            "normalized_question": norm_question,
            "message": "以下筛选条件已自动识别，请确认分析维度：",
        }
        return {
            "filter_indicators": filter_indicators,
            "search_results": search_results,
            "iteration_round": iteration_round,
            "iteration_log": iteration_log,
            "analysis_dimensions": analysis_dimensions,
            "normalized_question": norm_question,
            "is_low_confidence": False,
            "pending_confirmation": pending_confirmation,
            "user_confirmation": None,
            "low_confidence_message": "",
            "low_confidence_suggestions": [],
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

        # 收集用户需要的指标 ID
        matched_indicator_ids = []

        for ind in theme.get("selected_filter_indicators", []):
            if ind.get("indicator_id"):
                matched_indicator_ids.append(ind["indicator_id"])

        for ind in theme.get("selected_analysis_indicators", []):
            if ind.get("indicator_id"):
                matched_indicator_ids.append(ind["indicator_id"])

        if not matched_indicator_ids:
            continue

        result = template_tools.get_theme_templates_with_coverage(
            theme_id=theme_id,
            matched_indicator_ids=matched_indicator_ids,
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
        # 低置信度中断
        writer({"stage": "wait_for_confirmation", "step": "low_confidence", "status": "interrupted"})
        user_input = interrupt({
            "type": "low_confidence",
            "message": state.get("low_confidence_message", ""),
            "suggestions": state.get("low_confidence_suggestions", []),
        })
        # 用户选择继续时，使用当前状态继续执行
        confirmed_dimensions = [
            d.get("search_term")
            for d in state.get("analysis_dimensions", [])
        ]
        confirmed_question = state.get("normalized_question", state.get("user_question", ""))
    else:
        # 正常确认流程
        writer({"stage": "wait_for_confirmation", "step": "waiting_confirmation", "status": "in_progress"})
        user_input = interrupt(state.get("pending_confirmation"))

        confirmed_dimensions = user_input.get("confirmed_dimensions", [])
        confirmed_question = user_input.get("confirmed_question", state.get("normalized_question", ""))

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
    """整理最终输出"""
    writer = get_stream_writer()
    writer({"stage": "format_output", "step": "generating", "status": "in_progress"})

    # 生成 Markdown 格式的推荐结果
    markdown_output = _format_markdown_output(state)

    writer({"stage": "format_output", "step": "completed", "status": "done", "markdown": markdown_output})

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
        "markdown": markdown_output,
    }

    return {
        "final_output": final_output,
        "conversation_history": history,
    }


# ═══════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════

def _build_search_results_str(search_results: dict[str, list]) -> str:
    """构建搜索结果字符串"""
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


# 可用性 emoji 映射
USABILITY_EMOJI = {
    "可直接使用": "✅",
    "补充后可用": "🔧",
    "缺口较大建议谨慎": "⚠️",
}


def _format_markdown_output(state: AgentState) -> str:
    """生成 Markdown 格式的推荐结果（对齐 SKILL.md 输出规范）"""
    lines = []
    user_question = state.get("user_question", "")
    normalized_question = state.get("normalized_question", "")
    filter_indicators = state.get("filter_indicators", [])
    analysis_dimensions = state.get("analysis_dimensions", [])
    recommended_themes = state.get("recommended_themes", [])
    recommended_templates = state.get("recommended_templates", [])

    # ── 标题框 ──────────────────────────────────────────────
    lines += [
        "╔═══════════════════════════════════════════════════════════════════╗",
        "║                      主题&模板推荐                                  ║",
        "╠═══════════════════════════════════════════════════════════════════╣",
        f'║ 用户问题: "{user_question}"',
        "╚═══════════════════════════════════════════════════════════════════╝",
        "",
        "━" * 67,
        "",
    ]

    # ── 需求澄清区块 ─────────────────────────────────────────
    lines += [
        "┌───────────────────────────────────────────────────────────────────┐",
        "│ 📋 需求澄清 📋                                                      │",
        "├───────────────────────────────────────────────────────────────────┤",
        "│                                                                   │",
    ]
    if normalized_question:
        lines.append(f'│ 规范化需求（已确认）: "{normalized_question}"')
        lines.append("│")

    if filter_indicators:
        lines.append("│ 筛选条件（自动应用）：")
        for f in filter_indicators:
            alias = f.get("alias", "")
            value = f.get("value", "")
            lines.append(f'│   🏦 {alias} = "{value}"')
        lines.append("│")

    if analysis_dimensions:
        lines.append(f"│ 确认的分析维度 ({len(analysis_dimensions)}):")
        for dim in analysis_dimensions:
            search_term = dim.get("search_term", "")
            indicators = dim.get("indicators", [])
            top_aliases = [i.get("alias", "") for i in indicators[:5]]
            lines.append(f"│   ☑ {search_term}")
            if top_aliases:
                lines.append(f"│     └ 关联指标：{'、'.join(top_aliases)}")
        lines.append("│")

    lines += [
        "└───────────────────────────────────────────────────────────────────┘",
        "",
        "━" * 67,
        "",
    ]

    # ── 推荐主题区块 ─────────────────────────────────────────
    lines += [
        "┌───────────────────────────────────────────────────────────────────┐",
        "│ 📁 推荐主题 📁                                                      │",
        "├───────────────────────────────────────────────────────────────────┤",
        "│                                                                   │",
    ]

    medals = ["🥇 首选主题", "🥈 备选主题", "🥉 备选主题"]
    for i, theme in enumerate(recommended_themes):
        medal = medals[i] if i < len(medals) else f"  备选主题 {i+1}"
        theme_name = theme.get("theme_alias", "")
        theme_path = theme.get("theme_path", "")
        is_supported = theme.get("is_supported", True)

        if not is_supported:
            reason = theme.get("support_reason", "")
            lines.append(f"│ {medal}: {theme_name}")
            lines.append(f"│    路径: {theme_path}")
            lines.append(f"│    ⚠️ 不推荐：{reason}")
            lines.append("│")
            continue

        lines.append(f"│ {medal}: {theme_name}")
        lines.append(f"│    路径: {theme_path}")
        lines.append("│")

        filter_inds = theme.get("selected_filter_indicators", [])
        if filter_inds:
            lines.append("│    🔘 筛选指标:")
            for ind in filter_inds:
                lines.append(f"│    ☑ {ind.get('alias', '')}")
            lines.append("│")

        analysis_inds = theme.get("selected_analysis_indicators", [])
        if analysis_inds:
            lines.append("│    📊 分析指标（覆盖用户问题）:")
            for ind in analysis_inds:
                alias = ind.get("alias", "")
                reason = ind.get("reason", "")
                lines.append(f"│    ☑ {alias}")
                if reason:
                    lines.append(f"│      💡 {reason}")
            lines.append("│")

    if not recommended_themes:
        lines.append("│ 未找到匹配的主题")
        lines.append("│")

    lines += [
        "└───────────────────────────────────────────────────────────────────┘",
        "",
        "━" * 67,
        "",
    ]

    # ── 模板推荐区块 ─────────────────────────────────────────
    has_qualified = any(
        t.get("coverage_ratio", 0) >= 0.8
        for t in recommended_templates
    )

    lines += [
        "┌───────────────────────────────────────────────────────────────────┐",
    ]
    if has_qualified:
        lines.append("│ 📄 模板推荐结果（匹配度 ≥ 80%，按覆盖率排序） 📄                         │")
    else:
        lines.append("│ 📄 模板推荐结果 📄                                                   │")
    lines += [
        "├───────────────────────────────────────────────────────────────────┤",
        "│                                                                   │",
    ]

    if not recommended_templates:
        lines.append("│ 未找到匹配的模板")
        lines.append("│")
    elif not has_qualified and recommended_templates:
        lines += [
            "│ ⚠️ 未找到覆盖率 ≥ 80% 的达标模板 ⚠️",
            "│",
            "│ 以下为参考推荐（按覆盖率和热度排序）：",
            "│",
        ]

    for i, t in enumerate(recommended_templates, 1):
        usability = t.get("usability", {})
        overall = usability.get("overall_usability", "")
        emoji = USABILITY_EMOJI.get(overall, "📄")
        coverage_ratio = t.get("coverage_ratio", 0)
        coverage_count = t.get("coverage_count", 0)
        total_count = t.get("total_count", 0)
        usage_count = t.get("usage_count", 0)
        template_alias = t.get("template_alias", "")
        template_id = t.get("template_id", "")
        usability_summary = usability.get("usability_summary", "")
        missing_analyses = usability.get("missing_indicator_analysis", [])
        recommend_reason = t.get("recommend_reason", "")

        # 降级推荐时显示推荐理由
        fallback_reason = t.get("fallback_reason", "")
        if fallback_reason and not recommend_reason:
            recommend_reason = fallback_reason

        lines.append(f"│ {i}. {emoji} {template_alias}")
        lines.append(f"│    ID: {template_id}")
        # 优先使用 coverage_count/total_count，否则回退到 coverage_ratio
        if coverage_count and total_count:
            lines.append(
                f"│    热度: 🔥 {usage_count} 次使用 | "
                f"覆盖率: {coverage_count}/{total_count} ({coverage_ratio * 100:.0f}%)"
            )
        else:
            lines.append(
                f"│    热度: 🔥 {usage_count} 次使用 | "
                f"覆盖率: {coverage_ratio * 100:.0f}%"
            )
        if recommend_reason:
            lines.append(f"│    推荐理由: {recommend_reason}")
        if usability_summary:
            lines.append(f"│    可用性: {usability_summary}")

        # 缺口说明
        core_missing = [
            m for m in missing_analyses
            if m.get("importance") in ["核心", "辅助"]
        ]
        if core_missing:
            lines.append("│    缺口说明:")
            for m in core_missing:
                alias = m.get("indicator_alias", "")
                impact = m.get("impact", "")
                suggestion = m.get("supplement_suggestion", "")
                lines.append(f"│             缺少「{alias}」，{impact}")
                if suggestion and suggestion != "无":
                    lines.append(f"│             {suggestion}")
        lines.append("│")

    lines.append("└───────────────────────────────────────────────────────────────────┘")

    return "\n".join(lines)


def _build_template_indicators_str(template_inds: list) -> str:
    """构建模板指标字符串"""
    if not template_inds:
        return "（无）"
    return "\n".join(
        f"- {ind.get('alias', '')}：{ind.get('description', '')}"
        for ind in template_inds
    )
