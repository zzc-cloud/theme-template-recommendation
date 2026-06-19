import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_service.tools.confirmation_tools import AskUserQuestion_tools, Option, Section
EXPECTED = [
    "search_indicators_by_vector",
    "batch_get_indicator_themes",
    "aggregate_themes_from_indicators",
    "get_sectors_from_root",
    "get_sector_themes",
    "get_theme_filter_indicators",
    "get_theme_analysis_indicators",
    "get_theme_templates_with_coverage",
    "AskUserQuestion_tools",
]


def test_tool_registry_whitelist_only():
    module = ast.parse((ROOT / "src/agent_service/deepagents/tool_registry.py").read_text(encoding="utf-8"))
    names = None
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "TOOL_NAMES":
                    names = ast.literal_eval(node.value)
    assert names == EXPECTED


def test_confirmation_tool_exists():
    text = (ROOT / "src/agent_service/tools/confirmation_tools.py").read_text(encoding="utf-8")
    assert "def AskUserQuestion_tools" in text
    assert "interrupt_type" in text


def test_confirmation_tool_schema_and_payload():
    section = Section(
        key="candidate_filters",
        title="基于你的分析需求，得到以下筛选条件，请确认",
        select_mode="multiple",
        options=[Option(label="机构 = 南京分行", description="", value="org=南京分行")],
        allow_freeform=True,
        freeform_hint="以上筛选条件有问题，请直接输入",
    )

    payload = AskUserQuestion_tools(
        thread_id="test-001",
        interrupt_type="dimension_and_filters_confirmation",
        sections=[section],
    )

    assert payload == {
        "interrupt_type": "dimension_and_filters_confirmation",
        "thread_id": "test-001",
        "sections": [
            {
                "key": "candidate_filters",
                "title": "基于你的分析需求，得到以下筛选条件，请确认",
                "select_mode": "multiple",
                "options": [
                    {"label": "机构 = 南京分行", "description": "", "value": "org=南京分行"}
                ],
                "allow_freeform": True,
                "freeform_hint": "以上筛选条件有问题，请直接输入",
            }
        ],
    }
