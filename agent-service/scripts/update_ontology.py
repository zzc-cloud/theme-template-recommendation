#!/usr/bin/env python3
"""
本体层增量更新脚本

专用于 Theme Template Recommendation 项目。
执行增量更新，包括：
1. 检测新增/修改的指标层节点
2. 检测新增/修改的模板
3. 增量更新 Neo4j 图谱

使用方式：
    cd agent-service/scripts
    python update_ontology.py [--last-update "2024-01-01 00:00:00"]

参数：
    --last-update: 上次更新时间（格式：YYYY-MM-DD HH:MM:SS）
                   如果不指定，则从 last_update.txt 读取
    --full:        强制全量更新
"""

import argparse
import json
from pathlib import Path
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

# 添加脚本目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    LOG_DIR, NEO4J_CONFIG, MYSQL_CONFIG
)
from extract_indicators import IndicatorExtractor
from extract_templates import TemplateExtractor
from build_hierarchy import HierarchyBuilder, TemplateHierarchyBuilder
from neo4j_loader import Neo4jLoader


# 上次更新时间记录文件
LAST_UPDATE_FILE = Path(__file__).parent / "last_update.txt"


def get_last_update_time() -> Optional[str]:
    """读取上次更新时间"""
    if LAST_UPDATE_FILE.exists():
        with open(LAST_UPDATE_FILE, "r") as f:
            return f.read().strip()
    return None


def save_last_update_time(timestamp: str):
    """保存更新时间"""
    with open(LAST_UPDATE_FILE, "w") as f:
        f.write(timestamp)


def update_indicator_layer(last_update_time: Optional[str] = None) -> Tuple[int, int]:
    """
    更新指标层

    Args:
        last_update_time: 上次更新时间

    Returns:
        (new_nodes, updated_nodes)
    """
    print("\n" + "=" * 60)
    print("  更新魔数师指标层")
    print("=" * 60)

    extractor = IndicatorExtractor()
    loader = Neo4jLoader()

    # 获取当前统计
    current_stats = extractor.get_ontology_stats()
    total_count = extractor.get_total_count()
    print(f"  当前 MySQL 记录数: {total_count:,}")

    if last_update_time:
        print(f"  上次更新时间: {last_update_time}")
        # 增量抽取
        incremental_data = extractor.extract_incremental(last_update_time)
        print(f"  增量记录数: {len(incremental_data):,}")

        if not incremental_data:
            print("  无需更新")
            return (0, 0)

        # 构建层级
        builder = HierarchyBuilder()
        builder.load_from_restree(incremental_data)
        nodes = builder.build_nodes()
        relationships = builder.build_relationships()

        # 导入 Neo4j
        with loader:
            loader.load_indicator_layer_nodes(nodes)
            loader.load_has_child_relationships(relationships)

        return (len(nodes), len(relationships))
    else:
        # 全量更新
        print("  执行全量更新...")
        all_data = extractor.extract_all()

        builder = HierarchyBuilder()
        builder.load_from_restree(all_data)
        nodes = builder.build_nodes()
        relationships = builder.build_relationships()

        with loader:
            # 先清空指标层
            loader.clear_indicator_layer()
            # 重新导入
            loader.load_indicator_layer_nodes(nodes)
            loader.load_has_child_relationships(relationships)

        return (len(nodes), len(relationships))


