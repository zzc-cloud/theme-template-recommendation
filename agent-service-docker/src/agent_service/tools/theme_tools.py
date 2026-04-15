
import json
import logging
from typing import Any

from neo4j import GraphDatabase

from .. import config

logger = logging.getLogger(__name__)

_neo4j_driver = None


def get_neo4j_driver():
    global _neo4j_driver
    if _neo4j_driver is None:
        _neo4j_driver = GraphDatabase.driver(
            config.NEO4J_URI,
            auth=(config.NEO4J_USER, config.NEO4J_PASSWORD),
        )
    return _neo4j_driver


def close_neo4j_driver():
    global _neo4j_driver
    if _neo4j_driver is not None:
        _neo4j_driver.close()
        _neo4j_driver = None


def aggregate_themes_from_indicators(
    matched_indicators: list[str], top_k: int = 3
) -> dict:
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
            WITH indicator.id as indicator_id, nodes(path) as path_nodes
            UNWIND path_nodes as node
            WITH indicator_id, path_nodes, node
            WHERE labels(node)[0] = 'THEME'
            // 找到 THEME 节点在路径中的索引
            WITH indicator_id,
                 [i IN range(0, size(path_nodes)-1) WHERE labels(path_nodes[i])[0] = 'THEME' | i][0] as theme_idx,
                 path_nodes
            WHERE theme_idx IS NOT NULL
            // 收集从"自主分析"到 THEME 的路径别名（排除 INDICATOR 类型）
            WITH indicator_id,
                 [i IN range(0, theme_idx) WHERE labels(path_nodes[i])[0] <> 'INDICATOR' | path_nodes[i].alias] as path_aliases,
                 path_nodes[theme_idx] as theme
            RETURN indicator_id,
                   theme.id as theme_id,
                   theme.alias as theme_alias,
                   theme.level as theme_level,
                   reduce(s = "", x IN path_aliases | CASE WHEN s = "" THEN x ELSE s + " > " + x END) as theme_path
            """
            result = session.run(
                cypher,
                indicator_ids=matched_indicators,
                business_labels=business_labels
            )

            theme_map: dict = {}
            for row in result:
                indicator_id = row["indicator_id"]
                theme_id = row["theme_id"]
                theme_alias = row["theme_alias"]
                theme_level = row.get("theme_level")
                theme_path = row.get("theme_path") or f"自主分析 > {theme_alias}"

                if theme_id not in theme_map:
                    theme_map[theme_id] = {
                        "theme_alias": theme_alias,
                        "theme_level": theme_level,
                        "theme_path": theme_path,
                        "matched_indicator_ids": [],
                    }
                theme_map[theme_id]["matched_indicator_ids"].append(indicator_id)

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
                "theme_path": info.get("theme_path", f"自主分析 > {info['theme_alias']}"),
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


def get_theme_full_path(theme_id: str) -> dict:
    import time
    start = time.time()

    try:
        with get_neo4j_driver().session() as session:
            business_labels = ["SECTOR", "CATEGORY", "THEME", "SUBPATH"]

            cypher = """
            MATCH path = (entry)-[:HAS_CHILD*]->(theme:THEME {id: $theme_id})
            WHERE entry.alias = '自主分析'
              AND labels(entry)[0] IN $business_labels
            RETURN [node in nodes(path) | {
                id: node.id,
                alias: node.alias,
                type: labels(node)[0],
                level: node.level
            }] as path_nodes
            """
            result = session.run(
                cypher, theme_id=theme_id, business_labels=business_labels
            ).single()

            if not result or not result.get("path_nodes"):
                return {
                    "success": False,
                    "error": f"未找到主题 {theme_id} 的路径信息",
                    "execution_time_ms": round((time.time() - start) * 1000, 2),
                }

            path_nodes = result["path_nodes"]

            theme_alias = None
            for node in path_nodes:
                if node["type"] == "THEME":
                    theme_alias = node["alias"]
                    break

            path_aliases = [node["alias"] for node in path_nodes]
            theme_path = " > ".join(path_aliases)

            path_node_list = [
                {
                    "alias": node["alias"],
                    "type": node["type"],
                }
                for node in path_nodes
            ]

            return {
                "success": True,
                "theme_id": theme_id,
                "theme_alias": theme_alias,
                "theme_path": theme_path,
                "path_nodes": path_node_list,
                "execution_time_ms": round((time.time() - start) * 1000, 2),
            }

    except Exception as e:
        logger.exception(f"获取主题路径失败: {e}")
        return {"success": False, "error": str(e)}


def get_theme_filter_indicators(theme_id: str) -> dict:
    import time
    start = time.time()

    try:
        with get_neo4j_driver().session() as session:
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

            time_patterns = ["数据日期", "ETL数据日期"]

            org_patterns = [
                "机构名称", "机构编号",
                "管理机构名称", "管理机构编号",
                "账务机构名称", "账务机构编号",
            ]

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


def get_theme_analysis_indicators(theme_id: str) -> dict:
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

            time_patterns = ["数据日期", "ETL数据日期"]
            org_patterns = [
                "机构名称", "机构编号",
                "管理机构名称", "管理机构编号",
                "账务机构名称", "账务机构编号",
            ]

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


def get_indicator_full_path(indicator_id: str) -> dict:
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


def batch_get_indicator_themes(indicator_ids: list[str]) -> dict[str, list[dict]]:
    import time
    start = time.time()

    if not indicator_ids:
        return {}

    try:
        with get_neo4j_driver().session() as session:
            cypher = """
            MATCH path = (entry)-[:HAS_CHILD*]->(i:INDICATOR)
            WHERE entry.alias = '自主分析'
              AND i.id IN $indicator_ids
            WITH i, [node IN nodes(path) WHERE labels(node)[0] = 'THEME'] AS theme_nodes
            UNWIND theme_nodes AS tn
            WITH i.id AS indicator_id, collect(DISTINCT {
                id: tn.id, alias: tn.alias
            }) AS themes
            RETURN indicator_id, themes
            """
            results = session.run(cypher, indicator_ids=indicator_ids)

            mapping: dict[str, list[dict]] = {}
            for record in results:
                mapping[record["indicator_id"]] = record["themes"]

            logger.info(f"batch_get_indicator_themes: {len(mapping)}/{len(indicator_ids)} 指标命中主题, "
                        f"耗时 {round((time.time() - start) * 1000, 1)}ms")
            return mapping

    except Exception as e:
        logger.exception(f"批量查询指标主题失败: {e}")
        return {}


_ROOT_ENTRY_ALIAS = "自主分析"
_NAVIGABLE_LABELS = ["SECTOR", "CATEGORY", "SUBPATH", "THEME"]


def get_sectors_from_root() -> dict:
    import time
    start = time.time()

    try:
        with get_neo4j_driver().session() as session:
            cypher = """
            MATCH (root:CATEGORY {alias: $root_alias})-[:HAS_CHILD]->(s:SECTOR)
            OPTIONAL MATCH (s)-[:HAS_CHILD]->(c)
            WITH s, count(DISTINCT c) as direct_child_count
            OPTIONAL MATCH (s)-[:HAS_CHILD*1..]->(t:THEME)
            WITH s, direct_child_count, count(DISTINCT t) as theme_child_count
            RETURN s.id as id, s.alias as alias, s.level as level,
                   s.path as path,
                   direct_child_count,
                   theme_child_count > 0 as has_theme_children
            ORDER BY s.alias
            """
            result = session.run(cypher, root_alias=_ROOT_ENTRY_ALIAS)

            sectors = []
            for row in result:
                sectors.append({
                    "id": row["id"],
                    "alias": row["alias"],
                    "level": row["level"],
                    "path": row["path"],
                    "direct_child_count": row["direct_child_count"] or 0,
                    "has_theme_children": row["has_theme_children"],
                })

            return {
                "success": True,
                "root_alias": _ROOT_ENTRY_ALIAS,
                "sectors": sectors,
                "total_sectors": len(sectors),
                "execution_time_ms": round((time.time() - start) * 1000, 2),
            }

    except Exception as e:
        logger.exception(f"获取板块列表失败: {e}")
        return {"success": False, "error": str(e)}


def get_sector_themes(sector_id: str, top_k: int = 500) -> dict:
    import time
    start = time.time()

    try:
        top_k = min(max(1, top_k), 500)

        with get_neo4j_driver().session() as session:
            sector_info_cypher = """
            MATCH (sector:SECTOR {id: $sector_id})
            RETURN sector.id as id, sector.alias as alias,
                   sector.level as level, sector.path as path
            """
            sector_result = session.run(
                sector_info_cypher, sector_id=sector_id
            ).single()

            if not sector_result:
                return {
                    "success": False,
                    "error": f"未找到板块 {sector_id}",
                    "execution_time_ms": round((time.time() - start) * 1000, 2),
                }

            themes_cypher = """
            MATCH path = (sector:SECTOR {id: $sector_id})-[:HAS_CHILD*]->(theme:THEME)
            MATCH (theme)<-[:HAS_CHILD]-(parent_node)
            WHERE labels(parent_node)[0] IN ['CATEGORY', 'SUBPATH', 'THEME']

            WITH sector, theme, parent_node, nodes(path) as path_nodes,
                 [n IN nodes(path) WHERE labels(n)[0] <> 'INDICATOR'] as non_ind_nodes

            WITH sector, theme, parent_node, non_ind_nodes,
                 reduce(s = '', item IN [n IN non_ind_nodes | n.alias] |
                        s + CASE WHEN s = '' THEN item ELSE ' > ' + item END) as full_path,
                 size(non_ind_nodes) as depth

            RETURN
                theme.id as theme_id,
                theme.alias as theme_alias,
                theme.level as theme_level,
                labels(parent_node)[0] as parent_type,
                parent_node.alias as parent_alias,
                parent_node.id as parent_id,
                depth,
                full_path
            ORDER BY theme_alias
            LIMIT $top_k
            """
            result = session.run(themes_cypher, sector_id=sector_id, top_k=top_k)

            themes = []
            for row in result:
                themes.append({
                    "theme_id": row["theme_id"],
                    "theme_alias": row["theme_alias"],
                    "theme_level": row["theme_level"],
                    "depth": row["depth"],
                    "parent_alias": row["parent_alias"],
                    "parent_type": row["parent_type"],
                    "full_path": row["full_path"],
                })

            count_cypher = """
            MATCH (sector:SECTOR {id: $sector_id})-[:HAS_CHILD*]->(theme:THEME)
            RETURN count(theme) as total
            """
            count_result = session.run(count_cypher, sector_id=sector_id).single()
            total = count_result["total"] if count_result else 0

            return {
                "success": True,
                "sector_id": sector_result["id"],
                "sector_alias": sector_result["alias"],
                "sector_path": sector_result["path"],
                "themes": themes,
                "total_themes": total,
                "execution_time_ms": round((time.time() - start) * 1000, 2),
            }

    except Exception as e:
        logger.exception(f"获取板块主题失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "execution_time_ms": round((time.time() - start) * 1000, 2),
        }


def get_children_of_node(
    parent_id: str,
    type_filter: str | None = None,
    include_sibling_themes: bool = False,
    top_k: int = 50,
) -> dict:
    import time
    start = time.time()

    try:
        top_k = min(max(1, top_k), 100)

        with get_neo4j_driver().session() as session:
            parent_info_cypher = """
            MATCH (parent {id: $parent_id})
            RETURN parent.id as id, parent.alias as alias,
                   labels(parent)[0] as type, parent.level as level,
                   parent.path as path
            """
            parent_result = session.run(
                parent_info_cypher, parent_id=parent_id
            ).single()

            if not parent_result:
                return {
                    "success": False,
                    "error": f"未找到节点 {parent_id}",
                    "execution_time_ms": round((time.time() - start) * 1000, 2),
                }

            if type_filter and type_filter in _NAVIGABLE_LABELS:
                child_where = f"labels(child)[0] = '{type_filter}'"
            else:
                labels_str = str(_NAVIGABLE_LABELS).replace("'", '"')
                child_where = f"labels(child)[0] IN {labels_str}"

            children_cypher = f"""
            MATCH (parent {{id: $parent_id}})-[:HAS_CHILD]->(child)
            WHERE {child_where}
            OPTIONAL MATCH (child)-[:HAS_CHILD]->(gc)
            WITH child, count(DISTINCT gc) as direct_child_count
            OPTIONAL MATCH (child)-[:HAS_CHILD*1..]->(t:THEME)
            WITH child, direct_child_count, count(DISTINCT t) as theme_child_count
            OPTIONAL MATCH (child)-[:HAS_CHILD*1..]->(i:INDICATOR)
            WITH child, direct_child_count, theme_child_count, count(DISTINCT i) as leaf_count
            RETURN child.id as id, child.alias as alias, labels(child)[0] as type,
                   child.level as level, child.path as path,
                   direct_child_count,
                   theme_child_count > 0 as has_theme_children,
                   leaf_count > 0 as has_leaf_children
            ORDER BY child.alias
            LIMIT $top_k
            """

            result = session.run(children_cypher, parent_id=parent_id, top_k=top_k)

            children = []
            for row in result:
                children.append({
                    "id": row["id"],
                    "alias": row["alias"],
                    "type": row["type"],
                    "level": row["level"],
                    "path": row["path"],
                    "direct_child_count": row["direct_child_count"] or 0,
                    "has_theme_children": row["has_theme_children"],
                    "has_leaf_children": row["has_leaf_children"],
                })

            has_more = len(children) == top_k

            sibling_themes = []
            if include_sibling_themes:
                sibling_cypher = """
                MATCH (parent {id: $parent_id})-[:HAS_CHILD]->(t:THEME)
                RETURN t.id as id, t.alias as alias, t.level as level, t.path as path
                ORDER BY t.alias
                LIMIT $top_k
                """
                sibling_result = session.run(
                    sibling_cypher, parent_id=parent_id, top_k=top_k
                )
                for row in sibling_result:
                    sibling_themes.append({
                        "id": row["id"],
                        "alias": row["alias"],
                        "level": row["level"],
                        "path": row["path"],
                    })

            return {
                "success": True,
                "parent_id": parent_result["id"],
                "parent_alias": parent_result["alias"],
                "parent_type": parent_result["type"],
                "parent_level": parent_result["level"],
                "parent_path": parent_result["path"],
                "children": children,
                "sibling_themes": sibling_themes,
                "total_children": len(children),
                "has_more": has_more,
                "execution_time_ms": round((time.time() - start) * 1000, 2),
            }

    except Exception as e:
        logger.exception(f"获取子节点失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "execution_time_ms": round((time.time() - start) * 1000, 2),
        }


def get_path_to_theme(
    theme_id: str,
    include_siblings: bool = True,
) -> dict:
    import time
    start = time.time()

    try:
        with get_neo4j_driver().session() as session:
            cypher = """
            MATCH path = (entry)-[:HAS_CHILD*]->(theme:THEME {id: $theme_id})
            WHERE entry.alias = $root_alias
              AND labels(entry)[0] IN $business_labels
            RETURN [node in nodes(path) | {
                id: node.id,
                alias: node.alias,
                type: labels(node)[0],
                level: node.level
            }] as path_nodes,
               length(path) as depth
            """
            result = session.run(
                cypher,
                theme_id=theme_id,
                root_alias=_ROOT_ENTRY_ALIAS,
                business_labels=_NAVIGABLE_LABELS,
            ).single()

            if not result:
                return {
                    "success": False,
                    "error": f"未找到主题 {theme_id} 的路径",
                    "execution_time_ms": round((time.time() - start) * 1000, 2),
                }

            path_nodes = result["path_nodes"]
            depth = result["depth"]

            theme_alias = None
            theme_idx = None
            parent_id = None
            parent_alias = None
            parent_type = None

            for i, node in enumerate(path_nodes):
                if node["type"] == "THEME" and node["id"] == theme_id:
                    theme_alias = node["alias"]
                    theme_idx = i
                    if i > 0:
                        parent_id = path_nodes[i - 1]["id"]
                        parent_alias = path_nodes[i - 1]["alias"]
                        parent_type = path_nodes[i - 1]["type"]
                    break

            path_node_list = []
            for i, node in enumerate(path_nodes):
                node_dict = {
                    "id": node["id"],
                    "alias": node["alias"],
                    "type": node["type"],
                    "level": node["level"],
                }
                if i == 0:
                    node_dict["is_entry"] = True
                if node["id"] == theme_id:
                    node_dict["is_target"] = True
                    node_dict["direct_parent_id"] = parent_id
                    node_dict["direct_parent_alias"] = parent_alias
                    node_dict["direct_parent_type"] = parent_type
                path_node_list.append(node_dict)

            full_path = " > ".join(node["alias"] for node in path_nodes)

            sibling_themes = []
            if include_siblings and parent_id:
                sibling_cypher = """
                MATCH (parent {id: $parent_id})-[:HAS_CHILD]->(t:THEME)
                WHERE t.id <> $theme_id
                RETURN t.id as id, t.alias as alias, t.level as level, t.path as path
                ORDER BY t.alias
                LIMIT 50
                """
                sibling_result = session.run(
                    sibling_cypher, parent_id=parent_id, theme_id=theme_id
                )
                for row in sibling_result:
                    sibling_themes.append({
                        "id": row["id"],
                        "alias": row["alias"],
                        "level": row["level"],
                        "path": row["path"],
                    })

            return {
                "success": True,
                "theme_id": theme_id,
                "theme_alias": theme_alias,
                "theme_level": (
                    path_nodes[theme_idx]["level"] if theme_idx is not None else 0
                ),
                "depth": depth,
                "full_path": full_path,
                "path_nodes": path_node_list,
                "sibling_themes": sibling_themes,
                "sibling_theme_count": len(sibling_themes),
                "execution_time_ms": round((time.time() - start) * 1000, 2),
            }

    except Exception as e:
        logger.exception(f"获取主题路径失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "execution_time_ms": round((time.time() - start) * 1000, 2),
        }
