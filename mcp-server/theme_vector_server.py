#!/usr/bin/env python3
"""
Theme Template Recommendation - 向量搜索 MCP 服务器

基于向量化语义匹配搜索魔数师指标：
- 使用 Chroma 作为向量数据库
- 使用 SiliconFlow API 生成向量

工具列表：
- search_indicators_by_vector : 基于向量化语义匹配搜索指标
- vector_search_stats         : 获取向量库统计信息
- add_indicator_vector        : 添加单个指标向量
- delete_indicator_vector     : 删除单个指标向量
"""

import os
import sys
import json
import time
import requests
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

# ─────────────────────────────────────────────
# 加载环境变量
# ─────────────────────────────────────────────
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

from mcp.server.fastmcp import FastMCP

# ─────────────────────────────────────────────
# MCP 服务器实例
# ─────────────────────────────────────────────
mcp = FastMCP("theme-vector")

# ─────────────────────────────────────────────
# SiliconFlow API 配置
# ─────────────────────────────────────────────
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY")
SILICONFLOW_URL     = "https://api.siliconflow.cn/v1/embeddings"
EMBEDDING_MODEL     = "Qwen/Qwen3-Embedding-8B"   # ✅ 升级为 Qwen3
EMBEDDING_DIM       = 1024                         # 可选: 64/128/256/512/768/1024/1536/2048/2560/4096

# ─────────────────────────────────────────────
# Chroma 配置
# ─────────────────────────────────────────────
CHROMA_PATH     = os.path.join(os.path.dirname(__file__), "data/indicators_vector")
COLLECTION_NAME = "indicators"

# 全局 Chroma collection（延迟初始化）
_collection = None
_chroma_client = None


# ══════════════════════════════════════════════
# HTTP Session（带自动重试）
# ══════════════════════════════════════════════
def _build_session() -> requests.Session:
    """创建带重试策略的 requests Session"""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


_SESSION = _build_session()


# ══════════════════════════════════════════════
# Embedding API
# ══════════════════════════════════════════════
def get_embedding(text: str) -> list[float]:
    """获取单条文本向量"""
    return get_embedding_batch([text])[0]


def get_embedding_batch(
    texts: list[str],
    batch_size: int = 32,
) -> list[list[float]]:
    """
    批量获取文本向量，支持：
    - 自动分批（每批最多 32 条）
    - 失败自动重试（最多 3 次，指数退避）
    """
    if not SILICONFLOW_API_KEY:
        raise EnvironmentError(
            "未设置 SILICONFLOW_API_KEY，请在 .env 文件中配置:\n"
            "SILICONFLOW_API_KEY=sk-xxxxxxxx"
        )

    headers = {
        "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
        "Content-Type": "application/json",
    }

    all_embeddings: list[list[float]] = []

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        payload = {
            "model": EMBEDDING_MODEL,
            "input": batch,
            "dimensions": EMBEDDING_DIM,   # ✅ Qwen3 支持自定义维度
        }

        # 手动重试（指数退避）
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = _SESSION.post(
                    SILICONFLOW_URL,
                    headers=headers,
                    json=payload,
                    timeout=60,
                )
                resp.raise_for_status()
                last_exc = None
                break
            except requests.RequestException as e:
                last_exc = e
                wait = 2 ** attempt
                time.sleep(wait)

        if last_exc:
            raise RuntimeError(
                f"Embedding API 请求失败，已重试 3 次: {last_exc}"
            ) from last_exc

        batch_embeddings = [item["embedding"] for item in resp.json()["data"]]
        all_embeddings.extend(batch_embeddings)
        time.sleep(0.05)  # 避免触发限流

    return all_embeddings


# ══════════════════════════════════════════════
# Chroma 工具
# ══════════════════════════════════════════════
def get_collection():
    """获取 Chroma collection（延迟初始化，全局单例）"""
    global _collection, _chroma_client

    if _collection is not None:
        return _collection

    try:
        import chromadb
    except ImportError:
        raise ImportError("请先安装 chromadb: pip install chromadb")

    os.makedirs(CHROMA_PATH, exist_ok=True)
    _chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)

    # ✅ 修复：统一用 get_or_create，避免 collection 不存在时抛异常
    _collection = _chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "魔数师指标向量库"},
    )

    return _collection


def _reset_collection_cache() -> None:
    """清除全局 collection 缓存（collection 被重建后调用）"""
    global _collection
    _collection = None


