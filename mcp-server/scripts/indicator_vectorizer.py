#!/usr/bin/env python3
"""
魔数师指标向量化脚本

功能：
1. 从 Neo4j 获取所有 INDICATOR 节点
2. 对每条指标的 alias + description 生成向量
3. 存储向量到 Chroma 数据库

使用方法：
    python indicator_vectorizer.py --rebuild  # 重建向量索引
    python indicator_vectorizer.py --update   # 更新新增指标
    python indicator_vectorizer.py --stats    # 查看统计信息
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path

# 添加父目录到路径，以便导入 neo4j
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from neo4j import GraphDatabase
from dotenv import load_dotenv
import chromadb
from chromadb.config import Settings

# 加载环境变量
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

# SiliconFlow API 配置
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY")
SILICONFLOW_URL = "https://api.siliconflow.cn/v1/embeddings"
EMBEDDING_MODEL = "BAAI/bge-m3"

# Neo4j 配置
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

# Chroma 配置
CHROMA_PATH = os.path.join(os.path.dirname(__file__), "../data/indicators_vector")
COLLECTION_NAME = "indicators"


def get_embedding(text: str) -> list[float]:
    """通过 SiliconFlow API 获取文本向量"""
    headers = {
        "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": EMBEDDING_MODEL,
        "input": text
    }
    response = requests.post(SILICONFLOW_URL, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def get_embedding_batch(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """通过 SiliconFlow API 批量获取文本向量"""
    headers = {
        "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
        "Content-Type": "application/json"
    }

    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        payload = {
            "model": EMBEDDING_MODEL,
            "input": batch
        }
        response = requests.post(SILICONFLOW_URL, headers=headers, json=payload)
        response.raise_for_status()
        batch_embeddings = [item["embedding"] for item in response.json()["data"]]
        all_embeddings.extend(batch_embeddings)
        print(f"  已处理 {min(i + batch_size, len(texts))}/{len(texts)} 条文本")
        time.sleep(0.1)  # 避免 API 限流

    return all_embeddings


def get_all_indicators() -> list[dict]:
    """从 Neo4j 获取所有 INDICATOR 节点"""
    print(f"正在连接 Neo4j: {NEO4J_URI}")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        with driver.session() as session:
            cypher = """
            MATCH (i:INDICATOR)
            OPTIONAL MATCH (i)-[:BELONGS_TO]->(t:THEME)
            RETURN i.id as id, i.alias as alias,
                   i.description as description,
                   t.id as theme_id, t.alias as theme_alias
            """
            results = session.run(cypher)
            indicators = [dict(record) for record in results]
            print(f"获取到 {len(indicators)} 个指标")
            return indicators
    finally:
        driver.close()


def init_chroma_collection() -> chromadb.Collection:
    """初始化 Chroma collection"""
    os.makedirs(CHROMA_PATH, exist_ok=True)
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "魔数师指标向量库"}
    )
    return collection


def vectorize_and_store(rebuild: bool = False):
    """对所有指标进行向量化并存储到 Chroma"""
    print("=" * 50)
    print("魔数师指标向量化脚本")
    print("=" * 50)

    # 检查 API Key
    if not SILICONFLOW_API_KEY:
        print("错误: 未设置 SILICONFLOW_API_KEY 环境变量")
        print("请在 .env 文件中配置: SILICONFLOW_API_KEY=your_api_key")
        return

    # 获取所有指标
    print("\n[1/3] 获取指标数据...")
    indicators = get_all_indicators()
    if not indicators:
        print("错误: 未找到任何指标")
        return

    # 初始化 Chroma
    print("\n[2/3] 初始化 Chroma...")
    collection = init_chroma_collection()

    if rebuild:
        print("  重建模式：清空旧数据")
        collection.delete(where={})
    else:
        print("  更新模式：仅添加新数据")

    # 批量向量化
    print(f"\n[3/3] 生成向量并存储到 Chroma...")

    # 准备批量数据
    texts = []
    valid_indicators = []

    for indicator in indicators:
        # 构造文本（指标别名 + 描述）
        alias = indicator.get("alias") or ""
        description = indicator.get("description") or ""
        text = f"{alias} {description}".strip()

        if text:
            texts.append(text)
            valid_indicators.append(indicator)

    print(f"  有效指标数: {len(valid_indicators)}/{len(indicators)}")

    # 批量获取向量
    print("  开始生成向量（批量）...")
    batch_size = 32
    embeddings = get_embedding_batch(texts, batch_size=batch_size)

    # 批量添加到 Chroma
    print("  添加到 Chroma...")
    ids = []
    embeddings_list = []
    metadatas = []
    documents = []

    for i, indicator in enumerate(valid_indicators):
        ids.append(indicator["id"])
        embeddings_list.append(embeddings[i])
        metadatas.append({
            "alias": indicator.get("alias", ""),
            "description": indicator.get("description", ""),
            "theme_id": indicator.get("theme_id") or "",
            "theme_alias": indicator.get("theme_alias") or ""
        })
        documents.append(texts[i])

    collection.add(
        ids=ids,
        embeddings=embeddings_list,
        metadatas=metadatas,
        documents=documents
    )

    print("\n" + "=" * 50)
    print(f"完成！共向量化 {len(valid_indicators)} 个指标")
    print(f"向量数据存储路径: {CHROMA_PATH}")
    print("=" * 50)


def show_stats():
    """显示向量库统计信息"""
    print("=" * 50)
    print("向量库统计信息")
    print("=" * 50)

    try:
        collection = init_chroma_collection()
        count = collection.count()

        print(f"\n指标总数: {count}")
        print(f"存储路径: {CHROMA_PATH}")
        print(f"Collection 名称: {COLLECTION_NAME}")

        # 获取样本数据
        if count > 0:
            sample = collection.get(limit=3)
            print(f"\n样本数据 (前 3 条):")
            for i, metadata in enumerate(sample["metadatas"]):
                print(f"  {i + 1}. {metadata.get('alias', 'N/A')}")
                print(f"     Theme: {metadata.get('theme_alias', 'N/A')}")

    except Exception as e:
        print(f"错误: {e}")
        print("提示: 请先运行 --rebuild 生成向量数据")

    print("=" * 50)


def search_demo(query: str, top_k: int = 5):
    """演示向量搜索"""
    print("=" * 50)
    print(f"向量搜索演示: '{query}'")
    print("=" * 50)

    try:
        # 获取查询向量
        print("  生成查询向量...")
        query_vector = get_embedding(query)

        # 搜索
        print("  执行搜索...")
        collection = init_chroma_collection()
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=top_k
        )

        # 输出结果
        print(f"\n搜索结果 (Top {top_k}):")
        for i, (doc, metadata, distance) in enumerate(zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0]
        )):
            similarity = 1 - distance  # 转换距离为相似度
            print(f"\n  {i + 1}. {metadata.get('alias', 'N/A')}")
            print(f"     相似度: {similarity:.2%}")
            print(f"     Theme: {metadata.get('theme_alias', 'N/A')}")
            print(f"     描述: {metadata.get('description', 'N/A')[:50]}...")

    except Exception as e:
        print(f"错误: {e}")
        print("提示: 请先运行 --rebuild 生成向量数据")

    print("=" * 50)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="魔数师指标向量化脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --rebuild          # 重建向量索引
  %(prog)s --stats            # 查看统计信息
  %(prog)s --search "风险"    # 搜索演示
        """
    )
    parser.add_argument("--rebuild", action="store_true", help="重建向量索引")
    parser.add_argument("--stats", action="store_true", help="查看统计信息")
    parser.add_argument("--search", type=str, metavar="QUERY", help="搜索演示")

    args = parser.parse_args()

    if args.stats:
        show_stats()
    elif args.search:
        search_demo(args.search)
    elif args.rebuild:
        vectorize_and_store(rebuild=True)
    else:
        parser.print_help()
        print("\n提示: 使用 --rebuild 首次生成向量数据")
