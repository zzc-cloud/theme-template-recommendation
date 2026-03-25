"""
并发与 TTL 清理测试
"""

import time
import asyncio
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from agent_service.main import app
from agent_service.graph.graph import get_checkpointer, reset_agent
from agent_service.config import MAX_CONCURRENT_REQUESTS
from agent_service.api.routes import init_semaphore


@pytest.fixture(autouse=True)
def reset_singleton():
    """每个测试前重置全局单例"""
    reset_agent()
    yield
    reset_agent()


@pytest.mark.asyncio
async def test_10_concurrent_requests():
    """验证10个并发请求互不干扰"""
    questions = [
        f"查询{bank}的存款余额"
        for bank in [
            "北京分行",
            "上海分行",
            "广州分行",
            "深圳分行",
            "成都分行",
            "杭州分行",
            "武汉分行",
            "南京分行",
            "西安分行",
            "重庆分行",
        ]
    ]

    async def _single_request(client: AsyncClient, question: str) -> dict:
        """模拟一次完整的问答请求"""
        thread_id = str(uuid.uuid4())
        response = await client.post(
            "/recommend",
            json={"question": question, "thread_id": thread_id},
        )
        return {"thread_id": thread_id, "status": response.status_code}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 10个请求同时发出
        results = await asyncio.gather(
            *[_single_request(client, q) for q in questions],
            return_exceptions=True,
        )

    # 验证所有请求都成功
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 0, f"并发请求出现异常: {errors}"

    success = [r for r in results if isinstance(r, dict) and r["status"] == 200]
    assert len(success) == 10, f"期望10个成功，实际: {len(success)}"

    # 验证所有 thread_id 都不同（隔离性）
    thread_ids = [r["thread_id"] for r in results if isinstance(r, dict)]
    assert len(set(thread_ids)) == 10, "thread_id 存在重复！"


@pytest.mark.asyncio
async def test_ttl_cleanup():
    """验证 TTL 清理正常工作"""
    saver = get_checkpointer()
    assert saver.ttl_seconds == 86400, f"TTL 应为 86400，实际: {saver.ttl_seconds}"

    # 模拟写入一个 thread
    test_thread_id = "test-expired-thread"
    saver._timestamps[test_thread_id] = time.time() - 7200  # 2小时前

    # 模拟 InMemorySaver 写入数据
    saver.storage[test_thread_id] = {}

    # 执行清理
    count = saver.cleanup_expired()
    assert count >= 1, f"期望清理至少1个过期thread，实际: {count}"

    # 验证已清理
    assert test_thread_id not in saver._timestamps, "thread 时间戳未被清理"
    assert test_thread_id not in saver.storage, "thread 存储未被清理"
    assert test_thread_id not in saver.writes, "thread writes 未被清理"


@pytest.mark.asyncio
async def test_ttl_not_cleanup_active_thread():
    """验证活跃 thread 不会被清理"""
    saver = get_checkpointer()

    # 模拟写入一个活跃 thread
    active_thread_id = "test-active-thread"
    saver._timestamps[active_thread_id] = time.time()  # 刚刚活跃
    saver.storage[active_thread_id] = {}

    # 执行清理
    count = saver.cleanup_expired()

    # 活跃 thread 不应被清理
    assert active_thread_id in saver._timestamps, "活跃 thread 被错误清理"
    assert active_thread_id in saver.storage, "活跃 thread 存储被错误清理"


@pytest.mark.asyncio
async def test_stats_reporting():
    """验证 stats 方法正确报告状态"""
    saver = get_checkpointer()

    # 添加一些 threads
    now = time.time()
    saver._timestamps["thread-1"] = now  # 活跃
    saver._timestamps["thread-2"] = now - 3600  # 活跃（1小时前）
    saver._timestamps["thread-3"] = now - 90000  # 过期（>1天前）

    stats = saver.stats()
    assert stats["total_threads"] == 3
    assert stats["ttl_seconds"] == 86400
    # thread-1 和 thread-2 在 1 天内，thread-3 超过 1 天
    assert stats["active_threads"] == 2, f"期望 2 个活跃线程，实际: {stats['active_threads']}"
    assert stats["expired_threads"] == 1, f"期望 1 个过期线程，实际: {stats['expired_threads']}"


@pytest.mark.asyncio
async def test_multiple_cleanup_cycles():
    """验证多次清理循环的幂等性"""
    saver = get_checkpointer()
    now = time.time()

    # 添加多个过期 threads
    for i in range(5):
        tid = f"expired-thread-{i}"
        saver._timestamps[tid] = now - 90000
        saver.storage[tid] = {}

    # 第一次清理
    count1 = saver.cleanup_expired()
    assert count1 == 5

    # 第二次清理（应该无事可做）
    count2 = saver.cleanup_expired()
    assert count2 == 0, f"第二次清理不应清理任何 thread，实际: {count2}"

    # storage 应为空
    assert len(saver.storage) == 0, "清理后 storage 应为空"


@pytest.mark.asyncio
async def test_health_shows_concurrency():
    """验证 /health 接口返回并发状态"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        data = resp.json()

    assert "concurrency" in data
    assert data["concurrency"]["max"] == MAX_CONCURRENT_REQUESTS
    assert data["concurrency"]["current"] >= 0
    print(f"✅ 健康检查并发状态: {data['concurrency']}")
