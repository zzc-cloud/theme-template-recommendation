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


# ==================== 批量指标-主题映射 ====================


@mcp.tool(annotations={"readOnlyHint": True})
def batch_get_indicator_themes(indicator_ids: list, top_k: int = 20) -> str:
    """批量获取指标列表各自归属的 THEME 信息（去重）

    一次 Cypher 查询返回所有指标的 THEME 归属，避免逐指标循环查询。
    用于阶段 0.4 勾选引导的 Jaccard 相似度计算。

    Args:
        indicator_ids: 指标 ID 列表（来自阶段 0 的 matched_indicators）
        top_k: 每个维度最多返回的 theme 数量，默认 20

    Returns:
        {
            "success": true,
            "indicator_count": 10,
            "results": [
                {
                    "indicator_id": "INDICATOR.xxx",
                    "themes": [
                        {
                            "theme_id": "THEME.xxx",
                            "theme_alias": "主题别名",
                            "theme_path": "自主分析 > 板块 > 主题",
                            "frequency": 3
                        }
                    ]
                }
            ],
            "execution_time_ms": 120.5
        }

    说明：
    - 如果一个指标属于多个 THEME（通过不同路径可达），每个 THEME 均返回
    - 同一 indicator_id 的 themes 按 frequency（该指标在该 theme 下的命中频次）降序
    - 不同 indicator_id 的 results 顺序与输入 indicator_ids 一致
    """
    start = time.time()
    try:
        # 限制参数
        indicator_ids = indicator_ids[:100] if indicator_ids else []
        top_k = min(max(1, top_k), 50)

        if not indicator_ids:
            return json.dumps({
                "success": True,
                "indicator_count": 0,
                "results": [],
                "execution_time_ms": round((time.time() - start) * 1000, 2)
            }, ensure_ascii=False)

        with get_driver().session() as session:
            business_labels = ['SECTOR', 'CATEGORY', 'THEME', 'SUBPATH', 'INDICATOR',
                               'INSIGHT_TEMPLATE', 'COMBINEDQUERY_TEMPLATE']

            # 批量查询：所有指标共享一次图遍历
            cypher = """
            MATCH path = (entry)-[:HAS_CHILD*]->(indicator)
            WHERE entry.alias = '自主分析'
              AND indicator.id IN $indicator_ids
              AND labels(entry)[0] IN $business_labels
              AND labels(indicator)[0] IN $business_labels
            RETURN indicator.id as indicator_id,
                   [node in nodes(path) | {
                       id: node.id,
                       alias: node.alias,
                       type: labels(node)[0],
                       level: node.level
                   }] as path_nodes
            """
            result = session.run(
                cypher,
                indicator_ids=indicator_ids,
                business_labels=business_labels
            )

            # 按 indicator_id 分组，收集每个指标的所有路径
            indicator_paths: dict = {}
            for row in result:
                ind_id = row["indicator_id"]
                if ind_id not in indicator_paths:
                    indicator_paths[ind_id] = []
                indicator_paths[ind_id].append(row["path_nodes"])

            # 构建每个指标的 theme 映射
            results = []
            for indicator_id in indicator_ids:
                paths = indicator_paths.get(indicator_id, [])
                theme_map = {}

                for path_nodes in paths:
                    # 在路径中找 THEME 节点
                    for i, node in enumerate(path_nodes):
                        if node["type"] == "THEME":
                            theme_id = node["id"]
                            if theme_id not in theme_map:
                                # 构建从"自主分析"到该 THEME 的路径
                                theme_path_aliases = []
                                for j in range(i + 1):
                                    if path_nodes[j]["type"] != "INDICATOR":
                                        theme_path_aliases.append(path_nodes[j]["alias"])
                                theme_map[theme_id] = {
                                    "theme_id": theme_id,
                                    "theme_alias": node["alias"],
                                    "theme_path": " > ".join(theme_path_aliases),
                                    "frequency": 0
                                }
                            theme_map[theme_id]["frequency"] += 1

                # 按 frequency 降序，取 top_k
                themes = sorted(
                    theme_map.values(),
                    key=lambda x: x["frequency"],
                    reverse=True
                )[:top_k]

                results.append({
                    "indicator_id": indicator_id,
                    "themes": themes
                })

            return json.dumps({
                "success": True,
                "indicator_count": len(results),
                "results": results,
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


# ==================== 层级导航工具 ====================

_ROOT_ENTRY_ALIAS = "自主分析"
_NAVIGABLE_LABELS = ["SECTOR", "CATEGORY", "SUBPATH", "THEME"]


@mcp.tool(annotations={"readOnlyHint": True})
def get_sector_themes(
    sector_id: str,
    top_k: int = 100
) -> str:
    """获取指定板块下所有层级的 THEME 节点（批量一次查询）

    直接查询指定 SECTOR 下所有深度的 THEME 节点，无需逐层探索。
    用于主题模板推荐 Skill 阶段 1.2 层级导航。

    Args:
        sector_id: SECTOR 节点 ID
        top_k: 返回主题数量上限，默认 100，最大 500

    Returns:
        {
            "success": true,
            "sector_id": "SECTOR.xxx",
            "sector_alias": "资产板块",
            "themes": [
                {
                    "theme_id": "THEME.xxx",
                    "theme_alias": "对公贷款借据",
                    "theme_level": 5,
                    "depth": 4,
                    "parent_alias": "对公贷款",
                    "parent_type": "CATEGORY",
                    "full_path": "自主分析 > 资产板块 > 对公贷款 > 对公贷款借据"
                }
            ],
            "total_themes": 52,
            "execution_time_ms": 45.2
        }
    """
    start = time.time()
    try:
        top_k = min(max(1, top_k), 500)

        with get_driver().session() as session:
            # 先获取板块信息
            sector_info_cypher = """
            MATCH (sector:SECTOR {id: $sector_id})
            RETURN sector.id as id, sector.alias as alias,
                   sector.level as level, sector.path as path
            """
            sector_result = session.run(sector_info_cypher, sector_id=sector_id).single()

            if not sector_result:
                return json.dumps({
                    "success": False,
                    "error": f"未找到板块 {sector_id}",
                    "execution_time_ms": round((time.time() - start) * 1000, 2)
                }, ensure_ascii=False)

            # 批量获取所有主题（含完整路径）
            themes_cypher = """
            MATCH path = (sector:SECTOR {id: $sector_id})-[:HAS_CHILD*]->(theme:THEME)
            MATCH (theme)<-[:HAS_CHILD]-(parent_node)
            WHERE labels(parent_node)[0] IN ['CATEGORY', 'SUBPATH', 'THEME']

            WITH sector, theme, parent_node, nodes(path) as path_nodes,
                 [n IN nodes(path) WHERE labels(n)[0] <> 'INDICATOR'] as non_ind_nodes

            WITH sector, theme, parent_node, non_ind_nodes,
                 reduce(s = '', item IN [n IN non_ind_nodes | n.alias] | s + CASE WHEN s = '' THEN item ELSE ' > ' + item END) as full_path,
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
                    "full_path": row["full_path"]
                })

            # 获取总数（用于判断是否截断）
            count_cypher = """
            MATCH (sector:SECTOR {id: $sector_id})-[:HAS_CHILD*]->(theme:THEME)
            RETURN count(theme) as total
            """
            count_result = session.run(count_cypher, sector_id=sector_id).single()
            total = count_result["total"] if count_result else 0

            return json.dumps({
                "success": True,
                "sector_id": sector_result["id"],
                "sector_alias": sector_result["alias"],
                "sector_path": sector_result["path"].replace(".", " > "),
                "themes": themes,
                "total_themes": total,
                "execution_time_ms": round((time.time() - start) * 1000, 2)
            }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "execution_time_ms": round((time.time() - start) * 1000, 2)
        }, ensure_ascii=False)


@mcp.tool(annotations={"readOnlyHint": True})
def get_sectors_from_root() -> str:
    """获取"自主分析"下的所有 SECTOR（板块）节点

    用于层级导航的起点，展示所有可探索的板块。

    Returns:
        {
            "success": true,
            "root_alias": "自主分析",
            "sectors": [
                {
                    "id": "SECTOR.xxx",
                    "alias": "资产板块",
                    "level": 2,
                    "path": "自主分析 > 资产板块",
                    "direct_child_count": 5,
                    "has_theme_children": true
                }
            ],
            "total_sectors": 6,
            "execution_time_ms": 45.2
        }
    """
    start = time.time()
    try:
        with get_driver().session() as session:
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
                    "has_theme_children": row["has_theme_children"]
                })

            return json.dumps({
                "success": True,
                "root_alias": _ROOT_ENTRY_ALIAS,
                "sectors": sectors,
                "total_sectors": len(sectors),
                "execution_time_ms": round((time.time() - start) * 1000, 2)
            }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool(annotations={"readOnlyHint": True})
def get_children_of_node(
    parent_id: str,
    type_filter: str = None,
    include_sibling_themes: bool = False,
    top_k: int = 50
) -> str:
    """获取指定节点的直接子节点

    用于层级导航中的逐层向下探索。可按节点类型过滤，
    并可选返回同级 THEMEs 以便对比选择。

    Args:
        parent_id: 父节点 ID（如 SECTOR.xxx 或 CATEGORY.xxx）
        type_filter: 节点类型过滤，可选值：
            SECTOR / CATEGORY / SUBPATH / THEME
            若为 None，则返回所有类型的子节点
        include_sibling_themes: 是否返回父节点下的所有 THEME 兄弟节点，
            当 type_filter='THEME' 时生效，用于展示同级的候选主题供对比
        top_k: 返回数量上限（仅对 sibling_themes 生效），默认 50

    Returns:
        {
            "success": true,
            "parent_id": "SECTOR.xxx",
            "parent_alias": "资产板块",
            "parent_type": "SECTOR",
            "parent_path": "自主分析 > 资产板块",
            "children": [
                {
                    "id": "CATEGORY.xxx",
                    "alias": "对公贷款",
                    "type": "CATEGORY",
                    "level": 3,
                    "path": "自主分析 > 资产板块 > 对公贷款",
                    "direct_child_count": 12,
                    "has_theme_children": true,
                    "has_leaf_children": false
                }
            ],
            "sibling_themes": [],
            "total_children": 8,
            "has_more": false,
            "execution_time_ms": 45.2
        }
    """
    start = time.time()
    try:
        top_k = min(max(1, top_k), 100)

        with get_driver().session() as session:
            # 先获取父节点信息
            parent_info_cypher = """
            MATCH (parent {id: $parent_id})
            RETURN parent.id as id, parent.alias as alias,
                   labels(parent)[0] as type, parent.level as level,
                   parent.path as path
            """
            parent_result = session.run(parent_info_cypher, parent_id=parent_id).single()

            if not parent_result:
                return json.dumps({
                    "success": False,
                    "error": f"未找到节点 {parent_id}",
                    "execution_time_ms": round((time.time() - start) * 1000, 2)
                }, ensure_ascii=False)

            # 构建子节点查询
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
                    "has_leaf_children": row["has_leaf_children"]
                })

            has_more = len(children) == top_k

            # 可选获取父节点下的所有 THEME 兄弟节点
            sibling_themes = []
            if include_sibling_themes:
                sibling_cypher = """
                MATCH (parent {id: $parent_id})-[:HAS_CHILD]->(t:THEME)
                RETURN t.id as id, t.alias as alias, t.level as level, t.path as path
                ORDER BY t.alias
                LIMIT $top_k
                """
                sibling_result = session.run(sibling_cypher, parent_id=parent_id, top_k=top_k)
                for row in sibling_result:
                    sibling_themes.append({
                        "id": row["id"],
                        "alias": row["alias"],
                        "level": row["level"],
                        "path": row["path"]
                    })

            return json.dumps({
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
                "execution_time_ms": round((time.time() - start) * 1000, 2)
            }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool(annotations={"readOnlyHint": True})
def get_path_to_theme(
    theme_id: str,
    include_siblings: bool = True
) -> str:
    """获取从"自主分析"到指定主题的完整导航路径

    返回路径上的所有中间节点，并为每个非叶子层级提供同级兄弟节点，
    使 LLM 能够理解该主题在整个层级中的位置和相邻主题。

    Args:
        theme_id: THEME 节点 ID
        include_siblings: 是否返回同级 THEMEs，默认 True

    Returns:
        {
            "success": true,
            "theme_id": "THEME.xxx",
            "theme_alias": "对公贷款借据",
            "depth": 4,
            "full_path": "自主分析 > 资产板块 > 对公贷款 > 对公贷款借据",
            "path_nodes": [
                {
                    "alias": "自主分析",
                    "type": "CATEGORY",
                    "level": 1,
                    "is_entry": true
                },
                {
                    "alias": "资产板块",
                    "type": "SECTOR",
                    "level": 2
                },
                {
                    "alias": "对公贷款",
                    "type": "CATEGORY",
                    "level": 3
                },
                {
                    "alias": "对公贷款借据",
                    "type": "THEME",
                    "level": 5,
                    "is_target": true,
                    "direct_parent_alias": "对公贷款",
                    "direct_parent_type": "CATEGORY"
                }
            ],
            "sibling_themes": [
                {"id": "THEME.yyy", "alias": "对公贷款客户风险", "level": 4}
            ],
            "sibling_theme_count": 1,
            "execution_time_ms": 45.2
        }
    """
    start = time.time()
    try:
        with get_driver().session() as session:
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
                cypher, theme_id=theme_id, root_alias=_ROOT_ENTRY_ALIAS,
                business_labels=_NAVIGABLE_LABELS
            ).single()

            if not result:
                return json.dumps({
                    "success": False,
                    "error": f"未找到主题 {theme_id} 的路径",
                    "execution_time_ms": round((time.time() - start) * 1000, 2)
                }, ensure_ascii=False)

            path_nodes = result["path_nodes"]
            depth = result["depth"]

            # 找到 THEME 节点及其在路径中的索引
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

            # 构建 path_nodes 输出，带标记
            path_node_list = []
            for i, node in enumerate(path_nodes):
                node_dict = {
                    "id": node["id"],
                    "alias": node["alias"],
                    "type": node["type"],
                    "level": node["level"]
                }
                if i == 0:
                    node_dict["is_entry"] = True
                if node["id"] == theme_id:
                    node_dict["is_target"] = True
                    node_dict["direct_parent_id"] = parent_id
                    node_dict["direct_parent_alias"] = parent_alias
                    node_dict["direct_parent_type"] = parent_type
                path_node_list.append(node_dict)

            # 构建完整路径字符串
            full_path = " > ".join(node["alias"] for node in path_nodes)

            # 获取同级 THEMEs
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
                        "path": row["path"]
                    })

            return json.dumps({
                "success": True,
                "theme_id": theme_id,
                "theme_alias": theme_alias,
                "theme_level": path_nodes[theme_idx]["level"] if theme_idx is not None else 0,
                "depth": depth,
                "full_path": full_path,
                "path_nodes": path_node_list,
                "sibling_themes": sibling_themes,
                "sibling_theme_count": len(sibling_themes),
                "execution_time_ms": round((time.time() - start) * 1000, 2)
            }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
