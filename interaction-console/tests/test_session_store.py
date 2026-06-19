from interaction_console.session_store import SessionStore


def test_max_seq_returns_zero_for_unknown_thread():
    store = SessionStore()

    assert store.max_seq("missing") == 0


def test_max_seq_returns_largest_numeric_seq():
    store = SessionStore()
    store.append("t1", {"seq": 1})
    store.append("t1", {"seq": "3"})
    store.append("t1", {"seq": None})
    store.append("t1", {"seq": "bad"})

    assert store.max_seq("t1") == 3
