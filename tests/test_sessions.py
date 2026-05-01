from __future__ import annotations

from app import db


def test_session_crud():
    s = db.create_session("hello", "chat", {"foo": 1})
    assert s["title"] == "hello"
    assert s["meta"]["foo"] == 1

    listed = db.list_sessions()
    assert any(x["id"] == s["id"] for x in listed)

    s2 = db.update_session(s["id"], title="renamed")
    assert s2["title"] == "renamed"

    matched = db.list_sessions("renamed")
    assert any(x["id"] == s["id"] for x in matched)

    assert db.delete_session(s["id"]) is True
    assert db.get_session(s["id"]) is None


def test_message_appends_and_session_touch():
    s = db.create_session("t", "chat")
    a = db.add_message(s["id"], "user", "hi")
    b = db.add_message(s["id"], "assistant", "hello", speaker="model-a", model_id="m1")
    msgs = db.list_messages(s["id"])
    assert [m["id"] for m in msgs] == [a["id"], b["id"]]

    s2 = db.get_session(s["id"])
    assert s2["updated_at"] >= s["updated_at"]


def test_message_update_meta():
    s = db.create_session("t", "chat")
    m = db.add_message(s["id"], "assistant", "x", speaker="a")
    db.update_message(m["id"], content="y", meta={"adopted": True})
    m2 = db.get_message(m["id"])
    assert m2["content"] == "y"
    assert m2["meta"]["adopted"] is True
