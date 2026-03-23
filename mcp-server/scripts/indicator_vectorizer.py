#!/usr/bin/env python3
"""
魔数师指标向量化脚本

功能：
1. 从 Neo4j 获取所有 INDICATOR 节点
2. 对每条指标的 alias + description 生成向量（串行大批次）
3. 存储向量到 Chroma 数据库

使用方法：
    python indicator_vectorizer.py --rebuild  # 重建向量索引
    python indicator_vectorizer.py --update   # 更新新增指标
    python indicator_vectorizer.py --stats    # 查看统计信息
    python indicator_vectorizer.py --search "关键词"  # 搜索演示
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from neo4j import GraphDatabase
from dotenv import load_dotenv
import chromadb

# ─────────────────────────────────────────────
# 加载环境变量
# ─────────────────────────────────────────────
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

# ─────────────────────────────────────────────
# SiliconFlow API 配置
# ─────────────────────────────────────────────
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY")
SILICONFLOW_URL     = "https://api.siliconflow.cn/v1/embeddings"
EMBEDDING_MODEL     = "Qwen/Qwen3-Embedding-8B"
EMBEDDING_DIM       = 1024

# ─────────────────────────────────────────────
# Neo4j 配置
# ─────────────────────────────────────────────
NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

# ─────────────────────────────────────────────
# Chroma 配置
# ─────────────────────────────────────────────
CHROMA_PATH     = os.path.join(os.path.dirname(__file__), "../data/indicators_vector")
COLLECTION_NAME = "indicators"
PROGRESS_FILE   = os.path.join(CHROMA_PATH, "progress.json")

# ─────────────────────────────────────────────
# 性能参数
# ─────────────────────────────────────────────
API_BATCH_SIZE = 512   # 每次请求携带的文本条数（原来 32，提升 4 倍）
MAX_RETRIES    = 5     # 单个请求最大重试次数
CHROMA_BATCH   = 2000  # 每批写入 Chroma 的条数


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
    batch_size: int = API_BATCH_SIZE,
) -> list[list[float]]:
    """
    串行批量获取文本向量：
    - 每批携带更多文本（batch_size=128）减少请求次数
    - 移除批间延迟
    - 失败自动指数退避重试
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
    total_batches = (len(texts) + batch_size - 1) // batch_size

    for batch_idx, start in enumerate(range(0, len(texts), batch_size)):
        batch = texts[start : start + batch_size]
        payload = {
            "model": EMBEDDING_MODEL,
            "input": batch,
            "dimensions": EMBEDDING_DIM,
        }

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
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
                print(f"  ⚠️  重试 {attempt + 1}/{MAX_RETRIES}，{wait}s 后继续... ({e})")
                time.sleep(wait)

        if last_exc:
            raise RuntimeError(
                f"Embedding API 请求失败，已重试 {MAX_RETRIES} 次: {last_exc}"
            ) from last_exc

        batch_embeddings = [item["embedding"] for item in resp.json()["data"]]
        all_embeddings.extend(batch_embeddings)

        done = min(start + batch_size, len(texts))
        print(
            f"  📦 [{batch_idx + 1}/{total_batches}] "
            f"{done}/{len(texts)} 条  "
            f"({done / len(texts) * 100:.1f}%)"
        )

    return all_embeddings


# ══════════════════════════════════════════════
# Neo4j
# ══════════════════════════════════════════════
def get_all_indicators() -> list[dict]:
    """从 Neo4j 获取所有 INDICATOR 节点"""
    print(f"🔌 连接 Neo4j: {NEO4J_URI}")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            cypher = """
            MATCH (i:INDICATOR)
            OPTIONAL MATCH (i)-[:BELONGS_TO]->(t:THEME)
            RETURN
                i.id          AS id,
                i.alias       AS alias,
                i.description AS description,
                t.id          AS theme_id,
                t.alias       AS theme_alias
            """
            results = session.run(cypher)
            indicators = [dict(record) for record in results]
            print(f"✅ 获取到 {len(indicators)} 个指标")
            return indicators
    finally:
        driver.close()


