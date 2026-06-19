"""
数据抽取模块 - 从 MySQL 抽取透视分析和万能查询模板数据

专用于 Theme Template Recommendation 项目。
功能：
1. 抽取 T_EXT_INSIGHT 和 T_EXT_COMBINEDQUERY 数据
2. 解析 c_content XML，提取 theme_id、指标引用
3. 计算模板使用热度
"""

import pymysql
import json
import re
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional
from pathlib import Path
import sys

# 添加脚本目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    MYSQL_CONFIG, SOURCE_TABLES,
    TEMPLATE_EXTRACT_SQL, TEMPLATE_CLEAN_SQL, HEAT_QUERY_SQL
)


class TemplateExtractor:
    """模板数据抽取器"""

    # 中文字符正则
    _CHINESE_PATTERN = re.compile(r'[\u4e00-\u9fa5]')

    def __init__(self):
        self.config = MYSQL_CONFIG

    def get_connection(self):
        """获取 MySQL 连接"""
        return pymysql.connect(**self.config)

    def _has_chinese(self, text: str) -> bool:
        """检查文本是否包含中文字符"""
        return bool(text and self._CHINESE_PATTERN.search(text))

    def _is_valid_name(self, c_name: str, c_alias: str) -> bool:
        """验证模板名称是否有效（至少有一个包含中文）"""
        return self._has_chinese(c_name) or self._has_chinese(c_alias or '')

    def clean_timestamp_suffix(self, c_name: str) -> str:
        """清洗时间戳后缀：xxx_20251020090746 -> xxx"""
        return re.sub(r'_\d{14}$', '', c_name)

    # ==================== 数据清洗 ====================

    def clean_templates(self) -> Dict[str, int]:
        """
        执行模板数据清理：删除无效记录

        清理规则：
        1. 删除无中文名称的记录
        2. 删除带 UUID 后缀的记录
        3. 删除带时间戳后缀的记录

        Returns:
            各清理步骤删除的记录数
        """
        results = {}

        clean_steps = [
            ("insight_no_chinese", "T_EXT_INSIGHT", "删除无中文名称"),
            ("combinedquery_no_chinese", "T_EXT_COMBINEDQUERY", "删除无中文名称"),
            ("insight_timestamp", "T_EXT_INSIGHT", "删除时间戳/UUID后缀"),
            ("combinedquery_timestamp", "T_EXT_COMBINEDQUERY", "删除时间戳/UUID后缀"),
        ]

        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                for step_key, table_name, description in clean_steps:
                    sql = TEMPLATE_CLEAN_SQL.get(step_key, "")
                    if not sql:
                        continue

                    # 统计要删除的记录数
                    count_sql = sql.replace("DELETE FROM", "SELECT COUNT(*) FROM")
                    try:
                        cursor.execute(count_sql)
                        count = cursor.fetchone()[0]
                    except:
                        count = 0

                    # 执行删除
                    cursor.execute(sql)
                    conn.commit()

                    results[step_key] = count
                    print(f"  ✓ {table_name}: {description} - 删除 {count} 条")

        finally:
            conn.close()

        return results

    # ==================== XML 解析 ====================

    def sanitize_xml(self, xml_content: str) -> str:
        """清理非法 XML 字符"""
        if not xml_content:
            return ''
        xml_content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', xml_content)
        xml_content = xml_content.replace(']]', ']]>')
        return xml_content

    def parse_insight_xml(self, xml_content: str) -> Dict[str, Any]:
        """
        解析 INSIGHT 透视分析模板的 c_content XML

        Returns:
            {
                'theme_id': str,           # THEME 节点 ID
                'indicators': [            # 指标引用列表
                    {'id': str, 'position': int, 'field_type': str}
                ],
                'calc_fields': [],         # 计算字段
                'filters': {},             # 过滤条件 JSON
                'parameters': {},          # 参数配置 JSON
            }
        """
        result = {
            'theme_id': '',
            'indicators': [],
            'calc_fields': [],
            'filters': {},
            'parameters': {},
        }

        if not xml_content:
            return result

        xml_content = self.sanitize_xml(xml_content)
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError:
            return result

        # businessThemeId 属性
        result['theme_id'] = root.get('businessThemeId', '') or ''

        # 解析 fields
        fields_elem = root.find('fields')
        if fields_elem is not None:
            for i, field in enumerate(fields_elem.findall('field')):
                field_type = field.get('fieldType', 'basic') or 'basic'
                src_field_id = field.get('srcFieldId', '') or ''

                if src_field_id.startswith('BIZATTR.'):
                    item = {
                        'id': src_field_id,
                        'position': i,
                        'field_type': field_type,
                    }

                    # 计算字段
                    if field_type == 'calc':
                        result['calc_fields'].append({
                            'name': field.get('name', '') or '',
                            'alias': field.get('alias', '') or '',
                            'ref_field': src_field_id,
                            'position': i,
                        })

                    result['indicators'].append(item)

        # 解析 CONDITION
        cond = root.find('CONDITION')
        if cond is not None and cond.text:
            try:
                result['filters'] = json.loads(cond.text.strip())
            except (json.JSONDecodeError, TypeError):
                pass

        # 解析 CONDITION_PARAMETER
        cond_params = root.find('CONDITION_PARAMETER')
        if cond_params is not None and cond_params.text:
            try:
                result['parameters'] = json.loads(cond_params.text.strip())
            except (json.JSONDecodeError, TypeError):
                pass

        return result

    def parse_combinedquery_xml(self, xml_content: str) -> Dict[str, Any]:
        """
        解析 COMBINEDQUERY 万能查询模板的 c_content XML

        Returns:
            {
                'theme_id': str,
                'ds_id': str,
                'indicators': [],
                'bizview_output_fields': [],
                'calc_fields': [],
                'filters': {},
                'parameters': {},
            }
        """
        result = {
            'theme_id': '',
            'ds_id': '',
            'indicators': [],
            'bizview_output_fields': [],
            'calc_fields': [],
            'filters': {},
            'parameters': {},
        }

        if not xml_content:
            return result

        xml_content = self.sanitize_xml(xml_content)
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError:
            return result

        # themeId 和 dsId 属性
        result['theme_id'] = root.get('themeId', '') or ''
        result['ds_id'] = root.get('dsId', '') or ''

        # 解析 select-fields
        select_fields = root.find('.//select-fields')
        if select_fields is not None:
            for i, field in enumerate(select_fields.findall('field')):
                field_id = field.get('id', '') or ''
                if field_id.startswith('BIZATTR.'):
                    result['indicators'].append({
                        'id': field_id,
                        'position': i,
                    })

        # 解析 output-fields
        output_fields = root.find('.//output-fields')
        if output_fields is not None:
            for i, field in enumerate(output_fields.findall('field')):
                field_id = field.get('id', '') or ''
                if field_id:
                    result['bizview_output_fields'].append({
                        'id': field_id,
                        'position': i,
                    })

        # 解析 conditionpanel-expression
        cp_expr = root.find('.//conditionpanel-expression')
        if cp_expr is not None and cp_expr.text:
            try:
                result['filters'] = json.loads(cp_expr.text.strip())
            except (json.JSONDecodeError, TypeError):
                pass

        # 解析 conditionpanel-paramsetting
        cp_params = root.find('.//conditionpanel-paramsetting')
        if cp_params is not None and cp_params.text:
            try:
                result['parameters'] = json.loads(cp_params.text.strip())
            except (json.JSONDecodeError, TypeError):
                pass

        return result

    # ==================== 数据抽取 ====================

    def extract_insight_templates(self) -> List[Dict[str, Any]]:
        """抽取 T_EXT_INSIGHT 透视分析模板数据"""
        sql = TEMPLATE_EXTRACT_SQL["insight"]
        results = []
        skipped = 0

        with self.get_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(sql)
                for row in cursor:
                    c_name = row.get('c_name') or ''
                    c_alias = row.get('c_alias') or ''

                    if not self._is_valid_name(c_name, c_alias):
                        skipped += 1
                        continue

                    c_name = self.clean_timestamp_suffix(c_name)
                    parsed = self.parse_insight_xml(row.get('c_content', '') or '')

                    results.append({
                        'source_pk': row.get('id'),
                        'c_id': row.get('c_id'),
                        'c_name': c_name,
                        'c_alias': c_alias,
                        'c_desc': row.get('c_desc'),
                        'parsed': parsed,
                    })

        if skipped > 0:
            print(f"  跳过 {skipped} 条无中文名称的记录")
        return results

    def extract_combinedquery_templates(self) -> List[Dict[str, Any]]:
        """抽取 T_EXT_COMBINEDQUERY 万能查询模板数据"""
        sql = TEMPLATE_EXTRACT_SQL["combinedquery"]
        results = []
        skipped = 0

        with self.get_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(sql)
                for row in cursor:
                    c_name = row.get('c_name') or ''
                    c_alias = row.get('c_alias') or ''

                    if not self._is_valid_name(c_name, c_alias):
                        skipped += 1
                        continue

                    c_name = self.clean_timestamp_suffix(c_name)
                    parsed = self.parse_combinedquery_xml(row.get('c_content', '') or '')

                    results.append({
                        'source_pk': row.get('id'),
                        'c_id': row.get('c_id'),
                        'c_name': c_name,
                        'c_alias': c_alias,
                        'c_desc': row.get('c_desc'),
                        'parsed': parsed,
                    })

        if skipped > 0:
            print(f"  跳过 {skipped} 条无中文名称的记录")
        return results

    def extract_template_heat(self) -> Dict[str, int]:
        """从 t_ext_click_log 计算模板使用热度"""
        heat_map: Dict[str, int] = {}
        with self.get_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(HEAT_QUERY_SQL)
                for row in cursor:
                    template_c_id = row.get('template_c_id')
                    heat = row.get('heat', 0) or 0
                    if template_c_id:
                        heat_map[str(template_c_id)] = int(heat)
        return heat_map

    def get_statistics(self) -> Dict[str, Any]:
        """获取模板数据统计信息"""
        stats = {
            'insight_count': 0,
            'combinedquery_count': 0,
            'total_heat': 0,
        }

        with self.get_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM T_EXT_INSIGHT "
                    "WHERE c_content IS NOT NULL AND c_content != ''"
                )
                stats['insight_count'] = cursor.fetchone()['cnt']

                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM T_EXT_COMBINEDQUERY "
                    "WHERE c_content IS NOT NULL AND c_content != ''"
                )
                stats['combinedquery_count'] = cursor.fetchone()['cnt']

                cursor.execute(HEAT_QUERY_SQL)
                heat_map = {}
                for row in cursor:
                    c_id = str(row.get('template_c_id') or '')
                    heat_map[c_id] = int(row.get('heat') or 0)
                stats['total_heat'] = sum(heat_map.values())

        return stats


