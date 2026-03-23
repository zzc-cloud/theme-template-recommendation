#!/usr/bin/env python3
"""
Theme Template Recommendation - 精简版 MySQL MCP 服务器

仅包含 3 个必要工具：
- find_indicators_by_table: 阶段 0 需求澄清
- get_indicator_field_mapping: 阶段 1 指标字段映射（MySQL 版本）
- get_table_columns_bigmeta: 阶段 1 表字段详情
"""

from mcp.server.fastmcp import FastMCP
from datetime import datetime
import time
import pymysql
import json
import re
import traceback
from contextlib import contextmanager
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

# 创建 MCP 服务器实例
mcp = FastMCP("theme-resources")

# MySQL 数据库配置
DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "database": os.getenv("MYSQL_DATABASE", "chatbi_metadata"),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", "password"),
    "charset": "utf8mb4"
}


@contextmanager
def get_db_connection():
    """创建数据库连接的上下文管理器"""
    conn = pymysql.connect(**DB_CONFIG)
    try:
        yield conn
    finally:
        conn.close()


# ==================== 阶段 0：需求澄清 ====================

@mcp.tool(annotations={"readOnlyHint": True})
def find_indicators_by_table(schema: str, table: str) -> str:
    """根据表查找关联的所有指标

    Args:
        schema: Schema 名称
        table: 表名

    返回示例:
        {
            "success": true,
            "schema": "dmrbm_data",
            "table": "E_LN_LOAN_SUMMARY",
            "indicator_count": 15,
            "indicators": [
                {
                    "indicator_id": "ATTR.LOAN.BALANCE",
                    "indicator_alias": "贷款余额",
                    "column": "loan_balance"
                }
            ]
        }
    """
    start_time = time.time()

    try:
        # 安全检查
        if not re.match(r'^[a-zA-Z0-9_]+$', schema):
            return json.dumps({
                "success": False,
                "error": "Invalid schema name"
            }, ensure_ascii=False, indent=2)

        if not re.match(r'^[a-zA-Z0-9_]+$', table):
            return json.dumps({
                "success": False,
                "error": "Invalid table name"
            }, ensure_ascii=False, indent=2)

        sql = """
        SELECT
            c_attrid AS indicator_id,
            c_attralias AS indicator_alias,
            c_attrdesc AS indicator_desc,
            c_expression AS expression
        FROM njcb_metadata_map_t_bizattr
        WHERE c_expression LIKE %s
        AND c_expression NOT LIKE '%%C_BizViewOutField%%'
        AND c_expression NOT LIKE '%%.BIMETA.%%'
        AND (c_expression LIKE '^C_FIELD%%' OR c_expression LIKE ' ^C_FIELD%%')
        AND (c_expression LIKE '%%^' OR c_expression LIKE '%%^ ' )
        AND c_attralias NOT LIKE '%%�%%'
        ORDER BY c_attralias
        """

        search_pattern = f"%^{schema}.{table}%"

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, (search_pattern,))
                results = cursor.fetchall()

                indicators = []
                for row in results:
                    expression = row[3] or ""
                    # 提取字段名
                    path_part = expression.replace('^', '').strip()
                    path_part = re.sub(r'C_FIELD(REF[0-9]*)?\.', '', path_part)
                    path_part = path_part.replace('.null.', '.')
                    parts = path_part.split('.')

                    column_name = parts[2] if len(parts) >= 3 else ""

                    indicators.append({
                        "indicator_id": row[0],
                        "indicator_alias": row[1],
                        "indicator_desc": row[2] or "",
                        "column": column_name
                    })

                elapsed = (time.time() - start_time) * 1000
                result = {
                    "success": True,
                    "schema": schema,
                    "table": table,
                    "full_path": f"{schema}.{table}",
                    "indicator_count": len(indicators),
                    "indicators": indicators,
                    "execution_time_ms": round(elapsed, 2)
                }
                return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "schema": schema,
            "table": table
        }, ensure_ascii=False, indent=2)


