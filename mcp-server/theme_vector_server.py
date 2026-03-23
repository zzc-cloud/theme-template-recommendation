#!/usr/bin/env python3
"""
Theme Template Recommendation - 向量搜索 MCP 服务器

基于向量化语义匹配搜索魔数师指标：
- 使用 Chroma 作为向量数据库
- 使用 SiliconFlow API 生成向量

工具列表：
- search_indicators_by_vector: 基于向量化语义匹配搜索指标
"""

import os
import sys
import json
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

from mcp.server.fastmcp import FastMCP

# 创建 MCP 服务器实例
mcp = FastMCP("theme-vector")

# SiliconFlow API 配置
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY")
SILICONFLOW_URL = "https://api.siliconflow.cn/v1/embeddings"
EMBEDDING_MODEL = "BAAI/bge-m3"

# Chroma 配置
CHROMA_PATH = os.path.join(os.path.dirname(__file__), "data/indicators_vector")
COLLECTION_NAME = "indicators"

# 全局 Chroma collection（延迟初始化）
_collection = None


def get_embedding(text: str) -> list[float]:
    """通过 SiliconFlow API 获取文本向量"""
    if not SILICONFLOW_API_KEY:
        raise ValueError("未设置 SILICONFLOW_API_KEY 环境变量")

    headers = {
        "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": EMBEDDING_MODEL,
        "input": text
    }
    response = requests.post(SILICONFLOW_URL, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def get_embedding_batch(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """通过 SiliconFlow API 批量获取文本向量"""
    if not SILICONFLOW_API_KEY:
        raise ValueError("未设置 SILICONFLOW_API_KEY 环境变量")

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
        response = requests.post(SILICONFLOW_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        batch_embeddings = [item["embedding"] for item in response.json()["data"]]
        all_embeddings.extend(batch_embeddings)
        time.sleep(0.05)  # 避免 API 限流

    return all_embeddings


def get_collection():
    """获取 Chroma collection（延迟初始化）"""
    global _collection

    if _collection is not None:
        return _collection

    # 导入 chromadb（需要先安装）
    try:
        import chromadb
    except ImportError:
        raise ImportError("请先安装 chromadb: pip install chromadb")

    # 初始化 Chroma
    os.makedirs(CHROMA_PATH, exist_ok=True)
    client = chromadb.PersistentClient(path=CHROMA_PATH)

    try:
        _collection = client.get_collection(name=COLLECTION_NAME)
    except Exception:
        # Collection 不存在，创建一个新的
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"description": "魔数师指标向量库"}
        )

    return _collection


# ==================== 向量搜索工具 ====================

@mcp.tool(annotations={"readOnlyHint": True})
def search_indicators_by_vector(query: str, top_k: int = 20) -> str:
    """基于向量化语义匹配搜索魔数师指标

    结合关键词和语义匹配，返回与查询最相似的指标列表。

    Args:
        query: 搜索查询（可以是关键词、短语或完整句子）
        top_k: 返回结果数量，默认20，最大100

    返回格式:
        {
            "success": true,
            "query": "风险",
            "indicator_count": 20,
            "indicators": [
                {
                    "id": "BIZATTR.DS_SSA_RISK.对公贷款借据.five_cls_cd",
                    "alias": "五级分类",
                    "description": "贷款风险分类代码",
                    "theme_id": "THEME.DS_SSA_RISK.对公贷款借据",
                    "theme_alias": "对公贷款借据",
                    "similarity_score": 0.92
                }
            ],
            "execution_time_ms": 125.5
        }
    """
    start_time = time.time()

    try:
        # 限制 top_k 范围
        top_k = min(max(1, top_k), 100)

        # 获取查询向量
        query_vector = get_embedding(query)

        # 搜索
        collection = get_collection()
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=top_k,
            include=["metadatas", "documents", "distances"]
        )

        # 转换距离为相似度并构建输出
        indicators = []
        if results["ids"] and results["ids"][0]:
            for i, (indicator_id, metadata, document, distance) in enumerate(zip(
                results["ids"][0],
                results["metadatas"][0],
                results["documents"][0],
                results["distances"][0]
            )):
                # 转换距离为相似度（余弦距离转相似度）
                similarity = max(0, 1 - distance)

                indicators.append({
                    "id": indicator_id,
                    "alias": metadata.get("alias", ""),
                    "description": metadata.get("description", ""),
                    "theme_id": metadata.get("theme_id", ""),
                    "theme_alias": metadata.get("theme_alias", ""),
                    "similarity_score": round(similarity, 4)
                })

        elapsed = (time.time() - start_time) * 1000

        return json.dumps({
            "success": True,
            "query": query,
            "indicator_count": len(indicators),
            "indicators": indicators,
            "execution_time_ms": round(elapsed, 2)
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        return json.dumps({
            "success": False,
            "error": str(e),
            "query": query,
            "execution_time_ms": round(elapsed, 2)
        }, ensure_ascii=False, indent=2)


@mcp.tool(annotations={"readOnlyHint": True})
def vector_search_stats() -> str:
    """获取向量搜索统计信息

    返回向量库的统计信息，包括指标总数等。

    返回格式:
        {
            "success": true,
            "total_indicators": 5000,
            "storage_path": "/path/to/chroma",
            "collection_name": "indicators"
        }
    """
    start_time = time.time()

    try:
        collection = get_collection()
        count = collection.count()

        elapsed = (time.time() - start_time) * 1000

        return json.dumps({
            "success": True,
            "total_indicators": count,
            "storage_path": CHROMA_PATH,
            "collection_name": COLLECTION_NAME,
            "embedding_model": EMBEDDING_MODEL,
            "execution_time_ms": round(elapsed, 2)
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        return json.dumps({
            "success": False,
            "error": str(e),
            "execution_time_ms": round(elapsed, 2)
        }, ensure_ascii=False, indent=2)


@mcp.tool()
def add_indicator_vector(
    indicator_id: str,
    alias: str,
    description: str,
    theme_id: str = "",
    theme_alias: str = ""
) -> str:
    """添加单个指标的向量到向量库

    Args:
        indicator_id: 指标 ID
        alias: 指标别名
        description: 指标描述
        theme_id: 主题 ID
        theme_alias: 主题别名

    返回格式:
        {
            "success": true,
            "indicator_id": "xxx",
            "message": "添加成功"
        }
    """
    start_time = time.time()

    try:
        # 构造文本
        text = f"{alias} {description}".strip()

        # 生成向量
        vector = get_embedding(text)

        # 添加到 Chroma
        collection = get_collection()
        collection.add(
            ids=[indicator_id],
            embeddings=[vector],
            metadatas=[{
                "alias": alias,
                "description": description,
                "theme_id": theme_id,
                "theme_alias": theme_alias
            }],
            documents=[text]
        )

        elapsed = (time.time() - start_time) * 1000

        return json.dumps({
            "success": True,
            "indicator_id": indicator_id,
            "message": "添加成功",
            "execution_time_ms": round(elapsed, 2)
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        return json.dumps({
            "success": False,
            "error": str(e),
            "indicator_id": indicator_id,
            "execution_time_ms": round(elapsed, 2)
        }, ensure_ascii=False, indent=2)


@mcp.tool()
def delete_indicator_vector(indicator_id: str) -> str:
    """从向量库删除单个指标

    Args:
        indicator_id: 指标 ID

    返回格式:
        {
            "success": true,
            "indicator_id": "xxx",
            "message": "删除成功"
        }
    """
    start_time = time.time()

    try:
        collection = get_collection()
        collection.delete(ids=[indicator_id])

        elapsed = (time.time() - start_time) * 1000

        return json.dumps({
            "success": True,
            "indicator_id": indicator_id,
            "message": "删除成功",
            "execution_time_ms": round(elapsed, 2)
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        return json.dumps({
            "success": False,
            "error": str(e),
            "indicator_id": indicator_id,
            "execution_time_ms": round(elapsed, 2)
        }, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    # 运行服务器
    mcp.run()
