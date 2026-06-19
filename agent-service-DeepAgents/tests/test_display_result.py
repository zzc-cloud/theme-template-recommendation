import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def test_skill_documents_markdown_final_output():
    text = (ROOT / "skills/theme-template-recommendation/SKILL.md").read_text(encoding="utf-8")

    assert "最后一条消息必须直接输出面向用户的 Markdown 文本" in text
    assert "不要包装成 JSON" in text
    assert "需求澄清" in text
    assert "推荐主题" in text
    assert "推荐模板" in text
    assert "使用建议" in text


def test_skill_no_longer_requires_display_result_json():
    text = (ROOT / "skills/theme-template-recommendation/SKILL.md").read_text(encoding="utf-8")

    forbidden_terms = ["Display" + "Result", '"status": "' + "final" + '"', '"sections"', "content_" + "markdown"]
    for term in forbidden_terms:
        assert term not in text
