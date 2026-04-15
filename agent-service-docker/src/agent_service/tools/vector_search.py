
import json
import logging
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .. import config

logger = logging.getLogger(__name__)

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


_chroma_collection = None


def _get_chroma_collection():
    global _chroma_collection
    if _chroma_collection is not None:
        return _chroma_collection

    import chromadb
    import os

    os.makedirs(config.CHROMA_PATH, exist_ok=True)

    client = chromadb.PersistentClient(path=config.CHROMA_PATH)

    try:
        _chroma_collection = client.get_collection(name=config.COLLECTION_NAME)
    except Exception:
        _chroma_collection = client.create_collection(
            name=config.COLLECTION_NAME,
            metadata={"description": "魔数师指标向量库"},
        )
    return _chroma_collection


def get_embedding(text: str) -> list[float]:
    return get_embedding_batch([text])[0]


def get_embedding_batch(texts: list[str], batch_size: int = 32) -> list[list[float]]:
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


def search_indicators_by_vector(query: str, top_k: int = 20) -> dict:
    start_time = time.time()

    try:
        top_k = min(max(1, top_k), 100)

        query_vector = get_embedding(query)

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
