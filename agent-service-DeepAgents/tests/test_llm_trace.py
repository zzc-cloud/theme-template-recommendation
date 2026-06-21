import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_service.deepagents.llm_trace import LLMTraceCallback, _safe_json


class FakeMessage:
    def model_dump(self, mode="json"):
        return {"type": "human", "content": "hello", "id": "msg-1"}


def test_safe_json_serializes_langchain_like_objects():
    payload = _safe_json([[FakeMessage()]])

    assert payload == [[{"type": "human", "content": "hello", "id": "msg-1"}]]


def test_chat_model_start_does_not_save_callback_messages(monkeypatch):
    saved = []
    monkeypatch.setattr("agent_service.deepagents.llm_trace.save_trace_event", saved.append)
    callback = LLMTraceCallback(user_id="local", conversation_id="thread-1", thread_id="thread-1", request_id="req-1")

    callback.on_chat_model_start(
        {"kwargs": {"model": "test-model"}},
        [[FakeMessage()]],
        run_id="run-1",
        parent_run_id=None,
    )

    assert saved == []


def test_llm_end_does_not_save_langchain_result_wrapper(monkeypatch):
    saved = []
    monkeypatch.setattr("agent_service.deepagents.llm_trace.save_trace_event", saved.append)
    callback = LLMTraceCallback(user_id="local", conversation_id="thread-1", thread_id="thread-1", request_id="req-1")

    callback.on_llm_end(
        {
            "type": "LLMResult",
            "generations": [[{"message": {"type": "ai", "content": "world"}}]],
            "llm_output": {"token_usage": {"total_tokens": 3}, "model_name": "test-model"},
        },
        run_id="run-2",
    )

    assert saved == []


def test_llm_error_save_event(monkeypatch):
    saved = []
    monkeypatch.setattr("agent_service.deepagents.llm_trace.save_trace_event", saved.append)
    callback = LLMTraceCallback(user_id="local", conversation_id="thread-1", thread_id="thread-1", request_id="req-1")

    callback.on_llm_error(RuntimeError("boom"), run_id="run-3")

    assert len(saved) == 1
    assert saved[0]["event_type"] == "llm_error"
    assert saved[0]["payload"] == {"error": {"type": "RuntimeError", "message": "boom"}}


def test_tool_callbacks_are_not_collected():
    assert "on_tool_start" not in LLMTraceCallback.__dict__
    assert "on_tool_end" not in LLMTraceCallback.__dict__
    assert "on_tool_error" not in LLMTraceCallback.__dict__
