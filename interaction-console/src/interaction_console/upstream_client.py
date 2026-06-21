"""上游 DeepAgents 推荐接口的流式 HTTP 客户端。

本模块是后端唯一直接访问上游 `/api/v1/recommend` 的地方，只负责发送请求并逐行
产出 SSE 文本；具体事件识别由 event_normalizer.py 处理，避免网络层混入协议解析逻辑。
"""

from urllib.parse import urljoin

import httpx


async def stream_recommend(url: str, thread_id: str, user_input: str):
    """以 POST 方式调用上游推荐接口，并逐行 yield SSE 内容。"""
    payload = {"thread_id": thread_id, "user_input": user_input}
    # read=None 允许上游长时间流式输出；trust_env=False 避免本机代理变量影响 localhost 调用。
    timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        async with client.stream("POST", url, json=payload) as response:
            if response.status_code >= 400:
                # 先读取响应体，main.py 捕获 HTTPStatusError 后才能把 body 展示到 error 事件。
                await response.aread()
                response.raise_for_status()
            async for line in response.aiter_lines():
                yield line


async def fetch_llm_traces(url: str, thread_id: str, limit: int = 200, event_type: str | None = None, request_id: str | None = None) -> dict:
    """从上游查询指定 thread_id 的 LLM trace。"""
    base_url = url.rsplit("/api/v1/recommend", 1)[0]
    trace_url = urljoin(f"{base_url}/", f"api/v1/traces/{thread_id}")
    params = {"limit": limit}
    if event_type:
        params["event_type"] = event_type
    if request_id:
        params["request_id"] = request_id
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        response = await client.get(trace_url, params=params)
        response.raise_for_status()
        return response.json()
