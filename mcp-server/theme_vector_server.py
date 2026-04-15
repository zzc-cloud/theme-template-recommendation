#!/usr/bin/env python3
"""
Theme Template Recommendation - 向量搜索 MCP 服务器

提供基于 Chroma + SiliconFlow 的语义向量搜索能力：
- search_indicators_by_vector: 向量化语义搜索魔数师指标

对应 Skill 阶段 0：需求澄清中的向量搜索环节。
"""

from mcp.server.fastmcp import FastMCP
import json
import time
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

# 创建 MCP 服务器实例
mcp = FastMCP("theme-vector")

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "")
SILICONFLOW_EMBEDDING_URL = os.getenv(
    "SILICONFLOW_EMBEDDING_URL",
    "https://api.siliconflow.cn/v1/embeddings",
)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "/")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "4096"))

# Chroma 配置（默认使用 mcp-server/data/indicators_vector）
_DEFAULT_CHROMA_PATH = str(Path(__file__).parent / "data" / "indicators_vector")
CHROMA_PATH = os.getenv("CHROMA_PATH", _DEFAULT_CHROMA_PATH)
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "indicators")

# HTTP Session（带自动重试）
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_session = None


def _get_session():
    global _session
    if _session is None:
        _session = __import__("requests").Session()
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
        )
        _session.mount("https://", HTTPAdapter(max_retries=retry))
    return _session


# ─────────────────────────────────────────────
# Chroma 客户端（延迟初始化）
# ─────────────────────────────────────────────
_chroma_collection = None


def _get_chroma_collection():
    global _chroma_collection
    if _chroma_collection is not None:
        return _chroma_collection

    import chromadb
    import os

    os.makedirs(CHROMA_PATH, exist_ok=True)
    client = chromadb.PersistentClient(path=CHROMA_PATH)

    try:
        _chroma_collection = client.get_collection(name=COLLECTION_NAME)
    except Exception:
        _chroma_collection = client.create_collection(
            name=COLLECTION_NAME,
            metadata={"description": "魔数师指标向量库"},
        )
    return _chroma_collection


# ─────────────────────────────────────────────
# Embedding API
# ─────────────────────────────────────────────

def get_embedding(text: str) -> list[float]:
    """获取单条文本向量"""
    return get_embedding_batch([text])[0]


def get_embedding_batch(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """批量获取文本向量"""
    if not SILICONFLOW_API_KEY:
        raise EnvironmentError("未设置 SILICONFLOW_API_KEY，请检查环境变量配置")

    headers = {
        "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
        "Content-Type": "application/json",
    }

    all_embeddings: list[list[float]] = []
    session = _get_session()

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        payload = {
            "model": EMBEDDING_MODEL,
            "input": batch,
        }

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = session.post(
                    SILICONFLOW_EMBEDDING_URL,
                    headers=headers,
                    json=payload,
                    timeout=60,
                )
                resp.raise_for_status()
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                time.sleep(2 ** attempt)

        if last_exc:
            raise RuntimeError(f"Embedding API 请求失败，已重试 3 次: {last_exc}") from last_exc

        batch_embeddings = [item["embedding"] for item in resp.json()["data"]]
        all_embeddings.extend(batch_embeddings)
        time.sleep(0.05)

    return all_embeddings


# ─────────────────────────────────────────────
# MCP 工具
# ─────────────────────────────────────────────

@mcp.tool(annotations={"readOnlyHint": True})
def search_indicators_by_vector(query: str, top_k: int = 20) -> str:
    """
    基于向量化语义匹配搜索魔数师指标

    用于主题模板推荐 Skill 的阶段 0 需求澄清，通过向量搜索将用户分析概念映射到魔数师指标。

    Args:
        query: 搜索查询（分析概念词或用户问题片段）
        top_k: 返回结果数量，默认 20，最大 100

    Returns:
        {
            "success": true,
            "query": "...",
            "indicator_count": 20,
            "indicators": [
                {
                    "id": "INDICATOR.xxx",
                    "alias": "指标别名",
                    "description": "指标描述",
                    "theme_id": "THEME.xxx",
                    "theme_alias": "主题别名",
                    "similarity_score": 0.92
                }
            ],
            "execution_time_ms": 125.5
        }
    """
    start_time = time.time()

    try:
        top_k = min(max(1, top_k), 100)

        # 获取查询向量
        query_vector = get_embedding(query)

        # 向量搜索
        collection = _get_chroma_collection()
        actual_top_k = min(top_k, collection.count())

        if actual_top_k == 0:
            return json.dumps({
                "success": False,
                "error": "向量库为空，请先运行 indicator_vectorizer.py --rebuild",
                "query": query,
                "execution_time_ms": round((time.time() - start_time) * 1000, 2),
            }, ensure_ascii=False)

        results = collection.query(
            query_embeddings=[query_vector],
            n_results=actual_top_k,
            include=["metadatas", "documents", "distances"],
        )

        indicators = []
        if results["ids"] and results["ids"][0]:
            for indicator_id, metadata, document, distance in zip(
                results["ids"][0],
                results["metadatas"][0],
                results["documents"][0],
                results["distances"][0],
            ):
                # 余弦距离 → 相似度
                similarity = max(0.0, 1.0 - distance)
                indicators.append({
                    "id": indicator_id,
                    "alias": metadata.get("alias", ""),
                    "description": metadata.get("description", ""),
                    "theme_id": metadata.get("theme_id", ""),
                    "theme_alias": metadata.get("theme_alias", ""),
                    "similarity_score": round(similarity, 4),
                })

        elapsed = (time.time() - start_time) * 1000
        return json.dumps({
            "success": True,
            "query": query,
            "indicator_count": len(indicators),
            "indicators": indicators,
            "execution_time_ms": round(elapsed, 2),
        }, ensure_ascii=False)

    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        return json.dumps({
            "success": False,
            "error": str(e),
            "query": query,
            "execution_time_ms": round(elapsed, 2),
        }, ensure_ascii=False)


@mcp.tool(annotations={"readOnlyHint": True})
def get_vector_stats() -> str:
    """
    获取向量库统计信息

    Returns:
        {
            "success": true,
            "total_indicators": 1500,
            "storage_path": "...",
            "collection_name": "indicators",
            "embedding_model": "/-8B",
            "embedding_dim": 4096
        }
    """
    try:
        collection = _get_chroma_collection()
        return json.dumps({
            "success": True,
            "total_indicators": collection.count(),
            "storage_path": CHROMA_PATH,
            "collection_name": COLLECTION_NAME,
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dim": EMBEDDING_DIM,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


# ─────────────────────────────────────────────
# 启动服务器
# ─────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
