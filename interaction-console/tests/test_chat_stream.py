import json

from fastapi.testclient import TestClient

from interaction_console.main import app
from interaction_console.session_store import session_store


def parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.strip().split("\n\n"):
        line = next((item for item in block.splitlines() if item.startswith("data:")), None)
        if line:
            events.append(json.loads(line.removeprefix("data:").strip()))
    return events


def chat_payload(user_input: str, thread_id: str = "thread-1") -> dict:
    return {
        "agent_id": "theme-template-recommendation-deepagents",
        "thread_id": thread_id,
        "user_input": user_input,
    }


def test_chat_stream_starts_with_user_message(monkeypatch):
    session_store.events.clear()

    async def fake_stream_recommend(upstream_url, thread_id, user_input):
        yield 'data: {"model": {"messages": [{"role": "assistant", "content": "收到"}]}}'

    monkeypatch.setattr("interaction_console.main.stream_recommend", fake_stream_recommend)

    client = TestClient(app)
    response = client.post("/api/chat/stream", json=chat_payload("查小微贷款风险"))
    events = parse_sse(response.text)

    assert response.status_code == 200
    assert events[0]["type"] == "user_message"
    assert events[0]["payload"]["content"] == "查小微贷款风险"
    assert events[1]["type"] == "assistant_message"
    assert session_store.list_events("thread-1")[0]["type"] == "user_message"


def test_chat_stream_seq_continues_for_same_thread(monkeypatch):
    session_store.events.clear()
    calls = []

    async def fake_stream_recommend(upstream_url, thread_id, user_input):
        calls.append({"thread_id": thread_id, "user_input": user_input})
        yield 'data: {"model": {"messages": [{"role": "assistant", "content": "收到"}]}}'

    monkeypatch.setattr("interaction_console.main.stream_recommend", fake_stream_recommend)

    client = TestClient(app)
    client.post("/api/chat/stream", json=chat_payload("第一轮"))
    client.post("/api/chat/stream", json=chat_payload("第二轮"))

    seqs = [event["seq"] for event in session_store.list_events("thread-1")]

    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))
    assert calls == [
        {"thread_id": "thread-1", "user_input": "第一轮"},
        {"thread_id": "thread-1", "user_input": "第二轮"},
    ]


def test_chat_stream_forwards_distinct_thread_ids(monkeypatch):
    session_store.events.clear()
    calls = []

    async def fake_stream_recommend(upstream_url, thread_id, user_input):
        calls.append({"thread_id": thread_id, "user_input": user_input})
        yield 'data: {"status": "done"}'

    monkeypatch.setattr("interaction_console.main.stream_recommend", fake_stream_recommend)

    client = TestClient(app)
    client.post("/api/chat/stream", json=chat_payload("旧会话", thread_id="thread-A"))
    client.post("/api/chat/stream", json=chat_payload("新会话", thread_id="thread-B"))

    assert calls == [
        {"thread_id": "thread-A", "user_input": "旧会话"},
        {"thread_id": "thread-B", "user_input": "新会话"},
    ]


def test_chat_stream_does_not_duplicate_upstream_done(monkeypatch):
    session_store.events.clear()

    async def fake_stream_recommend(upstream_url, thread_id, user_input):
        yield 'data: {"status": "done"}'

    monkeypatch.setattr("interaction_console.main.stream_recommend", fake_stream_recommend)

    client = TestClient(app)
    response = client.post("/api/chat/stream", json=chat_payload("第一轮"))
    events = parse_sse(response.text)

    assert [event["type"] for event in events].count("done") == 1
