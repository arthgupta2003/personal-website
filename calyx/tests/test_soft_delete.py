"""Soft-delete: events vanish from live queries but stay restorable."""
from __future__ import annotations


def test_delete_hides_event_from_live_queries(db, group_with_two_members):
    gid, host, _ = group_with_two_members
    eid = db.add_group_event(gid, host, "Vinyasa Flow", "2030-01-01T10:00:00")
    db.delete_group_event(eid, host)
    # Vanished from default lookups.
    assert db.get_group_event_by_id(eid) is None
    assert db.get_group_user_events(gid) == []
    # Still fetchable when include_deleted=True.
    raw = db.get_group_event_by_id(eid, include_deleted=True)
    assert raw is not None
    assert raw["deleted_at"]


def test_deleted_events_show_in_archive(db, group_with_two_members):
    gid, host, _ = group_with_two_members
    eid = db.add_group_event(gid, host, "Archived", "2030-01-01T10:00:00")
    db.delete_group_event(eid, host)
    archived = db.get_deleted_group_events(gid)
    assert len(archived) == 1
    assert archived[0]["title"] == "Archived"
    assert archived[0]["deleted_at"]


def test_restore_brings_event_back(db, group_with_two_members):
    gid, host, member = group_with_two_members
    eid = db.add_group_event(gid, host, "Bring back", "2030-01-01T10:00:00")
    db.delete_group_event(eid, host)
    # Any member can restore.
    assert db.restore_group_event(eid, member) is True
    fetched = db.get_group_event_by_id(eid)
    assert fetched is not None
    assert fetched["title"] == "Bring back"
    assert fetched.get("deleted_at") in (None, "")
    assert db.get_deleted_group_events(gid) == []


def test_purge_drops_row_permanently(db, group_with_two_members):
    gid, host, _ = group_with_two_members
    eid = db.add_group_event(gid, host, "Doomed", "2030-01-01T10:00:00")
    db.delete_group_event(eid, host)
    assert db.purge_group_event(eid, host) is True
    # Gone for good — not even include_deleted brings it back.
    assert db.get_group_event_by_id(eid, include_deleted=True) is None


def test_non_member_cannot_restore_or_purge(db, group_with_two_members):
    gid, host, _ = group_with_two_members
    outsider = db.create_user("outsider@test.local", "Outsider")
    eid = db.add_group_event(gid, host, "Members only", "2030-01-01T10:00:00")
    db.delete_group_event(eid, host)
    assert db.restore_group_event(eid, outsider) is False
    assert db.purge_group_event(eid, outsider) is False
    # Still archived and untouched.
    assert len(db.get_deleted_group_events(gid)) == 1
