from pathlib import Path

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
    assert "_to_interrupt_payload" not in text
    assert '@router.post("/resume")' not in text
    assert "ResumeRequest" not in text
    assert "Display" + "Result" not in text
    assert "_extract_final_json" not in text


def test_router_loads_only_service_skill_source():
    text = read("src/agent_service/deepagents/router.py")
    assert 'RECOMMENDATION_SKILL = "skills"' in text
    forbidden_skill = "theme-template-" + "selection"
    assert forbidden_skill not in text
