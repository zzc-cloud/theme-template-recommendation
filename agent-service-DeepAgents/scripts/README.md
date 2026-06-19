# Theme Template Recommendation 本体层构建脚本

本目录包含 Theme Template Recommendation 项目的本体层（Neo4j 知识图谱）构建脚本。

## 📁 目录结构

```
scripts/
├── config.py              # 配置文件（MySQL、Neo4j 连接等）
├── extract_indicators.py  # 魔数师指标层抽取
├── extract_templates.py   # 模板层抽取（INSIGHT / COMBINEDQUERY）
├── build_hierarchy.py     # 层级结构构建
├── neo4j_loader.py        # Neo4j 数据加载器
├── init_ontology.py       # 全量初始化脚本
├── update_ontology.py     # 增量更新脚本
├── healthcheck.py         # 一键健康检查脚本（10项检查）
├── .env.example           # 环境变量模板
└── README.md             # 本文件
```

## 🔧 配置

### 1. 环境变量

```bash
# 复制模板
cp .env.example .env

# 编辑配置
vim .env
```

### 2. 必需配置

| 变量 | 说明 | 示例 |
|------|------|------|
| `MYSQL_HOST` | MySQL 主机 | `localhost` |
| `MYSQL_PORT` | MySQL 端口 | `3306` |
| `MYSQL_USER` | MySQL 用户 | `root` |
| `MYSQL_PASSWORD` | MySQL 密码 | `your_password` |
| `MYSQL_DATABASE` | 数据库名 | `chatbi_metadata` |
| `NEO4J_URI` | Neo4j URI | `bolt://localhost:7687` |
| `NEO4J_USER` | Neo4j 用户 | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j 密码 | `your_password` |

## 🚀 使用方式

### 首次初始化（全量导入）

```bash
cd agent-service/scripts
python init_ontology.py
```

执行流程：
1. 测试数据库连接
2. 抽取魔数师指标层数据（~17万条）
3. 构建层级结构
4. 抽取模板数据（INSIGHT + COMBINEDQUERY）
5. 计算模板热度
6. 创建 Neo4j 约束和索引
7. 导入指标层节点和关系
8. 导入模板层节点和关系
9. 清理临时板块

### 增量更新（每月执行）

```bash
# 自动读取上次更新时间
python update_ontology.py

# 指定更新时间
python update_ontology.py --last-update "2024-01-01 00:00:00"

# 强制全量更新
python update_ontology.py --full
```

### 自动化调度（crontab）

```bash
# 编辑 crontab
crontab -e

# 添加每月 1 日凌晨执行
0 2 1 * * cd /path/to/agent-service/scripts && /usr/bin/python3 update_ontology.py >> logs/update.log 2>&1
```

## 🔍 健康检查

### 一键健康检查

```bash
# 在 Docker 容器内执行
docker exec theme-template-agent python scripts/healthcheck.py

# 单项检查
docker exec theme-template-agent python scripts/healthcheck.py --only neo4j
docker exec theme-template-agent python scripts/healthcheck.py --only chroma_data
```

### 检查项说明

| Key | 说明 | 致命 |
|-----|------|------|
| `env` | 8 个环境变量完整性 | ✅ |
| `embedding` | Embedding 返回维度=1024 | ✅ |
| `llm` | LLM 调用可用性 | ✅ |
| `neo4j` | Neo4j 连接 | ✅ |
| `neo4j_data` | THEME/INDICATOR 节点数 >0 | ✅ |
| `chroma` | CHROMA_PATH + chroma.sqlite3 存在 | ✅ |
| `chroma_data` | collection.count() > 0 | ✅ |
| `vector` | 语义搜索返回结果 | ✅ |
| `http` | /health 接口状态 | ⚠️非致命 |
| `memory` | /health/memory 接口状态 | ⚠️非致命 |

> **CI/CD 集成**：退出码 0=全部通过，1=有致命失败，可直接用于 CI/CD：
> ```bash
> docker exec theme-template-agent python scripts/healthcheck.py && echo "验证通过"
> ```

## 📊 本体层结构

### 节点类型

| 标签 | 说明 | 来源 |
|------|------|------|
| `SECTOR` | 板块 | t_restree |
| `CATEGORY` | 分类 | t_restree |
| `THEME` | 主题（核心） | t_restree |
| `SUBPATH` | 子路径 | t_restree |
| `INDICATOR` | 指标（核心） | t_restree |
| `INSIGHT_TEMPLATE` | 透视分析模板 | T_EXT_INSIGHT |
| `COMBINEDQUERY_TEMPLATE` | 万能查询模板 | T_EXT_COMBINEDQUERY |

### 关系类型

| 关系 | 起点 | 终点 | 说明 |
|------|------|------|------|
| `HAS_CHILD` | 父节点 | 子节点 | 层级导航（树形结构） |
| `CONTAINS` | TEMPLATE | INDICATOR | 模板包含指标（带 position 属性） |

### 层级结构

```
自主分析 (根)
├── SECTOR (板块)
│   └── CATEGORY (分类)
│       └── THEME (主题) ← 核心节点
│           ├── SUBPATH (子路径)
│           │   └── INDICATOR (指标) ← 核心节点
│           └── INSIGHT_TEMPLATE (透视分析模板)
│               └── CONTAINS → INDICATOR
└── ...
```

## 🔍 验证

### Neo4j Browser 查询

```cypher
// 查看所有节点类型统计
MATCH (n)
RETURN labels(n)[0] as type, count(n) as count
ORDER BY count DESC;

// 查看模板层
MATCH (t:INSIGHT_TEMPLATE)
WHERE t.heat > 0
RETURN t.alias, t.heat, t.theme_id
ORDER BY t.heat DESC
LIMIT 10;

// 查看主题路径
MATCH path = (entry)-[:HAS_CHILD*]->(theme:THEME)
WHERE entry.alias = '自主分析'
RETURN path
LIMIT 25;
```

## 🛠️ 故障排除

### 连接失败

1. 检查 MySQL 是否启动：`mysql -u root -p`
2. 检查 Neo4j 是否启动：访问 http://localhost:7474
3. 检查 `.env` 配置是否正确

### 数据不一致

```bash
# 重新全量初始化
python init_ontology.py
```

### 性能问题

- Neo4j 建议配置：堆内存 4GB+
- 大数据量导入时使用 `--non-interactive` 模式

## 📝 更新日志

- **v1.0** - 初始版本，支持指标层和模板层全量/增量导入