# ==================== 阶段 1：指标信息获取 ====================

@mcp.tool(annotations={"readOnlyHint": True})
def get_indicator_field_mapping_mysql(indicator_id: str) -> str:
    """根据指标ID获取其映射的物理表字段信息（从 MySQL 读取）

    这是 get_indicator_field_mapping 的 MySQL 版本，用于语义增强。

    Args:
        indicator_id: 指标ID (如: ATTR.CUST.SIGN.FLAG)

    返回格式:
        {
            "success": true,
            "indicator_id": "ATTR.CUST.SIGN.FLAG",
            "indicator_alias": "手机银行新签约活跃客户标志",
            "indicator_desc": "客户首次签约手机银行的标志",
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
    start_time = time.time()

    try:
        sql = """
        SELECT
            c_attrid AS indicator_id,
            c_attralias AS indicator_alias,
            c_attrdesc AS indicator_desc,
            c_expression AS expression
        FROM njcb_metadata_map_t_bizattr
        WHERE c_attrid = %s
        """

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, (indicator_id,))
                row = cursor.fetchone()

                if not row:
                    return json.dumps({
                        "success": False,
                        "error": f"指标不存在: {indicator_id}",
                        "indicator_id": indicator_id
                    }, ensure_ascii=False, indent=2)

                expression = row[3] or ""

                # 判断是否有可解析的字段映射
                has_field_mapping = (
                    'C_FIELD' in expression and
                    'C_BizViewOutField' not in expression and
                    '.BIMETA.' not in expression
                )

                if not has_field_mapping:
                    return json.dumps({
                        "success": True,
                        "indicator_id": row[0],
                        "indicator_alias": row[1],
                        "indicator_desc": row[2] or "",
                        "has_field_mapping": False,
                        "field_mappings": [],
                        "mapping_count": 0,
                        "note": "该指标没有字段映射信息",
                        "execution_time_ms": round((time.time() - start_time) * 1000, 2)
                    }, ensure_ascii=False, indent=2)

                # 解析表达式中的字段路径
                # 格式示例: ^C_FIELD.DMRBM_DATA.E_PT_CUST_CHNL_SIGN_FEATURE.mbank_first_sign_acct_org_no^
                field_mappings = []

                # 提取 C_FIELD 后面的路径
                c_field_pattern = r'C_FIELD(?:REF[0-9]*)?\.([^\s\^]+)'
                matches = re.findall(c_field_pattern, expression)

                for match in matches:
                    # 处理 .null. 的情况
                    path = match.replace('.null.', '.')
                    parts = path.split('.')

                    if len(parts) >= 3:
                        field_mappings.append({
                            "schema": parts[0],
                            "table": parts[1],
                            "column": parts[2],
                            "full_path": path
                        })

                elapsed = (time.time() - start_time) * 1000
                return json.dumps({
                    "success": True,
                    "indicator_id": row[0],
                    "indicator_alias": row[1],
                    "indicator_desc": row[2] or "",
                    "has_field_mapping": True,
                    "field_mappings": field_mappings,
                    "mapping_count": len(field_mappings),
                    "data_source": "mysql",
                    "execution_time_ms": round(elapsed, 2)
                }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "indicator_id": indicator_id
        }, ensure_ascii=False, indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
def get_table_columns_bigmeta(schema: str, table: str) -> str:
    """基于表名获取所有字段信息（从 bigmeta_entity_column），含热度信息

    这个工具直接从 bigmeta_entity_column 表获取字段的物理定义，
    包括字段名、描述、数据类型等，用于理解表的完整结构。
    同时包含热度信息用于判断表是否为孤点表。

    Args:
        schema: Schema 名称（如 dmrbm_data）
        table: 表名（如 E_PT_CUST_INFO）

    返回示例:
        {
            "success": true,
            "schema": "dmrbm_data",
            "table": "E_PT_CUST_INFO",
            "table_guid": "database.gp.njdwdb.dmrbm_data.E_PT_CUST_INFO",
            "is_isolated": false,
            "column_count": 25,
            "columns": [
                {
                    "guid": "...",
                    "name": "cust_id",
                    "description": "客户编号",
                    "data_type": "VARCHAR(32)"
                },
                ...
            ]
        }
    """
    start_time = time.time()

    try:
        # 安全检查
        if not re.match(r'^[a-zA-Z0-9_]+$', schema):
            return json.dumps({
                "success": False,
                "error": "Invalid schema name"
            }, ensure_ascii=False, indent=2)

        if not re.match(r'^[a-zA-Z0-9_]+$', table):
            return json.dumps({
                "success": False,
                "error": "Invalid table name"
            }, ensure_ascii=False, indent=2)

        # 构建 table_guid，格式: database.gp.njdwdb.{schema}.{table}
        table_guid = f"database.gp.njdwdb.{schema}.{table}"

        # 同时获取表的热度信息
        table_sql = """
        SELECT Input, Output, description
        FROM bigmeta_entity_table
        WHERE guid = %s
        """

        column_sql = """
        SELECT
            guid,
            name,
            description,
            Data_type
        FROM bigmeta_entity_column
        WHERE table_guid = %s
        ORDER BY name
        """

        def _parse_table_heat(io_json: str) -> tuple:
            """解析 Input/Output JSON，计算内部和外部下游表数量"""
            if not io_json:
                return 0, 0

            internal_count = 0
            external_count = 0

            try:
                data = json.loads(io_json)
                items = data.get('data', []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

                for item in items:
                    if not isinstance(item, dict):
                        continue

                    entity = item.get('dstEntity', {}) or item.get('srcEntity', {})
                    guid_str = entity.get('guid', '')

                    if not guid_str:
                        continue

                    parts = guid_str.split('.')
                    if len(parts) >= 5 and parts[0] == 'database':
                        internal_count += 1
                    else:
                        external_count += 1

            except (json.JSONDecodeError, KeyError, TypeError):
                pass

            return internal_count, external_count

        with get_db_connection() as conn:
            # 获取表信息（用于计算热度）
            input_json = ''
            output_json = ''
            table_description = ''

            with conn.cursor() as cursor:
                cursor.execute(table_sql, (table_guid,))
                table_result = cursor.fetchone()
                if table_result:
                    input_json = table_result[0] or ''
                    output_json = table_result[1] or ''
                    table_description = table_result[2] or ''

            # 获取字段信息
            with conn.cursor() as cursor:
                cursor.execute(column_sql, (table_guid,))
                results = cursor.fetchall()

                columns = []
                for row in results:
                    columns.append({
                        "guid": row[0],
                        "name": row[1],
                        "description": row[2] or "",
                        "data_type": row[3] or ""
                    })

            # 计算热度信息
            internal_downstream, external_downstream = _parse_table_heat(output_json)
            total_downstream = internal_downstream + external_downstream
            internal_upstream, external_upstream = _parse_table_heat(input_json)
            total_upstream = internal_upstream + external_upstream
            is_isolated = (total_downstream == 0 and total_upstream == 0)

            elapsed = (time.time() - start_time) * 1000
            result = {
                "success": True,
                "schema": schema,
                "table": table,
                "table_guid": table_guid,
                "description": table_description,
                "is_isolated": is_isolated,
                "heat": {
                    "total_downstream_count": total_downstream,
                    "internal_downstream_count": internal_downstream,
                    "external_downstream_count": external_downstream,
                    "total_upstream_count": total_upstream,
                    "internal_upstream_count": internal_upstream,
                    "external_upstream_count": external_upstream
                },
                "column_count": len(columns),
                "columns": columns,
                "execution_time_ms": round(elapsed, 2)
            }
            return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "schema": schema,
            "table": table,
            "table_guid": f"database.gp.njdwdb.{schema}.{table}"
        }, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    # 运行服务器
    mcp.run()
