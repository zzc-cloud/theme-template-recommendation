"""
数据抽取模块 - 从 MySQL 抽取魔数师指标层树形结构数据

专用于 Theme Template Recommendation 项目。
仅抽取魔数师指标层相关的 4 种类型：
- BUSINESS_THEMES (SECTOR, CATEGORY)
- BUSINESS_THEME (THEME)
- BUSINESS_OBJECT (SUBPATH)
- BUSINESS_ATTRIBUTE (INDICATOR)
"""

import pymysql
from typing import Dict, Any, List
from pathlib import Path
import sys

# 添加脚���目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from config import MYSQL_CONFIG, SOURCE_TABLES, ONTOLOGY_RESTYPES, ENTRY_NODE_ID


class IndicatorExtractor:
    """从 t_restree 表抽取魔数师指标层树形结构数据"""

    def __init__(self):
        self.config = MYSQL_CONFIG
        self.table_name = SOURCE_TABLES["restree"]

    def get_connection(self):
        """获取 MySQL 连接"""
        return pymysql.connect(**self.config)

    def extract_all(self) -> List[Dict[str, Any]]:
        """
        抽取魔数师指标层相关数据

        优化：
        - 只抽取 4 种本体层需要的类型
        - 只抽取 "自主分析" 根节点下的后代节点
        - 只抽取必要字段

        Returns:
            节点数据列表，每项包含：c_resid, c_resalias, c_restype, c_pid, c_order
        """
        sql = f"""
        WITH RECURSIVE descendant_tree AS (
            -- 基础：根节点（自主分析）
            SELECT c_resid
            FROM {self.table_name}
            WHERE c_resid = %s

            UNION ALL

            -- 递归：所有子节点
            SELECT t.c_resid
            FROM {self.table_name} t
            INNER JOIN descendant_tree d ON t.c_pid = d.c_resid
            WHERE t.c_status != '1'  -- 排除已删除的
        )
        SELECT
            t.c_resid,
            t.c_resalias,
            t.c_restype,
            t.c_pid,
            t.c_order
        FROM {self.table_name} t
        INNER JOIN descendant_tree d ON t.c_resid = d.c_resid
        WHERE t.c_status != '1'                    -- 排除已删除的
          AND t.c_restype IN {ONTOLOGY_RESTYPES}   -- 只抽取本体层需要的类型
        ORDER BY t.c_resid
        """

        with self.get_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(sql, (ENTRY_NODE_ID,))
                return list(cursor.fetchall())

    def extract_incremental(self, last_update_time: str) -> List[Dict[str, Any]]:
        """
        增量抽取：只抽取指定时间后更新的节点

        Args:
            last_update_time: 上次更新时间（格式：YYYY-MM-DD HH:MM:SS）

        Returns:
            新增或更新的节点列表
        """
        sql = f"""
        WITH RECURSIVE descendant_tree AS (
            SELECT c_resid
            FROM {self.table_name}
            WHERE c_resid = %s
            UNION ALL
            SELECT t.c_resid
            FROM {self.table_name} t
            INNER JOIN descendant_tree d ON t.c_pid = d.c_resid
            WHERE t.c_status != '1'
        )
        SELECT
            t.c_resid,
            t.c_resalias,
            t.c_restype,
            t.c_pid,
            t.c_order
        FROM {self.table_name} t
        INNER JOIN descendant_tree d ON t.c_resid = d.c_resid
        WHERE t.c_status != '1'
          AND t.c_restype IN {ONTOLOGY_RESTYPES}
          AND t.c_update_time >= %s
        ORDER BY t.c_resid
        """

        with self.get_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(sql, (ENTRY_NODE_ID, last_update_time))
                return list(cursor.fetchall())

    def get_ontology_stats(self) -> Dict[str, int]:
        """
        获取本体层相关类型的统计信息

        Returns:
            类型 -> 数量的映射
        """
        sql = f"""
        WITH RECURSIVE descendant_tree AS (
            SELECT c_resid
            FROM {self.table_name}
            WHERE c_resid = %s
            UNION ALL
            SELECT t.c_resid
            FROM {self.table_name} t
            INNER JOIN descendant_tree d ON t.c_pid = d.c_resid
            WHERE t.c_status != '1'
        )
        SELECT
            t.c_restype,
            COUNT(*) as count
        FROM {self.table_name} t
        INNER JOIN descendant_tree d ON t.c_resid = d.c_resid
        WHERE t.c_status != '1'
          AND t.c_restype IN {ONTOLOGY_RESTYPES}
        GROUP BY t.c_restype
        ORDER BY count DESC
        """

        with self.get_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(sql, (ENTRY_NODE_ID,))
                return {row["c_restype"]: row["count"] for row in cursor.fetchall()}

    def get_total_count(self) -> int:
        """获取本体层相关类型的总记录数"""
        sql = f"""
        WITH RECURSIVE descendant_tree AS (
            SELECT c_resid
            FROM {self.table_name}
            WHERE c_resid = %s
            UNION ALL
            SELECT t.c_resid
            FROM {self.table_name} t
            INNER JOIN descendant_tree d ON t.c_pid = d.c_resid
            WHERE t.c_status != '1'
        )
        SELECT COUNT(*) as total
        FROM {self.table_name} t
        INNER JOIN descendant_tree d ON t.c_resid = d.c_resid
        WHERE t.c_status != '1'
          AND t.c_restype IN {ONTOLOGY_RESTYPES}
        """

        with self.get_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(sql, (ENTRY_NODE_ID,))
                return cursor.fetchone()["total"]


if __name__ == "__main__":
    extractor = IndicatorExtractor()

    # 打印统计信息
    stats = extractor.get_ontology_stats()
    total = extractor.get_total_count()

    print("=== 魔数师指标层统计 ===")
    for restype, count in stats.items():
        print(f"  {restype}: {count:,}")
    print(f"\n  合计: {total:,} 条记录")

    # 测试抽取
    print("\n=== 测试抽取 ===")
    data = extractor.extract_all()
    print(f"已抽取 {len(data)} 条记录")

    if data:
        print("\n示例数据（前 3 条）:")
        for row in data[:3]:
            print(f"  {row['c_resalias']} ({row['c_restype']})")
