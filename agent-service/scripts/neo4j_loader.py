"""
数据加载模块 - 将节点和关系导入 Neo4j

专用于 Theme Template Recommendation 项目。
支持：
- 魔数师指标层节点（SECTOR, CATEGORY, THEME, SUBPATH, INDICATOR）
- 模板层节点（INSIGHT_TEMPLATE, COMBINEDQUERY_TEMPLATE）
- 关系（HAS_CHILD, CONTAINS）
"""

from typing import Dict, Any, List, Optional
from neo4j import GraphDatabase
from tqdm import tqdm
from pathlib import Path
import sys
import json

# 添加脚本目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from config import NEO4J_CONFIG, BATCH_SIZE, DEFAULT_DELETE_SECTORS


class Neo4jLoader:
    """Neo4j 数据加载器"""

    def __init__(self, uri: str = None, user: str = None, password: str = None):
        """
        初始化 Neo4j 连接

        Args:
            uri: Neo4j URI，默认从配置读取
            user: 用户名，默认从配置读取
            password: 密码，默认从配置读取
        """
        self.uri = uri or NEO4J_CONFIG["uri"]
        self.user = user or NEO4J_CONFIG["user"]
        self.password = password or NEO4J_CONFIG["password"]
        self.driver = None

    def connect(self):
        """建立连接"""
        self.driver = GraphDatabase.driver(
            self.uri,
            auth=(self.user, self.password)
        )
        return self

    def close(self):
        """关闭连接"""
        if self.driver:
            self.driver.close()

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ==================== 约束和索引 ====================

    def create_constraints(self):
        """创建约束和索引"""
        with self.driver.session() as session:
            # 节点唯一性约束（魔数师指标层）
            constraints = [
                "CREATE CONSTRAINT sector_id IF NOT EXISTS FOR (n:SECTOR) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT category_id IF NOT EXISTS FOR (n:CATEGORY) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT theme_id IF NOT EXISTS FOR (n:THEME) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT subpath_id IF NOT EXISTS FOR (n:SUBPATH) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT indicator_id IF NOT EXISTS FOR (n:INDICATOR) REQUIRE n.id IS UNIQUE",
                # 模板层约束
                "CREATE CONSTRAINT insight_template_id IF NOT EXISTS FOR (n:INSIGHT_TEMPLATE) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT combinedquery_template_id IF NOT EXISTS FOR (n:COMBINEDQUERY_TEMPLATE) REQUIRE n.id IS UNIQUE",
            ]

            for constraint in constraints:
                try:
                    session.run(constraint)
                    print(f"✓ {constraint}")
                except Exception as e:
                    if "already exists" in str(e):
                        print(f"⊙ {constraint} (已存在)")
                    else:
                        print(f"✗ {constraint}: {e}")

            # 创建普通索引
            indexes = [
                # 魔数师指标层索引
                "CREATE INDEX sector_alias IF NOT EXISTS FOR (n:SECTOR) ON (n.alias)",
                "CREATE INDEX category_alias IF NOT EXISTS FOR (n:CATEGORY) ON (n.alias)",
                "CREATE INDEX theme_alias IF NOT EXISTS FOR (n:THEME) ON (n.alias)",
                "CREATE INDEX theme_id_index IF NOT EXISTS FOR (n:THEME) ON (n.id)",
                "CREATE INDEX subpath_alias IF NOT EXISTS FOR (n:SUBPATH) ON (n.alias)",
                "CREATE INDEX indicator_alias IF NOT EXISTS FOR (n:INDICATOR) ON (n.alias)",
                # 模板层索引
                "CREATE INDEX insight_template_theme_id IF NOT EXISTS FOR (n:INSIGHT_TEMPLATE) ON (n.theme_id)",
                "CREATE INDEX insight_template_heat IF NOT EXISTS FOR (n:INSIGHT_TEMPLATE) ON (n.heat)",
                "CREATE INDEX combinedquery_template_theme_id IF NOT EXISTS FOR (n:COMBINEDQUERY_TEMPLATE) ON (n.theme_id)",
                "CREATE INDEX combinedquery_template_heat IF NOT EXISTS FOR (n:COMBINEDQUERY_TEMPLATE) ON (n.heat)",
            ]

            for index in indexes:
                try:
                    session.run(index)
                    print(f"✓ {index}")
                except Exception as e:
                    if "already exists" in str(e):
                        print(f"⊙ {index} (已存在)")
                    else:
                        print(f"✗ {index}: {e}")

    # ==================== 节点加载 ====================

    def load_indicator_layer_nodes(self, nodes: List[Dict[str, Any]]):
        """
        批量加载魔数师指标层节点

        Args:
            nodes: 节点数据列表
        """
        with self.driver.session() as session:
            # 按类型分组
            nodes_by_type = {
                'SECTOR': [],
                'CATEGORY': [],
                'THEME': [],
                'SUBPATH': [],
                'INDICATOR': [],
            }

            for node in nodes:
                node_type = node.get('type', 'UNKNOWN')
                if node_type in nodes_by_type:
                    nodes_by_type[node_type].append(node)

            # 为每种类型分别创建节点
            for node_type, type_nodes in nodes_by_type.items():
                if not type_nodes:
                    continue

                for i in tqdm(range(0, len(type_nodes), BATCH_SIZE), desc=f"加载 {node_type}"):
                    batch = type_nodes[i:i + BATCH_SIZE]

                    session.run(f"""
                        UNWIND $batch AS row
                        MERGE (n:{node_type} {{id: row.id}})
                        SET n.alias = row.alias,
                            n.type = row.type,
                            n.level = row.level,
                            n.path = row.path,
                            n.parent_id = row.parent_id
                        RETURN count(*)
                    """, batch=batch)

        print(f"✓ 已加载 {len(nodes)} 个魔数师指标层节点")

    def load_template_nodes(self, nodes: List[Dict[str, Any]], node_label: str):
        """
        加载模板节点

        Args:
            nodes: 节点数据列表
            node_label: INSIGHT_TEMPLATE 或 COMBINEDQUERY_TEMPLATE
        """
        with self.driver.session() as session:
            for i in tqdm(range(0, len(nodes), BATCH_SIZE), desc=f"加载 {node_label}"):
                batch = nodes[i:i + BATCH_SIZE]

                # 过滤非基本类型属性
                sample = batch[0] if batch else {}
                allowed_keys = []
                for key, value in sample.items():
                    if key == 'id':
                        continue
                    if isinstance(value, (dict, list)):
                        continue
                    allowed_keys.append(key)

                if not allowed_keys:
                    set_stmt = "n.id = row.id"
                else:
                    set_clauses = [f"n.{key} = row.{key}" for key in allowed_keys]
                    set_stmt = ",\n                            ".join(set_clauses)

                session.run(f"""
                    UNWIND $batch AS row
                    MERGE (n:{node_label} {{id: row.id}})
                    SET {set_stmt}
                    RETURN count(*)
                """, batch=batch)

        print(f"✓ 已加载 {len(nodes)} 个 {node_label} 节点")

    # ==================== 关系加载 ====================

    def load_has_child_relationships(self, relationships: List[Dict[str, Any]]):
        """
        批量加载 HAS_CHILD 关系

        Args:
            relationships: 关系数据列表
        """
        with self.driver.session() as session:
            # 首先获取所有节点的类型映射
            node_types = {}
            for label in ["SECTOR", "CATEGORY", "THEME", "SUBPATH", "INDICATOR",
                          "INSIGHT_TEMPLATE", "COMBINEDQUERY_TEMPLATE"]:
                result = session.run(f"MATCH (n:{label}) RETURN n.id as id")
                for row in result:
                    node_types[row["id"]] = label

            print(f"已获取 {len(node_types)} 个节点的类型信息")

            # 按 (from_type, to_type) 分组关系
            rel_groups = {}
            for rel in relationships:
                from_id = rel["from"]
                to_id = rel["to"]
                from_type = node_types.get(from_id)
                to_type = node_types.get(to_id)

                if from_type and to_type:
                    key = (from_type, to_type)
                    if key not in rel_groups:
                        rel_groups[key] = []
                    rel_groups[key].append(rel)

            print(f"HAS_CHILD 关系分为 {len(rel_groups)} 组")

            # 为每组关系执行加载
            total_loaded = 0
            for (from_type, to_type), rels in rel_groups.items():
                desc = f"HAS_CHILD: {from_type}->{to_type}"
                for i in tqdm(range(0, len(rels), BATCH_SIZE), desc=desc):
                    batch = rels[i:i + BATCH_SIZE]
                    session.run(f"""
                        UNWIND $batch AS row
                        MATCH (from:{from_type} {{id: row.from}})
                        MATCH (to:{to_type} {{id: row.to}})
                        MERGE (from)-[r:HAS_CHILD]->(to)
                        RETURN count(*)
                    """, batch=batch)
                    total_loaded += len(batch)

            print(f"✓ 已加载 {total_loaded} 条 HAS_CHILD 关系")

    def load_contains_relationships(
        self,
        relationships: List[Dict[str, Any]],
        from_label: str = "INSIGHT_TEMPLATE",
        to_label: str = "INDICATOR"
    ):
        """
        加载 CONTAINS 关系（带 position 属性）

        Args:
            relationships: 关系列表
            from_label: 起始节点标签
            to_label: 目标节点标签
        """
        with self.driver.session() as session:
            loaded = 0
            skipped = 0

            for i in tqdm(range(0, len(relationships), BATCH_SIZE),
                         desc=f"加载 {from_label}-CONTAINS->{to_label}"):
                batch = relationships[i:i + BATCH_SIZE]

                valid_batch = []
                for rel in batch:
                    from_id = rel.get('from')
                    to_id = rel.get('to')

                    # 验证节点存在
                    template_exists = session.run(
                        f"MATCH (t:{from_label} {{id: $id}}) RETURN count(t) > 0",
                        id=from_id
                    ).single()[0]

                    indicator_exists = session.run(
                        f"MATCH (i:{to_label} {{id: $id}}) RETURN count(i) > 0",
                        id=to_id
                    ).single()[0]

                    if template_exists and indicator_exists:
                        valid_batch.append(rel)
                    else:
                        skipped += 1

                if valid_batch:
                    session.run(f"""
                        UNWIND $batch AS row
                        MATCH (from:{from_label} {{id: row.from}})
                        MATCH (to:{to_label} {{id: row.to}})
                        MERGE (from)-[r:CONTAINS]->(to)
                        SET r.position = row.properties.position
                        RETURN count(*)
                    """, batch=valid_batch)
                    loaded += len(valid_batch)

            print(f"✓ 已加载 {loaded} 条 CONTAINS 关系（跳过 {skipped} 条）")

    # ==================== 统计信息 ====================

    def get_stats(self) -> Dict[str, Any]:
        """获取图谱统计信息"""
        with self.driver.session() as session:
            # 节点统计
            node_count = session.run("MATCH (n) RETURN count(n) as count").single()["count"]

            # 按类型统计
            type_stats = session.run("""
                MATCH (n)
                RETURN labels(n)[0] as type, count(n) as count
                ORDER BY count DESC
            """).data()

            # 关系统计
            has_child_count = session.run(
                "MATCH ()-[r:HAS_CHILD]->() RETURN count(r) as count"
            ).single()["count"]

            contains_count = session.run(
                "MATCH ()-[r:CONTAINS]->() RETURN count(r) as count"
            ).single()["count"]

            return {
                "total_nodes": node_count,
                "by_type": {row["type"]: row["count"] for row in type_stats},
                "has_child_relationships": has_child_count,
                "contains_relationships": contains_count,
                "total_relationships": has_child_count + contains_count,
            }

    def get_template_stats(self) -> Dict[str, Any]:
        """获取模板层统计信息"""
        with self.driver.session() as session:
            insight_count = session.run(
                "MATCH (t:INSIGHT_TEMPLATE) RETURN count(t) as count"
            ).single()["count"]

            combinedquery_count = session.run(
                "MATCH (t:COMBINEDQUERY_TEMPLATE) RETURN count(t) as count"
            ).single()["count"]

            insight_with_heat = session.run(
                "MATCH (t:INSIGHT_TEMPLATE) WHERE t.heat > 0 RETURN count(t) as count"
            ).single()["count"]

            combinedquery_with_heat = session.run(
                "MATCH (t:COMBINEDQUERY_TEMPLATE) WHERE t.heat > 0 RETURN count(t) as count"
            ).single()["count"]

            contains_count = session.run(
                "MATCH ()-[r:CONTAINS]->() RETURN count(r) as count"
            ).single()["count"]

            return {
                "insight_template_count": insight_count,
                "combinedquery_template_count": combinedquery_count,
                "insight_with_heat": insight_with_heat,
                "combinedquery_with_heat": combinedquery_with_heat,
                "contains_relationship_count": contains_count,
            }

    # ==================== 数据清理 ====================

    def clear_all(self):
        """清空所有数据（慎用！）"""
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
            print("✓ 数据库已清空")

    def clear_template_layer(self):
        """清空模板层（INSIGHT_TEMPLATE, COMBINEDQUERY_TEMPLATE）"""
        with self.driver.session() as session:
            # 先删除关系
            session.run("MATCH ()-[r:CONTAINS]->() DELETE r")

            # 删除模板节点
            insight_count = session.run(
                "MATCH (n:INSIGHT_TEMPLATE) DETACH DELETE n RETURN count(n)"
            ).single()[0]
            combinedquery_count = session.run(
                "MATCH (n:COMBINEDQUERY_TEMPLATE) DETACH DELETE n RETURN count(n)"
            ).single()[0]

            print(f"✓ 已删除 {insight_count} 个 INSIGHT_TEMPLATE 节点")
            print(f"✓ 已删除 {combinedquery_count} 个 COMBINEDQUERY_TEMPLATE 节点")

    def delete_sectors_cascade(self, sector_ids: List[str]) -> Dict[str, Any]:
        """
        级联删除指定 SECTOR 及其所有下游节点

        Args:
            sector_ids: 要删除的 SECTOR 节点 ID 列表

        Returns:
            删除统计信息
        """
        with self.driver.session() as session:
            total_deleted = 0
            all_stats = {}

            for sector_id in sector_ids:
                # 统计将要删除的节点
                stats_result = session.run("""
                    MATCH (sector:SECTOR {id: $sector_id})
                    OPTIONAL MATCH (sector)-[:HAS_CHILD*]->(descendant)
                    WITH sector, collect(DISTINCT descendant) + [sector] as all_nodes
                    UNWIND all_nodes as node
                    RETURN labels(node)[0] as label, count(node) as count
                """, sector_id=sector_id)

                stats_by_type = {row["label"]: row["count"] for row in stats_result}
                total_nodes = sum(stats_by_type.values())

                # 执行级联删除
                session.run("""
                    MATCH (sector:SECTOR {id: $sector_id})-[:HAS_CHILD*]->(descendant)
                    DETACH DELETE descendant
                """, sector_id=sector_id)

                session.run("""
                    MATCH (sector:SECTOR {id: $sector_id})
                    DETACH DELETE sector
                """, sector_id=sector_id)

                total_deleted += total_nodes
                for label, count in stats_by_type.items():
                    all_stats[label] = all_stats.get(label, 0) + count

            return {
                "total_sectors": len(sector_ids),
                "total_deleted": total_deleted,
                "by_type": all_stats,
            }

    def list_sectors(self) -> List[Dict[str, str]]:
        """列出所有 SECTOR 节点"""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (s:SECTOR)
                RETURN s.id as id, s.alias as alias
                ORDER BY s.alias
            """)
            return [{"id": row["id"], "alias": row["alias"]} for row in result]


if __name__ == "__main__":
    loader = Neo4jLoader()

    try:
        with loader:
            # 创建约束
            print("=== 创建约束和索引 ===")
            loader.create_constraints()

            # 获取统计
            stats = loader.get_stats()
            print("\n=== 图谱统计 ===")
            print(json.dumps(stats, ensure_ascii=False, indent=2))

    except Exception as e:
        print(f"连接失败: {e}")
        print("请确保 Neo4j 已启动")
