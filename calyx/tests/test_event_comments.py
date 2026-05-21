"""Comment thread per group event: add, list, delete-by-author-only."""
from __future__ import annotations


def test_add_and_list_comments(db, group_with_two_members):
    gid, host, member = group_with_two_members
    eid = db.add_group_event(gid, host, "Walk", "2030-01-01T10:00:00")
    ev = f"grp_evt_{eid}"
    db.add_event_comment(ev, host, "What time leaving?")
    db.add_event_comment(ev, member, "Around 9.")
    comments = db.get_event_comments(ev)
    assert [c["body"] for c in comments] == ["What time leaving?", "Around 9."]
    assert comments[0]["user_id"] == host
    assert comments[1]["user_id"] == member


def test_author_can_delete_own_comment(db, group_with_two_members):
    gid, host, member = group_with_two_members
    eid = db.add_group_event(gid, host, "Walk", "2030-01-01T10:00:00")
    ev = f"grp_evt_{eid}"
    cid = db.add_event_comment(ev, member, "Mistake")
    assert db.delete_event_comment(cid, member) is True
    assert db.get_event_comments(ev) == []


def test_non_author_cannot_delete_comment(db, group_with_two_members):
    gid, host, member = group_with_two_members
    eid = db.add_group_event(gid, host, "Walk", "2030-01-01T10:00:00")
    ev = f"grp_evt_{eid}"
    cid = db.add_event_comment(ev, member, "Mine")
    # host (not author) tries to delete → no-op
    assert db.delete_event_comment(cid, host) is False
    assert len(db.get_event_comments(ev)) == 1
