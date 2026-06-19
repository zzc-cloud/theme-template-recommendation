from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_skill_frontmatter_names_match_directories():
    for name in ["theme-template-recommendation"]:
        text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert f"name: {name}" in text.split("---", 2)[1]

def test_recommendation_skill_documents_confirmation_contract():
    text = (ROOT / "skills/theme-template-recommendation/SKILL.md").read_text(encoding="utf-8")
    assert "前端确认页面的 interrupt payload" in text
    assert "Section` / `Option`" in text
    assert "candidate_filters" in text
    assert "candidate_dimensions" in text
    assert "action_requests[0].args" not in text
    assert "自然语言回复" in text
    assert "再次调用 `AskUserQuestion_tools`" in text
    assert "再看看" in text


def test_recommendation_skill_documents_markdown_final_output():
    text = (ROOT / "skills/theme-template-recommendation/SKILL.md").read_text(encoding="utf-8")
    assert "最终输出" in text
    assert "Markdown 文本" in text
    assert "不要包装成 JSON" in text
    forbidden_terms = ["Display" + "Result", '"status": "' + "final" + '"']
    for term in forbidden_terms:
        assert term not in text


def test_service_skills_do_not_reference_claude_code_tools():
    for path in (ROOT / "skills").glob("*/SKILL.md"):
        text = path.read_text(encoding="utf-8")
        assert "Skill" + "(skill=" not in text
        assert "MCP server" not in text
        assert "Claude Code" not in text


def test_no_stategraph_or_old_graph_runtime():
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "State" + "Graph" not in text
        assert "from ..graph" not in text
        assert "from .graph" not in text
