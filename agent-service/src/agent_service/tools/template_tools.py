"""
模板推荐工具
复用 theme_ontology_server.py 中的模板覆盖率计算逻辑
"""

import logging
import time
from typing import Any

from .. import config
from .theme_tools import get_neo4j_driver

logger = logging.getLogger(__name__)


def get_theme_templates_with_coverage(
    theme_id: str,
    matched_indicator_aliases: list[str],
    template_type: str | None = None,
    top_k: int = 10,
) -> dict[str, Any]:
    """
    获取主题下的模板，并计算与匹配指标的覆盖率

    Args:
        theme_id: 主题 ID
        matched_indicator_aliases: 用户匹配的指标别名列表
        template_type: 模板类型过滤（"INSIGHT" / "COMBINEDQUERY" / None 全部）
        top_k: 返回数量，默认 10

    Returns:
        {
            "success": true,
            "theme_id": "...",
            "has_qualified_templates": true,
            "matched_templates": [...],
            "fallback_reason": "...",
            "execution_time_ms": 45.2
        }
    """
    start = time.time()

    try:
        matched_indicator_aliases = matched_indicator_aliases[:100] if matched_indicator_aliases else []
        top_k = min(max(1, top_k), 50)

        logger.info(f"[get_theme_templates_with_coverage] >>> 输入: theme_id={theme_id}, alias_count={len(matched_indicator_aliases)}, top_k={top_k}")
        logger.info(f"[get_theme_templates_with_coverage]    matched_indicator_aliases={matched_indicator_aliases}")

        with get_neo4j_driver().session() as session:
            # 构建模板类型过滤条件
            type_filter = ""
            if template_type == "INSIGHT":
                type_filter = "AND t:INSIGHT_TEMPLATE"
            elif template_type == "COMBINEDQUERY":
                type_filter = "AND t:COMBINEDQUERY_TEMPLATE"

            user_indicator_set = set(matched_indicator_aliases)
            user_indicator_count = len(user_indicator_set)
            type_filter_str = type_filter or "全部"
            logger.info(f"[get_theme_templates_with_coverage]    user_indicator_set size={user_indicator_count}, type_filter='{type_filter_str}'")

            # 查询主题下的模板及其包含的所有指标（只取 heat > 0 的模板）
            cypher = f"""
            MATCH (t) WHERE t.theme_id = $theme_id AND t.heat > 0 {type_filter}
            OPTIONAL MATCH (t)-[:CONTAINS]->(i:INDICATOR)
            WITH t, collect({{id: i.id, alias: i.alias, description: i.description}}) as template_indicators
            WHERE size(template_indicators) > 0
            RETURN t.id as template_id, t.alias as template_alias,
                   t.description as template_description,
                   t.heat as usage_count,
                   template_indicators
            ORDER BY t.heat DESC
            LIMIT $top_k
            """
            logger.info(f"[get_theme_templates_with_coverage]    执行 Cypher: theme_id={theme_id}, top_k={top_k}")

            result = session.run(
                cypher, theme_id=theme_id, top_k=top_k
            )

            all_templates = []
            for row in result:
                template_indicators = row["template_indicators"] or []
                template_indicator_aliases = set(
                    i["alias"] for i in template_indicators if i.get("alias")
                )

                # 覆盖率 = 模板覆盖的用户指标别名数 / 用户需要的指标别名总数
                covered_aliases = list(user_indicator_set & template_indicator_aliases)
                matched_count = len(covered_aliases)
                coverage_ratio = (
                    matched_count / user_indicator_count
                    if user_indicator_count > 0
                    else 0.0
                )

                # 缺失指标别名 = 用户指标别名 - 模板指标别名交集
                missing_aliases = list(user_indicator_set - template_indicator_aliases)

                logger.info(f"[get_theme_templates_with_coverage]    模板={row['template_alias']}({row['template_id']}), "
                            f"heat={row['usage_count']}, 模板指标数={len(template_indicator_aliases)}, "
                            f"匹配数={matched_count}/{user_indicator_count}, 覆盖率={coverage_ratio:.3f}, "
                            f"covered={covered_aliases}, missing={missing_aliases}")

                all_templates.append({
                    "template_id": row["template_id"],
                    "template_alias": row["template_alias"],
                    "template_description": row["template_description"] or "",
                    "usage_count": row["usage_count"] or 0,
                    "coverage_ratio": round(coverage_ratio, 3),
                    "covered_indicator_aliases": covered_aliases,
                    "missing_indicator_aliases": missing_aliases,
                    "all_template_indicators": [
                        {
                            "indicator_id": i.get("id", ""),
                            "alias": i.get("alias", ""),
                            "description": i.get("description", ""),
                        }
                        for i in template_indicators
                        if i.get("id")
                    ],
                })

            logger.info(f"[get_theme_templates_with_coverage]    查询到模板数={len(all_templates)}")

            # 过滤出覆盖率 >= 阈值的达标模板
            threshold = config.TEMPLATE_COVERAGE_THRESHOLD
            logger.info(f"[get_theme_templates_with_coverage]    覆盖率阈值={threshold}({threshold*100:.0f}%)")
            qualified_templates = [
                t for t in all_templates if t["coverage_ratio"] >= threshold
            ]
            logger.info(f"[get_theme_templates_with_coverage]    达标模板数={len(qualified_templates)}/{len(all_templates)}")

            if qualified_templates:
                qualified_templates.sort(
                    key=lambda x: x["coverage_ratio"], reverse=True
                )
                return {
                    "success": True,
                    "theme_id": theme_id,
                    "template_type": template_type,
                    "has_qualified_templates": True,
                    "matched_templates": qualified_templates,
                    "matched_template_count": len(qualified_templates),
                    # 所有扫描过的模板（含覆盖率详情）
                    "all_templates": all_templates,
                    "all_template_count": len(all_templates),
                    "fallback_reason": "",
                    "execution_time_ms": round((time.time() - start) * 1000, 2),
                }
            else:
                if not all_templates:
                    reason = "该主题下无热度大于 0 的模板"
                else:
                    best = max(all_templates, key=lambda x: x["coverage_ratio"])
                    reason = (
                        f"该主题下模板最高覆盖率仅 {best['coverage_ratio']*100:.0f}%，"
                        f"未达到 {threshold*100:.0f}% 的推荐阈值"
                    )
                return {
                    "success": True,
                    "theme_id": theme_id,
                    "template_type": template_type,
                    "has_qualified_templates": False,
                    "matched_templates": [],
                    "matched_template_count": 0,
                    # 所有扫描过的模板（含覆盖率详情，用于展示降级推荐）
                    "all_templates": all_templates,
                    "all_template_count": len(all_templates),
                    "fallback_reason": reason,
                    "execution_time_ms": round((time.time() - start) * 1000, 2),
                }

    except Exception as e:
        logger.exception(f"获取模板覆盖率失败: {e}")
        return {"success": False, "error": str(e)}
