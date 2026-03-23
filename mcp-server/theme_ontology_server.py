#!/usr/bin/env python3
"""
Theme Template Recommendation - 精简版 Neo4j MCP 服务器

包含 10 个工具，用于主题模板推荐：
- 阶段 0：search_terms_by_keyword, get_tables_by_term
- 阶段 1：get_indicator_full_path, get_indicator_field_mapping, get_table_terms
- 阶段 2：aggregate_themes_from_indicators
- 阶段 3：get_theme_templates_with_coverage, get_template_indicators
- 指标补全：get_theme_filter_indicators, get_theme_analysis_indicators
"""

from mcp.server.fastmcp import FastMCP
from neo4j import GraphDatabase
import json
import time
import os
import sys
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


# ==================== 阶段 0：需求澄清 ====================

@mcp.tool(annotations={"readOnlyHint": True})
def search_terms_by_keyword(keyword: str, search_type: str = "all", top_k: int = 100) -> str:
    """搜索业务术语（术语驱动路径入口）

    结合关键词和模糊匹配，返回匹配的术语列表。
    术语是连接用户业务语言与物理字段的桥梁。

    Args:
        keyword: 搜索关键词（支持中文或英文）
        search_type: 搜索类型 - "all"(全部)、"cn"(中文名)、"en"(英文名)，默认"all"
        top_k: 返回结果数量，默认100（可根据需要调整，最大200）
    """
    start = time.time()
    try:
        # 限制 top_k 最大值为 200
        top_k = min(max(1, top_k), 200)
        results = []
        seen_ids = set()

        with get_driver().session() as session:
            # 根据搜索类型选择匹配字段
            if search_type == "cn":
                where_clause = "t.cn_name CONTAINS $keyword"
            elif search_type == "en":
                where_clause = "t.en_name CONTAINS $keyword"
            else:
                where_clause = "t.cn_name CONTAINS $keyword OR t.en_name CONTAINS $keyword"

            cypher = f"""
            MATCH (t:TERM)
            WHERE {where_clause}
            RETURN t.id as id, t.en_name as en_name, t.cn_name as cn_name,
                   t.description as description, t.standard_id as standard_id
            LIMIT $top_k
            """
            terms = [dict(r) for r in session.run(cypher, keyword=keyword, top_k=top_k)]

            # 获取每个术语关联的表数量
            for term in terms:
                term_id = term["id"]
                table_cypher = """
                MATCH (t:TERM {id: $term_id})<-[:HAS_TERM]-(table:TABLE)
                RETURN count(DISTINCT table) as table_count
                """
                table_count_result = session.run(table_cypher, term_id=term_id).single()
                term["table_count"] = table_count_result["table_count"] if table_count_result else 0
                term["match_type"] = "keyword"

                # 获取关联的标准信息（如果有）
                if term.get("standard_id"):
                    std_cypher = """
                    MATCH (std:DATA_STANDARD) WHERE std.id = $standard_id
                    RETURN std.cn_name as standard_name, std.type as standard_type
                    """
                    std_result = session.run(std_cypher, standard_id=f"STANDARD.{term['standard_id']}")
                    std_data = std_result.single()
                    if std_data:
                        term["standard_name"] = std_data["standard_name"]
                        term["standard_type"] = std_data["standard_type"]

                results.append(term)
                seen_ids.add(term_id)

        return json.dumps({
            "success": True, "keyword": keyword, "search_type": search_type,
            "count": len(results), "terms": results,
            "execution_time_ms": round((time.time() - start) * 1000, 2)
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool(annotations={"readOnlyHint": True})
def get_tables_by_term(term_en_name: str = None, term_cn_name: str = None, schema_name: str = None) -> str:
    """根据术语获取关联的表（术语驱动路径核心方法）

    通过业务术语（英文或中文名）查找包含该术语字段的表。
    这是术语驱动查询的核心入口，将用户业务语言映射到物理表。

    Args:
        term_en_name: 术语英文名（如 cust_id）
        term_cn_name: 术语中文名（如 客户编号）
        schema_name: 可选，过滤 Schema

    Examples:
        # 查找包含"客户编号"术语的所有表
        get_tables_by_term(term_cn_name="客户编号")

        # 查找特定字段名的表
        get_tables_by_term(term_en_name="cust_id")

        # 在特定 Schema 中查找
        get_tables_by_term(term_cn_name="客户", schema_name="dmrbm_data")
    """
    start = time.time()
    try:
        # 参数校验
        if not term_en_name and not term_cn_name:
            return json.dumps({
                "success": False, "error": "必须提供 term_en_name 或 term_cn_name 参数"
            }, ensure_ascii=False, indent=2)

        with get_driver().session() as session:
            # 构建查询条件
            conditions = []
            params = {}

            if term_en_name:
                conditions.append("term.en_name = $term_en_name")
                params["term_en_name"] = term_en_name

            if term_cn_name:
                conditions.append("term.cn_name CONTAINS $term_cn_name")
                params["term_cn_name"] = term_cn_name

            where_clause = f"WHERE {' AND '.join(conditions)}"

            schema_clause = ""
            if schema_name:
                schema_clause = "AND table.schema = $schema_name"
                params["schema_name"] = schema_name

            cypher = f"""
            MATCH (term:TERM)<-[:HAS_TERM]-(table:TABLE)
            {where_clause} {schema_clause}
            RETURN DISTINCT table.name as name, table.schema as schema,
                            table.description as description,
                            table.id as table_id
            ORDER BY table.schema, table.name
            """
            tables = [dict(r) for r in session.run(cypher, **params)]

            # 获取每个表的匹配术语详情
            for table in tables:
                where_cause = conditions[0]
                if len(conditions) > 1:
                    where_cause += " OR " + " AND ".join(conditions[1:])

                term_details_cypher = f"""
                MATCH (t:TABLE {{id: $table_id}})-[:HAS_TERM]->(term:TERM)
                WHERE {where_cause}
                RETURN term.en_name as field_name, term.cn_name as term_cn_name
                ORDER BY term.en_name
                """
                # 传递必要的参数
                term_params = {"table_id": table["table_id"]}
                if term_en_name:
                    term_params["term_en_name"] = term_en_name
                if term_cn_name:
                    term_params["term_cn_name"] = term_cn_name

                term_details = [dict(r) for r in session.run(term_details_cypher, **term_params)]
                table["matched_terms"] = term_details

        return json.dumps({
            "success": True,
            "term_en_name": term_en_name,
            "term_cn_name": term_cn_name,
            "schema_name": schema_name,
            "count": len(tables),
            "tables": tables,
            "execution_time_ms": round((time.time() - start) * 1000, 2)
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


# ==================== 阶段 1：指标信息获取 ====================

@mcp.tool(annotations={"readOnlyHint": True})
def get_indicator_full_path(indicator_id: str) -> str:
    """获取指标的完整业务路径（用于 Smart BI 拖拉拽）

    Args:
        indicator_id: 指标节点ID
    """
    start = time.time()
    try:
        with get_driver().session() as session:
            business_labels = ['SECTOR', 'CATEGORY', 'THEME', 'SUBPATH', 'INDICATOR',
                               'INSIGHT_TEMPLATE', 'COMBINEDQUERY_TEMPLATE']

            # 获取业务路径
            path_cypher = """
            MATCH path = (entry)-[:HAS_CHILD*]->(indicator)
            WHERE entry.alias = '自主分析' AND indicator.id = $indicator_id
              AND labels(entry)[0] IN $business_labels AND labels(indicator)[0] IN $business_labels
            RETURN [node in nodes(path) | {id: node.id, alias: node.alias, type: node.type, level: node.level}] as path_nodes
            """
            result = session.run(path_cypher, indicator_id=indicator_id, business_labels=business_labels).single()

            if not result:
                return json.dumps({"success": False, "error": "指标不存在或路径不可达"}, ensure_ascii=False)

            path_nodes = result["path_nodes"]

            # 获取关联的物理表
            table_cypher = """
            MATCH (i:INDICATOR {id: $indicator_id})<-[:HAS_INDICATOR]-(t:TABLE)
            RETURN t.schema as schema, t.name as table_name, t.id as table_id
            """
            tables = [dict(r) for r in session.run(table_cypher, indicator_id=indicator_id)]

        return json.dumps({
            "success": True,
            "indicator_id": indicator_id,
            "indicator_alias": path_nodes[-1]["alias"] if path_nodes else "",
            "full_path": ".".join([n["alias"] for n in path_nodes]),
            "path_nodes": path_nodes,
            "tables": tables,
            "execution_time_ms": round((time.time() - start) * 1000, 2)
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool(annotations={"readOnlyHint": True})
def get_indicator_field_mapping(indicator_id: str) -> str:
    """根据指标ID获取其映射的物理表字段信息（从 Neo4j 预计算数据读取）

    Args:
        indicator_id: 指标ID (如: ATTR.CUST.SIGN.FLAG)

    返回格式:
        {
            "success": true,
            "indicator_id": "ATTR.CUST.SIGN.FLAG",
            "indicator_alias": "手机银行新签约活跃客户标志",
            "indicator_desc": "客户首次签约手机银行的标志",
            "expression_type": "C_FIELD",
            "has_field_mapping": true,
            "field_mappings": [
                {
                    "schema": "DMRBM_DATA",
                    "table": "E_PT_CUST_CHNL_SIGN_FEATURE",
                    "column": "mbank_first_sign_acct_org_no",
                    "full_path": "DMRBM_DATA.E_PT_CUST_CHNL_SIGN_FEATURE.mbank_first_sign_acct_org_no"
                }
            ],
            "mapping_count": 1
        }
    """
    start = time.time()
    try:
        with get_driver().session() as session:
            cypher = """
            MATCH (i:INDICATOR {id: $indicator_id})
            RETURN i.c_alias as alias,
                   i.c_desc as description,
                   i.expression_type as expression_type,
                   i.has_field_mapping as has_field_mapping,
                   i.mapped_schemas as schemas,
                   i.mapped_tables as tables,
                   i.mapped_columns as columns,
                   i.mapped_full_paths as full_paths
            """
            result = session.run(cypher, indicator_id=indicator_id).single()

            if not result:
                return json.dumps({
                    "success": False,
                    "error": f"指标不存在: {indicator_id}",
                    "indicator_id": indicator_id
                }, ensure_ascii=False, indent=2)

            has_field_mapping = result.get("has_field_mapping", False)

            if not has_field_mapping:
                return json.dumps({
                    "success": True,
                    "indicator_id": indicator_id,
                    "indicator_alias": result.get("alias", ""),
                    "indicator_desc": result.get("description", ""),
                    "expression_type": result.get("expression_type", ""),
                    "has_field_mapping": False,
                    "field_mappings": [],
                    "mapping_count": 0,
                    "note": "该指标没有字段映射信息",
                    "execution_time_ms": round((time.time() - start) * 1000, 2)
                }, ensure_ascii=False, indent=2)

            schemas = result.get("schemas", [])
            tables = result.get("tables", [])
            columns = result.get("columns", [])
            full_paths = result.get("full_paths", [])

            # 构建字段映射列表
            field_mappings = []
            for i, full_path in enumerate(full_paths):
                parts = full_path.split('.')
                if len(parts) >= 3:
                    col = parts[2] if len(parts) > 2 else (columns[i] if i < len(columns) else "")
                    field_mappings.append({
                        "schema": parts[0],
                        "table": parts[1],
                        "column": col,
                        "full_path": full_path
                    })

            return json.dumps({
                "success": True,
                "indicator_id": indicator_id,
                "indicator_alias": result.get("alias", ""),
                "indicator_desc": result.get("description", ""),
                "expression_type": result.get("expression_type", ""),
                "has_field_mapping": True,
                "field_mappings": field_mappings,
                "mapping_count": len(field_mappings),
                "data_source": "neo4j_precomputed",
                "execution_time_ms": round((time.time() - start) * 1000, 2)
            }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "indicator_id": indicator_id
        }, ensure_ascii=False, indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
def get_table_terms(schema_name: str, table_name: str) -> str:
    """获取表关联的所有术语及标准信息

    返回表中字段关联的术语，用于理解字段的业务含义和标准规范。

    Args:
        schema_name: Schema 名称（如 dmrbm_data）
        table_name: 表名（如 E_PT_CUST_INFO）
    """
    start = time.time()
    try:
        with get_driver().session() as session:
            cypher = """
            MATCH (t:TABLE {schema: $schema_name, name: $table_name})-[:HAS_TERM]->(term:TERM)
            OPTIONAL MATCH (term)-[:BELONGS_TO_STANDARD]->(std:DATA_STANDARD)
            RETURN term.en_name as field_name, term.cn_name as term_cn_name,
                   term.description as term_description,
                   std.id as standard_id, std.cn_name as standard_cn_name,
                   std.description as standard_description, std.type as standard_type
            ORDER BY term.en_name
            """
            terms = [dict(r) for r in session.run(cypher, schema_name=schema_name, table_name=table_name)]

        return json.dumps({
            "success": True, "schema": schema_name, "table": table_name,
            "count": len(terms), "terms": terms,
            "execution_time_ms": round((time.time() - start) * 1000, 2)
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


# ==================== 阶段 2：主题聚合 ====================

# ==================== 阶段 3：模板推荐 ====================

@mcp.tool(annotations={"readOnlyHint": True})
def get_theme_templates_with_coverage(
    theme_id: str,
    matched_indicator_ids: list,
    template_type: str = None,
    top_k: int = 10
) -> str:
    """获取主题下的模板，并计算与匹配指标的覆盖率

    用于主题模板推荐 Skill 的阶段 3，在推荐主题范围内推荐模板。

    Args:
        theme_id: 主题 ID
        matched_indicator_ids: 用户匹配的指标 ID 列表
        template_type: 模板类型过滤（"INSIGHT" / "COMBINEDQUERY" / None 全部）
        top_k: 返回数量，默认 10

    Returns:
        包含覆盖率计算的模板列表
    """
    start = time.time()
    try:
        # 限制参数
        matched_indicator_ids = matched_indicator_ids[:100] if matched_indicator_ids else []
        top_k = min(max(1, top_k), 50)

        with get_driver().session() as session:
            # 构建模板类型过滤条件
            type_filter = ""
            if template_type == "INSIGHT":
                type_filter = "AND t:INSIGHT_TEMPLATE"
            elif template_type == "COMBINEDQUERY":
                type_filter = "AND t:COMBINEDQUERY_TEMPLATE"

            # 查询主题下的模板，计算覆盖率
            # 覆盖率 = 模板覆盖的用户指标数 / 用户需要的指标总数
            user_indicator_count = len(matched_indicator_ids) if matched_indicator_ids else 0

            cypher = f"""
            MATCH (t) WHERE t.theme_id = $theme_id {type_filter}
            OPTIONAL MATCH (t)-[:CONTAINS]->(all_i:INDICATOR)
            WITH t, count(DISTINCT all_i) as total_count
            OPTIONAL MATCH (t)-[:CONTAINS]->(matched_i:INDICATOR)
            WHERE matched_i.id IN $matched_indicator_ids
            WITH t, total_count, count(DISTINCT matched_i) as matched_count,
                 collect(DISTINCT matched_i.id) as matched_indicator_ids
            WHERE total_count > 0
            RETURN t.id as id, t.alias as alias,
                   CASE WHEN t:INSIGHT_TEMPLATE THEN 'INSIGHT' ELSE 'COMBINEDQUERY' END as template_type,
                   t.heat as heat, t.description as description,
                   total_count, matched_count, matched_indicator_ids,
                   CASE WHEN $user_indicator_count > 0
                     THEN toFloat(matched_count) / $user_indicator_count
                     ELSE 0
                   END as coverage_ratio,
                   CASE WHEN $user_indicator_count > 0
                     THEN toFloat(matched_count) / $user_indicator_count * 0.6 + t.heat * 0.0001
                     ELSE t.heat * 0.0001
                   END as score
            ORDER BY score DESC
            LIMIT $top_k
            """

            result = session.run(
                cypher,
                theme_id=theme_id,
                matched_indicator_ids=matched_indicator_ids,
                user_indicator_count=user_indicator_count,
                top_k=top_k
            )

            templates = []
            for row in result:
                templates.append({
                    "id": row["id"],
                    "alias": row["alias"],
                    "template_type": row["template_type"],
                    "description": row["description"],
                    "heat": row["heat"] or 0,
                    "total_indicators": row["total_count"],
                    "matched_indicators": row["matched_count"],
                    "matched_indicator_ids": row["matched_indicator_ids"],
                    "coverage_ratio": round(row["coverage_ratio"], 3) if row["coverage_ratio"] else 0,
                    "score": round(row["score"], 3) if row["score"] else 0
                })

        return json.dumps({
            "success": True,
            "theme_id": theme_id,
            "template_type": template_type,
            "count": len(templates),
            "templates": templates,
            "execution_time_ms": round((time.time() - start) * 1000, 2)
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool(annotations={"readOnlyHint": True})
def get_template_indicators(template_id: str) -> str:
    """获取模板包含的所有指标

    通过 CONTAINS 关系遍历获取模板引用的所有指标

    Args:
        template_id: 模板节点 ID（如 TEMPLATE.INSIGHT.xxx）

    Returns:
        模板详情及包含的指标列表
    """
    start = time.time()
    try:
        with get_driver().session() as session:
            # 获取模板节点
            cypher = """
            MATCH (n) WHERE n.id = $template_id
              AND labels(n)[0] IN ['INSIGHT_TEMPLATE', 'COMBINEDQUERY_TEMPLATE']
            RETURN n.id as id, n.alias as alias, n.description as description,
                   n.template_type as template_type, n.heat as heat,
                   n.theme_id as theme_id, n.indicator_count as indicator_count,
                   n.calc_fields as calc_fields, n.filters as filters,
                   n.parameters as parameters
            """
            result = session.run(cypher, template_id=template_id)
            template_node = result.single()

            if not template_node:
                return json.dumps({
                    "success": False,
                    "error": f"Template not found: {template_id}"
                }, ensure_ascii=False)

            # 获取包含的指标（通过 CONTAINS 关系）
            cypher_indicators = """
            MATCH (t) WHERE t.id = $template_id
            MATCH (t)-[r:CONTAINS]->(i:INDICATOR)
            RETURN i.id as id, i.alias as alias, r.position as position
            ORDER BY r.position
            """
            result = session.run(cypher_indicators, template_id=template_id)
            indicators = [dict(r) for r in result]

            # 获取主题信息
            theme_id = template_node.get("theme_id")
            theme_info = None
            if theme_id:
                cypher_theme = """
                MATCH (n) WHERE n.id = $theme_id
                RETURN n.id as id, n.alias as alias
                """
                theme_result = session.run(cypher_theme, theme_id=theme_id)
                theme_info = dict(theme_result.single()) if theme_result.single() else None

        return json.dumps({
            "success": True,
            "template": dict(template_node),
            "theme": theme_info,
            "indicators": indicators,
            "indicator_count": len(indicators),
            "execution_time_ms": round((time.time() - start) * 1000, 2),
        }, ensure_ascii=False, indent=2)
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
                   i.path as path, i.description as description
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
                }, ensure_ascii=False, indent=2)

            # 时间筛选指标：别名包含 "数据日期" 或 "ETL数据日期"
            time_patterns = ["数据日期", "ETL数据日期"]

            # 机构筛选指标：别名匹配以下任一模式
            org_patterns = [
                "机构名称", "机构编号",
                "管理机构名称", "管理机构编号",
                "账务机构名称", "账务机构编号"
            ]

            time_filter = []
            org_filter = []

            for ind in indicators:
                alias = ind.get("alias", "") or ""

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
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool(annotations={"readOnlyHint": True})
def get_theme_analysis_indicators(theme_id: str, top_k: int = 200) -> str:
    """获取主题下全量的分析指标

    分析指标指除筛选指标之外的所有业务分析指标，用于数据分析场景。
    用于 1.2.1 全量指标补全场景。

    Args:
        theme_id: THEME 节点 ID
        top_k: 返回结果数量上限，默认200（最大500）

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
    """
    start = time.time()
    try:
        top_k = min(max(1, top_k), 500)

        with get_driver().session() as session:
            # 获取主题下全量 INDICATOR（通过 HAS_CHILD 关系，支持直接和间接连接）
            cypher = """
            MATCH (theme:THEME {id: $theme_id})
            MATCH (theme)-[:HAS_CHILD*1..2]->(i:INDICATOR)
            RETURN i.id as id, i.alias as alias,
                   i.path as path, i.description as description
            ORDER BY i.alias
            LIMIT $top_k
            """
            result = session.run(cypher, theme_id=theme_id, top_k=top_k)
            indicators = [dict(r) for r in result]

            if not indicators:
                return json.dumps({
                    "success": True,
                    "theme_id": theme_id,
                    "analysis_indicators": [],
                    "total_count": 0,
                    "execution_time_ms": round((time.time() - start) * 1000, 2)
                }, ensure_ascii=False, indent=2)

            # 筛选指标识别规则（与分析指标互斥）
            time_patterns = ["数据日期", "ETL数据日期"]
            org_patterns = [
                "机构名称", "机构编号",
                "管理机构名称", "管理机构编号",
                "账务机构名称", "账务机构编号"
            ]

            analysis_indicators = []
            for ind in indicators:
                alias = ind.get("alias", "") or ""

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
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


# ==================== 主题聚合工具 ====================


def _get_indicator_theme_internal(indicator_id: str) -> dict:
    """内部方法：获取单个指标的 THEME 信息

    从业务路径中提取 type=THEME 的节点，与 get_indicator_full_path 的路径查询逻辑一致。
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

            # 从路径节点中提取 THEME 类型的节点
            for node in result["path_nodes"]:
                if node["type"] == "THEME":
                    return {
                        "indicator_id": indicator_id,
                        "theme_id": node["id"],
                        "theme_alias": node["alias"],
                        "theme_level": node.get("level"),
                    }
            return None
    except Exception:
        return None


@mcp.tool(annotations={"readOnlyHint": True})
def aggregate_themes_from_indicators(matched_indicators: list, top_k: int = 3) -> str:
    """从指标列表中聚合候选主题（按频次排序）

    对 matched_indicators 中的每个指标调用 _get_indicator_theme_internal 获取其 THEME 信息，
    统计各 THEME 出现频次，按频次降序排列，取 Top K 作为初始候选主题。

    用于主题模板推荐 Skill 阶段 0.5 整理输出 → 阶段 1 的过渡。

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
            }, ensure_ascii=False, indent=2)

        # Python 循环：每个指标调用一次 _get_indicator_theme_internal
        theme_map: dict = {}  # theme_id -> {theme_alias, theme_level, matched_indicator_ids}

        for indicator_id in matched_indicators:
            result = _get_indicator_theme_internal(indicator_id)
            if result:
                theme_id = result["theme_id"]
                if theme_id not in theme_map:
                    theme_map[theme_id] = {
                        "theme_alias": result["theme_alias"],
                        "theme_level": result.get("theme_level"),
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
                "frequency": len(matched_ids),
                "matched_indicator_ids": matched_ids
            })

        return json.dumps({
            "success": True,
            "candidate_themes": candidate_themes,
            "total_themes": len(candidate_themes),
            "total_indicators": len(matched_indicators),
            "execution_time_ms": round((time.time() - start) * 1000, 2)
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
