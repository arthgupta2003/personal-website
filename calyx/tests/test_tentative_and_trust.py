"""Tentative (no-time) events + group-wide edit/delete trust."""
from __future__ import annotations


def test_event_can_be_created_with_no_start_time(db, group_with_two_members):
    gid, host, _ = group_with_two_members
    eid = db.add_group_event(gid, host, "Maybe a picnic", start_time="")
    fetched = db.get_group_event_by_id(eid)
    assert fetched is not None
    assert fetched["title"] == "Maybe a picnic"
    assert fetched["start_time"] in (None, "")


def test_tentative_events_show_up_in_user_event_list(db, group_with_two_members):
    gid, host, _ = group_with_two_members
    db.add_group_event(gid, host, "Tentative", start_time="")
    db.add_group_event(gid, host, "Dated", start_time="2030-01-01T10:00:00")
    titles = {e["title"] for e in db.get_group_user_events(gid)}
    assert titles == {"Tentative", "Dated"}


def test_non_creator_member_can_edit_event(db, group_with_two_members):
    """We trust the group: any member may edit any event."""
    gid, host, member = group_with_two_members
    eid = db.add_group_event(gid, host, "Original", "2030-01-01T10:00:00")
    ok = db.update_group_event(
        eid, member, title="Edited by member",
        start_time="2030-01-01T11:00:00",
        end_time="", location="", url="", notes="",
    )
    assert ok is True
    after = db.get_group_event_by_id(eid)
    assert after["title"] == "Edited by member"


def test_non_creator_member_can_delete_event(db, group_with_two_members):
    gid, host, member = group_with_two_members
    eid = db.add_group_event(gid, host, "Doomed", "2030-01-01T10:00:00")
    db.delete_group_event(eid, member)
    assert db.get_group_event_by_id(eid) is None


def test_non_member_cannot_edit_or_delete(db, group_with_two_members):
    """Non-members get nothing — the trust extends to the group, not strangers."""
    gid, host, _ = group_with_two_members
    outsider = db.create_user("outsider@test.local", "Outsider")
    eid = db.add_group_event(gid, host, "Members only", "2030-01-01T10:00:00")
    ok = db.update_group_event(
        eid, outsider, title="Hacked",
        start_time="2030-01-01T11:00:00",
        end_time="", location="", url="", notes="",
    )
    assert ok is False
    db.delete_group_event(eid, outsider)
    assert db.get_group_event_by_id(eid) is not None
