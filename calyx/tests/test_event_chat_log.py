"""Chat thread doubles as event log: system entries vs user messages."""
from __future__ import annotations


def test_system_log_entries_carry_kind(db, group_with_two_members):
    gid, host, _ = group_with_two_members
    eid = db.add_group_event(gid, host, "Walk", "2030-01-01T10:00:00")
    ev = f"grp_evt_{eid}"
    db.add_event_system_log(ev, host, "Arth added this event")
    db.add_event_comment(ev, host, "Hi everyone")
    entries = db.get_event_comments(ev)
    assert len(entries) == 2
    assert entries[0]["kind"] == "system"
    assert entries[0]["body"] == "Arth added this event"
    assert entries[1]["kind"] == "message"
    assert entries[1]["body"] == "Hi everyone"


def test_system_entries_cannot_be_deleted_via_user_endpoint(db, group_with_two_members):
    gid, host, _ = group_with_two_members
    eid = db.add_group_event(gid, host, "Walk", "2030-01-01T10:00:00")
    ev = f"grp_evt_{eid}"
    sid = db.add_event_system_log(ev, host, "Arth is going")
    # Even the user who triggered the log can't delete the system entry.
    assert db.delete_event_comment(sid, host) is False
    assert len(db.get_event_comments(ev)) == 1


def test_user_messages_still_deletable(db, group_with_two_members):
    gid, host, _ = group_with_two_members
    eid = db.add_group_event(gid, host, "Walk", "2030-01-01T10:00:00")
    ev = f"grp_evt_{eid}"
    cid = db.add_event_comment(ev, host, "delete me")
    assert db.delete_event_comment(cid, host) is True
    assert db.get_event_comments(ev) == []
