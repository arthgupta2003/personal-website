"""Auth model: users only see groups they belong to. ID-enumeration leaks closed."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _client_for(db, monkeypatch):
    """Spin up the FastAPI app with our test DB and disable Settings dependencies."""
    monkeypatch.setenv("CALYX_DB_PATH", db.db_path)
    # Reset the module-level db singleton if present
    import calyx.dashboard.app as mod
    mod._db = db
    return TestClient(mod.app)


def _signin(client, db, email: str, name: str = "Test") -> int:
    """Create a user and set the auth cookie so requests are 'logged in'."""
    uid = db.create_user(email, name)
    u = db.get_user(uid)
    client.cookies.set("recom_token", u["user_token"])
    return uid


def test_groups_index_only_lists_user_groups(db, monkeypatch):
    client = _client_for(db, monkeypatch)
    me = _signin(client, db, "me@test.local", "Me")
    other = db.create_user("other@test.local", "Other")
    my_group = db.create_group(me, "My Group")
    db.add_group_member(my_group, me)
    other_group = db.create_group(other, "Private Group")
    db.add_group_member(other_group, other)

    resp = client.get("/groups", follow_redirects=False)
    assert resp.status_code == 200
    assert "My Group" in resp.text
    assert "Private Group" not in resp.text


def test_groups_index_redirects_when_logged_out(db, monkeypatch):
    client = _client_for(db, monkeypatch)
    resp = client.get("/groups", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/login" in resp.headers.get("location", "")


def test_group_page_404s_for_non_members(db, monkeypatch):
    client = _client_for(db, monkeypatch)
    me = _signin(client, db, "me@test.local", "Me")
    other = db.create_user("other@test.local", "Other")
    gid = db.create_group(other, "Stranger Group")
    db.add_group_member(gid, other)

    resp = client.get(f"/group/{gid}", follow_redirects=False)
    assert resp.status_code == 404
    assert "Stranger Group" not in resp.text


def test_group_page_shows_when_member(db, monkeypatch):
    client = _client_for(db, monkeypatch)
    me = _signin(client, db, "me@test.local", "Me")
    gid = db.create_group(me, "Mine")
    db.add_group_member(gid, me)

    resp = client.get(f"/group/{gid}")
    assert resp.status_code == 200
    assert "Mine" in resp.text


def test_join_post_requires_valid_invite_code(db, monkeypatch):
    client = _client_for(db, monkeypatch)
    me = _signin(client, db, "me@test.local", "Me")
    other = db.create_user("other@test.local", "Other")
    gid = db.create_group(other, "Secret")
    db.add_group_member(gid, other)
    g = db.get_group_by_id(gid)

    # Wrong code — should not be added.
    resp = client.post(f"/group/{gid}/join", data={"invite_code": "wrongcode"}, follow_redirects=False)
    assert resp.status_code == 403
    assert not db.is_group_member(gid, me)

    # Missing code entirely
    resp = client.post(f"/group/{gid}/join", data={}, follow_redirects=False)
    assert resp.status_code == 403
    assert not db.is_group_member(gid, me)

    # Right code — joins.
    resp = client.post(f"/group/{gid}/join",
                       data={"invite_code": g["invite_code"]}, follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert db.is_group_member(gid, me)
