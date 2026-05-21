"""Shared fixtures: each test gets a fresh on-disk SQLite DB so migrations + foreign keys all behave like production."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from calyx.db import Database


@pytest.fixture
def db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    try:
        d = Database(tmp.name)
        yield d
        d.conn.close()
    finally:
        Path(tmp.name).unlink(missing_ok=True)


@pytest.fixture
def group_with_two_members(db):
    """Returns (group_id, host_user_id, member_user_id)."""
    host = db.create_user("host@test.local", "Host User")
    member = db.create_user("member@test.local", "Member User")
    gid = db.create_group(host, "Test Group")
    db.add_group_member(gid, host)
    db.add_group_member(gid, member)
    return gid, host, member
