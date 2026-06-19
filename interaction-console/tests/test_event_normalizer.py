from interaction_console.event_normalizer import EventNormalizer

AGENT_ID = "theme-template-recommendation-deepagents"
THREAD_ID = "test-thread"


def normalizer() -> EventNormalizer:
    return EventNormalizer(thread_id=THREAD_ID, agent_id=AGENT_ID)


def test_user_message_event():
    event = normalizer().user_message("查询小微贷款风险")

    assert event.type == "user_message"
    assert event.payload["content"] == "查询小微贷款风险"
    assert event.raw is None
    assert event.seq == 1


def test_initial_seq_continues_from_existing_max():
    instance = EventNormalizer(thread_id=THREAD_ID, agent_id=AGENT_ID, initial_seq=10)

    event = instance.user_message("继续")

    assert event.seq == 11


def test_skills_middleware_to_skill_loaded():
    events = normalizer().normalize({"SkillsMiddleware.before_agent": {"skills_metadata": [{"name": "theme-template-recommendation"}]}})

    assert len(events) == 1
    assert events[0].type == "skill_loaded"
    assert events[0].payload["skills"][0]["name"] == "theme-template-recommendation"


def test_model_user_role_to_user_message():
    events = normalizer().normalize({"model": {"messages": [{"role": "user", "content": "用户问题"}]}})

    assert len(events) == 1
    assert events[0].type == "user_message"
    assert events[0].payload["content"] == "用户问题"


def test_model_unknown_role_content_defaults_to_assistant_message():
    events = normalizer().normalize({"model": {"messages": [{"content": "助手输出"}]}})

    assert len(events) == 1
    assert events[0].type == "assistant_message"
    assert events[0].payload["content"] == "助手输出"


def test_model_messages_tool_calls_to_tool_use():
    raw = {
        "model": {
            "messages": [
                "content='[主题和模板推荐] 开始执行' additional_kwargs={} tool_calls=[{'name': 'search_indicators_by_vector', 'args': {'query': '小微企业贷款风险', 'top_k': 20}, 'id': 'call_1'}]"
            ]
        }
    }

    events = normalizer().normalize(raw)

    assert [event.type for event in events] == ["assistant_message", "tool_use"]
    assert events[0].payload["content"] == "[主题和模板推荐] 开始执行"
    assert events[1].payload["tool_name"] == "search_indicators_by_vector"
    assert events[1].payload["args"]["query"] == "小微企业贷款风险"


def test_tools_messages_to_tool_result():
    raw = {
        "tools": {
            "messages": [
                "content='{\"success\": true, \"indicator_count\": 2}' name='search_indicators_by_vector' tool_call_id='call_1'"
            ]
        }
    }

    events = normalizer().normalize(raw)

    assert len(events) == 1
    assert events[0].type == "tool_result"
    assert events[0].payload["tool_name"] == "search_indicators_by_vector"
    assert events[0].payload["tool_call_id"] == "call_1"
    assert events[0].payload["is_json"] is True
    assert events[0].payload["content"]["success"] is True


def test_action_requests_to_interrupt():
    raw = {
        "action_requests": [
            {
                "name": "AskUserQuestion_tools",
                "args": {
                    "interrupt_type": "dimension_and_filters_confirmation",
                    "thread_id": THREAD_ID,
                    "sections": [{"title": "候选筛选条件", "select_mode": "multiple"}],
                },
            }
        ],
        "review_configs": [{"allowed_decisions": ["approve", "respond"]}],
    }

    events = normalizer().normalize(raw)

    assert len(events) == 1
    assert events[0].type == "interrupt"
    assert events[0].payload["interrupt_type"] == "dimension_and_filters_confirmation"
    assert events[0].payload["sections"][0]["title"] == "候选筛选条件"
    assert events[0].payload["allowed_decisions"] == ["approve", "respond"]


def test_unknown_data_to_raw():
    events = normalizer().normalize({"unexpected": {"value": 1}})

    assert len(events) == 1
    assert events[0].type == "raw"
    assert events[0].raw == {"unexpected": {"value": 1}}


def test_legacy_result_payloads_fall_back_to_raw():
    for raw in ({"status": "final"}, {"display": {"sections": []}}):
        events = normalizer().normalize(raw)

        assert len(events) == 1
        assert events[0].type == "raw"
        assert events[0].raw == raw


def test_ping_heartbeat_and_event_lines_ignored():
    instance = normalizer()

    assert instance.normalize_line(": ping - 2026-06-17T00:00:00Z") == []
    assert instance.normalize_line("event: message") == []
