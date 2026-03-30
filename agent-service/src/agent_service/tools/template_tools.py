"""
模板推荐工具
复用 theme_ontology_server.py 中的模板覆盖率计算逻辑
"""

import logging
import time
from typing import Any

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

        with get_neo4j_driver().session() as session:
            # 构建模板类型过滤条件
            type_filter = ""
            if template_type == "INSIGHT":
                type_filter = "AND t:INSIGHT_TEMPLATE"
            elif template_type == "COMBINEDQUERY":
                type_filter = "AND t:COMBINEDQUERY_TEMPLATE"

            user_indicator_set = set(matched_indicator_aliases)
            user_indicator_count = len(user_indicator_set)

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

            # 过滤出覆盖率 >= 80% 的达标模板
            qualified_templates = [
                t for t in all_templates if t["coverage_ratio"] >= 0.8
            ]

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
                    "execution_time_ms": round((time.time() - start) * 1000, 2),
                }
            else:
                if not all_templates:
                    return {
                        "success": True,
                        "theme_id": theme_id,
                        "template_type": template_type,
                        "has_qualified_templates": False,
                        "matched_templates": [],
                        "matched_template_count": 0,
                        "fallback_reason": "该主题下无热度大于 0 的模板",
                        "execution_time_ms": round((time.time() - start) * 1000, 2),
                    }

                # 降级推荐：覆盖率最高 + 热度最高
                sorted_by_coverage = sorted(
                    all_templates, key=lambda x: x["coverage_ratio"], reverse=True
                )
                highest_coverage = sorted_by_coverage[0]

                sorted_by_heat = sorted(
                    all_templates, key=lambda x: x["usage_count"], reverse=True
                )
                highest_heat = sorted_by_heat[0]

                fallback_templates = [highest_coverage]
                if highest_heat["template_id"] != highest_coverage["template_id"]:
                    fallback_templates.append(highest_heat)

                # 根据实际推荐数量生成准确的 fallback_reason
                if len(fallback_templates) == 1:
                    fallback_reason = (
                        f"无覆盖率 >= 80% 的达标模板，该主题下仅有 1 个可用模板"
                        f"「{highest_coverage['template_alias']}」"
                        f"（覆盖率 {highest_coverage['coverage_ratio']*100:.0f}%，"
                        f"使用 {highest_coverage['usage_count']} 次）"
                    )
                else:
                    fallback_reason = (
                        f"无覆盖率 >= 80% 的达标模板，降级推荐覆盖率最高"
                        f"「{highest_coverage['template_alias']}」"
                        f"（{highest_coverage['coverage_ratio']*100:.0f}%）"
                        f"和热度最高「{highest_heat['template_alias']}」"
                        f"（{highest_heat['usage_count']}次使用）的模板"
                    )

                return {
                    "success": True,
                    "theme_id": theme_id,
                    "template_type": template_type,
                    "has_qualified_templates": False,
                    "matched_templates": fallback_templates,
                    "matched_template_count": len(fallback_templates),
                    "fallback_reason": fallback_reason,
                    "execution_time_ms": round((time.time() - start) * 1000, 2),
                }

    except Exception as e:
        logger.exception(f"获取模板覆盖率失败: {e}")
        return {"success": False, "error": str(e)}
