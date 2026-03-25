#!/usr/bin/env python3
"""
本体层初始化脚本 - 全量导入

专用于 Theme Template Recommendation 项目。
执行完整的本体层初始化，包括：
1. 魔数师指标层节点（SECTOR, CATEGORY, THEME, SUBPATH, INDICATOR）
2. 模板层节点（INSIGHT_TEMPLATE, COMBINEDQUERY_TEMPLATE）
3. HAS_CHILD 关系（层级导航）
4. CONTAINS 关系（模板包含指标）

使用方式：
    cd agent-service/scripts
    python init_ontology.py [--skip-clean]

参数：
    --skip-clean: 跳过数据清理步骤（保留临时板块等）
"""

import argparse
import json
from pathlib import Path
import sys
from datetime import datetime

# 添加脚本目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    LOG_DIR, DEFAULT_DELETE_SECTORS,
    NEO4J_CONFIG, MYSQL_CONFIG
)
from extract_indicators import IndicatorExtractor
from extract_templates import TemplateExtractor
from build_hierarchy import HierarchyBuilder, TemplateHierarchyBuilder
from neo4j_loader import Neo4jLoader


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="初始化 Theme Template Recommendation 本体层")
    parser.add_argument(
        "--skip-clean",
        action="store_true",
        help="跳过数据清理步骤"
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="非交互模式，跳过所有确认"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Theme Template Recommendation 本体层初始化")
    print("=" * 60)
    print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Neo4j: {NEO4J_CONFIG['uri']}")
    print(f"  MySQL: {MYSQL_CONFIG['host']}:{MYSQL_CONFIG['port']}")
    print("=" * 60)

    if not args.non_interactive:
        print("\n按 Enter 开始执行，或输入 'q' 退出...")
        choice = input().strip()
        if choice.lower() == 'q':
            print("已取消执行。")
            sys.exit(0)

    # ==================== 步骤 1: 测试连接 ====================
    print("\n" + "=" * 60)
    print("  [步骤 1/9] 测试数据库连接")
    print("=" * 60)

    try:
        indicator_extractor = IndicatorExtractor()
        stats = indicator_extractor.get_ontology_stats()
        total = indicator_extractor.get_total_count()
        print(f"  ✓ MySQL 连接成功")
        print(f"  ✓ 本体层相关记录: {total:,} 条")
        for restype, count in stats.items():
            print(f"    - {restype}: {count:,}")
    except Exception as e:
        print(f"  ✗ MySQL 连接失败: {e}")
        sys.exit(1)

    try:
        loader = Neo4jLoader()
        with loader:
            _ = loader.get_stats()
        print(f"  ✓ Neo4j 连接成功")
    except Exception as e:
        print(f"  ✗ Neo4j 连接失败: {e}")
        sys.exit(1)

    if not args.non_interactive:
        print("\n按 Enter 继续...")
        input()

    # ==================== 步骤 2: 抽取指标层数据 ====================
    print("\n" + "=" * 60)
    print("  [步骤 2/9] 抽取魔数师指标层数据")
    print("=" * 60)

    indicator_extractor = IndicatorExtractor()
    restree_data = indicator_extractor.extract_all()
    print(f"  ✓ 已抽取 {len(restree_data):,} 条记录")

    if not args.non_interactive:
        print("\n按 Enter 继续...")
        input()

    # ==================== 步骤 3: 构建指标层层级 ====================
    print("\n" + "=" * 60)
    print("  [步骤 3/9] 构建指标层层级结构")
    print("=" * 60)

    hierarchy_builder = HierarchyBuilder()
    hierarchy_builder.load_from_restree(restree_data)

    indicator_nodes = hierarchy_builder.build_nodes()
    indicator_relationships = hierarchy_builder.build_relationships()
    summary = hierarchy_builder.get_tree_summary()

    print(f"  ✓ 节点: {summary['total_nodes']:,}")
    print(f"  ✓ 关系: {summary['total_relationships']:,}")
    print(f"  ✓ 按类型分布:")
    for node_type, count in summary['by_type'].items():
        print(f"    - {node_type}: {count:,}")

    if not args.non_interactive:
        print("\n按 Enter 继续...")
        input()

    # ==================== 步骤 4: 抽取模板数据 ====================
    print("\n" + "=" * 60)
    print("  [步骤 4/9] 抽取模板数据")
    print("=" * 60)

    template_extractor = TemplateExtractor()

    # 数据清洗
    if not args.skip_clean:
        print("  [4.1] 执行模板数据清洗...")
        clean_results = template_extractor.clean_templates()
        total_cleaned = sum(clean_results.values())
        print(f"  ✓ 清理完成，删除 {total_cleaned} 条无效记录")
    else:
        print("  [4.1] 跳过数据清洗")

    # 抽取 INSIGHT 模板
    print("  [4.2] 抽取 T_EXT_INSIGHT 透视分析模板...")
    insight_data = template_extractor.extract_insight_templates()
    print(f"  ✓ 已抽取 {len(insight_data)} 条 INSIGHT 模板")

    # 抽取 COMBINEDQUERY 模板
    print("  [4.3] 抽取 T_EXT_COMBINEDQUERY 万能查询模板...")
    combinedquery_data = template_extractor.extract_combinedquery_templates()
    print(f"  ✓ 已抽取 {len(combinedquery_data)} 条 COMBINEDQUERY 模板")

    # 计算热度
    print("  [4.4] 计算模板使用热度...")
    heat_map = template_extractor.extract_template_heat()
    print(f"  ✓ 已计算热度，{len(heat_map)} 个模板有点击记录")

    if not args.non_interactive:
        print("\n按 Enter 继续...")
        input()

    # ==================== 步骤 5: 构建模板层级 ====================
    print("\n" + "=" * 60)
    print("  [步骤 5/9] 构建模板层级结构")
    print("=" * 60)

    template_builder = TemplateHierarchyBuilder()

    # 构建 INSIGHT_TEMPLATE 节点
    print("  [5.1] 构建 INSIGHT_TEMPLATE 节点...")
    insight_nodes = template_builder.build_template_nodes(insight_data, "INSIGHT", heat_map)
    print(f"  ✓ 已构建 {len(insight_nodes)} 个节点")

    # 构建 COMBINEDQUERY_TEMPLATE 节点
    print("  [5.2] 构建 COMBINEDQUERY_TEMPLATE 节点...")
    cq_nodes = template_builder.build_template_nodes(combinedquery_data, "COMBINEDQUERY", heat_map)
    print(f"  ✓ 已构建 {len(cq_nodes)} 个节点")

    # 构建 HAS_CHILD 关系
    print("  [5.3] 构建 HAS_CHILD 关系...")
    insight_has_child = template_builder.build_has_child_relationships(insight_data, "INSIGHT")
    cq_has_child = template_builder.build_has_child_relationships(combinedquery_data, "COMBINEDQUERY")
    print(f"  ✓ INSIGHT: {len(insight_has_child)} 条")
    print(f"  ✓ COMBINEDQUERY: {len(cq_has_child)} 条")

    # 构建 CONTAINS 关系
    print("  [5.4] 构建 CONTAINS 关系...")
    insight_contains = template_builder.build_contains_relationships(insight_data, "INSIGHT")
    cq_contains = template_builder.build_contains_relationships(combinedquery_data, "COMBINEDQUERY")
    print(f"  ✓ INSIGHT: {len(insight_contains)} 条")
    print(f"  ✓ COMBINEDQUERY: {len(cq_contains)} 条")

    if not args.non_interactive:
        print("\n按 Enter 继续...")
        input()

    # ==================== 步骤 6: 连接 Neo4j 并创建约束 ====================
    print("\n" + "=" * 60)
    print("  [步骤 6/9] 连接 Neo4j 并创建约束")
    print("=" * 60)

    loader = Neo4jLoader()
    with loader:
        loader.create_constraints()

    if not args.non_interactive:
        print("\n按 Enter 继续...")
        input()

    # ==================== 步骤 7: 导入指标层节点和关系 ====================
    print("\n" + "=" * 60)
    print("  [步骤 7/9] 导入魔数师指标层节点和关系")
    print("=" * 60)

    with loader:
        print("  [7.1] 导入节点...")
        loader.load_indicator_layer_nodes(indicator_nodes)

        print("  [7.2] 导入关系...")
        loader.load_has_child_relationships(indicator_relationships)

    if not args.non_interactive:
        print("\n按 Enter 继续...")
        input()

    # ==================== 步骤 8: 导入模板层节点和关系 ====================
    print("\n" + "=" * 60)
    print("  [步骤 8/9] 导入模板层节点和关系")
    print("=" * 60)

    with loader:
        print("  [8.1] 导入 INSIGHT_TEMPLATE 节点...")
        loader.load_template_nodes(insight_nodes, "INSIGHT_TEMPLATE")

        print("  [8.2] 导入 COMBINEDQUERY_TEMPLATE 节点...")
        loader.load_template_nodes(cq_nodes, "COMBINEDQUERY_TEMPLATE")

        print("  [8.3] 导入模板 HAS_CHILD 关系...")
        all_template_has_child = insight_has_child + cq_has_child
        loader.load_has_child_relationships(all_template_has_child)

        print("  [8.4] 导入 INSIGHT CONTAINS 关系...")
        loader.load_contains_relationships(insight_contains, "INSIGHT_TEMPLATE", "INDICATOR")

        print("  [8.5] 导入 COMBINEDQUERY CONTAINS 关系...")
        loader.load_contains_relationships(cq_contains, "COMBINEDQUERY_TEMPLATE", "INDICATOR")

    if not args.non_interactive:
        print("\n按 Enter 继续...")
        input()

    # ==================== 步骤 9: 数据清理（删除临时板块） ====================
    print("\n" + "=" * 60)
    print("  [步骤 9/9] 数据清理")
    print("=" * 60)

    with loader:
        # 列出所有 SECTOR
        sectors = loader.list_sectors()

        print("  当前所有 SECTOR:")
        for sector in sectors:
            mark = " [默认删除]" if sector['alias'] in DEFAULT_DELETE_SECTORS else ""
            print(f"    - {sector['alias']}{mark}")

        # 删除默认 SECTOR
        default_sectors = [s for s in sectors if s['alias'] in DEFAULT_DELETE_SECTORS]
        if default_sectors:
            print(f"\n  删除默认 SECTOR: {[s['alias'] for s in default_sectors]}")
            for sector in default_sectors:
                result = loader.delete_sectors_cascade([sector['id']])
                print(f"    ✓ {sector['alias']}: 删除 {result['total_deleted']} 个节点")

    # ==================== 完成 ====================
    print("\n" + "=" * 60)
    print("  最终统计")
    print("=" * 60)

    with loader:
        stats = loader.get_stats()
        template_stats = loader.get_template_stats()

    print(f"  总节点数: {stats['total_nodes']:,}")
    print(f"  总关系数: {stats['total_relationships']:,}")
    print(f"\n  按类型分布:")
    for node_type, count in stats['by_type'].items():
        print(f"    - {node_type}: {count:,}")

    print(f"\n  模板层统计:")
    print(f"    - INSIGHT_TEMPLATE: {template_stats['insight_template_count']:,}")
    print(f"    - COMBINEDQUERY_TEMPLATE: {template_stats['combinedquery_template_count']:,}")
    print(f"    - 有热度的 INSIGHT: {template_stats['insight_with_heat']:,}")
    print(f"    - 有热度的 COMBINEDQUERY: {template_stats['combinedquery_with_heat']:,}")
    print(f"    - CONTAINS 关系: {template_stats['contains_relationship_count']:,}")

    print("\n" + "=" * 60)
    print("  ✓ 本体层初始化完成！")
    print("=" * 60)
    print(f"  完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n  可通过 Neo4j Browser 查看:")
    print("    1. 访问 http://localhost:7474")
    print("    2. 执行 MATCH (n) RETURN n LIMIT 100")


if __name__ == "__main__":
    main()