def update_template_layer(last_update_time: Optional[str] = None) -> Tuple[int, int, int]:
    """
    更新模板层

    Args:
        last_update_time: 上次更新时间

    Returns:
        (new_templates, new_relationships, updated_heat)
    """
    print("\n" + "=" * 60)
    print("  更新模板层")
    print("=" * 60)

    extractor = TemplateExtractor()
    loader = Neo4jLoader()

    # 抽取模板数据
    print("  [1/4] 抽取 INSIGHT 模板...")
    insight_data = extractor.extract_insight_templates()
    print(f"  ✓ 已抽取 {len(insight_data)} 条")

    print("  [2/4] 抽取 COMBINEDQUERY 模板...")
    combinedquery_data = extractor.extract_combinedquery_templates()
    print(f"  ✓ 已抽取 {len(combinedquery_data)} 条")

    print("  [3/4] 计算模板热度...")
    heat_map = extractor.extract_template_heat()
    print(f"  ✓ 已计算热度，{len(heat_map)} 个模板有点击记录")

    # 构建层级
    builder = TemplateHierarchyBuilder()

    insight_nodes = builder.build_template_nodes(insight_data, "INSIGHT", heat_map)
    cq_nodes = builder.build_template_nodes(combinedquery_data, "COMBINEDQUERY", heat_map)

    insight_has_child = builder.build_has_child_relationships(insight_data, "INSIGHT")
    cq_has_child = builder.build_has_child_relationships(combinedquery_data, "COMBINEDQUERY")

    insight_contains = builder.build_contains_relationships(insight_data, "INSIGHT")
    cq_contains = builder.build_contains_relationships(combinedquery_data, "COMBINEDQUERY")

    # 导入 Neo4j
    print("  [4/4] 导入 Neo4j...")

    with loader:
        # 清空模板层
        loader.clear_template_layer()

        # 导入节点
        loader.load_template_nodes(insight_nodes, "INSIGHT_TEMPLATE")
        loader.load_template_nodes(cq_nodes, "COMBINEDQUERY_TEMPLATE")

        # 导入关系
        all_has_child = insight_has_child + cq_has_child
        loader.load_has_child_relationships(all_has_child)

        loader.load_contains_relationships(insight_contains, "INSIGHT_TEMPLATE", "INDICATOR")
        loader.load_contains_relationships(cq_contains, "COMBINEDQUERY_TEMPLATE", "INDICATOR")

    total_templates = len(insight_nodes) + len(cq_nodes)
    total_rels = len(all_has_child) + len(insight_contains) + len(cq_contains)

    return (total_templates, total_rels, len(heat_map))


def verify_update() -> bool:
    """验证更新结果"""
    print("\n" + "=" * 60)
    print("  验证更新结果")
    print("=" * 60)

    loader = Neo4jLoader()

    with loader:
        stats = loader.get_stats()
        template_stats = loader.get_template_stats()

    print(f"  总节点数: {stats['total_nodes']:,}")
    print(f"  总关系数: {stats['total_relationships']:,}")

    # 基本验证
    has_indicators = stats['by_type'].get('INDICATOR', 0) > 0
    has_themes = stats['by_type'].get('THEME', 0) > 0
    has_templates = (
        template_stats['insight_template_count'] > 0 or
        template_stats['combinedquery_template_count'] > 0
    )

    if has_indicators and has_themes:
        print("  ✓ 指标层验证通过")
    else:
        print("  ✗ 指标层验证失败")
        return False

    if has_templates:
        print("  ✓ 模板层验证通过")
    else:
        print("  ✗ 模板层验证失败")
        return False

    return True


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="更新 Theme Template Recommendation 本体层")
    parser.add_argument(
        "--last-update",
        type=str,
        help="上次更新时间（格式：YYYY-MM-DD HH:MM:SS）"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="强制全量更新"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Theme Template Recommendation 本体层更新")
    print("=" * 60)
    print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Neo4j: {NEO4J_CONFIG['uri']}")
    print(f"  MySQL: {MYSQL_CONFIG['host']}:{MYSQL_CONFIG['port']}")
    print("=" * 60)

    # 确定更新模式
    if args.full:
        last_update_time = None
        print("\n  模式: 全量更新")
    elif args.last_update:
        last_update_time = args.last_update
        print(f"\n  模式: 增量更新（自 {last_update_time}）")
    else:
        last_update_time = get_last_update_time()
        if last_update_time:
            print(f"\n  模式: 增量更新（自 {last_update_time}）")
        else:
            print("\n  模式: 全量更新（首次运行）")

    # 执行更新
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. 更新指标层
    new_nodes, new_rels = update_indicator_layer(
        None if args.full else last_update_time
    )

    # 2. 更新模板层（总是全量更新，因为模板数据量相对较小且变化频繁）
    new_templates, new_template_rels, updated_heat = update_template_layer()

    # 3. 验证
    if not verify_update():
        print("\n  ✗ 更新验证失败，请检查数据")
        sys.exit(1)

    # 保存更新时间
    save_last_update_time(current_time)
    print(f"\n  已保存更新时间: {current_time}")

    # 最终统计
    print("\n" + "=" * 60)
    print("  更新完成")
    print("=" * 60)
    print(f"  指标层:")
    print(f"    - 新增/更新节点: {new_nodes:,}")
    print(f"    - 新增/更新关系: {new_rels:,}")
    print(f"  模板层:")
    print(f"    - 模板数: {new_templates:,}")
    print(f"    - 关系数: {new_template_rels:,}")
    print(f"    - 热度更新: {updated_heat:,}")
    print(f"\n  完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
