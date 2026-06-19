"""
Theme Template Recommendation - 本体层配置文件

专用于 Theme Template Recommendation 项目的本体构建配置。
仅包含该项目所需的实体类型和关系：
- 魔数师指标层：SECTOR, CATEGORY, THEME, SUBPATH, INDICATOR
- 模板层：INSIGHT_TEMPLATE, COMBINEDQUERY_TEMPLATE
- 关系：HAS_CHILD, CONTAINS

使用方式：
1. 复制 .env.example 为 .env
2. 在 .env 中填写数据库密码
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# ============== 项目根目录 ==============
PROJECT_ROOT = Path(__file__).parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ============== MySQL 配置 ==============
MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "chatbi_metadata"),
    "charset": "utf8mb4",
}

# ============== Neo4j 配置 ==============
NEO4J_CONFIG = {
    "uri": os.getenv("NEO4J_URI", "bolt://localhost:7687"),
    "user": os.getenv("NEO4J_USER", "neo4j"),
    "password": os.getenv("NEO4J_PASSWORD", "password"),
}

# ============== 源数据表配置 ==============
SOURCE_TABLES = {
    # 魔数师指标树形结构表
    "restree": "njcb_metadata_map_t_restree",
    # 模板数据表
    "t_ext_insight": "T_EXT_INSIGHT",
    "t_ext_combinedquery": "T_EXT_COMBINEDQUERY",
    "t_ext_click_log": "t_ext_click_log",
}

# ============== 节点类型映射 ==============
# THEME template recommendation 只需要魔数师指标层的节点
NODE_TYPE_MAPPING = {
    # 板块：自主分析下的第一层
    "SECTOR": {
        "c_restype": "BUSINESS_THEMES",
        "level": 2,
    },
    # 分类：BUSINESS_THEMES 中板块下的层级
    "CATEGORY": {
        "c_restype": "BUSINESS_THEMES",
        "level": None,  # 动态层级
    },
    # 主题：BUSINESS_THEME（核心节点）
    "THEME": {
        "c_restype": "BUSINESS_THEME",
        "level": None,
    },
    # 子路径：BUSINESS_OBJECT
    "SUBPATH": {
        "c_restype": "BUSINESS_OBJECT",
        "level": None,
    },
    # 指标：BUSINESS_ATTRIBUTE（核心节点）
    "INDICATOR": {
        "c_restype": "BUSINESS_ATTRIBUTE",
        "level": None,
    },
}

# ============== 根节点配置 ==============
# 自主分析根节点 ID
ENTRY_NODE_ID = "I1f81a9b501690f510f5168e301690f6b2b4f004c"

# ============== 本体层需要的资源类型 ==============
# 仅包含魔数师指标层相关的 4 种类型
ONTOLOGY_RESTYPES = (
    'BUSINESS_THEMES',    # SECTOR, CATEGORY
    'BUSINESS_THEME',     # THEME
    'BUSINESS_OBJECT',    # SUBPATH
    'BUSINESS_ATTRIBUTE'  # INDICATOR
)

# ============== 批量处理配置 ==============
BATCH_SIZE = 1000

# ============== 模板节点类型配置 ==============
TEMPLATE_NODE_TYPES = {
    "INSIGHT_TEMPLATE": {"type": "INSIGHT", "xml_root": "insight"},
    "COMBINEDQUERY_TEMPLATE": {"type": "COMBINEDQUERY", "xml_root": "combined-query"},
}

# ============== 热度查询 SQL ==============
HEAT_QUERY_SQL = """
SELECT C_RES_ID AS template_c_id, COUNT(*) AS heat
FROM t_ext_click_log
WHERE C_CLICK_TYPE_CODE IN ('VIEW_INSIGHT', 'NEW_INSIGHT', 'VIEW_COMBINED_QUERY', 'NEW_COMBINED_QUERY')
GROUP BY C_RES_ID
"""

# ============== 模板数据清洗 SQL ==============
TEMPLATE_CLEAN_SQL = {
    "insight_no_chinese": """
DELETE FROM T_EXT_INSIGHT
WHERE c_name NOT REGEXP '[\\u4e00-\\u9fa5]'
  AND (c_alias IS NULL OR c_alias NOT REGEXP '[\\u4e00-\\u9fa5]')
""",
    "combinedquery_no_chinese": """
DELETE FROM T_EXT_COMBINEDQUERY
WHERE c_name NOT REGEXP '[\\u4e00-\\u9fa5]'
  AND (c_alias IS NULL OR c_alias NOT REGEXP '[\\u4e00-\\u9fa5]')
""",
    "insight_timestamp": """
DELETE FROM T_EXT_INSIGHT
WHERE c_name REGEXP '_I0481[a-f0-9]+$'
   OR c_alias REGEXP '_[0-9]{{14}}$'
""",
    "combinedquery_timestamp": """
DELETE FROM T_EXT_COMBINEDQUERY
WHERE c_name REGEXP '_I0481[a-f0-9]+$'
   OR c_alias REGEXP '_[0-9]{{14}}$'
""",
}

# ============== 模板数据抽取 SQL ==============
TEMPLATE_EXTRACT_SQL = {
    "insight": """
SELECT id, c_id, c_name, c_alias, c_desc, c_content
FROM T_EXT_INSIGHT
WHERE c_content IS NOT NULL AND c_content != ''
""",
    "combinedquery": """
SELECT id, c_id, c_name, c_alias, c_desc, c_content
FROM T_EXT_COMBINEDQUERY
WHERE c_content IS NOT NULL AND c_content != ''
""",
}

# ============== 默认删除的板块 ==============
DEFAULT_DELETE_SECTORS = ["临时板块", "系统维护"]
