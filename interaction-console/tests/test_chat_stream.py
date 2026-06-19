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

    async def fake_stream_recommend(upstream_url, thread_id, user_input):
        yield 'data: {"model": {"messages": [{"role": "assistant", "content": "收到"}]}}'

    monkeypatch.setattr("interaction_console.main.stream_recommend", fake_stream_recommend)

    client = TestClient(app)
    client.post("/api/chat/stream", json=chat_payload("第一轮"))
    client.post("/api/chat/stream", json=chat_payload("第二轮"))

    seqs = [event["seq"] for event in session_store.list_events("thread-1")]

    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))
