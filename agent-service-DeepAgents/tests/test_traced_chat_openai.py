import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from langchain_core.messages import HumanMessage, SystemMessage

from agent_service.deepagents.llm_trace import llm_trace_context
from agent_service.deepagents.traced_chat_openai import TracedChatOpenAI


def test_traced_chat_openai_saves_final_provider_payload(monkeypatch):
    saved = []
    monkeypatch.setattr("agent_service.deepagents.llm_trace.save_trace_event", saved.append)

    model = TracedChatOpenAI(
        model="test-model",
        api_key="test-key",
        base_url="https://example.com/v1",
        temperature=0,
        max_tokens=123,
    )

    with llm_trace_context(user_id="local", conversation_id="conv-1", thread_id="thread-1", request_id="req-1"):
        payload = model._get_request_payload(
            [SystemMessage(content="system prompt"), HumanMessage(content="hello")],
            stop=["STOP"],
            tools=[{
                "type": "function",
                "function": {
                    "name": "search_indicators",
                    "description": "Search indicators",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            }],
            tool_choice="auto",
        )

    assert len(saved) == 1
    event = saved[0]
    assert event["event_type"] == "llm_input"
    assert event["user_id"] == "local"
    assert event["conversation_id"] == "conv-1"
    assert event["thread_id"] == "thread-1"
    assert event["request_id"] == "req-1"
    assert event["run_id"] is None
    assert event["model_name"] == "test-model"

    trace_payload = event["payload"]
    assert trace_payload["source"] == "ChatOpenAI._get_request_payload"
    assert trace_payload["provider"] == "openai-compatible"
    assert trace_payload["api_shape"] == "chat_completions_or_responses"
    assert trace_payload["payload"] == payload
    assert trace_payload["payload"]["model"] == "test-model"
    assert trace_payload["payload"]["messages"] == [
        {"content": "system prompt", "role": "system"},
        {"content": "hello", "role": "user"},
    ]
    assert trace_payload["payload"]["tools"][0]["function"]["name"] == "search_indicators"
    assert trace_payload["payload"]["tool_choice"] == "auto"
    assert trace_payload["payload"]["temperature"] == 0.0
    assert trace_payload["payload"]["max_completion_tokens"] == 123
    assert trace_payload["payload"]["stop"] == ["STOP"]


def test_traced_chat_openai_saves_direct_provider_output(monkeypatch):
    saved = []
    monkeypatch.setattr("agent_service.deepagents.llm_trace.save_trace_event", saved.append)

    provider_response = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 123,
        "model": "test-model",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "search_indicators", "arguments": '{"query":"贷款余额"}'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }
    model = TracedChatOpenAI(model="test-model", api_key="test-key", base_url="https://example.com/v1")

    with llm_trace_context(user_id="local", conversation_id="conv-1", thread_id="thread-1", request_id="req-1"):
        model._save_provider_output(provider_response)

    assert len(saved) == 1
    event = saved[0]
    assert event["event_type"] == "llm_output"
    assert event["model_name"] == "test-model"
    assert event["token_usage"] == {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    assert event["payload"] == provider_response
    assert "generations" not in event["payload"]
    assert event["payload"]["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "search_indicators"


def test_traced_chat_openai_skips_trace_without_context(monkeypatch):
    saved = []
    monkeypatch.setattr("agent_service.deepagents.llm_trace.save_trace_event", saved.append)

    model = TracedChatOpenAI(model="test-model", api_key="test-key", base_url="https://example.com/v1")
    model._get_request_payload([HumanMessage(content="hello")])
    model._save_provider_output({"model": "test-model", "choices": []})

    assert saved == []
