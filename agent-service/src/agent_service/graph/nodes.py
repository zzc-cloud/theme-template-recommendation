"""
LangGraph 节点函数
每个阶段对应一个节点函数
使用结构化输出 (with_structured_output) 替代手动 JSON 解析
"""

import logging
import time
from typing import Any

from langgraph.config import get_stream_writer

from .. import config
from ..llm import client as llm_client
from ..llm import prompts as llm_prompts
from ..tools import template_tools, theme_tools, vector_search
from .state import (
    AgentState,
    FilterIndicator,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# 阶段 0 节点
# ═══════════════════════════════════════════════════════════════════════

def extract_phrases(state: AgentState) -> dict:
    """阶段 0.1：提取原始词组"""
    user_question = state["user_question"]
    writer = get_stream_writer()
    writer({"stage": "extract_phrases", "step": "extracting_phrases", "status": "in_progress"})

    result = llm_client.extract_phrases(user_question)
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

    # 构建筛选指标
    filter_indicators: list[FilterIndicator] = []
    for pf in filter_phrases:
        filter_indicators.append({
            "indicator_id": "",
            "value": pf,
            "alias": pf,
            "type": "机构筛选指标",
        })

    # ── 0.3 迭代精炼 ──
    search_results: dict[str, list] = {}
    iteration_log = []
    iteration_round = 0
    current_concepts = list(analysis_concepts)

    while iteration_round < config.MAX_ITERATION_ROUNDS and current_concepts:
        iteration_round += 1

        round_search_results: dict[str, list] = {}

        writer({"stage": "classify_and_iterate", "step": "searching", "status": "in_progress", "round": iteration_round, "concepts": current_concepts})

        # 对每个分析概念执行向量搜索
        for concept in current_concepts:
            result = vector_search.search_indicators_by_vector(
                concept, top_k=config.VECTOR_SEARCH_TOP_K
            )
            if result.get("success"):
                round_search_results[concept] = result.get("indicators", [])
                # 合并到总结果（去重）
                if concept not in search_results:
                    search_results[concept] = []
                existing_ids = {i["id"] for i in search_results[concept]}
                for ind in result.get("indicators", []):
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
    return {
        "filter_indicators": filter_indicators,
        "search_results": search_results,
        "iteration_round": iteration_round,
        "iteration_log": iteration_log,
        "analysis_dimensions": analysis_dimensions,
        "normalized_question": norm_question,
        "is_low_confidence": is_low_confidence,
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
    """阶段 1.3：LLM 裁决 - 判断主题可用性 + 指标精筛"""
    user_question = state["user_question"]
    analysis_dimensions = state.get("analysis_dimensions", [])
    candidate_themes = state.get("candidate_themes", [])
    writer = get_stream_writer()

    writer({"stage": "judge_themes", "step": "judging", "status": "in_progress", "theme_count": len(candidate_themes)})
    recommended_themes = []

    for i, theme in enumerate(candidate_themes):
        writer({"stage": "judge_themes", "step": "judging_theme", "status": "in_progress", "theme_index": i + 1, "theme_alias": theme.get("theme_alias", "")})
        theme_id = theme["theme_id"]
        theme_alias = theme["theme_alias"]
        theme_path = f"自主分析 > {theme_alias}"
        filter_inds = theme.get("filter_indicators_detail", [])
        analysis_inds = theme.get("analysis_indicators_detail", [])

        # 构建分析维度字符串
        dim_str = _build_analysis_dimensions_str(analysis_dimensions)

        # 构建筛选指标字符串
        filter_str = _build_filter_indicators_str(filter_inds)

        # 构建分析指标字符串
        analysis_str = _build_analysis_indicators_str(analysis_inds)

        # LLM 裁决（结构化输出）
        try:
            judgment = llm_client.judge_theme(
                user_question=user_question,
                analysis_dimensions_str=dim_str,
                theme_alias=theme_alias,
                theme_path=theme_path,
                filter_indicators_str=filter_str,
                analysis_indicators_str=analysis_str,
            )
        except Exception as e:
            logger.warning(f"主题裁决失败 {theme_alias}: {e}")
            judgment = None

        if judgment:
            recommended_themes.append({
                "theme_id": theme_id,
                "theme_alias": theme_alias,
                "theme_level": theme.get("theme_level", 0),
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
        else:
            recommended_themes.append({
                "theme_id": theme_id,
                "theme_alias": theme_alias,
                "theme_level": theme.get("theme_level", 0),
                "is_supported": False,
                "support_reason": "裁决失败",
                "selected_filter_indicators": [],
                "selected_analysis_indicators": [],
                "unsupported_dimensions": [],
            })

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
    """阶段 2.2：LLM 可用性与缺口分析"""
    user_question = state["user_question"]
    analysis_dimensions = state.get("analysis_dimensions", [])
    templates = state.get("recommended_templates", [])
    writer = get_stream_writer()

    writer({"stage": "analyze_templates", "step": "analyzing", "status": "in_progress", "template_count": len(templates)})
    dim_str = _build_analysis_dimensions_str(analysis_dimensions)

    for i, template in enumerate(templates):
        writer({"stage": "analyze_templates", "step": "analyzing_template", "status": "in_progress", "template_index": i + 1, "template_alias": template.get("template_alias", "")})
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

        # LLM 可用性分析（结构化输出）
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
            template["usability"] = usability.model_dump()
        except Exception as e:
            logger.warning(f"模板可用性分析失败 {template_alias}: {e}")
            template["usability"] = {
                "template_id": template_id,
                "overall_usability": "缺口较大建议谨慎",
                "usability_summary": f"分析失败: {e}",
                "missing_indicator_analysis": [],
            }

    writer({"stage": "analyze_templates", "step": "completed", "status": "done"})
    return {"recommended_templates": templates}


# ═══════════════════════════════════════════════════════════════════════
# 完成节点
# ═══════════════════════════════════════════════════════════════════════

def format_output(state: AgentState) -> dict:
    """整理最终输出"""
    return {
        "final_output": {
            "user_question": state["user_question"],
            "normalized_question": state.get("normalized_question", ""),
            "filter_indicators": state.get("filter_indicators", []),
            "analysis_dimensions": state.get("analysis_dimensions", []),
            "is_low_confidence": state.get("is_low_confidence", False),
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
        }
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


def _build_template_indicators_str(template_inds: list) -> str:
    """构建模板指标字符串"""
    if not template_inds:
        return "（无）"
    return "\n".join(
        f"- {ind.get('alias', '')}：{ind.get('description', '')}"
        for ind in template_inds
    )
