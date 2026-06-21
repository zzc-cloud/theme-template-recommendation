import json

from fastapi.testclient import TestClient

from interaction_console.main import app


def test_llm_traces_proxy(monkeypatch):
    async def fake_fetch_llm_traces(upstream_url, thread_id, limit=200, event_type=None, request_id=None):
        return {
            "thread_id": thread_id,
            "events": [
                {
                    "event_type": event_type,
                    "request_id": request_id,
                    "payload": {"messages": ["hi"]},
                }
            ],
        }

    monkeypatch.setattr("interaction_console.main.fetch_llm_traces", fake_fetch_llm_traces)

    client = TestClient(app)
    response = client.get(
        "/api/sessions/thread-1/llm-traces",
        params={
            "agent_id": "theme-template-recommendation-deepagents",
            "limit": 10,
            "event_type": "llm_input",
            "request_id": "req-1",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-1",
        "events": [{"event_type": "llm_input", "request_id": "req-1", "payload": {"messages": ["hi"]}}],
    }