if __name__ == "__main__":
    extractor = TemplateExtractor()

    print("=== 模板数据统计 ===")
    stats = extractor.get_statistics()
    print(f"  INSIGHT 模板数: {stats['insight_count']}")
    print(f"  COMBINEDQUERY 模板数: {stats['combinedquery_count']}")
    print(f"  总点击次数: {stats['total_heat']}")

    print("\n=== 抽取模板数据 ===")

    print("\n[1/3] 抽取 INSIGHT 模板...")
    insight_data = extractor.extract_insight_templates()
    print(f"  已抽取 {len(insight_data)} 条 INSIGHT 模板")

    print("\n[2/3] 抽取 COMBINEDQUERY 模板...")
    cq_data = extractor.extract_combinedquery_templates()
    print(f"  已抽取 {len(cq_data)} 条 COMBINEDQUERY 模板")

    print("\n[3/3] 计算模板热度...")
    heat_map = extractor.extract_template_heat()
    print(f"  已计算 {len(heat_map)} 个模板的热度")

    # 显示示例
    if insight_data:
        sample = insight_data[0]
        print(f"\n=== INSIGHT 示例 ===")
        print(f"  c_id: {sample.get('c_id')}")
        print(f"  名称: {sample.get('c_name')}")
        parsed = sample.get('parsed', {})
        print(f"  theme_id: {parsed.get('theme_id')}")
        print(f"  指标数: {len(parsed.get('indicators', []))}")
