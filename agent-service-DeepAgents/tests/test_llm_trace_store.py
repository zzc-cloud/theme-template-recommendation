import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_service.deepagents.llm_trace_store import ALLOWED_EVENT_TYPES, list_trace_events_by_thread, save_trace_event


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_obj = cursor
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True


def test_save_trace_event_serializes_json_fields(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    monkeypatch.setattr("agent_service.deepagents.llm_trace_store._connect", lambda: conn)

    save_trace_event({
        "event_id": "evt-1",
        "event_type": "llm_input",
        "user_id": "local",
        "conversation_id": "thread-1",
        "thread_id": "thread-1",
        "request_id": "req-1",
        "run_id": "run-1",
        "parent_run_id": None,
        "model_name": "model-1",
        "token_usage": {"total_tokens": 1},
        "payload": {"messages": ["hi"]},
    })

    assert conn.committed is True
    assert json.loads(cursor.params["token_usage"]) == {"total_tokens": 1}
    assert json.loads(cursor.params["payload"]) == {"messages": ["hi"]}


def test_list_trace_events_by_thread_filters_and_decodes(monkeypatch):
    cursor = FakeCursor(rows=[{
        "id": 1,
        "event_id": "evt-1",
        "event_type": "llm_input",
        "user_id": "local",
        "conversation_id": "thread-1",
        "thread_id": "thread-1",
        "request_id": "req-1",
        "run_id": "run-1",
        "parent_run_id": None,
        "model_name": "model-1",
        "token_usage": '{"total_tokens": 1}',
        "payload": '{"messages": ["hi"]}',
        "created_at": dt.datetime(2026, 6, 20, 1, 2, 3),
    }])
    monkeypatch.setattr("agent_service.deepagents.llm_trace_store._connect", lambda: FakeConnection(cursor))

    rows = list_trace_events_by_thread("thread-1", limit=10, event_type="llm_input", request_id="req-1")

    assert rows[0]["token_usage"] == {"total_tokens": 1}
    assert rows[0]["payload"] == {"messages": ["hi"]}
    assert rows[0]["created_at"] == "2026-06-20T01:02:03"
    assert cursor.params == ["thread-1", ALLOWED_EVENT_TYPES, "llm_input", "req-1", 10]
