"""Prerequisites text + host (creator) metadata are persisted and surfaced
through the group-event accessors that the dashboard renders from."""
from __future__ import annotations


def test_prerequisites_persist_and_surface(db, group_with_two_members):
    gid, host, _ = group_with_two_members
    eid = db.add_group_event(
        gid, host, "Beehive hike", "2030-01-01T07:00:00",
        prerequisites="must be a comfortable swimmer; prior 7mi hike",
        capacity=10,
    )
    fetched = db.get_group_event_by_id(eid)
    assert fetched["prerequisites"] == "must be a comfortable swimmer; prior 7mi hike"
    assert fetched["capacity"] == 10


def test_creator_name_and_email_join_into_event_rows(db, group_with_two_members):
    gid, host, _ = group_with_two_members
    db.update_user(host, name="Bahar S.")
    eid = db.add_group_event(gid, host, "Traverse hike", "2030-01-01T07:00:00")
    rows = db.get_group_user_events(gid)
    assert len(rows) == 1
    r = rows[0]
    assert r["creator_name"] == "Bahar S."
    assert r["creator_email"] == "host@test.local"


def test_update_bumps_gcal_sequence(db, group_with_two_members):
    gid, host, _ = group_with_two_members
    eid = db.add_group_event(gid, host, "Original", "2030-01-01T07:00:00")
    before = db.get_group_event_by_id(eid)
    assert (before.get("gcal_sequence") or 0) == 0
    ok = db.update_group_event(
        eid, host, title="Updated", start_time="2030-01-01T08:00:00",
        end_time="", location="", url="", notes="",
    )
    assert ok
    after = db.get_group_event_by_id(eid)
    assert (after.get("gcal_sequence") or 0) == 1
