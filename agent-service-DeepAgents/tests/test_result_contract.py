import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from langchain_core.messages import HumanMessage
from langgraph.types import Interrupt

from agent_service.api.routes import _find_interrupt
from agent_service.api.schemas import RecommendRequest
from agent_service.deepagents.skill_protocol import build_recommend_messages, build_resume_payload_from_text


def test_schemas_keep_only_api_boundary_models():
    module = ast.parse((ROOT / "src/agent_service/api/schemas.py").read_text(encoding="utf-8"))
    classes = {node.name for node in module.body if isinstance(node, ast.ClassDef)}
    assert classes == {
        "RecommendRequest",
        "ErrorPayload",
        "HealthResponse",
    }


def test_routes_do_not_define_sync_or_memory_endpoints():
    text = (ROOT / "src/agent_service/api/routes.py").read_text(encoding="utf-8")
    assert "recommend" + "-sync" not in text
    assert "resume" + "-sync" not in text
    assert "health" + "/memory" not in text


def test_find_interrupt_returns_raw_hitl_payload():
    raw = {
        "action_requests": [
            {
                "name": "AskUserQuestion_tools",
                "args": {
                    "normalized_question": "统计贷款余额",
                    "candidate_dimensions": [{"dimension": "贷款余额"}],
                    "filter_indicators": [{"alias": "数据日期"}],
                    "matched_indicators": [{"id": "INDICATOR.x"}],
                },
            }
        ],
        "review_configs": [{"action_name": "AskUserQuestion_tools"}],
        "unknown": {"keep": True},
    }

    interrupt = _find_interrupt({"__interrupt__": (Interrupt(value=raw),)})

    assert interrupt == raw
    assert interrupt["action_requests"][0]["args"]["normalized_question"] == "统计贷款余额"
    assert interrupt["review_configs"] == [{"action_name": "AskUserQuestion_tools"}]
    assert interrupt["unknown"] == {"keep": True}


def test_recommend_messages_use_langchain_human_message():
    req = RecommendRequest(
        thread_id="test-001",
        user_input="我想分析南京分行的小微企业贷款风险",
    )

    payload = build_recommend_messages(req)

    assert len(payload) == 1
    message = payload[0]
    assert isinstance(message, HumanMessage)
    assert message.content.startswith("thread_id: test-001")
    assert "thread_id: test-001" in message.content
    assert "用户输入: 我想分析南京分行的小微企业贷款风险" in message.content


def test_resume_payload_from_text_uses_original_user_message():
    payload = build_resume_payload_from_text("我确认选择贷款余额，不选择客户分布。")

    assert payload == {
        "decisions": [
            {
                "type": "respond",
                "message": "我确认选择贷款余额，不选择客户分布。",
            }
        ]
    }