# ══════════════════════════════════════════════
# Chroma 工具
# ══════════════════════════════════════════════
def get_chroma_client() -> chromadb.PersistentClient:
    """获取 Chroma 持久化客户端"""
    os.makedirs(CHROMA_PATH, exist_ok=True)
    return chromadb.PersistentClient(path=CHROMA_PATH)


def get_or_create_collection(
    client: chromadb.PersistentClient,
) -> chromadb.Collection:
    """获取或创建 Collection"""
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "魔数师指标向量库"},
    )


def rebuild_collection(
    client: chromadb.PersistentClient,
) -> chromadb.Collection:
    """删除旧 Collection 并重建（用于 --rebuild 模式）"""
    try:
        client.delete_collection(COLLECTION_NAME)
        print("  🗑️  已删除旧集合")
    except Exception:
        pass
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "魔数师指标向量库"},
    )


# ══════════════════════════════════════════════
# 断点续传
# ══════════════════════════════════════════════
def load_progress() -> set[str]:
    """读取已处理的 ID 集合"""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_progress(processed_ids: list[str]) -> None:
    """持久化已处理的 ID 列表"""
    os.makedirs(CHROMA_PATH, exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(processed_ids, f, ensure_ascii=False)


# ══════════════════════════════════════════════
# 核心流程
# ══════════════════════════════════════════════
def vectorize_and_store(rebuild: bool = False) -> None:
    """对所有指标进行向量化并存储到 Chroma"""
    print("=" * 55)
    print("  魔数师指标向量化脚本")
    print("=" * 55)

    # ── 1. 检查 API Key ──────────────────────────
    if not SILICONFLOW_API_KEY:
        print("❌ 错误: 未设置 SILICONFLOW_API_KEY 环境变量")
        print("   请在 .env 文件中配置: SILICONFLOW_API_KEY=sk-xxxxxxxx")
        sys.exit(1)

    # ── 2. 获取指标 ──────────────────────────────
    print("\n[1/4] 获取指标数据...")
    indicators = get_all_indicators()
    if not indicators:
        print("❌ 错误: 未找到任何指标")
        sys.exit(1)

    # ── 3. 初始化 Chroma ─────────────────────────
    print("\n[2/4] 初始化 Chroma...")
    client = get_chroma_client()

    if rebuild:
        print("  ♻️  重建模式：清空旧数据")
        collection = rebuild_collection(client)
    else:
        print("  🔄 更新模式：仅添加新数据")
        collection = get_or_create_collection(client)

    # ── 4. 过滤有效 & 去重 ───────────────────────
    print("\n[3/4] 过滤数据...")

    texts: list[str] = []
    valid_indicators: list[dict] = []
    for ind in indicators:
        alias       = (ind.get("alias")       or "").strip()
        description = (ind.get("description") or "").strip()
        text        = f"{alias} {description}".strip()
        if text:
            texts.append(text)
            valid_indicators.append(ind)

    print(f"  有效指标: {len(valid_indicators)}/{len(indicators)}")

    if not rebuild:
        # 优先用断点进度文件（更快）
        done_ids = load_progress()
        if not done_ids:
            # 回退到查询 Chroma
            existing = collection.get(ids=[i["id"] for i in valid_indicators])
            done_ids = set(existing["ids"])

        before = len(valid_indicators)
        paired = [
            (t, i) for t, i in zip(texts, valid_indicators)
            if i["id"] not in done_ids
        ]
        if paired:
            texts, valid_indicators = zip(*paired)
            texts            = list(texts)
            valid_indicators = list(valid_indicators)
        else:
            texts            = []
            valid_indicators = []

        print(f"  已存在: {len(done_ids)}，本次新增: {len(valid_indicators)}")
        if not valid_indicators:
            print("✅ 无需更新，所有指标已向量化。")
            return

    # ── 5. 串行向量化 + 写入 Chroma ──────────────
    total         = len(valid_indicators)
    total_batches = (total + API_BATCH_SIZE - 1) // API_BATCH_SIZE
    est_secs      = total_batches * 1.5   # 粗略估算，每批约 1.5s

    print(f"\n[4/4] 生成向量并写入 Chroma...")
    print(f"  模型     : {EMBEDDING_MODEL}  维度: {EMBEDDING_DIM}")
    print(f"  批大小   : {API_BATCH_SIZE} 条/批  共 {total_batches} 批")
    print(f"  待处理   : {total} 条，预计耗时 ≈ {est_secs:.0f}s\n")

    t0 = time.time()

    # 分批 Embedding + 分批写入 Chroma（交错进行，节省内存）
    processed_ids: list[str] = list(load_progress())

    chroma_ids:        list[str]         = []
    chroma_embeddings: list[list[float]] = []
    chroma_metadatas:  list[dict]        = []
    chroma_documents:  list[str]         = []

    for batch_idx, start in enumerate(range(0, total, API_BATCH_SIZE)):
        end   = min(start + API_BATCH_SIZE, total)
        batch_texts      = texts[start:end]
        batch_indicators = valid_indicators[start:end]

        # ── Embedding ──
        payload = {
            "model": EMBEDDING_MODEL,
            "input": batch_texts,
            "dimensions": EMBEDDING_DIM,
        }
        headers = {
            "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
            "Content-Type": "application/json",
        }

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
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
                print(f"  ⚠️  重试 {attempt + 1}/{MAX_RETRIES}，{wait}s 后继续... ({e})")
                time.sleep(wait)

        if last_exc:
            raise RuntimeError(
                f"Embedding API 请求失败，已重试 {MAX_RETRIES} 次: {last_exc}"
            ) from last_exc

        batch_embeddings = [item["embedding"] for item in resp.json()["data"]]

        # ── 累积到 Chroma 缓冲区 ──
        for i, ind in enumerate(batch_indicators):
            chroma_ids.append(ind["id"])
            chroma_embeddings.append(batch_embeddings[i])
            chroma_metadatas.append({
                "alias":       ind.get("alias",       "") or "",
                "description": ind.get("description", "") or "",
                "theme_id":    ind.get("theme_id",    "") or "",
                "theme_alias": ind.get("theme_alias", "") or "",
            })
            chroma_documents.append(batch_texts[i])

        # ── 达到 CHROMA_BATCH 则写入一次 ──
        if len(chroma_ids) >= CHROMA_BATCH:
            collection.add(
                ids        = chroma_ids,
                embeddings = chroma_embeddings,
                metadatas  = chroma_metadatas,
                documents  = chroma_documents,
            )
            processed_ids.extend(chroma_ids)
            save_progress(processed_ids)

            print(f"  💾 Chroma 已写入 {len(processed_ids)}/{total} 条")

            # 清空缓冲区
            chroma_ids        = []
            chroma_embeddings = []
            chroma_metadatas  = []
            chroma_documents  = []

        # ── 进度打印 ──
        elapsed = time.time() - t0
        speed   = end / elapsed if elapsed > 0 else 0
        eta     = (total - end) / speed if speed > 0 else 0
        print(
            f"  📦 [{batch_idx + 1}/{total_batches}] "
            f"{end}/{total} 条  "
            f"({end / total * 100:.1f}%)  "
            f"速度 {speed:.0f} 条/s  "
            f"剩余 {eta:.0f}s"
        )

    # ── 写入剩余缓冲区 ──
    if chroma_ids:
        collection.add(
            ids        = chroma_ids,
            embeddings = chroma_embeddings,
            metadatas  = chroma_metadatas,
            documents  = chroma_documents,
        )
        processed_ids.extend(chroma_ids)
        save_progress(processed_ids)
        print(f"  💾 Chroma 已写入 {len(processed_ids)}/{total} 条")

    elapsed = time.time() - t0
    print("\n" + "=" * 55)
    print(f"  ✅ 完成！向量化 {total} 个指标，总耗时 {elapsed:.1f}s")
    print(f"  ⚡ 平均速度: {total / elapsed:.0f} 条/s")
    print(f"  📁 存储路径: {CHROMA_PATH}")
    print("=" * 55)


# ══════════════════════════════════════════════
# 统计信息
# ══════════════════════════════════════════════
def show_stats() -> None:
    """显示向量库统计信息"""
    print("=" * 55)
    print("  向量库统计信息")
    print("=" * 55)
    try:
        client     = get_chroma_client()
        collection = get_or_create_collection(client)
        count      = collection.count()

        print(f"\n  指标总数      : {count}")
        print(f"  存储路径      : {CHROMA_PATH}")
        print(f"  Collection    : {COLLECTION_NAME}")
        print(f"  Embedding 模型: {EMBEDDING_MODEL}  维度: {EMBEDDING_DIM}")
        print(f"  批大小        : {API_BATCH_SIZE} 条/批")

        if count > 0:
            sample = collection.get(limit=3)
            print(f"\n  样本数据（前 3 条）:")
            for i, meta in enumerate(sample["metadatas"]):
                print(f"    {i + 1}. [{meta.get('theme_alias', 'N/A')}] "
                      f"{meta.get('alias', 'N/A')}")
                desc = meta.get("description", "") or ""
                print(f"       {desc[:60]}{'...' if len(desc) > 60 else ''}")
    except Exception as e:
        print(f"❌ 错误: {e}")
        print("   提示: 请先运行 --rebuild 生成向量数据")
    print("=" * 55)


# ══════════════════════════════════════════════
# 搜索演示
# ══════════════════════════════════════════════
def search_demo(query: str, top_k: int = 5) -> None:
    """向量相似度搜索演示"""
    print("=" * 55)
    print(f"  向量搜索: 「{query}」")
    print("=" * 55)
    try:
        print("  生成查询向量...")
        query_vector = get_embedding(query)

        client     = get_chroma_client()
        collection = get_or_create_collection(client)

        print("  执行搜索...")
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=top_k,
        )

        print(f"\n  搜索结果（Top {top_k}）:")
        for i, (doc, meta, dist) in enumerate(zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        )):
            similarity = 1 - dist
            desc = meta.get("description", "") or ""
            print(f"\n  {i + 1}. {meta.get('alias', 'N/A')}")
            print(f"     相似度  : {similarity:.2%}")
            print(f"     主题    : {meta.get('theme_alias', 'N/A')}")
            print(f"     描述    : {desc[:60]}{'...' if len(desc) > 60 else ''}")
    except Exception as e:
        print(f"❌ 错误: {e}")
        print("   提示: 请先运行 --rebuild 生成向量数据")
    print("=" * 55)


# ══════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="魔数师指标向量化脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --rebuild          # 重建向量索引（清空重来）
  %(prog)s --update           # 增量更新新增指标
  %(prog)s --stats            # 查看统计信息
  %(prog)s --search "风险"    # 搜索演示
        """,
    )
    parser.add_argument("--rebuild", action="store_true", help="重建向量索引（清空重来）")
    parser.add_argument("--update",  action="store_true", help="增量更新新增指标")
    parser.add_argument("--stats",   action="store_true", help="查看统计信息")
    parser.add_argument("--search",  type=str, metavar="QUERY", help="搜索演示")

    args = parser.parse_args()

    if args.stats:
        show_stats()
    elif args.search:
        search_demo(args.search)
    elif args.rebuild:
        vectorize_and_store(rebuild=True)
    elif args.update:
        vectorize_and_store(rebuild=False)
    else:
        parser.print_help()
        print("\n💡 提示: 首次使用请运行 --rebuild 生成向量数据")
