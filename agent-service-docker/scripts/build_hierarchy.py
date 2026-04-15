"""
数据转换模块 - 构建层级结构和节点关系

专用于 Theme Template Recommendation 项目。
功能：
1. 构建魔数师指标层层级关系（SECTOR → CATEGORY → THEME → SUBPATH → INDICATOR）
2. 构建模板节点（INSIGHT_TEMPLATE / COMBINEDQUERY_TEMPLATE）
3. 构建 HAS_CHILD 和 CONTAINS 关系
"""

import json
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict
from pathlib import Path
import sys

# 添加脚本目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from config import NODE_TYPE_MAPPING, ENTRY_NODE_ID


class HierarchyBuilder:
    """
    层级结构构建器

    构建魔数师指标层的树形结构：
    - 自主分析 (根)
      - SECTOR (板块)
        - CATEGORY (分类)
          - THEME (主题) - 核心节点
            - SUBPATH (子路径)
              - INDICATOR (指标) - 核心节点
    """

    # MySQL c_restype 到 Neo4j 标签的映射
    RESTYPE_TO_LABEL = {
        'BUSINESS_THEMES': 'CATEGORY',  # 除 SECTOR 外的 BUSINESS_THEMES
        'BUSINESS_THEME': 'THEME',
        'BUSINESS_OBJECT': 'SUBPATH',
        'BUSINESS_ATTRIBUTE': 'INDICATOR',
    }

    def __init__(self):
        self.nodes: List[Dict[str, Any]] = []
        self.relationships: List[Dict[str, Any]] = []
        self.node_map: Dict[str, Dict[str, Any]] = {}  # id -> node
        self.parent_map: Dict[str, str] = {}  # child_id -> parent_id
        self.level_map: Dict[str, int] = {}  # id -> level
        self.path_map: Dict[str, str] = {}  # id -> path

    def load_from_restree(self, restree_data: List[Dict[str, Any]]):
        """
        从 t_restree 抽取的数据构建层级结构

        Args:
            restree_data: extract_indicators.py 抽取的数据
        """
        # 第一遍：构建节点映射
        for row in restree_data:
            node_id = row['c_resid']
            parent_id = row['c_pid']
            alias = row['c_resalias'] or ''
            restype = row['c_restype']
            order = row.get('c_order', 0)

            self.node_map[node_id] = {
                'id': node_id,
                'alias': alias,
                'restype': restype,
                'order': order,
            }
            self.parent_map[node_id] = parent_id

        # 计算层级和路径
        self._calculate_levels()
        self._calculate_paths()

        # 第二遍：构建最终节点列表
        for node_id, node_data in self.node_map.items():
            node_type = self._get_node_type(node_id, node_data['restype'])
            if node_type:
                self.nodes.append({
                    'id': node_id,
                    'alias': node_data['alias'],
                    'type': node_type,
                    'level': self.level_map.get(node_id, 0),
                    'path': self.path_map.get(node_id, ''),
                    'parent_id': self.parent_map.get(node_id, ''),
                    'restype': node_data['restype'],
                    'order': node_data.get('order', 0),
                })

        # 构建关系
        for node_id, parent_id in self.parent_map.items():
            if parent_id and parent_id in self.node_map:
                self.relationships.append({
                    'from': parent_id,
                    'to': node_id,
                    'type': 'HAS_CHILD',
                })

    def _get_node_type(self, node_id: str, restype: str) -> Optional[str]:
        """确定节点的 Neo4j 标签"""
        if node_id == ENTRY_NODE_ID:
            return 'CATEGORY'  # 根节点 "自主分析"

        if restype == 'BUSINESS_THEMES':
            # 检查是否是 SECTOR（自主分析的直接子节点）
            parent_id = self.parent_map.get(node_id, '')
            if parent_id == ENTRY_NODE_ID:
                return 'SECTOR'
            return 'CATEGORY'

        return self.RESTYPE_TO_LABEL.get(restype)

    def _calculate_levels(self):
        """计算每个节点的层级深度"""
        def get_level(node_id: str, visited: set = None) -> int:
            if visited is None:
                visited = set()

            if node_id in self.level_map:
                return self.level_map[node_id]

            if node_id in visited:  # 防止循环
                return 0

            visited.add(node_id)

            if node_id == ENTRY_NODE_ID:
                self.level_map[node_id] = 1
                return 1

            parent_id = self.parent_map.get(node_id)
            if not parent_id or parent_id not in self.node_map:
                self.level_map[node_id] = 0
                return 0

            parent_level = get_level(parent_id, visited)
            self.level_map[node_id] = parent_level + 1
            return self.level_map[node_id]

        for node_id in self.node_map:
            get_level(node_id)

    def _calculate_paths(self):
        """计算每个节点的完整路径"""
        def get_path(node_id: str, visited: set = None) -> str:
            if visited is None:
                visited = set()

            if node_id in self.path_map:
                return self.path_map[node_id]

            if node_id in visited:  # 防止循环
                return ''

            visited.add(node_id)

            node = self.node_map.get(node_id, {})
            alias = node.get('alias', '')

            if node_id == ENTRY_NODE_ID:
                self.path_map[node_id] = alias
                return alias

            parent_id = self.parent_map.get(node_id)
            if not parent_id or parent_id not in self.node_map:
                self.path_map[node_id] = alias
                return alias

            parent_path = get_path(parent_id, visited)
            self.path_map[node_id] = f"{parent_path} > {alias}" if parent_path else alias
            return self.path_map[node_id]

        for node_id in self.node_map:
            get_path(node_id)

    def build_nodes(self) -> List[Dict[str, Any]]:
        """返回构建的节点列表"""
        return self.nodes

    def build_relationships(self) -> List[Dict[str, Any]]:
        """返回构建的关系列表"""
        return self.relationships

    def get_tree_summary(self) -> Dict[str, Any]:
        """获取树结构摘要"""
        type_counts = defaultdict(int)
        level_counts = defaultdict(int)

        for node in self.nodes:
            type_counts[node['type']] += 1
            level_counts[node['level']] += 1

        return {
            'total_nodes': len(self.nodes),
            'total_relationships': len(self.relationships),
            'by_type': dict(type_counts),
            'by_level': dict(level_counts),
        }


