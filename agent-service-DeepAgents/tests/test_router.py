from pathlib import Path

import pytest
from langgraph.types import Command

from agent_service.api import routes
from agent_service.api.schemas import RecommendRequest

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_agent_factory_uses_required_deepagents_runtime():
    text = read("src/agent_service/deepagents/agent_factory.py")
    assert "create_deep_agent" in text
    assert "FilesystemBackend" in text
    assert "skills=[" in text
    assert "checkpointer=" in text
    assert 'interrupt_on={"AskUserQuestion_tools": True}' in text

def test_router_uses_pending_interrupt_resume_path():
    text = read("src/agent_service/api/routes.py")
    assert "_pending_interrupts" in text
    assert "build_resume_payload_from_text" in text
    assert "Command(resume=build_resume_payload_from_text(req.user_input))" in text
    assert "_thread_has_pending_interrupt" in text
    assert "aget_state" in text
    assert "_to_interrupt_payload" not in text
    assert '@router.post("/resume")' not in text
    assert "ResumeRequest" not in text
    assert "Display" + "Result" not in text
    assert "_extract_final_json" not in text


@pytest.mark.asyncio
async def test_build_agent_input_resumes_only_when_checkpoint_has_interrupt():
    class FakeSnapshot:
        interrupts = (object(),)
        next = ()

    class FakeAgent:
        async def aget_state(self, config):
            return FakeSnapshot()

    req = RecommendRequest(thread_id="thread-1", user_input="已确认")
    routes._pending_interrupts["thread-1"] = {"type": "ask_user"}

    input_data = await routes._build_agent_input(req, FakeAgent())

    assert isinstance(input_data, Command)
    assert "thread-1" in routes._pending_interrupts
    routes._pending_interrupts.clear()


@pytest.mark.asyncio
async def test_build_agent_input_ignores_stale_pending_interrupt_for_new_question():
    class FakeSnapshot:
        interrupts = ()
        next = ()

    class FakeAgent:
        async def aget_state(self, config):
            return FakeSnapshot()

    req = RecommendRequest(thread_id="thread-1", user_input="继续说明首选模板怎么用")
    routes._pending_interrupts["thread-1"] = {"type": "ask_user"}

    input_data = await routes._build_agent_input(req, FakeAgent())

    assert not isinstance(input_data, Command)
    assert list(input_data) == ["messages"]
    assert input_data["messages"][0].content == "继续说明首选模板怎么用"
    assert "thread-1" not in routes._pending_interrupts


def test_router_loads_only_service_skill_source():
    text = read("src/agent_service/deepagents/router.py")
    assert 'RECOMMENDATION_SKILL = "skills"' in text
    forbidden_skill = "theme-template-" + "selection"
    assert forbidden_skill not in text
