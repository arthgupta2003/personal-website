"""Capacity-aware RSVPs: new RSVPs past the cap become 'waitlist', and the
oldest waitlister is promoted to 'going' when a slot frees up."""
from __future__ import annotations


def _add_event(db, group_id, host_id, capacity=None):
    return db.add_group_event(
        group_id, host_id, "Test event", "2030-01-01T10:00:00",
        capacity=capacity,
    )


def test_unlimited_capacity_keeps_everyone_going(db, group_with_two_members):
    gid, host, member = group_with_two_members
    extra = db.create_user("extra@test.local", "Extra")
    eid = _add_event(db, gid, host, capacity=None)
    ev = f"grp_evt_{eid}"
    for uid in (host, member, extra):
        effective, _prev = db.set_rsvp_with_capacity(uid, ev, 0, "going")
        assert effective == "going"
    assert db.count_rsvps(ev, "going") == 3
    assert db.count_rsvps(ev, "waitlist") == 0


def test_overflow_goes_to_waitlist(db, group_with_two_members):
    gid, host, member = group_with_two_members
    extra = db.create_user("extra@test.local", "Extra")
    eid = _add_event(db, gid, host, capacity=2)
    ev = f"grp_evt_{eid}"
    e1, _ = db.set_rsvp_with_capacity(host, ev, 0, "going")
    e2, _ = db.set_rsvp_with_capacity(member, ev, 0, "going")
    e3, _ = db.set_rsvp_with_capacity(extra, ev, 0, "going")
    assert e1 == "going"
    assert e2 == "going"
    assert e3 == "waitlist"
    assert db.count_rsvps(ev, "going") == 2
    assert db.count_rsvps(ev, "waitlist") == 1


def test_waitlist_promoted_when_going_drops(db, group_with_two_members):
    gid, host, member = group_with_two_members
    extra = db.create_user("extra@test.local", "Extra")
    eid = _add_event(db, gid, host, capacity=2)
    ev = f"grp_evt_{eid}"
    db.set_rsvp_with_capacity(host, ev, 0, "going")
    db.set_rsvp_with_capacity(member, ev, 0, "going")
    db.set_rsvp_with_capacity(extra, ev, 0, "going")
    # Host drops to maybe — extra should now be promoted.
    eff, prev = db.set_rsvp_with_capacity(host, ev, 0, "maybe")
    assert eff == "maybe"
    assert prev == "going"
    assert db.count_rsvps(ev, "going") == 2
    assert db.count_rsvps(ev, "waitlist") == 0
    extra_row = db.conn.execute(
        "SELECT status FROM rsvps WHERE user_id = ? AND event_id = ?",
        (extra, ev),
    ).fetchone()
    assert extra_row["status"] == "going"


def test_changing_existing_going_to_going_stays_going(db, group_with_two_members):
    """If you're already going, re-clicking 'going' must not bump you to waitlist."""
    gid, host, member = group_with_two_members
    eid = _add_event(db, gid, host, capacity=1)
    ev = f"grp_evt_{eid}"
    db.set_rsvp_with_capacity(host, ev, 0, "going")
    eff, _ = db.set_rsvp_with_capacity(host, ev, 0, "going")
    assert eff == "going"
    assert db.count_rsvps(ev, "going") == 1