class TemplateHierarchyBuilder:
    """
    模板层级构建器

    构建模板节点和关系：
    - THEME --HAS_CHILD--> TEMPLATE
    - TEMPLATE --CONTAINS--> INDICATOR
    """

    # 节点标签映射
    TEMPLATE_TYPE_TO_LABEL = {
        "INSIGHT": "INSIGHT_TEMPLATE",
        "COMBINEDQUERY": "COMBINEDQUERY_TEMPLATE",
    }

    def build_template_nodes(
        self,
        templates_data: List[Dict[str, Any]],
        template_type: str,
        heat_map: Dict[str, int],
    ) -> List[Dict[str, Any]]:
        """
        构建模板节点列表

        Args:
            templates_data: 抽取的模板数据
            template_type: "INSIGHT" 或 "COMBINEDQUERY"
            heat_map: 热度映射 {c_id: heat_count}

        Returns:
            节点列表
        """
        nodes = []

        for t in templates_data:
            parsed = t.get('parsed', {})
            c_id = t.get('c_id', '')

            node = {
                'id': f"TEMPLATE.{template_type.upper()}.{c_id}",
                'alias': t.get('c_name', '') or t.get('c_alias', '') or '',
                'description': self._truncate((t.get('c_desc') or ''), 512),
                'template_type': template_type.upper(),
                'heat': heat_map.get(c_id, 0),
                'theme_id': parsed.get('theme_id', ''),
                'indicator_count': len(parsed.get('indicators', [])),
                'source_pk': t.get('source_pk', 0),
                'status': 'active',
            }
            nodes.append(node)

        return nodes

    def _truncate(self, text: str, max_length: int) -> str:
        """截断文本到指定长度"""
        if len(text) <= max_length:
            return text
        return text[:max_length]

    def build_has_child_relationships(
        self,
        templates_data: List[Dict[str, Any]],
        template_type: str,
    ) -> List[Dict[str, Any]]:
        """
        构建 HAS_CHILD 关系

        包含两类：
        1. THEME -> TEMPLATE
        2. TEMPLATE -> INDICATOR
        """
        relationships = []

        for t in templates_data:
            parsed = t.get('parsed', {})
            c_id = t.get('c_id', '')
            template_node_id = f"TEMPLATE.{template_type.upper()}.{c_id}"

            # THEME -> TEMPLATE
            theme_id = parsed.get('theme_id', '')
            if theme_id:
                relationships.append({
                    'from': theme_id,
                    'to': template_node_id,
                    'type': 'HAS_CHILD',
                })

            # TEMPLATE -> INDICATOR (HAS_CHILD for tree hierarchy)
            for ind in parsed.get('indicators', []):
                ind_id = ind.get('id', '')
                if ind_id:
                    relationships.append({
                        'from': template_node_id,
                        'to': ind_id,
                        'type': 'HAS_CHILD',
                    })

        return relationships

    def build_contains_relationships(
        self,
        templates_data: List[Dict[str, Any]],
        template_type: str,
    ) -> List[Dict[str, Any]]:
        """
        构建 CONTAINS 语义关系（带 position 属性）
        """
        relationships = []

        for t in templates_data:
            parsed = t.get('parsed', {})
            c_id = t.get('c_id', '')
            template_node_id = f"TEMPLATE.{template_type.upper()}.{c_id}"

            for ind in parsed.get('indicators', []):
                ind_id = ind.get('id', '')
                position = ind.get('position', 0)
                if ind_id:
                    relationships.append({
                        'from': template_node_id,
                        'to': ind_id,
                        'type': 'CONTAINS',
                        'properties': {
                            'position': position,
                        }
                    })

        return relationships

    def get_build_statistics(
        self,
        templates_data: List[Dict[str, Any]],
        template_type: str,
    ) -> Dict[str, Any]:
        """获取构建统计信息"""
        stats = {
            'template_type': template_type,
            'total_templates': len(templates_data),
            'templates_with_theme': 0,
            'templates_with_indicators': 0,
            'total_indicator_refs': 0,
            'has_child_count': 0,
            'contains_count': 0,
        }

        for t in templates_data:
            parsed = t.get('parsed', {})

            if parsed.get('theme_id'):
                stats['templates_with_theme'] += 1

            indicators = parsed.get('indicators', [])
            if indicators:
                stats['templates_with_indicators'] += 1
                stats['total_indicator_refs'] += len(indicators)

        stats['has_child_count'] = (
            stats['templates_with_theme'] + stats['total_indicator_refs']
        )
        stats['contains_count'] = stats['total_indicator_refs']

        return stats


