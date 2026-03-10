from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from recom.models import CostRecord, Event, InterestProfile, RankedEvent, SourceStat

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT DEFAULT '',
    spotify_token_file TEXT,
    youtube_token_file TEXT,
    gmail_token_file TEXT,
    interests_file TEXT,
    bucket_list_file TEXT,
    location_query TEXT DEFAULT 'Cambridge, MA',
    zip_code TEXT DEFAULT '02139',
    created_at TEXT NOT NULL,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER DEFAULT 1,
    timestamp TEXT NOT NULL,
    interest_profile_json TEXT,
    cost_total REAL DEFAULT 0,
    tokens_in_total INTEGER DEFAULT 0,
    tokens_out_total INTEGER DEFAULT 0,
    model_used TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    url TEXT DEFAULT '',
    start_time TEXT,
    end_time TEXT,
    location_name TEXT DEFAULT '',
    location_address TEXT DEFAULT '',
    is_online INTEGER DEFAULT 0,
    price TEXT,
    attendee_count INTEGER,
    category TEXT,
    organizer TEXT,
    image_url TEXT,
    raw_json TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS rankings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    score REAL DEFAULT 0,
    interest_score REAL DEFAULT 0,
    social_score REAL DEFAULT 0,
    urgency_score REAL DEFAULT 0,
    logistics_score REAL DEFAULT 0,
    friend_score REAL DEFAULT 0,
    discovery_score REAL DEFAULT 0,
    quality_score REAL DEFAULT 0,
    vibe TEXT DEFAULT 'mixed',
    match_reason TEXT DEFAULT '',
    keep INTEGER DEFAULT 1,
    filter_reason TEXT,
    event_type TEXT DEFAULT 'event',
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    call_type TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS source_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    source_name TEXT NOT NULL,
    events_found INTEGER DEFAULT 0,
    error_message TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS ingest_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    item_count INTEGER DEFAULT 0,
    detail TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS attended (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    attended_at TEXT NOT NULL,
    rating INTEGER,
    notes TEXT,
    user_id INTEGER DEFAULT 1,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS rsvps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('going', 'maybe', 'cant')),
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    UNIQUE(user_id, event_id)
);

CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS group_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    joined_at TEXT NOT NULL,
    FOREIGN KEY (group_id) REFERENCES groups(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    UNIQUE(group_id, user_id)
);
"""


class Database:
    def __init__(self, db_path: str = "recom.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()

    def _migrate(self):
        """Add columns that may be missing from older databases."""
        cur = self.conn.execute("PRAGMA table_info(users)")
        user_cols = {row["name"] for row in cur.fetchall()}
        if "user_token" not in user_cols:
            self.conn.execute("ALTER TABLE users ADD COLUMN user_token TEXT")
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_token ON users(user_token)")
            self.conn.commit()

        cur = self.conn.execute("PRAGMA table_info(attended)")
        attended_cols = {row["name"] for row in cur.fetchall()}
        if "user_id" not in attended_cols:
            self.conn.execute("ALTER TABLE attended ADD COLUMN user_id INTEGER DEFAULT 1")
            self.conn.commit()

        # Generate tokens for existing users that don't have one
        rows = self.conn.execute("SELECT id FROM users WHERE user_token IS NULL").fetchall()
        for row in rows:
            token = secrets.token_hex(4)
            self.conn.execute("UPDATE users SET user_token = ? WHERE id = ?", (token, row["id"]))
        if rows:
            self.conn.commit()

    def close(self):
        self.conn.close()

    # --- User management ---

    def create_user(self, email: str, name: str = "") -> int:
        token = secrets.token_hex(4)
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO users (email, name, user_token, created_at) VALUES (?, ?, ?, ?)",
            (email, name, token, datetime.now().isoformat()),
        )
        self.conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = self.conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        return row["id"]

    def get_user(self, user_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_email(self, email: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None

    def get_users(self, active_only: bool = True) -> list[dict]:
        q = "SELECT * FROM users"
        if active_only:
            q += " WHERE active = 1"
        return [dict(r) for r in self.conn.execute(q).fetchall()]

    def update_user(self, user_id: int, **kwargs):
        for key, val in kwargs.items():
            self.conn.execute(f"UPDATE users SET {key} = ? WHERE id = ?", (val, user_id))
        self.conn.commit()

    # --- Runs ---

    def create_run(self, model: str, user_id: int = 1) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (user_id, timestamp, model_used) VALUES (?, ?, ?)",
            (user_id, datetime.now().isoformat(), model),
        )
        self.conn.commit()
        return cur.lastrowid

    def save_interest_profile(self, run_id: int, profile: InterestProfile):
        self.conn.execute(
            "UPDATE runs SET interest_profile_json = ? WHERE id = ?",
            (profile.model_dump_json(), run_id),
        )
        self.conn.commit()

    def save_events(self, run_id: int, events: list[Event]):
        for e in events:
            self.conn.execute(
                """INSERT INTO events (run_id, event_id, source, title, description, url,
                   start_time, end_time, location_name, location_address, is_online,
                   price, attendee_count, category, organizer, image_url, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, e.id, e.source.value, e.title, e.description, e.url,
                    e.start_time.isoformat() if e.start_time else None,
                    e.end_time.isoformat() if e.end_time else None,
                    e.location_name, e.location_address, int(e.is_online),
                    e.price, e.attendee_count, e.category, e.organizer, e.image_url,
                    e.model_dump_json(),
                ),
            )
        self.conn.commit()

    def save_rankings(self, run_id: int, rankings: list[RankedEvent]):
        for r in rankings:
            self.conn.execute(
                """INSERT INTO rankings (run_id, event_id, score, interest_score,
                   social_score, urgency_score, logistics_score, friend_score,
                   discovery_score, quality_score, vibe,
                   match_reason, keep, filter_reason, event_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, r.event.id, r.score, r.interest_score,
                    r.social_score, r.urgency_score, r.logistics_score,
                    r.friend_score, r.discovery_score, r.quality_score,
                    r.vibe, r.match_reason, int(r.keep), r.filter_reason,
                    r.event_type,
                ),
            )
        self.conn.commit()

    def save_cost(self, run_id: int, cost: CostRecord):
        self.conn.execute(
            """INSERT INTO costs (run_id, call_type, model, tokens_in, tokens_out, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, cost.call_type, cost.model, cost.tokens_in, cost.tokens_out, cost.cost_usd),
        )
        # Update run totals
        self.conn.execute(
            """UPDATE runs SET
               cost_total = cost_total + ?,
               tokens_in_total = tokens_in_total + ?,
               tokens_out_total = tokens_out_total + ?
               WHERE id = ?""",
            (cost.cost_usd, cost.tokens_in, cost.tokens_out, run_id),
        )
        self.conn.commit()

    def save_ingest_stat(self, run_id: int, source: str, count: int, detail: str = ""):
        self.conn.execute(
            "INSERT INTO ingest_stats (run_id, source, item_count, detail) VALUES (?, ?, ?, ?)",
            (run_id, source, count, detail),
        )
        self.conn.commit()

    def get_ingest_stats(self, run_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM ingest_stats WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def save_source_stat(self, run_id: int, stat: SourceStat):
        self.conn.execute(
            "INSERT INTO source_stats (run_id, source_name, events_found, error_message) VALUES (?, ?, ?, ?)",
            (run_id, stat.source_name, stat.events_found, stat.error_message),
        )
        self.conn.commit()

    # --- Query methods for dashboard ---

    def get_runs(self) -> list[dict]:
        rows = self.conn.execute(
            """SELECT r.*, COUNT(DISTINCT e.event_id) as event_count,
               MAX(rk.score) as top_score
               FROM runs r
               LEFT JOIN events e ON e.run_id = r.id
               LEFT JOIN rankings rk ON rk.run_id = r.id AND rk.keep = 1
               GROUP BY r.id ORDER BY r.timestamp DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def get_run_events(self, run_id: int) -> list[dict]:
        rows = self.conn.execute(
            """SELECT e.*, rk.score, rk.interest_score, rk.social_score,
               rk.urgency_score, rk.logistics_score, rk.friend_score,
               rk.discovery_score, rk.quality_score, rk.vibe,
               rk.match_reason, rk.keep, rk.filter_reason,
               COALESCE(rk.event_type, 'event') as event_type
               FROM events e
               LEFT JOIN rankings rk ON rk.run_id = e.run_id AND rk.event_id = e.event_id
               WHERE e.run_id = ?
               ORDER BY rk.score DESC""",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_run_costs(self, run_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM costs WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_run_source_stats(self, run_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM source_stats WHERE run_id = ? ORDER BY events_found DESC", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_seen_event_ids(self, days: int = 30) -> set[str]:
        cutoff = datetime.now().isoformat()
        rows = self.conn.execute(
            """SELECT DISTINCT event_id FROM events e
               JOIN runs r ON r.id = e.run_id
               WHERE r.timestamp > datetime(?, '-' || ? || ' days')""",
            (cutoff, days),
        ).fetchall()
        return {r["event_id"] for r in rows}

    def get_cached_interest_profile(self, max_age_days: int = 7) -> InterestProfile | None:
        row = self.conn.execute(
            """SELECT interest_profile_json FROM runs
               WHERE interest_profile_json IS NOT NULL
               AND timestamp > datetime('now', '-' || ? || ' days')
               ORDER BY timestamp DESC LIMIT 1""",
            (max_age_days,),
        ).fetchone()
        if row and row["interest_profile_json"]:
            return InterestProfile.model_validate_json(row["interest_profile_json"])
        return None

    # --- User token ---

    def get_user_by_token(self, token: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE user_token = ?", (token,)
        ).fetchone()
        return dict(row) if row else None

    # --- RSVPs ---

    def set_rsvp(self, user_id: int, event_id: str, run_id: int, status: str):
        self.conn.execute(
            """INSERT INTO rsvps (user_id, event_id, run_id, status, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, event_id) DO UPDATE SET status = ?, created_at = ?""",
            (user_id, event_id, run_id, status, datetime.now().isoformat(),
             status, datetime.now().isoformat()),
        )
        self.conn.commit()

    def get_event_rsvps(self, event_id: str) -> list[dict]:
        rows = self.conn.execute(
            """SELECT u.name as user_name, r.status
               FROM rsvps r JOIN users u ON u.id = r.user_id
               WHERE r.event_id = ?""",
            (event_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_rsvps_for_events(self, event_ids: list[str]) -> dict[str, list[dict]]:
        if not event_ids:
            return {}
        placeholders = ",".join("?" for _ in event_ids)
        rows = self.conn.execute(
            f"""SELECT r.event_id, u.name as user_name, r.status
                FROM rsvps r JOIN users u ON u.id = r.user_id
                WHERE r.event_id IN ({placeholders})""",
            event_ids,
        ).fetchall()
        result: dict[str, list[dict]] = {}
        for r in rows:
            r = dict(r)
            eid = r.pop("event_id")
            result.setdefault(eid, []).append(r)
        return result

    # --- Groups ---

    def create_group(self, name: str, slug: str, creator_id: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO groups (name, slug, created_by, created_at) VALUES (?, ?, ?, ?)",
            (name, slug, creator_id, datetime.now().isoformat()),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_group_member(self, group_id: int, user_id: int):
        self.conn.execute(
            "INSERT OR IGNORE INTO group_members (group_id, user_id, joined_at) VALUES (?, ?, ?)",
            (group_id, user_id, datetime.now().isoformat()),
        )
        self.conn.commit()

    def get_group(self, slug: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM groups WHERE slug = ?", (slug,)
        ).fetchone()
        return dict(row) if row else None

    def get_group_members(self, group_id: int) -> list[dict]:
        rows = self.conn.execute(
            """SELECT u.* FROM users u
               JOIN group_members gm ON gm.user_id = u.id
               WHERE gm.group_id = ?""",
            (group_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_group_events(self, group_id: int, run_id: int | None = None) -> list[dict]:
        """Get union of kept events from all group members' latest runs."""
        members = self.get_group_members(group_id)
        if not members:
            return []
        member_ids = [m["id"] for m in members]
        placeholders = ",".join("?" for _ in member_ids)

        if run_id:
            # Use specified run
            rows = self.conn.execute(
                f"""SELECT e.*, rk.score, rk.interest_score, rk.social_score,
                       rk.urgency_score, rk.logistics_score, rk.friend_score,
                       rk.discovery_score, rk.quality_score, rk.vibe,
                       rk.match_reason, rk.keep, rk.filter_reason,
                       COALESCE(rk.event_type, 'event') as event_type
                   FROM events e
                   LEFT JOIN rankings rk ON rk.run_id = e.run_id AND rk.event_id = e.event_id
                   WHERE e.run_id = ? AND rk.keep = 1
                   ORDER BY rk.score DESC""",
                (run_id,),
            ).fetchall()
        else:
            # Get each member's latest run and union events
            rows = self.conn.execute(
                f"""SELECT e.*, rk.score, rk.interest_score, rk.social_score,
                       rk.urgency_score, rk.logistics_score, rk.friend_score,
                       rk.discovery_score, rk.quality_score, rk.vibe,
                       rk.match_reason, rk.keep, rk.filter_reason,
                       COALESCE(rk.event_type, 'event') as event_type
                   FROM events e
                   LEFT JOIN rankings rk ON rk.run_id = e.run_id AND rk.event_id = e.event_id
                   JOIN runs r ON r.id = e.run_id
                   WHERE r.user_id IN ({placeholders}) AND rk.keep = 1
                   AND r.id IN (
                       SELECT MAX(r2.id) FROM runs r2
                       WHERE r2.user_id IN ({placeholders})
                       GROUP BY r2.user_id
                   )
                   ORDER BY rk.score DESC""",
                member_ids + member_ids,
            ).fetchall()

        # Deduplicate by event_id, keeping highest score
        seen: dict[str, dict] = {}
        for r in rows:
            d = dict(r)
            eid = d["event_id"]
            if eid not in seen or (d.get("score") or 0) > (seen[eid].get("score") or 0):
                seen[eid] = d
        return sorted(seen.values(), key=lambda x: -(x.get("score") or 0))
