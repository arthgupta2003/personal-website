"""Phone numbers on users + propagation to group event listings."""
from __future__ import annotations


def test_phone_persists_via_update_user(db):
    uid = db.create_user("p@test.local", "P")
    db.update_user(uid, phone="+1 555 123 4567")
    u = db.get_user(uid)
    assert u["phone"] == "+1 555 123 4567"


def test_creator_phone_appears_in_group_event_rows(db, group_with_two_members):
    gid, host, _ = group_with_two_members
    db.update_user(host, phone="+1 617 555 0100")
    db.add_group_event(gid, host, "Hike", "2030-01-01T08:00:00")
    rows = db.get_group_user_events(gid)
    assert rows[0]["creator_phone"] == "+1 617 555 0100"


def test_group_members_carry_phone(db, group_with_two_members):
    gid, host, member = group_with_two_members
    db.update_user(member, phone="+1 415 222 3333")
    members = db.get_group_members(gid)
    by_id = {m["id"]: m for m in members}
    assert by_id[member]["phone"] == "+1 415 222 3333"
