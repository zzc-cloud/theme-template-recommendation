"""
向量搜索工具
复用 theme_vector_server.py 中的 Chroma + SiliconFlow 逻辑
"""

import json
import logging
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .. import config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# HTTP Session（带自动重试）
# ─────────────────────────────────────────────
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
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

    os_path = __import__("os")
    os_path.makedirs(config.CHROMA_PATH, exist_ok=True)

    client = chromadb.PersistentClient(path=config.CHROMA_PATH)
    _chroma_collection = client.get_or_create_collection(
        name=config.COLLECTION_NAME,
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
    if not config.SILICONFLOW_EMBEDDING_API_KEY:
        raise EnvironmentError(
            "未设置 SILICONFLOW_EMBEDDING_API_KEY，请检查环境变量配置"
        )

    headers = {
        "Authorization": f"Bearer {config.SILICONFLOW_EMBEDDING_API_KEY}",
        "Content-Type": "application/json",
    }

    all_embeddings: list[list[float]] = []
    session = _get_session()

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        payload = {
            "model": config.EMBEDDING_MODEL,
            "input": batch,
            "dimensions": config.EMBEDDING_DIM,
        }

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = session.post(
                    config.SILICONFLOW_EMBEDDING_URL,
                    headers=headers,
                    json=payload,
                    timeout=60,
                )
                resp.raise_for_status()
                last_exc = None
                break
            except requests.RequestException as e:
                last_exc = e
                time.sleep(2**attempt)

        if last_exc:
            raise RuntimeError(
                f"Embedding API 请求失败，已重试 3 次: {last_exc}"
            ) from last_exc

        batch_embeddings = [item["embedding"] for item in resp.json()["data"]]
        all_embeddings.extend(batch_embeddings)
        time.sleep(0.05)

    return all_embeddings


# ─────────────────────────────────────────────
# 向量搜索 API
# ─────────────────────────────────────────────

def search_indicators_by_vector(query: str, top_k: int = 20) -> dict:
    """
    基于向量化语义匹配搜索魔数师指标

    Args:
        query: 搜索查询
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
            return {
                "success": False,
                "error": "向量库为空，请先运行 indicator_vectorizer.py --rebuild",
                "query": query,
                "execution_time_ms": round((time.time() - start_time) * 1000, 2),
            }

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
        return {
            "success": True,
            "query": query,
            "indicator_count": len(indicators),
            "indicators": indicators,
            "execution_time_ms": round(elapsed, 2),
        }

    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        logger.exception(f"向量搜索失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "query": query,
            "execution_time_ms": round(elapsed, 2),
        }


def get_vector_stats() -> dict:
    """获取向量库统计信息"""
    try:
        collection = _get_chroma_collection()
        return {
            "success": True,
            "total_indicators": collection.count(),
            "storage_path": config.CHROMA_PATH,
            "collection_name": config.COLLECTION_NAME,
            "embedding_model": config.EMBEDDING_MODEL,
            "embedding_dim": config.EMBEDDING_DIM,
        }
    except Exception as e:
        logger.exception(f"获取向量统计失败: {e}")
        return {"success": False, "error": str(e)}
