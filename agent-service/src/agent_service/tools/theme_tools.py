"""
主题和指标工具
复用 theme_ontology_server.py 中的 Neo4j 逻辑
"""

import json
import logging
from typing import Any

from neo4j import GraphDatabase

from .. import config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Neo4j 驱动（单例）
# ─────────────────────────────────────────────
_neo4j_driver = None


def get_neo4j_driver():
    """获取 Neo4j 驱动（单例）"""
    global _neo4j_driver
    if _neo4j_driver is None:
        _neo4j_driver = GraphDatabase.driver(
            config.NEO4J_URI,
            auth=(config.NEO4J_USER, config.NEO4J_PASSWORD),
        )
    return _neo4j_driver


def close_neo4j_driver():
    """关闭 Neo4j 驱动"""
    global _neo4j_driver
    if _neo4j_driver is not None:
        _neo4j_driver.close()
        _neo4j_driver = None


# ─────────────────────────────────────────────
# 主题聚合
# ─────────────────────────────────────────────

def aggregate_themes_from_indicators(
    matched_indicators: list[str], top_k: int = 3
) -> dict:
    """
    从指标列表中聚合候选主题（按频次排序）

    优化版本：使用单次批量查询替代 N+1 查询

    Args:
        matched_indicators: 指标 ID 列表
        top_k: 返回候选主题数量，默认 3

    Returns:
        {
            "success": true,
            "candidate_themes": [
                {
                    "theme_id": "THEME.xxx",
                    "theme_alias": "对公贷款借据",
                    "theme_level": 5,
                    "frequency": 15,
                    "matched_indicator_ids": ["INDICATOR.xxx", "..."]
                }
            ],
            "total_themes": 5,
            "total_indicators": 30,
            "execution_time_ms": 120.5
        }
    """
    import time
    start = time.time()

    try:
        top_k = min(max(1, top_k), 20)
        matched_indicators = matched_indicators[:100] if matched_indicators else []

        if not matched_indicators:
            return {
                "success": True,
                "candidate_themes": [],
                "total_themes": 0,
                "total_indicators": 0,
                "execution_time_ms": round((time.time() - start) * 1000, 2),
            }

        # 批量查询：一次性获取所有指标的 THEME 信息
        business_labels = [
            "SECTOR", "CATEGORY", "THEME", "SUBPATH", "INDICATOR",
            "INSIGHT_TEMPLATE", "COMBINEDQUERY_TEMPLATE",
        ]

        with get_neo4j_driver().session() as session:
            cypher = """
            MATCH path = (entry)-[:HAS_CHILD*]->(indicator)
            WHERE entry.alias = '自主分析'
              AND indicator.id IN $indicator_ids
              AND labels(entry)[0] IN $business_labels
              AND labels(indicator)[0] IN $business_labels
            WITH indicator.id as indicator_id,
                 [node in nodes(path) WHERE labels(node)[0] = 'THEME' | node][0] as theme
            WHERE theme IS NOT NULL
            RETURN indicator_id, theme.id as theme_id, theme.alias as theme_alias, theme.level as theme_level
            """
            result = session.run(
                cypher,
                indicator_ids=matched_indicators,
                business_labels=business_labels
            )

            # 聚合结果
            theme_map: dict = {}
            for row in result:
                indicator_id = row["indicator_id"]
                theme_id = row["theme_id"]
                theme_alias = row["theme_alias"]
                theme_level = row.get("theme_level")

                if theme_id not in theme_map:
                    theme_map[theme_id] = {
                        "theme_alias": theme_alias,
                        "theme_level": theme_level,
                        "matched_indicator_ids": [],
                    }
                theme_map[theme_id]["matched_indicator_ids"].append(indicator_id)

        # 按频次降序排列，取 Top K
        sorted_themes = sorted(
            theme_map.items(),
            key=lambda x: len(x[1]["matched_indicator_ids"]),
            reverse=True,
        )[:top_k]

        candidate_themes = []
        for theme_id, info in sorted_themes:
            matched_ids = info["matched_indicator_ids"]
            candidate_themes.append({
                "theme_id": theme_id,
                "theme_alias": info["theme_alias"],
                "theme_level": info.get("theme_level") or 0,
                "frequency": len(matched_ids),
                "matched_indicator_ids": matched_ids,
            })

        return {
            "success": True,
            "candidate_themes": candidate_themes,
            "total_themes": len(candidate_themes),
            "total_indicators": len(matched_indicators),
            "execution_time_ms": round((time.time() - start) * 1000, 2),
        }

    except Exception as e:
        logger.exception(f"主题聚合失败: {e}")
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────
# 获取主题下的筛选指标
# ─────────────────────────────────────────────

