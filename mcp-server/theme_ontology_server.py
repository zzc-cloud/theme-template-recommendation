#!/usr/bin/env python3
"""
Theme Template Recommendation - Neo4j MCP 服务器

仅保留执行流程中使用的工具：
- 阶段 1.1：aggregate_themes_from_indicators
- 阶段 1.2：get_theme_filter_indicators, get_theme_analysis_indicators
- 阶段 2：get_theme_templates_with_coverage
"""

from mcp.server.fastmcp import FastMCP
from neo4j import GraphDatabase
import json
import time
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

# 创建 MCP 服务器实例
mcp = FastMCP("theme-ontology")

# Neo4j 连接配置
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

# Neo4j 驱动（单例）
_driver = None


def get_driver():
    """获取 Neo4j 驱动（单例）"""
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USER, NEO4J_PASSWORD)
        )
    return _driver


# ==================== 阶段 2：模板推荐 ====================

@mcp.tool(annotations={"readOnlyHint": True})
def get_theme_templates_with_coverage(
    theme_id: str,
    matched_indicator_aliases: list,
    template_type: str = None,
    top_k: int = 10
) -> str:
    """获取主题下的模板，并计算与匹配指标的覆盖率

    用于主题模板推荐 Skill 的阶段 2，在推荐主题范围内推荐模板。

    Args:
        theme_id: 主题 ID
        matched_indicator_aliases: 用户匹配的指标别名列表
        template_type: 模板类型过滤（"INSIGHT" / "COMBINEDQUERY" / None 全部）
        top_k: 返回数量，默认 10

    Returns:
        {
            "success": true,
            "theme_id": "...",
            "has_qualified_templates": true,  // 是否有覆盖率 >= 80% 的达标模板
            "matched_templates": [...],       // 达标模板（>= 80%）或降级推荐模板
            "fallback_reason": "...",         // 降级原因（仅当无达标模板时）
            "execution_time_ms": 45.2
        }

    过滤逻辑：
    1. 先过滤掉 heat = 0 的模板（无使用记录不参与推荐）
    2. 在 heat > 0 的模板中查找覆盖率 >= 80% 的达标模板
    3. 如果没有 >= 80% 的模板，降级推荐：
       - 覆盖率最高的模板（1个）
       - 热度最高的模板（1个，如果与覆盖率最高不同）

    注意：覆盖率计算基于指标别名匹配，而非 ID 匹配。
    """
    start = time.time()
    try:
        # 限制参数
        matched_indicator_aliases = matched_indicator_aliases[:100] if matched_indicator_aliases else []
        top_k = min(max(1, top_k), 50)

        with get_driver().session() as session:
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
                cypher,
                theme_id=theme_id,
                top_k=top_k
            )

            all_templates = []
            for row in result:
                template_indicators = row["template_indicators"] or []
                template_indicator_aliases = set(i["alias"] for i in template_indicators if i.get("alias"))

                # 覆盖率 = 模板覆盖的用户指标别名数 / 用户需要的指标别名总数
                covered_aliases = list(user_indicator_set & template_indicator_aliases)
                matched_count = len(covered_aliases)
                coverage_ratio = matched_count / user_indicator_count if user_indicator_count > 0 else 0.0

                # 缺失指标别名 = 用户指标别名 - 模板指标别名交集
                missing_aliases = list(user_indicator_set - template_indicator_aliases)

                all_templates.append({
                    "template_id": row["template_id"],
                    "template_alias": row["template_alias"],
                    "template_description": row["template_description"],
                    "usage_count": row["usage_count"] or 0,
                    "coverage_ratio": round(coverage_ratio, 3),
                    "covered_indicator_aliases": covered_aliases,
                    "missing_indicator_aliases": missing_aliases,
                    "all_template_indicators": [
                        {
                            "indicator_id": i.get("id", ""),
                            "alias": i.get("alias", ""),
                            "description": i.get("description", "")
                        }
                        for i in template_indicators
                        if i.get("id")
                    ]
                })

            # 过滤出覆盖率 >= 80% 的达标模板
            qualified_templates = [t for t in all_templates if t["coverage_ratio"] >= 0.8]

            if qualified_templates:
                # 有达标模板，按覆盖率降序排序
                qualified_templates.sort(key=lambda x: x["coverage_ratio"], reverse=True)
                return json.dumps({
                    "success": True,
                    "theme_id": theme_id,
                    "template_type": template_type,
                    "has_qualified_templates": True,
                    "matched_templates": qualified_templates,
                    "matched_template_count": len(qualified_templates),
                    "execution_time_ms": round((time.time() - start) * 1000, 2)
                }, ensure_ascii=False)
            else:
                # 无达标模板，降级推荐：覆盖率最高 + 热度最高
                if not all_templates:
                    return json.dumps({
                        "success": True,
                        "theme_id": theme_id,
                        "template_type": template_type,
                        "has_qualified_templates": False,
                        "matched_templates": [],
                        "matched_template_count": 0,
                        "fallback_reason": "该主题下无热度大于 0 的模板",
                        "execution_time_ms": round((time.time() - start) * 1000, 2)
                    }, ensure_ascii=False)

                # 按覆盖率降序排序，取覆盖率最高的
                sorted_by_coverage = sorted(all_templates, key=lambda x: x["coverage_ratio"], reverse=True)
                highest_coverage = sorted_by_coverage[0]

                # 按热度降序排序，取热度最高的
                sorted_by_heat = sorted(all_templates, key=lambda x: x["usage_count"], reverse=True)
                highest_heat = sorted_by_heat[0]

                # 去重合并
                fallback_templates = [highest_coverage]
                if highest_heat["template_id"] != highest_coverage["template_id"]:
                    fallback_templates.append(highest_heat)

                return json.dumps({
                    "success": True,
                    "theme_id": theme_id,
                    "template_type": template_type,
                    "has_qualified_templates": False,
                    "matched_templates": fallback_templates,
                    "matched_template_count": len(fallback_templates),
                    "fallback_reason": f"无覆盖率 >= 80% 的达标模板，降级推荐覆盖率最高（{highest_coverage['coverage_ratio']*100:.0f}%）和热度最高（{highest_heat['usage_count']}次使用）的模板",
                    "execution_time_ms": round((time.time() - start) * 1000, 2)
                }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


# ==================== 主题指标补全工具 ====================


@mcp.tool(annotations={"readOnlyHint": True})
def get_theme_filter_indicators(theme_id: str) -> str:
    """获取主题下全量的筛选指标

    筛选指标包括时间筛选指标和机构筛选指标，通过指标别名模糊匹配识别。
    用于 1.2.1 全量指标补全场景。

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

    筛选指标识别规则：
    - 时间筛选指标：别名包含 "数据日期" 或 "ETL数据日期"
    - 机构筛选指标：别名匹配模式 "%级机构名称/%级机构编号/%管理机构名称/%管理机构编号/%账务机构名称/%账务机构编号"

    注意：返回结果按别名去重，同一别名的指标只保留一个。
    """
    start = time.time()
    try:
        with get_driver().session() as session:
            # 获取主题下全量 INDICATOR（通过 HAS_CHILD 关系，支持直接和间接连接）
            # THEME --HAS_CHILD--> INDICATOR（直接）
            # THEME --HAS_CHILD--> SUBPATH --HAS_CHILD--> INDICATOR（间接）
            cypher = """
            MATCH (theme:THEME {id: $theme_id})
            MATCH (theme)-[:HAS_CHILD*1..2]->(i:INDICATOR)
            RETURN i.id as id, i.alias as alias,
                   i.description as description
            """
            result = session.run(cypher, theme_id=theme_id)
            indicators = [dict(r) for r in result]

            if not indicators:
                return json.dumps({
                    "success": True,
                    "theme_id": theme_id,
                    "time_filter_indicators": [],
                    "org_filter_indicators": [],
                    "total_count": 0,
                    "execution_time_ms": round((time.time() - start) * 1000, 2)
                }, ensure_ascii=False)

            # 时间筛选指标：别名包含 "数据日期" 或 "ETL数据日期"
            time_patterns = ["数据日期", "ETL数据日期"]

            # 机构筛选指标：别名匹配以下任一模式
            org_patterns = [
                "机构名称", "机构编号",
                "管理机构名称", "管理机构编号",
                "账务机构名称", "账务机构编号"
            ]

            # 按别名去重：同一个别名只保留第一个
            seen_aliases = set()
            time_filter = []
            org_filter = []

            for ind in indicators:
                alias = ind.get("alias", "") or ""

                # 按别名去重
                if alias in seen_aliases:
                    continue
                seen_aliases.add(alias)

                # 时间筛选匹配
                is_time = any(p in alias for p in time_patterns)
                if is_time:
                    time_filter.append({
                        "id": ind["id"],
                        "alias": alias,
                        "description": ind.get("description") or ""
                    })

                # 机构筛选匹配
                is_org = any(p in alias for p in org_patterns)
                if is_org:
                    org_filter.append({
                        "id": ind["id"],
                        "alias": alias,
                        "description": ind.get("description") or ""
                    })

        return json.dumps({
            "success": True,
            "theme_id": theme_id,
            "time_filter_indicators": time_filter,
            "org_filter_indicators": org_filter,
            "time_filter_count": len(time_filter),
            "org_filter_count": len(org_filter),
            "total_count": len(indicators),
            "execution_time_ms": round((time.time() - start) * 1000, 2)
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool(annotations={"readOnlyHint": True})
def get_theme_analysis_indicators(theme_id: str) -> str:
    """获取主题下全量的分析指标

    分析指标指除筛选指标之外的所有业务分析指标，用于数据分析场景。
    用于 1.2.1 全量指标补全场景。

    Args:
        theme_id: THEME 节点 ID

    Returns:
        {
            "success": true,
            "theme_id": "...",
            "analysis_indicators": [
                {
                    "id": "INDICATOR.xxx",
                    "alias": "贷款余额",
                    "description": "..."
                }
            ],
            "total_count": 150,
            "execution_time_ms": 45.2
        }

    说明：
    - 分析指标 = 主题全量指标 - 筛选指标（时间 + 机构）
    - 返回结果按指标别名排序，便于浏览
    - 返回结果按别名去重，同一别名的指标只保留一个。
    """
    start = time.time()
    try:
        with get_driver().session() as session:
            # 获取主题下全量 INDICATOR（通过 HAS_CHILD 关系，支持直接和间接连接）
            cypher = """
            MATCH (theme:THEME {id: $theme_id})
            MATCH (theme)-[:HAS_CHILD*1..2]->(i:INDICATOR)
            RETURN i.id as id, i.alias as alias,
                   i.description as description
            ORDER BY i.alias
            """
            result = session.run(cypher, theme_id=theme_id)
            indicators = [dict(r) for r in result]

            if not indicators:
                return json.dumps({
                    "success": True,
                    "theme_id": theme_id,
                    "analysis_indicators": [],
                    "total_count": 0,
                    "execution_time_ms": round((time.time() - start) * 1000, 2)
                }, ensure_ascii=False)

            # 筛选指标识别规则（与分析指标互斥）
            time_patterns = ["数据日期", "ETL数据日期"]
            org_patterns = [
                "机构名称", "机构编号",
                "管理机构名称", "管理机构编号",
                "账务机构名称", "账务机构编号"
            ]

            # 按别名去重：同一个别名只保留第一个
            seen_aliases = set()
            analysis_indicators = []
            for ind in indicators:
                alias = ind.get("alias", "") or ""

                # 按别名去重
                if alias in seen_aliases:
                    continue
                seen_aliases.add(alias)

                # 排除筛选指标：只要不匹配任一筛选模式，即为分析指标
                is_time_filter = any(p in alias for p in time_patterns)
                is_org_filter = any(p in alias for p in org_patterns)

                if not (is_time_filter or is_org_filter):
                    analysis_indicators.append({
                        "id": ind["id"],
                        "alias": alias,
                        "description": ind.get("description") or ""
                    })

        return json.dumps({
            "success": True,
            "theme_id": theme_id,
            "analysis_indicators": analysis_indicators,
            "analysis_indicator_count": len(analysis_indicators),
            "total_theme_indicators": len(indicators),
            "execution_time_ms": round((time.time() - start) * 1000, 2)
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


# ==================== 主题聚合工具 ====================


def _get_indicator_theme_internal(indicator_id: str) -> dict:
    """内部方法：获取单个指标的 THEME 信息及完整路径

    从业务路径中提取 type=THEME 的节点，并构建从"自主分析"到该主题的完整路径。
    """
    try:
        with get_driver().session() as session:
            business_labels = ['SECTOR', 'CATEGORY', 'THEME', 'SUBPATH', 'INDICATOR',
                               'INSIGHT_TEMPLATE', 'COMBINEDQUERY_TEMPLATE']

            cypher = """
            MATCH path = (entry)-[:HAS_CHILD*]->(indicator)
            WHERE entry.alias = '自主分析' AND indicator.id = $indicator_id
              AND labels(entry)[0] IN $business_labels
              AND labels(indicator)[0] IN $business_labels
            RETURN [node in nodes(path) | {
                id: node.id,
                alias: node.alias,
                type: labels(node)[0],
                level: node.level
            }] as path_nodes
            """
            result = session.run(cypher, indicator_id=indicator_id, business_labels=business_labels).single()

            if not result:
                return None

            # 从路径节点中提取 THEME 类型的节点，同时构建完整路径
            path_nodes = result["path_nodes"]
            theme_info = None
            path_aliases = []

            for node in path_nodes:
                node_type = node["type"]
                node_alias = node["alias"]

                # 收集路径上的别名（排除 INDICATOR 类型）
                if node_type != "INDICATOR":
                    path_aliases.append(node_alias)

                # 提取 THEME 节点信息
                if node_type == "THEME":
                    theme_info = {
                        "indicator_id": indicator_id,
                        "theme_id": node["id"],
                        "theme_alias": node["alias"],
                        "theme_level": node.get("level"),
                    }

            if theme_info:
                # 构建完整路径字符串（从"自主分析"到 THEME）
                # 路径格式：自主分析 > 板块 > 类别 > ... > 主题
                theme_idx = None
                for i, node in enumerate(path_nodes):
                    if node["type"] == "THEME" and node["id"] == theme_info["theme_id"]:
                        theme_idx = i
                        break

                if theme_idx is not None:
                    # 截取从"自主分析"到 THEME 的路径
                    theme_path_aliases = []
                    for i, node in enumerate(path_nodes[:theme_idx + 1]):
                        if node["type"] != "INDICATOR":
                            theme_path_aliases.append(node["alias"])
                    theme_info["theme_path"] = " > ".join(theme_path_aliases)
                else:
                    theme_info["theme_path"] = f"自主分析 > {theme_info['theme_alias']}"

            return theme_info
    except Exception:
        return None


@mcp.tool(annotations={"readOnlyHint": True})
def get_theme_full_path(theme_id: str) -> str:
    """获取主题从"自主分析"到该主题的完整路径

    用于在推荐主题时展示完整路径，方便用户在魔数师平台中快速定位主题。

    Args:
        theme_id: THEME 节点 ID

    Returns:
        {
            "success": true,
            "theme_id": "THEME.xxx",
            "theme_alias": "对公贷款借据",
            "theme_path": "自主分析 > 资产板块 > 对公贷款 > 对公贷款借据",
            "path_nodes": [
                {"alias": "自主分析", "type": "SECTOR"},
                {"alias": "资产板块", "type": "CATEGORY"},
                {"alias": "对公贷款", "type": "SUBPATH"},
                {"alias": "对公贷款借据", "type": "THEME"}
            ],
            "execution_time_ms": 45.2
        }
    """
    start = time.time()
    try:
        with get_driver().session() as session:
            business_labels = ['SECTOR', 'CATEGORY', 'THEME', 'SUBPATH']

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
            result = session.run(cypher, theme_id=theme_id, business_labels=business_labels).single()

            if not result or not result.get("path_nodes"):
                return json.dumps({
                    "success": False,
                    "error": f"未找到主题 {theme_id} 的路径信息",
                    "execution_time_ms": round((time.time() - start) * 1000, 2)
                }, ensure_ascii=False)

            path_nodes = result["path_nodes"]

            # 提取 THEME 节点信息
            theme_alias = None
            for node in path_nodes:
                if node["type"] == "THEME":
                    theme_alias = node["alias"]
                    break

            # 构建完整路径
            path_aliases = [node["alias"] for node in path_nodes]
            theme_path = " > ".join(path_aliases)

            # 构建路径节点列表（用于展示）
            path_node_list = [
                {
                    "alias": node["alias"],
                    "type": node["type"]
                }
                for node in path_nodes
            ]

            return json.dumps({
                "success": True,
                "theme_id": theme_id,
                "theme_alias": theme_alias,
                "theme_path": theme_path,
                "path_nodes": path_node_list,
                "execution_time_ms": round((time.time() - start) * 1000, 2)
            }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool(annotations={"readOnlyHint": True})
def aggregate_themes_from_indicators(matched_indicators: list, top_k: int = 3) -> str:
    """从指标列表中聚合候选主题（按频次排序）

    对 matched_indicators 中的每个指标调用 _get_indicator_theme_internal 获取其 THEME 信息，
    统计各 THEME 出现频次，按频次降序排列，取 Top K 作为初始候选主题。

    用于主题模板推荐 Skill 阶段 1.1 主题统计聚合。

    Args:
        matched_indicators: 指标 ID 列表（来自阶段 0 的 matched_indicators）
        top_k: 返回候选主题数量，默认 3

    Returns:
        {
            "success": true,
            "candidate_themes": [
                {
                    "theme_id": "THEME.xxx",
                    "theme_alias": "对公贷款借据",
                    "theme_level": 5,
                    "theme_path": "自主分析 > 资产板块 > 对公贷款 > 对公贷款借据",
                    "frequency": 15,
                    "matched_indicator_ids": ["INDICATOR.xxx", "..."]
                }
            ],
            "total_themes": 5,
            "total_indicators": 30,
            "execution_time_ms": 120.5
        }
    """
    start = time.time()
    try:
        top_k = min(max(1, top_k), 20)
        # 限制最多 100 个指标
        matched_indicators = matched_indicators[:100] if matched_indicators else []

        if not matched_indicators:
            return json.dumps({
                "success": True,
                "candidate_themes": [],
                "total_themes": 0,
                "total_indicators": 0,
                "execution_time_ms": round((time.time() - start) * 1000, 2)
            }, ensure_ascii=False)

        # Python 循环：每个指标调用一次 _get_indicator_theme_internal
        theme_map: dict = {}  # theme_id -> {theme_alias, theme_level, theme_path, matched_indicator_ids}

        for indicator_id in matched_indicators:
            result = _get_indicator_theme_internal(indicator_id)
            if result:
                theme_id = result["theme_id"]
                if theme_id not in theme_map:
                    theme_map[theme_id] = {
                        "theme_alias": result["theme_alias"],
                        "theme_level": result.get("theme_level"),
                        "theme_path": result.get("theme_path", f"自主分析 > {result['theme_alias']}"),
                        "matched_indicator_ids": []
                    }
                theme_map[theme_id]["matched_indicator_ids"].append(indicator_id)

        # 按频次降序排列，取 Top K
        sorted_themes = sorted(
            theme_map.items(),
            key=lambda x: len(x[1]["matched_indicator_ids"]),
            reverse=True
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
                "matched_indicator_ids": matched_ids
            })

        return json.dumps({
            "success": True,
            "candidate_themes": candidate_themes,
            "total_themes": len(candidate_themes),
            "total_indicators": len(matched_indicators),
            "execution_time_ms": round((time.time() - start) * 1000, 2)
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