if __name__ == "__main__":
    # 测试层级构建
    print("=== 测试层级构建 ===")

    # 模拟数据
    test_data = [
        {'c_resid': ENTRY_NODE_ID, 'c_resalias': '自主分析', 'c_restype': 'BUSINESS_THEMES', 'c_pid': '', 'c_order': 0},
        {'c_resid': 'sector1', 'c_resalias': '资产板块', 'c_restype': 'BUSINESS_THEMES', 'c_pid': ENTRY_NODE_ID, 'c_order': 1},
        {'c_resid': 'theme1', 'c_resalias': '对公贷款', 'c_restype': 'BUSINESS_THEME', 'c_pid': 'sector1', 'c_order': 1},
        {'c_resid': 'indicator1', 'c_resalias': '贷款余额', 'c_restype': 'BUSINESS_ATTRIBUTE', 'c_pid': 'theme1', 'c_order': 1},
    ]

    builder = HierarchyBuilder()
    builder.load_from_restree(test_data)

    print("\n节点列表:")
    for node in builder.build_nodes():
        print(f"  [{node['type']}] {node['alias']} (level={node['level']})")

    print("\n关系统表:")
    for rel in builder.build_relationships():
        print(f"  {rel['from']} --{rel['type']}--> {rel['to']}")

    print("\n摘要:")
    print(json.dumps(builder.get_tree_summary(), ensure_ascii=False, indent=2))