def get_theme_filter_indicators(theme_id: str) -> dict:
    """
    获取主题下全量的筛选指标

    Args:
        theme_id: THEME 节点 ID

    Returns:
        {
            "success": true,
            "theme_id": "...",
            "time_filter_indicators": [...],
            "org_filter_indicators": [...],
            "total_count": 10,
            "execution_time_ms": 45.2
        }
    """
    import time
    start = time.time()

    try:
        with get_neo4j_driver().session() as session:
            # 获取主题下全量 INDICATOR（支持直接和间接连接）
            cypher = """
            MATCH (theme:THEME {id: $theme_id})
            MATCH (theme)-[:HAS_CHILD*1..2]->(i:INDICATOR)
            RETURN i.id as id, i.alias as alias, i.description as description
            """
            result = session.run(cypher, theme_id=theme_id)
            indicators = [dict(r) for r in result]

            if not indicators:
                return {
                    "success": True,
                    "theme_id": theme_id,
                    "time_filter_indicators": [],
                    "org_filter_indicators": [],
                    "total_count": 0,
                    "execution_time_ms": round((time.time() - start) * 1000, 2),
                }

            # 时间筛选指标：别名包含 "数据日期" 或 "ETL数据日期"
            time_patterns = ["数据日期", "ETL数据日期"]

            # 机构筛选指标：别名匹配以下任一模式
            org_patterns = [
                "机构名称", "机构编号",
                "管理机构名称", "管理机构编号",
                "账务机构名称", "账务机构编号",
            ]

            # 按别名去重
            seen_aliases = set()
            time_filter = []
            org_filter = []

            for ind in indicators:
                alias = ind.get("alias", "") or ""

                if alias in seen_aliases:
                    continue
                seen_aliases.add(alias)

                is_time = any(p in alias for p in time_patterns)
                if is_time:
                    time_filter.append({
                        "id": ind["id"],
                        "alias": alias,
                        "description": ind.get("description") or "",
                    })

                is_org = any(p in alias for p in org_patterns)
                if is_org:
                    org_filter.append({
                        "id": ind["id"],
                        "alias": alias,
                        "description": ind.get("description") or "",
                    })

        return {
            "success": True,
            "theme_id": theme_id,
            "time_filter_indicators": time_filter,
            "org_filter_indicators": org_filter,
            "time_filter_count": len(time_filter),
            "org_filter_count": len(org_filter),
            "total_count": len(indicators),
            "execution_time_ms": round((time.time() - start) * 1000, 2),
        }

    except Exception as e:
        logger.exception(f"获取筛选指标失败: {e}")
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────
# 获取主题下的分析指标
# ─────────────────────────────────────────────

def get_theme_analysis_indicators(theme_id: str) -> dict:
    """
    获取主题下全量的分析指标

    Args:
        theme_id: THEME 节点 ID

    Returns:
        {
            "success": true,
            "theme_id": "...",
            "analysis_indicators": [...],
            "total_count": 150,
            "execution_time_ms": 45.2
        }
    """
    import time
    start = time.time()

    try:
        with get_neo4j_driver().session() as session:
            cypher = """
            MATCH (theme:THEME {id: $theme_id})
            MATCH (theme)-[:HAS_CHILD*1..2]->(i:INDICATOR)
            RETURN i.id as id, i.alias as alias, i.description as description
            ORDER BY i.alias
            """
            result = session.run(cypher, theme_id=theme_id)
            indicators = [dict(r) for r in result]

            if not indicators:
                return {
                    "success": True,
                    "theme_id": theme_id,
                    "analysis_indicators": [],
                    "total_count": 0,
                    "execution_time_ms": round((time.time() - start) * 1000, 2),
                }

            # 筛选指标识别规则
            time_patterns = ["数据日期", "ETL数据日期"]
            org_patterns = [
                "机构名称", "机构编号",
                "管理机构名称", "管理机构编号",
                "账务机构名称", "账务机构编号",
            ]

            # 按别名去重，排除筛选指标
            seen_aliases = set()
            analysis_indicators = []

            for ind in indicators:
                alias = ind.get("alias", "") or ""

                if alias in seen_aliases:
                    continue
                seen_aliases.add(alias)

                is_time_filter = any(p in alias for p in time_patterns)
                is_org_filter = any(p in alias for p in org_patterns)

                if not (is_time_filter or is_org_filter):
                    analysis_indicators.append({
                        "id": ind["id"],
                        "alias": alias,
                        "description": ind.get("description") or "",
                    })

        return {
            "success": True,
            "theme_id": theme_id,
            "analysis_indicators": analysis_indicators,
            "analysis_indicator_count": len(analysis_indicators),
            "total_theme_indicators": len(indicators),
            "execution_time_ms": round((time.time() - start) * 1000, 2),
        }

    except Exception as e:
        logger.exception(f"获取分析指标失败: {e}")
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────────
# 获取指标完整业务路径
# ─────────────────────────────────────────────

def get_indicator_full_path(indicator_id: str) -> dict:
    """获取指标的完整业务路径"""
    import time
    start = time.time()

    try:
        with get_neo4j_driver().session() as session:
            business_labels = [
                "SECTOR", "CATEGORY", "THEME", "SUBPATH", "INDICATOR",
            ]

            cypher = """
            MATCH path = (entry)-[:HAS_CHILD*]->(i:INDICATOR {id: $indicator_id})
            WHERE entry.alias = '自主分析'
              AND labels(entry)[0] IN $business_labels
              AND labels(i)[0] = 'INDICATOR'
            RETURN [node in nodes(path) | {
                id: node.id,
                alias: node.alias,
                type: labels(node)[0]
            }] as path_nodes
            """
            result = session.run(
                cypher, indicator_id=indicator_id, business_labels=business_labels
            ).single()

            if not result:
                return {
                    "success": False,
                    "error": f"未找到指标 {indicator_id} 的路径",
                    "execution_time_ms": round((time.time() - start) * 1000, 2),
                }

            return {
                "success": True,
                "indicator_id": indicator_id,
                "path_nodes": result["path_nodes"],
                "execution_time_ms": round((time.time() - start) * 1000, 2),
            }

    except Exception as e:
        logger.exception(f"获取指标路径失败: {e}")
        return {"success": False, "error": str(e)}
