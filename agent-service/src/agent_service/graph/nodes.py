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

        # Step 2：收敛判定（客观阈值）
        newly_converged: list[str] = []
        for concept in list(pending_concepts.keys()):
            indicators = pending_concepts[concept]
            top1_score = indicators[0]["similarity_score"] if indicators else 0.0
            if top1_score >= config.CONVERGENCE_SIMILARITY_THRESHOLD:
                # 确认收敛：移入 converged_dimensions
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

            # 意图锚定：处理 LLM 标记的偏离度过高的概念
            if refinement.deviation_warning:
                logger.info(f"意图偏离警告: {refinement.reasoning}")
                # 偏离的概念已被 LLM 标记，回退到原搜索词
                new_concepts = [
                    c if c != "DEVIATION_ABORT" else orig
                    for c, orig in zip(new_concepts, pending_concepts.keys())
                ]
                new_concepts = list(dict.fromkeys(new_concepts))  # 去重保序
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
    # 注意：normalized_question 延迟到 wait_for_confirmation 节点生成，基于用户最终确认的维度

    # Step A：出口判断
    is_low_confidence = bool(pending_concepts)

    # Step C：构建 analysis_dimensions
    analysis_dimensions = []
    for concept, indicators in converged_dimensions.items():
        analysis_dimensions.append({
            "search_term": concept,
            "converged": True,
            "deviation_warning": False,  # 新增：已收敛的概念默认无偏离
            "indicators": indicators,
        })
    for concept, indicators in pending_concepts.items():
        analysis_dimensions.append({
            "search_term": concept,
            "converged": False,
            "deviation_warning": False,
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
            "normalized_question": "",
            "message": "以下筛选条件已自动识别，请选择要分析的维度（已标注收敛状态）：",
            "dimension_guidance": dimension_guidance,
        }
        return {
            "filter_indicators": filter_indicators,
            "search_results": {**converged_dimensions, **pending_concepts},
            "iteration_round": iteration_round,
            "iteration_log": iteration_log,
            "analysis_dimensions": analysis_dimensions,
            "normalized_question": "",
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
            "normalized_question": "",
            "message": "以下筛选条件已自动识别，请确认分析维度：",
            "dimension_guidance": dimension_guidance,
        }
        return {
            "filter_indicators": filter_indicators,
            "search_results": converged_dimensions,
            "iteration_round": iteration_round,
            "iteration_log": iteration_log,
            "analysis_dimensions": analysis_dimensions,
            "normalized_question": "",
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

def navigate_hierarchy(state: AgentState) -> dict:
    """阶段 1.2：双路径并行探查 - 层级导航

    路径A（统计聚合）→ ─┐
                        ├──→ 候选主题合并去重
    路径B（层级导航）→ ─┘

    重构后的三阶段流程：
    1. 获取所有板块
    2. LLM 筛选相关板块
    3. 对每个选中板块 → 获取全量主题 → LLM 筛选 → 汇总

    与旧实现的区别：
    - 先筛选板块，避免盲目加载所有板块的主题
    - 每个板块获取全量主题（top_k=500），不做截断
    - 分板块独立筛选，保留结构信息
    """
    user_question = state["user_question"]
    analysis_dimensions = state.get("analysis_dimensions", [])
    writer = get_stream_writer()

    # Step 1: 获取所有板块
    writer({"stage": "navigate_hierarchy", "step": "fetching_sectors", "status": "in_progress"})
    sectors_result = theme_tools.get_sectors_from_root()
    if not sectors_result.get("success"):
        logger.warning(f"获取板块列表失败: {sectors_result.get('error')}")
        return {"navigation_path_themes": []}

    sectors = sectors_result.get("sectors", [])
    writer({
        "stage": "navigate_hierarchy",
        "step": "sectors_loaded",
        "sector_count": len(sectors),
    })

    # Step 2: LLM 筛选相关板块
    writer({"stage": "navigate_hierarchy", "step": "filtering_sectors", "status": "in_progress"})
    sector_list_parts = []
    for sector in sectors:
        sector_list_parts.append(
            f"- sector_id: {sector.get('id', '')} | "
            f"板块: {sector.get('alias', '')} | "
            f"路径: {sector.get('path', '')}"
        )
    sector_list_str = "\n".join(sector_list_parts)

    try:
        sector_filter_result = llm_client.filter_sectors_by_question(
            user_question=user_question,
            sector_list_str=sector_list_str,
        )
        selected_sectors = sector_filter_result.selected_sectors
        writer({
            "stage": "navigate_hierarchy",
            "step": "sectors_filtered",
            "selected_sector_count": len(selected_sectors),
        })
    except Exception as e:
        logger.warning(f"板块筛选 LLM 调用失败: {e}，回退到获取所有板块")
        selected_sectors = [
            type("S", (), {"sector_id": s.get("id", ""), "sector_alias": s.get("alias", ""), "sector_path": s.get("path", "")})()
            for s in sectors if s.get("id")
        ]

    if not selected_sectors:
        return {"navigation_path_themes": []}

    # 构建分析维度字符串（用于板块内主题筛选）
    dim_str = _build_analysis_dimensions_str(analysis_dimensions)
    if not dim_str:
        logger.warning(
            f"[层级导航] analysis_dimensions 为空，LLM 将仅基于用户问题「{user_question}」筛选主题，"
            f"筛选效果可能下降。请确认 resume 接口是否正确传递了 confirmed_dimensions。"
        )

    # Step 3: 对每个选中板块 → 获取全量主题 → LLM 筛选 → 汇总
    navigation_path_themes: list[dict] = []
    navigation_path_detail: list[dict] = []   # 每个板块的详情
    selected_sector_ids = {s.sector_id for s in selected_sectors}

    # ── Step 3: 分批并行处理板块（每批 5 个）──
    BATCH_SIZE = 5
    total_sectors = len(selected_sectors)
    total_batches = (total_sectors + BATCH_SIZE - 1) // BATCH_SIZE

    def process_sector(sector) -> dict | None:
        """在子线程中处理单个板块，返回结果字典"""
        sector_id = sector.sector_id
        if not sector_id or sector_id not in selected_sector_ids:
            return None

        sector_alias = sector.sector_alias

        # 获取板块下全量主题
        sector_themes_result = theme_tools.get_sector_themes(sector_id, top_k=500)
        if not isinstance(sector_themes_result, dict) or not sector_themes_result.get("success"):
            logger.warning(f"获取板块 {sector_alias} 主题失败: {sector_themes_result.get('error')}")
            return None

        sector_themes = sector_themes_result.get("themes", [])
        if not sector_themes:
            return None

        # 分块处理：如果主题过多，按每块 100 个进行 LLM 筛选
        theme_blocks = _chunk_themes_by_size(sector_themes, chunk_size=100)
        block_selected_ids: set[str] = set()
        total_blocks = len(theme_blocks)

        for block_idx, theme_block in enumerate(theme_blocks):
            block_str_parts = []
            for theme in theme_block:
                block_str_parts.append(
                    f"- 主题ID: {theme.get('theme_id', '')} | "
                    f"主题: {theme.get('theme_alias', '')} | "
                    f"路径: {theme.get('full_path', '')}"
                )
            block_str = "\n".join(block_str_parts)

            try:
                block_result = llm_client.filter_themes_by_hierarchy(
                    user_question=user_question,
                    analysis_dimensions_str=dim_str,
                    theme_list_str=block_str,
                )
                block_ids = {t.theme_id for t in block_result.selected_themes}
                block_selected_ids.update(block_ids)
                logger.info(
                    f"[层级导航] 板块「{sector_alias}」"
                    f"第{block_idx + 1}/{total_blocks}块筛选完成，"
                    f"选中 {len(block_ids)}/{len(theme_block)} 个"
                )
            except Exception as e:
                logger.warning(
                    f"[层级导航] 板块「{sector_alias}」"
                    f"第{block_idx + 1}/{total_blocks}块 LLM 筛选失败: {e}"
                )

        logger.info(
            f"[层级导航] 板块「{sector_alias}」筛选结束，"
            f"共 {len(sector_themes)} 个主题中选中 {len(block_selected_ids)} 个"
        )

        # 构建该板块筛选后的主题列表
        sector_selected_themes = []
        for theme in sector_themes:
            theme_id = theme.get("theme_id", "")
            if theme_id in block_selected_ids:
                theme_entry = {
                    "theme_id": theme_id,
                    "theme_alias": theme.get("theme_alias", ""),
                    "theme_level": theme.get("theme_level", 0),
                    "depth": theme.get("depth", 0),
                    "parent_alias": theme.get("parent_alias", ""),
                    "parent_type": theme.get("parent_type", ""),
                    "full_path": theme.get("full_path", ""),
                    "sector_id": sector_id,
                    "sector_alias": sector_alias,
                }
                sector_selected_themes.append(theme_entry)

        return {
            "sector_id": sector_id,
            "sector_alias": sector_alias,
            "sector_path": getattr(sector, "sector_path", ""),
            "total_themes": len(sector_themes),
            "selected_themes": sector_selected_themes,
        }

    # 分批并行执行
    batch_results: list[dict] = []
    for batch_idx in range(total_batches):
        batch_start = batch_idx * BATCH_SIZE
        batch_end = batch_start + BATCH_SIZE
        batch_sectors = selected_sectors[batch_start:batch_end]

        writer({
            "stage": "navigate_hierarchy",
            "step": "batch_start",
            "batch_idx": batch_idx + 1,
            "total_batches": total_batches,
            "sectors_in_batch": [s.sector_alias for s in batch_sectors],
        })

        # 并行处理当前批次内的所有板块
        current_batch_results: list[dict] = []
        with ThreadPoolExecutor(max_workers=len(batch_sectors)) as executor:
            futures = {executor.submit(process_sector, s): s for s in batch_sectors}
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        current_batch_results.append(result)
                        batch_results.append(result)
                except Exception as e:
                    sector_alias = futures[future].sector_alias
                    logger.warning(f"[层级导航] 板块「{sector_alias}」处理异常: {e}")

        # 发送批次完成的 progress 事件
        batch_total_selected = sum(len(r.get("selected_themes", [])) for r in current_batch_results)
        writer({
            "stage": "navigate_hierarchy",
            "step": "batch_completed",
            "batch_idx": batch_idx + 1,
            "total_batches": total_batches,
            "succeeded_sectors": len(current_batch_results),
            "failed_sectors": len(batch_sectors) - len(current_batch_results),
            "selected_in_batch": batch_total_selected,
        })

    # 汇总所有批次结果
    for result in batch_results:
        navigation_path_themes.extend(result.get("selected_themes", []))
        navigation_path_detail.append({
            "sector_id": result["sector_id"],
            "sector_alias": result["sector_alias"],
            "sector_path": result.get("sector_path", ""),
            "total_themes": result.get("total_themes", 0),
            "selected_themes": result.get("selected_themes", []),
        })

    writer({
        "stage": "navigate_hierarchy",
        "step": "completed",
        "selected_count": len(navigation_path_themes),
    })
    return {
        "navigation_path_themes": navigation_path_themes,
        "navigation_path_detail": navigation_path_detail,
    }


def _chunk_themes_by_size(themes: list[dict], chunk_size: int = 100) -> list[list[dict]]:
    """将主题列表分块，每块不超过 chunk_size 个"""
    return [themes[i:i + chunk_size] for i in range(0, len(themes), chunk_size)]

def merge_themes(state: AgentState) -> dict:
    """阶段 1.1.5: 合并去重 - 聚合路径 + 层级导航路径的结果合并

    合并策略：
    1. 以聚合路径 (candidate_themes) 为主，保留频次和 matched_indicator_ids
    2. 层级导航路径 (navigation_path_themes) 补充不在聚合结果中的主题
    3. 去重后按 weighted_frequency 降序排列
    4. 返回所有合并后的主题（不再固定数量）

    输出格式与 candidate_themes 一致，确保后续节点兼容
    """
    candidate_themes = state.get("candidate_themes", [])
    navigation_themes = state.get("navigation_path_themes", [])
    writer = get_stream_writer()

    writer({
        "stage": "merge_themes",
        "step": "merging",
        "aggregate_count": len(candidate_themes),
        "navigation_count": len(navigation_themes),
    })

    # 聚合路径结果按 theme_id 建立索引
    theme_map: dict = {}
    for theme in candidate_themes:
        theme_id = theme.get("theme_id", "")
        if theme_id:
            theme_map[theme_id] = {
                "theme_id": theme_id,
                "theme_alias": theme.get("theme_alias", ""),
                "theme_level": theme.get("theme_level", 0),
                "theme_path": theme.get("theme_path", ""),
                "frequency": theme.get("frequency", 0),
                "weighted_frequency": theme.get("weighted_frequency", 0.0),
                "matched_indicator_ids": theme.get("matched_indicator_ids", []),
                "source": "aggregate",
            }

    # 补充层级导航路径的主题
    nav_ids = set(theme_map.keys())
    for nav_theme in navigation_themes:
        theme_id = nav_theme.get("theme_id", "")
        if theme_id and theme_id not in nav_ids:
            theme_map[theme_id] = {
                "theme_id": theme_id,
                "theme_alias": nav_theme.get("theme_alias", ""),
                "theme_level": nav_theme.get("theme_level", 0),
                "theme_path": nav_theme.get("full_path", ""),
                "frequency": 0,
                "weighted_frequency": 0.0,
                "matched_indicator_ids": [],
                "source": "navigation",
            }

    # 按 weighted_frequency 降序排列，返回所有主题（不再限制数量）
    merged = sorted(
        theme_map.values(),
        key=lambda x: x.get("weighted_frequency", 0.0),
        reverse=True,
    )

    writer({
        "stage": "merge_themes",
        "step": "completed",
        "merged_count": len(merged),
    })

    # 返回 candidate_themes 格式，兼容后续节点
    return {"candidate_themes": merged}


def aggregate_themes(state: AgentState) -> dict:
    """阶段 1.1：聚合候选主题（相似度加权 + 动态阈值过滤）

    改进点（与 SKILL.md 对齐）：
    1. 按相似度加权频次（weighted_frequency = Σ indicator_similarity_score）
    2. 指标去重：同一 indicator_id 取最大相似度
    3. 动态选择：所有 weighted_frequency >= 阈值的 THEME 作为候选主题
    4. 兜底规则：若所有主题都 < 阈值，返回加权频次最高的主题
    """
    analysis_dimensions = state.get("analysis_dimensions", [])
    writer = get_stream_writer()

    writer({"stage": "aggregate_themes", "step": "aggregating", "status": "in_progress"})

    # Step 1: 构建指标 ID → 最大相似度的映射（去重）
    # 注意：similarity_score 在 IndicatorMatch 级别（即 ind["similarity_score"]）
    indicator_max_sim: dict[str, float] = {}
    for dim in analysis_dimensions:
        for ind in dim.get("indicators", []):
            ind_id = ind.get("id", "")
            if ind_id:
                sim_score = ind.get("similarity_score", 0.0)
                # 同一指标取最大相似度
                if ind_id not in indicator_max_sim or sim_score > indicator_max_sim[ind_id]:
                    indicator_max_sim[ind_id] = sim_score

    matched_indicators = list(indicator_max_sim.keys())

    if not matched_indicators:
        writer({"stage": "aggregate_themes", "step": "completed", "status": "done", "theme_count": 0})
        return {"candidate_themes": []}

    # 调用工具获取主题聚合结果（不限制数量，获取全部）
    result = theme_tools.aggregate_themes_from_indicators(
        matched_indicators, top_k=100  # 获取足够多的主题用于后续阈值过滤
    )

    if result.get("success"):
        candidate_themes = result.get("candidate_themes", [])

        # Step 2: 计算每个主题的 weighted_frequency
        for theme in candidate_themes:
            total_weight = 0.0
            for ind_id in theme.get("matched_indicator_ids", []):
                total_weight += indicator_max_sim.get(ind_id, 0.0)
            theme["weighted_frequency"] = round(total_weight, 4)

        # Step 3: 按 weighted_frequency 降序排列
        candidate_themes.sort(
            key=lambda x: x.get("weighted_frequency", 0.0),
            reverse=True,
        )

        # Step 4: 动态阈值过滤
        # 规则：所有 weighted_frequency >= 阈值的主题作为候选
        # 兜底：若都 < 阈值，返回 weighted_frequency 最高的主题
        threshold = config.THEME_WEIGHTED_FREQUENCY_THRESHOLD
        qualified_themes = [
            t for t in candidate_themes
            if t.get("weighted_frequency", 0.0) >= threshold
        ]

        if qualified_themes:
            # 有达标主题，取前 10 个（已按 weighted_frequency 降序）
            top_themes = qualified_themes[:10]
            writer({"stage": "aggregate_themes", "step": "completed", "status": "done", "theme_count": len(top_themes)})
            return {"candidate_themes": top_themes}
        else:
            # 无达标主题，返回 weighted_frequency 最高的一个（兜底）
            if candidate_themes:
                writer({"stage": "aggregate_themes", "step": "completed", "status": "done", "theme_count": 1})
                return {"candidate_themes": [candidate_themes[0]]}
            writer({"stage": "aggregate_themes", "step": "completed", "status": "done", "theme_count": 0})
            return {"candidate_themes": []}
    else:
        writer({"stage": "aggregate_themes", "step": "completed", "status": "done", "theme_count": 0})
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
    """阶段 1.3：LLM 裁决 - 判断主题可用性 + 指标精筛（分批执行优化版）"""
    user_question = state.get("user_question", "")
    analysis_dimensions = state.get("analysis_dimensions", [])
    candidate_themes = state.get("candidate_themes", [])
    writer = get_stream_writer()

    logger.info(f"[judge_themes] === 阶段1.3主题裁决开始 ===")
    logger.info(f"[judge_themes] 用户问题: {user_question}")
    logger.info(f"[judge_themes] 候选主题数量: {len(candidate_themes)}")
    for i, t in enumerate(candidate_themes):
        logger.info(f"[judge_themes]   主题{i+1}: {t.get('theme_alias','')} (id={t.get('theme_id','')}), "
                    f"weighted_freq={t.get('weighted_frequency',0):.4f}, "
                    f"source={t.get('source','unknown')}")

    writer({"stage": "judge_themes", "step": "judging", "status": "in_progress", "theme_count": len(candidate_themes)})

    if not candidate_themes:
        logger.info(f"[judge_themes] >>> 无候选主题，跳过裁决")
        writer({"stage": "judge_themes", "step": "completed", "status": "done"})
        return {"recommended_themes": []}

    recommended_themes = []
    batch_size = config.JUDGE_THEMES_BATCH_SIZE
    total_themes = len(candidate_themes)
    total_batches = (total_themes + batch_size - 1) // batch_size  # 向上取整

    # 分批处理，每批 batch_size 个主题
    for batch_idx in range(total_batches):
        start_idx = batch_idx * batch_size
        end_idx = min(start_idx + batch_size, total_themes)
        batch_themes = candidate_themes[start_idx:end_idx]

        logger.info(f"[主题裁决] 批次 {batch_idx + 1}/{total_batches}，处理主题 {start_idx + 1}-{end_idx}/{total_themes}")

        # 发送批次进度事件
        writer({
            "stage": "judge_themes",
            "step": "batch_progress",
            "status": "in_progress",
            "batch": batch_idx + 1,
            "total_batches": total_batches,
            "processed": start_idx,
            "total": total_themes,
        })

        # 并行处理当前批次
        max_workers = min(len(batch_themes), 3)
        batch_results = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_theme = {
                executor.submit(
                    _judge_theme_parallel, theme, user_question, analysis_dimensions
                ): theme
                for theme in batch_themes
            }

            try:
                for future in as_completed(future_to_theme, timeout=config.JUDGE_THEMES_BATCH_TIMEOUT_SECONDS):
                    result = future.result()
                    theme = future_to_theme[future]
                    batch_results.append((theme, result))
            except FuturesTimeoutError:
                # 批次超时，记录警告但继续处理已完成的结果
                logger.warning(f"[主题裁决] 批次 {batch_idx + 1}/{total_batches} 部分超时，已完成 {len(batch_results)}/{len(batch_themes)} 个")
                # 取消未完成的 futures
                for future in future_to_theme:
                    if not future.done():
                        future.cancel()

        # 处理批次结果
        for theme, result in batch_results:
            try:
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
            except Exception as e:
                logger.error(f"[主题裁决] 处理主题 {theme.get('theme_alias', theme['theme_id'])} 结果失败: {e}")
                # 继续处理其他结果，不中断整个流程

    writer({"stage": "judge_themes", "step": "completed", "status": "done", "processed": len(recommended_themes), "total": total_themes})
    return {"recommended_themes": recommended_themes}


# ═══════════════════════════════════════════════════════════════════════
# 阶段 2 节点
# ═══════════════════════════════════════════════════════════════════════

def retrieve_templates(state: AgentState) -> dict:
    """阶段 2.1：检索模板（带覆盖率计算）

    所有检索到的模板均加入结果（含覆盖率详情），
    以便在 final 事件中展示给调用方。
    """
    user_question = state.get("user_question", "")
    recommended_themes = state.get("recommended_themes", [])
    top_k = state.get("top_k_templates", 5)

    logger.info(f"[retrieve_templates] === 阶段2模板检索开始 ===")
    logger.info(f"[retrieve_templates] 用户问题: {user_question}")
    logger.info(f"[retrieve_templates] 推荐主题数量: {len(recommended_themes)}, top_k_templates={top_k}")

    all_templates = []
    template_search_detail: list[dict] = []  # 每个主题的模板检索详情

    for idx, theme in enumerate(recommended_themes):
        theme_id = theme.get("theme_id", "")
        theme_alias = theme.get("theme_alias", "")
        is_supported = theme.get("is_supported", False)
        theme_path = theme.get("theme_path", "")
        selected_filter = theme.get("selected_filter_indicators", [])
        selected_analysis = theme.get("selected_analysis_indicators", [])

        logger.info(f"[retrieve_templates] --- 主题{idx+1}/{len(recommended_themes)}: {theme_alias} (id={theme_id}) ---")
        logger.info(f"[retrieve_templates]   is_supported={is_supported}, path={theme_path}")
        logger.info(f"[retrieve_templates]   selected_filter_indicators: {[i.get('alias','') for i in selected_filter]}")
        logger.info(f"[retrieve_templates]   selected_analysis_indicators: {[i.get('alias','') for i in selected_analysis]}")

        if not is_supported:
            logger.info(f"[retrieve_templates]   >>> 跳过（非支持主题）")
            continue

        # 收集 LLM 裁决后的指标别名（覆盖率基于别名匹配）
        matched_indicator_aliases = []

        for ind in selected_filter:
            if ind.get("alias"):
                matched_indicator_aliases.append(ind["alias"])

        for ind in selected_analysis:
            if ind.get("alias"):
                matched_indicator_aliases.append(ind["alias"])

        logger.info(f"[retrieve_templates]   汇总 matched_indicator_aliases ({len(matched_indicator_aliases)}): {matched_indicator_aliases}")

        if not matched_indicator_aliases:
            logger.info(f"[retrieve_templates]   >>> 跳过（matched_indicator_aliases 为空）")
            continue

        logger.info(f"[retrieve_templates]   调用 template_tools.get_theme_templates_with_coverage(theme_id={theme_id}, alias_count={len(matched_indicator_aliases)}, top_k={top_k})")
        result = template_tools.get_theme_templates_with_coverage(
            theme_id=theme_id,
            matched_indicator_aliases=matched_indicator_aliases,
            top_k=top_k,
        )

        logger.info(f"[retrieve_templates]   工具返回: success={result.get('success')}, has_qualified={result.get('has_qualified_templates')}, matched_templates_count={len(result.get('matched_templates', []))}, all_template_count={result.get('all_template_count', 0)}, fallback_reason={result.get('fallback_reason', '')}")

        if result.get("success"):
            # 工具返回的所有模板（含达标和降级）
            all_theme_templates = result.get("all_templates", [])
            # 达标模板（覆盖率 >= 阈值）
            qualified_theme_templates = result.get("matched_templates", [])
            has_qualified = result.get("has_qualified_templates", False)
            fallback_reason = result.get("fallback_reason", "")

            # 始终添加所有模板（无论达标与否），确保 analyze_templates 能对每个模板进行 LLM 评估
            # 如果有达标模板，优先使用达标模板；否则使用降级推荐的全量模板
            if all_theme_templates:
                templates_for_analysis = all_theme_templates
            elif qualified_theme_templates:
                templates_for_analysis = qualified_theme_templates
            else:
                templates_for_analysis = []

            logger.info(f"[retrieve_templates]   达标模板: {len(qualified_theme_templates)} 个, "
                        f"全量模板(含降级): {len(all_theme_templates)} 个, "
                        f"送入LLM评估: {len(templates_for_analysis)} 个, "
                        f"has_qualified={has_qualified}")

            # 记录每个主题的检索详情
            search_entry = {
                "theme_id": theme_id,
                "theme_alias": theme_alias,
                "theme_path": theme_path,
                "is_supported": is_supported,
                "matched_indicator_aliases": matched_indicator_aliases,
                "has_qualified_templates": has_qualified,
                "fallback_reason": fallback_reason,
                "all_template_count": result.get("all_template_count", len(all_theme_templates)),
            }
            template_search_detail.append(search_entry)

            # 为每个模板补充主题信息和覆盖率详情（构建独立副本避免污染原对象）
            for t in templates_for_analysis:
                template_entry = {
                    "template_id": t.get("template_id", ""),
                    "template_alias": t.get("template_alias", ""),
                    "template_description": t.get("template_description", ""),
                    "theme_id": theme_id,
                    "theme_alias": theme_alias,
                    "usage_count": t.get("usage_count", 0),
                    "coverage_ratio": t.get("coverage_ratio", 0),
                    "covered_indicator_aliases": t.get("covered_indicator_aliases", []),
                    "missing_indicator_aliases": t.get("missing_indicator_aliases", []),
                    "matched_count": len(t.get("covered_indicator_aliases", [])),
                    "total_user_indicators": (
                        len(t.get("covered_indicator_aliases", []))
                        + len(t.get("missing_indicator_aliases", []))
                    ),
                    "has_qualified_templates": has_qualified,
                    "fallback_reason": fallback_reason,
                }
                all_templates.append(template_entry)
                logger.info(
                    f"[retrieve_templates]     模板: {t.get('template_alias','')} "
                    f"覆盖率={t.get('coverage_ratio', 0):.3f} "
                    f"covered={[x for x in t.get('covered_indicator_aliases', [])[:3]]}"
                )
        else:
            logger.warning(f"[retrieve_templates]   >>> 工具调用失败: {result.get('error', 'unknown error')}")

    logger.info(f"[retrieve_templates] === 阶段2模板检索结束，共推荐 {len(all_templates)} 个模板 ===")
    if all_templates:
        logger.info(f"[retrieve_templates]   模板列表: {[(t.get('template_alias',''), t.get('coverage_ratio',0)) for t in all_templates]}")
    return {
        "recommended_templates": all_templates,
        "template_search_detail": template_search_detail,
    }


def analyze_templates(state: AgentState) -> dict:
    """阶段 2.2：LLM 可用性与缺口分析（分批执行优化版）"""
    user_question = state.get("user_question", "")
    analysis_dimensions = state.get("analysis_dimensions", [])
    templates = state.get("recommended_templates", [])
    writer = get_stream_writer()

    logger.info(f"[analyze_templates] ═══════════════════════════════════════════════════════════")
    logger.info(f"[analyze_templates] === 阶段2.2 LLM模板分析开始 ===")
    logger.info(f"[analyze_templates] 用户问题: {user_question}")
    logger.info(f"[analyze_templates] state['recommended_templates'] 长度: {len(templates)}")
    if templates:
        for i, t in enumerate(templates):
            logger.info(f"[analyze_templates]   模板{i+1}: {t.get('template_alias','')} (id={t.get('template_id','')}) "
                        f"覆盖率={t.get('coverage_ratio',0):.3f}, heat={t.get('usage_count',0)}")
    else:
        logger.warning(f"[analyze_templates] ⚠️ 警告: state['recommended_templates'] 为空，analyze_templates 将跳过！")

    writer({"stage": "analyze_templates", "step": "analyzing", "status": "in_progress", "template_count": len(templates)})

    if not templates:
        logger.info(f"[analyze_templates] >>> 无模板，跳过分析")
        writer({"stage": "analyze_templates", "step": "completed", "status": "done"})
        return {"recommended_templates": []}

    batch_size = config.ANALYZE_TEMPLATES_BATCH_SIZE
    total_templates = len(templates)
    total_batches = (total_templates + batch_size - 1) // batch_size

    # 分批处理
    for batch_idx in range(total_batches):
        start_idx = batch_idx * batch_size
        end_idx = min(start_idx + batch_size, total_templates)
        batch_templates = [(i, templates[i]) for i in range(start_idx, end_idx)]

        logger.info(f"[模板分析] 批次 {batch_idx + 1}/{total_batches}，处理模板 {start_idx + 1}-{end_idx}/{total_templates}")

        # 发送批次进度事件
        writer({
            "stage": "analyze_templates",
            "step": "batch_progress",
            "status": "in_progress",
            "batch": batch_idx + 1,
            "total_batches": total_batches,
            "processed": start_idx,
            "total": total_templates,
        })

        max_workers = min(len(batch_templates), 3)  # 每批最多3个并行

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(
                    _analyze_template_parallel, template, user_question, analysis_dimensions
                ): idx
                for idx, template in batch_templates
            }

            try:
                for future in as_completed(future_to_idx, timeout=config.ANALYZE_TEMPLATES_BATCH_TIMEOUT_SECONDS):
                    idx = future_to_idx[future]
                    result = future.result()
                    templates[idx]["usability"] = result["usability"]
                    writer({
                        "stage": "analyze_templates",
                        "step": "analyzing_template",
                        "status": "in_progress",
                        "template_index": idx + 1,
                        "template_alias": templates[idx].get("template_alias", ""),
                    })
            except FuturesTimeoutError:
                # 批次超时，记录警告但继续处理已完成的结果
                logger.warning(f"[模板分析] 批次 {batch_idx + 1}/{total_batches} 部分超时")
                # 取消未完成的 futures
                for future in future_to_idx:
                    if not future.done():
                        future.cancel()

    logger.info(f"[analyze_templates] === LLM分析完成，LLM判定结果 ===")
    for i, t in enumerate(templates):
        usability = t.get("usability", {})
        logger.info(f"[analyze_templates]   模板{i+1}: {t.get('template_alias','')} "
                    f"is_supported={usability.get('is_supported')}, reason={usability.get('support_reason','')}")

    # 过滤掉 LLM 判定为不支持的模板
    supported_templates = []
    for t in templates:
        usability = t.get("usability", {})
        if usability.get("is_supported", False):
            supported_templates.append(t)
        else:
            logger.info(
                f"[模板分析] 模板「{t.get('template_alias', '')}」"
                f"被 LLM 判定为不支持: {usability.get('support_reason', '')}"
            )

    logger.info(f"[analyze_templates] === 阶段2.2结束，LLM过滤后剩余 {len(supported_templates)}/{len(templates)} 个模板 ===")
    writer({
        "stage": "analyze_templates",
        "step": "filtered",
        "before_count": len(templates),
        "after_count": len(supported_templates),
    })

    return {
        "recommended_templates": supported_templates,
        "template_search_detail": state.get("template_search_detail", []),
    }


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
            "dimension_guidance": state.get("dimension_guidance"),  # Jaccard 勾选引导
            "action_required": "请选择要进入分析的维度（可多选），然后点击继续；或修改问题后重新提交",
        }
        user_input = interrupt(interrupt_data)

        # 读取用户实际选择的维度（用户可能只勾选收敛维度）
        confirmed_dimensions = user_input.get("confirmed_dimensions", [])
        confirmed_question_from_ui = user_input.get("confirmed_question")
    else:
        # 正常确认流程
        writer({"stage": "wait_for_confirmation", "step": "waiting_confirmation", "status": "in_progress"})
        user_input = interrupt(state.get("pending_confirmation"))

        confirmed_dimensions = user_input.get("confirmed_dimensions", [])
        confirmed_question_from_ui = user_input.get("confirmed_question")

    # 两种情况统一处理：过滤 analysis_dimensions，只保留用户确认的维度
    filtered_dimensions = [
        d for d in state.get("analysis_dimensions", [])
        if d.get("search_term") in confirmed_dimensions
    ]

    # 基于用户确认的维度生成 normalized_question
    # 如果用户在界面手动修改了问题描述（confirmed_question_from_ui），优先使用
    if confirmed_question_from_ui:
        final_normalized_question = confirmed_question_from_ui
    elif filtered_dimensions:
        confirmed_str = _build_confirmed_concepts_str(filtered_dimensions)
        filter_str = _build_filter_phrases_str(state.get("filter_indicators", []))
        try:
            norm_result = llm_client.generate_normalized_question(
                user_question=state.get("user_question", ""),
                filter_phrases_str=filter_str,
                converged_concepts_str=confirmed_str,
            )
            final_normalized_question = norm_result.normalized_question
        except Exception as e:
            logger.warning(f"规范化问题生成失败，使用原文: {e}")
            final_normalized_question = state.get("user_question", "")
    else:
        final_normalized_question = state.get("user_question", "")

    user_confirmation: UserConfirmation = {
        "confirmed_dimensions": confirmed_dimensions,
        "confirmed_question": final_normalized_question,
    }

    return {
        "analysis_dimensions": filtered_dimensions,
        "normalized_question": final_normalized_question,
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
        # ── 双路径探查结果 ──
        "candidate_themes_from_aggregate": [
            {
                "theme_id": t.get("theme_id", ""),
                "theme_alias": t.get("theme_alias", ""),
                "theme_level": t.get("theme_level", 0),
                "theme_path": t.get("theme_path", ""),
                "frequency": t.get("frequency", 0),
                "weighted_frequency": round(t.get("weighted_frequency", 0.0), 4),
                "matched_indicator_ids": t.get("matched_indicator_ids", []),
            }
            for t in state.get("candidate_themes", [])
        ],
        "navigation_path_detail": [
            {
                "sector_id": s.get("sector_id", ""),
                "sector_alias": s.get("sector_alias", ""),
                "sector_path": s.get("sector_path", ""),
                "total_themes": s.get("total_themes", 0),
                "selected_themes": [
                    {
                        "theme_id": t.get("theme_id", ""),
                        "theme_alias": t.get("theme_alias", ""),
                        "theme_path": t.get("full_path", ""),
                    }
                    for t in s.get("selected_themes", [])
                ],
            }
            for s in state.get("navigation_path_detail", [])
        ],
        # ── 推荐结果 ──
        "recommended_themes": [
            {
                "theme_id": t["theme_id"],
                "theme_alias": t["theme_alias"],
                "theme_level": t["theme_level"],
                "theme_path": t.get("theme_path", ""),
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
                "theme_id": t.get("theme_id", ""),
                "theme_alias": t.get("theme_alias", ""),
                "usage_count": t.get("usage_count", 0),
                "coverage_ratio": t.get("coverage_ratio", 0),
                # 覆盖率详情
                "covered_indicator_aliases": t.get("covered_indicator_aliases", []),
                "missing_indicator_aliases": t.get("missing_indicator_aliases", []),
                "matched_count": len(t.get("covered_indicator_aliases", [])),
                "total_user_indicators": len(t.get("covered_indicator_aliases", [])) + len(t.get("missing_indicator_aliases", [])),
                # 该主题的达标情况
                "theme_has_qualified_templates": t.get("has_qualified_templates", False),
                "theme_fallback_reason": t.get("fallback_reason", ""),
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

    # ── 模板检索详情（按主题分组，含 LLM 评估结果）──
    # 按 theme_id 分组 templates
    templates_by_theme = {}
    for t in state.get("recommended_templates", []):
        tid = t.get("theme_id", "")
        if tid:
            templates_by_theme.setdefault(tid, []).append(t)

    template_search_detail = []
    for d in state.get("template_search_detail", []):
        theme_id = d.get("theme_id", "")
        theme_templates = templates_by_theme.get(theme_id, [])
        entry = {
            "theme_id": theme_id,
            "theme_alias": d.get("theme_alias", ""),
            "theme_path": d.get("theme_path", ""),
            "is_supported": d.get("is_supported", False),
            "matched_indicator_aliases": d.get("matched_indicator_aliases", []),
            "has_qualified_templates": d.get("has_qualified_templates", False),
            "fallback_reason": d.get("fallback_reason", ""),
            "all_template_count": d.get("all_template_count", 0),
            # 该主题下被 LLM 评估过的所有模板（含评估结果）
            "templates": [
                {
                    "template_id": t.get("template_id", ""),
                    "template_alias": t.get("template_alias", ""),
                    "template_description": t.get("template_description", ""),
                    "usage_count": t.get("usage_count", 0),
                    "coverage_ratio": t.get("coverage_ratio", 0),
                    "covered_indicator_aliases": t.get("covered_indicator_aliases", []),
                    "missing_indicator_aliases": t.get("missing_indicator_aliases", []),
                    "matched_count": len(t.get("covered_indicator_aliases", [])),
                    "total_user_indicators": len(t.get("covered_indicator_aliases", [])) + len(t.get("missing_indicator_aliases", [])),
                    # LLM 评估结果
                    "is_supported": t.get("usability", {}).get("is_supported", False),
                    "usability_reason": t.get("usability", {}).get("support_reason", ""),
                }
                for t in theme_templates
            ],
        }
        template_search_detail.append(entry)

    final_output["template_search_detail"] = template_search_detail

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


def _compute_weighted_jaccard(
    themes_a: dict[str, dict],
    themes_b: dict[str, dict],
) -> float:
    """
    计算两个维度的加权 Jaccard 相似度。

    加权 Jaccard = Σ(min(w_A, w_B)) / Σ(max(w_A, w_B))
    - w_A: theme 对维度 A 的贡献权重（= 映射到该 theme 的指标相似度之和）
    - w_B: theme 对维度 B 的贡献权重

    themes 结构: {theme_id: {"alias": str, "weight": float, "indicators": [...]}}
    """
    all_theme_ids = set(themes_a.keys()) | set(themes_b.keys())
    if not all_theme_ids:
        return 1.0

    sum_min = 0.0
    sum_max = 0.0
    for tid in all_theme_ids:
        w_a = themes_a.get(tid, {}).get("weight", 0.0)
        w_b = themes_b.get(tid, {}).get("weight", 0.0)
        sum_min += min(w_a, w_b)
        sum_max += max(w_a, w_b)

    return sum_min / sum_max if sum_max > 0 else 0.0


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

    # Step 3: 构建每个维度的主题权重映射
    # 按 theme_id 聚合：每个 theme 对维度的贡献权重 = 映射到该 theme 的指标相似度之和
    # 结构: dim_themes[dim_name] = {theme_id: {"alias": str, "weight": float, "indicators": [ind_id, ...]}}
    dim_themes: dict[str, dict[str, dict]] = {}
    for dim_name, indicators in dim_indicator_map.items():
        theme_weight_map: dict[str, dict] = {}
        for ind in indicators[:20]:
            ind_id = ind.get("id")
            ind_sim = ind.get("similarity_score", 0)
            if ind_id and ind_id in theme_mapping:
                for theme in theme_mapping[ind_id]:
                    tid = theme["id"]
                    if tid not in theme_weight_map:
                        theme_weight_map[tid] = {
                            "alias": theme["alias"],
                            "weight": 0.0,
                            "indicators": [],
                        }
                    theme_weight_map[tid]["weight"] += ind_sim
                    theme_weight_map[tid]["indicators"].append(ind_id)
        dim_themes[dim_name] = theme_weight_map

    # Step 4: 计算加权 Jaccard 相似度矩阵
    # 加权 Jaccard = Σ(min(w_A, w_B)) / Σ(max(w_A, w_B))
    dim_names = list(dim_themes.keys())
    jaccard_matrix: dict[str, dict[str, float]] = {}
    for i, dim_a in enumerate(dim_names):
        jaccard_matrix[dim_a] = {}
        for j, dim_b in enumerate(dim_names):
            if i == j:
                jaccard_matrix[dim_a][dim_b] = 1.0
            else:
                themes_a = dim_themes[dim_a]
                themes_b = dim_themes[dim_b]
                jaccard_matrix[dim_a][dim_b] = _compute_weighted_jaccard(themes_a, themes_b)

    # Step 5: 构建分析维度字符串（含真实 theme 信息）
    analysis_dimensions_str_parts = []
    for dim in analysis_dimensions:
        dim_name = dim.get("search_term", dim.get("dimension", ""))
        indicators = dim.get("indicators", [])
        themes_map = dim_themes.get(dim_name, {})  # {theme_id: {alias, weight, indicators}}

        # Top-5 指标
        top_inds = indicators[:5]
        ind_lines = []
        for ind in top_inds:
            alias = ind.get("alias", "")
            score = ind.get("similarity_score", 0)
            desc = ind.get("description", "")
            ind_lines.append(f"  - {alias}（相似度: {score:.2f}）描述: {desc}")

        # 命中的主题列表（按权重降序）
        theme_lines = []
        for tid, t_info in sorted(themes_map.items(), key=lambda x: x[1]["weight"], reverse=True):
            theme_lines.append(f"  - [{tid}] {t_info['alias']}（权重: {t_info['weight']:.2f}）")

        part = f"""分析维度：「{dim_name}」
  命中主题数: {len(themes_map)}
  命中主题列表:
{chr(10).join(theme_lines) if theme_lines else "  （无主题信息）"}
  关联指标 Top-5:
{chr(10).join(ind_lines)}"""
        analysis_dimensions_str_parts.append(part)

    analysis_dimensions_str = "\n\n".join(analysis_dimensions_str_parts)

    # Step 6: 构建 dimensions_str（加权 Jaccard 矩阵摘要）
    dimensions_str_parts = []
    for i, dim_a in enumerate(dim_names):
        row = [f"{dim_a} vs {dim_b}: 加权Jaccard={jaccard_matrix[dim_a][dim_b]:.2f}"
               for j, dim_b in enumerate(dim_names) if i < j]
        dimensions_str_parts.extend(row)

    dimensions_str = "\n".join(dimensions_str_parts) if dimensions_str_parts else "（仅一个维度）"

    # Step 7: 调用 LLM 生成引导
    try:
        guidance = llm_client.generate_dimension_selection_guidance(
            user_question=user_question,
            dimensions_str=dimensions_str,
            analysis_dimensions_str=analysis_dimensions_str,
            jaccard_threshold=config.JACCARD_SIMILARITY_THRESHOLD,
        )
        # Step 8: 程序化计算所有一致性字段（基于加权 Jaccard 矩阵）
        # 注意：has_conflict 和 selection_advice 必须由程序化计算，
        # 确保与 can_select_all 严格一致，避免 LLM 自由生成导致矛盾
        threshold = config.JACCARD_SIMILARITY_THRESHOLD
        can_select_all = True
        for i, dim_a in enumerate(dim_names):
            for j, dim_b in enumerate(dim_names):
                if i < j and jaccard_matrix[dim_a][dim_b] < threshold:
                    can_select_all = False
                    break
            if not can_select_all:
                break

        # 程序化确定 has_conflict（与 can_select_all 互为反面）
        prog_has_conflict = not can_select_all

        # 程序化确定 selection_advice（与 can_select_all 严格对应）
        if can_select_all:
            prog_selection_advice = (
                f"各维度的主题方向一致，可以同时勾选，推荐结果会互为补充。"
            )
        else:
            core_dims = guidance.recommended_first
            if core_dims:
                dim_list = "、".join(core_dims)
                prog_selection_advice = (
                    f"各维度导向的主题方向不同，同时勾选会让推荐结果分散。"
                    f"建议优先勾选您最关心的核心维度：{dim_list}，获得聚焦的推荐后再考虑是否补充其他维度。"
                )
            else:
                prog_selection_advice = (
                    f"各维度导向的主题方向不同，同时勾选会让推荐结果分散。"
                    f"建议优先勾选您最关心的核心维度，确认推荐结果满意后再补充其他维度。"
                )

        # 转换为 dict（兼容直接返回）
        result = {
            "has_conflict": prog_has_conflict,
            "can_select_all": can_select_all,
            "recommended_first": guidance.recommended_first,
            "conflict_analysis": guidance.conflict_analysis,
            "selection_advice": prog_selection_advice,
            "dimension_analysis": [
                {
                    "dimension": item.dimension,
                    "matched_themes": [
                        {"theme": t_info["alias"], "weight": t_info["weight"]}
                        for t_info in sorted(
                            dim_themes.get(item.dimension, {}).values(),
                            key=lambda x: x["weight"],
                            reverse=True,
                        )
                    ],
                    "theme_count": len(dim_themes.get(item.dimension, {})),
                    "primary_theme": item.primary_theme,
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
                    f"can_select_all={result['can_select_all']}, "
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



def _build_confirmed_concepts_str(filtered_dimensions: list) -> str:
    """构建用户确认的分析维度字符串（用于 normalized_question 生成）"""
    if not filtered_dimensions:
        return "（无确认的分析维度）"
    lines = []
    for d in filtered_dimensions:
        concept = d.get("search_term", "")
        top_indicators = [i.get("alias", "") for i in d.get("indicators", [])[:3]]
        if top_indicators:
            lines.append(f"- 「{concept}」（关联指标: {', '.join(top_indicators)}）")
        else:
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

    # 5. 按主题维度推荐模板
    supported_themes = [t for t in themes if t.get('is_supported', False)] if themes else []

    if supported_themes:
        # 以 is_supported 的主题为维度，每个主题展示其关联模板
        parts.append("关于模板推荐：")
        for theme in supported_themes:
            theme_name = theme.get('theme_alias', '')
            # 展示时去掉 "主题" 后缀，避免 "针对「xxx主题」主题" 的重复
            theme_display = theme_name.removesuffix('主题') if theme_name.endswith('主题') else theme_name
            # 找到属于该主题的模板
            theme_templates = [t for t in templates if t.get('theme_alias', '') == theme_name]

            if theme_templates:
                for t in theme_templates:
                    template_name = t.get('template_alias', '')
                    coverage = t.get('coverage_ratio', 0) * 100
                    usage = t.get('usage_count', 0)
                    usability = t.get('usability', {})
                    support_reason = usability.get('support_reason', '')

                    parts.append(f"针对「{theme_display}」主题：推荐「{template_name}」模板（覆盖率 {coverage:.0f}%，使用 {usage} 次）")
                    if support_reason:
                        parts.append(f"，{support_reason}")
                    parts.append("。")
            else:
                parts.append(f"针对「{theme_display}」主题：该主题下没有可用模板，建议直接在主题中勾选所需指标进行分析。")
    elif templates:
        # 无 supported 主题但有模板时，兜底展示
        parts.append("关于模板推荐：")
        for t in templates:
            template_name = t.get('template_alias', '')
            coverage = t.get('coverage_ratio', 0) * 100
            usage = t.get('usage_count', 0)
            usability = t.get('usability', {})
            support_reason = usability.get('support_reason', '')

            parts.append(f"「{template_name}」模板（覆盖率 {coverage:.0f}%，使用 {usage} 次）")
            if support_reason:
                parts.append(f"，{support_reason}")
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
        reason = t.get("usability", {}).get("support_reason", "")
        lines.append(f"- {t.get('template_alias', '')}（热度{usage}, 覆盖率{coverage*100:.0f}%）")
        if reason:
            lines.append(f"  {reason}")
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