# ══════════════════════════════════════════════
# MCP 工具：向量搜索
# ══════════════════════════════════════════════
@mcp.tool(annotations={"readOnlyHint": True})
def search_indicators_by_vector(query: str, top_k: int = 20) -> str:
    """基于向量化语义匹配搜索魔数师指标

    结合关键词和语义匹配，返回与查询最相似的指标列表。

    Args:
        query: 搜索查询（可以是关键词、短语或完整句子）
        top_k: 返回结果数量，默认 20，最大 100

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
        top_k = min(max(1, top_k), 100)

        # 获取查询向量
        query_vector = get_embedding(query)

        # 向量搜索
        collection = get_collection()

        # ✅ 修复：n_results 不能超过 collection 实际数量
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

        # 构建输出
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
                    "id":               indicator_id,
                    "alias":            metadata.get("alias",       ""),
                    "description":      metadata.get("description", ""),
                    "theme_id":         metadata.get("theme_id",    ""),
                    "theme_alias":      metadata.get("theme_alias", ""),
                    "similarity_score": round(similarity, 4),
                })

        elapsed = (time.time() - start_time) * 1000
        return json.dumps({
            "success":         True,
            "query":           query,
            "indicator_count": len(indicators),
            "indicators":      indicators,
            "execution_time_ms": round(elapsed, 2),
        }, ensure_ascii=False)

    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        return json.dumps({
            "success": False,
            "error":   str(e),
            "query":   query,
            "execution_time_ms": round(elapsed, 2),
        }, ensure_ascii=False)


# ══════════════════════════════════════════════
# MCP 工具：统计信息
# ══════════════════════════════════════════════
@mcp.tool(annotations={"readOnlyHint": True})
def vector_search_stats() -> str:
    """获取向量搜索统计信息

    返回向量库的统计信息，包括指标总数、模型信息等。

    返回格式:
        {
            "success": true,
            "total_indicators": 5000,
            "storage_path": "/path/to/chroma",
            "collection_name": "indicators",
            "embedding_model": "Qwen/Qwen3-Embedding-8B",
            "embedding_dim": 1024,
            "execution_time_ms": 12.3
        }
    """
    start_time = time.time()

    try:
        collection = get_collection()
        count      = collection.count()
        elapsed    = (time.time() - start_time) * 1000

        return json.dumps({
            "success":          True,
            "total_indicators": count,
            "storage_path":     CHROMA_PATH,
            "collection_name":  COLLECTION_NAME,
            "embedding_model":  EMBEDDING_MODEL,
            "embedding_dim":    EMBEDDING_DIM,       # ✅ 新增维度信息
            "execution_time_ms": round(elapsed, 2),
        }, ensure_ascii=False)

    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        return json.dumps({
            "success": False,
            "error":   str(e),
            "execution_time_ms": round(elapsed, 2),
        }, ensure_ascii=False)


# ══════════════════════════════════════════════
# MCP 工具：添加单个指标向量
# ══════════════════════════════════════════════
@mcp.tool()
def add_indicator_vector(
    indicator_id: str,
    alias:        str,
    description:  str,
    theme_id:     str = "",
    theme_alias:  str = "",
) -> str:
    """添加单个指标的向量到向量库

    若 indicator_id 已存在则执行更新（upsert）。

    Args:
        indicator_id: 指标 ID
        alias:        指标别名
        description:  指标描述
        theme_id:     主题 ID（可选）
        theme_alias:  主题别名（可选）

    返回格式:
        {
            "success": true,
            "indicator_id": "xxx",
            "message": "添加成功"
        }
    """
    start_time = time.time()

    try:
        text   = f"{alias} {description}".strip()
        vector = get_embedding(text)

        collection = get_collection()

        # ✅ 修复：使用 upsert 代替 add，避免重复 ID 报错
        collection.upsert(
            ids        = [indicator_id],
            embeddings = [vector],
            metadatas  = [{
                "alias":       alias,
                "description": description,
                "theme_id":    theme_id,
                "theme_alias": theme_alias,
            }],
            documents  = [text],
        )

        elapsed = (time.time() - start_time) * 1000
        return json.dumps({
            "success":      True,
            "indicator_id": indicator_id,
            "message":      "添加/更新成功",
            "execution_time_ms": round(elapsed, 2),
        }, ensure_ascii=False)

    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        return json.dumps({
            "success":      False,
            "error":        str(e),
            "indicator_id": indicator_id,
            "execution_time_ms": round(elapsed, 2),
        }, ensure_ascii=False)


# ══════════════════════════════════════════════
# MCP 工具：删除单个指标向量
# ══════════════════════════════════════════════
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

        # ✅ 修复：删除前检查 ID 是否存在，避免静默失败
        existing = collection.get(ids=[indicator_id])
        if not existing["ids"]:
            elapsed = (time.time() - start_time) * 1000
            return json.dumps({
                "success":      False,
                "error":        f"指标 ID 不存在: {indicator_id}",
                "indicator_id": indicator_id,
                "execution_time_ms": round(elapsed, 2),
            }, ensure_ascii=False)

        collection.delete(ids=[indicator_id])

        elapsed = (time.time() - start_time) * 1000
        return json.dumps({
            "success":      True,
            "indicator_id": indicator_id,
            "message":      "删除成功",
            "execution_time_ms": round(elapsed, 2),
        }, ensure_ascii=False)

    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        return json.dumps({
            "success":      False,
            "error":        str(e),
            "indicator_id": indicator_id,
            "execution_time_ms": round(elapsed, 2),
        }, ensure_ascii=False)


# ══════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════
if __name__ == "__main__":
    mcp.run()
