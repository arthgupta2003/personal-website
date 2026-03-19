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
    lat REAL,
    lon REAL,
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
    display_name TEXT,
    slug TEXT DEFAULT '',
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS group_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    created_by INTEGER NOT NULL,
    title TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT,
    location TEXT DEFAULT '',
    url TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (group_id) REFERENCES groups(id),
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

CREATE TABLE IF NOT EXISTS user_interests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    keyword TEXT NOT NULL,
    confidence REAL DEFAULT 0.9,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    UNIQUE(user_id, keyword)
);

CREATE TABLE IF NOT EXISTS user_bucket_list (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    activity TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    UNIQUE(user_id, activity)
);

CREATE TABLE IF NOT EXISTS taste_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    label TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    elo_rating REAL NOT NULL DEFAULT 1400,
    matchup_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    UNIQUE(user_id, label)
);

CREATE TABLE IF NOT EXISTS taste_matchups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    item_a_id INTEGER NOT NULL,
    item_b_id INTEGER NOT NULL,
    winner_id INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (item_a_id) REFERENCES taste_items(id),
    FOREIGN KEY (item_b_id) REFERENCES taste_items(id),
    FOREIGN KEY (winner_id) REFERENCES taste_items(id)
);

CREATE TABLE IF NOT EXISTS impressions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    event_id TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    channel TEXT NOT NULL DEFAULT 'calendar',
    shown_at TEXT NOT NULL,
    clicked INTEGER DEFAULT 0,
    acted INTEGER DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS steering (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    target_type TEXT NOT NULL,
    target_value TEXT NOT NULL,
    action TEXT NOT NULL,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, target_type, target_value)
);

CREATE TABLE IF NOT EXISTS travel_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    city TEXT NOT NULL,
    lat REAL,
    lon REAL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, city, start_date)
);

CREATE TABLE IF NOT EXISTS source_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL UNIQUE,
    last_scraped TEXT NOT NULL,
    events_count INTEGER DEFAULT 0,
    refresh_interval_hours REAL DEFAULT 24.0
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_picks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    day TEXT NOT NULL,
    score INTEGER NOT NULL DEFAULT 0,
    vibe TEXT DEFAULT 'mixed',
    rank_in_day INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (run_id) REFERENCES runs(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    UNIQUE(run_id, event_id)
);

CREATE TABLE IF NOT EXISTS gcal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    gcal_event_id TEXT NOT NULL,
    gcal_calendar_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(event_id)
);

CREATE TABLE IF NOT EXISTS retro_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    query TEXT NOT NULL,
    db_result_count INTEGER NOT NULL DEFAULT 0,
    web_result_count INTEGER NOT NULL DEFAULT 0,
    web_results_json TEXT,
    analysis TEXT,
    gap_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS guest_rsvps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    guest_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('going', 'maybe', 'cant')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (group_id) REFERENCES groups(id),
    UNIQUE(group_id, event_id, guest_name)
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

        # Migrate: seed user_interests and user_bucket_list from flat files if empty
        count = self.conn.execute("SELECT COUNT(*) FROM user_interests").fetchone()[0]
        if count == 0:
            interests_path = Path("my_interests.txt")
            if interests_path.exists():
                for line in interests_path.read_text().splitlines():
                    kw = line.strip()
                    if kw and not kw.startswith("#"):
                        try:
                            self.conn.execute(
                                "INSERT OR IGNORE INTO user_interests (user_id, keyword, confidence, created_at) VALUES (1, ?, 0.9, ?)",
                                (kw, datetime.now().isoformat()),
                            )
                        except Exception:
                            pass
                self.conn.commit()

        count = self.conn.execute("SELECT COUNT(*) FROM user_bucket_list").fetchone()[0]
        if count == 0:
            bucket_path = Path("bucket_list.txt")
            if bucket_path.exists():
                for line in bucket_path.read_text().splitlines():
                    item = line.strip()
                    if item and not item.startswith("#"):
                        try:
                            self.conn.execute(
                                "INSERT OR IGNORE INTO user_bucket_list (user_id, activity, created_at) VALUES (1, ?, ?)",
                                (item, datetime.now().isoformat()),
                            )
                        except Exception:
                            pass
                self.conn.commit()

        # Migrate: add lat/lon columns to events if missing
        cur = self.conn.execute("PRAGMA table_info(events)")
        event_cols = {row["name"] for row in cur.fetchall()}
        if "lat" not in event_cols:
            self.conn.execute("ALTER TABLE events ADD COLUMN lat REAL")
            self.conn.execute("ALTER TABLE events ADD COLUMN lon REAL")
            self.conn.commit()

        cur = self.conn.execute("PRAGMA table_info(users)")
        user_cols2 = {row["name"] for row in cur.fetchall()}
        if "home_lat" not in user_cols2:
            self.conn.execute("ALTER TABLE users ADD COLUMN home_lat REAL")
            self.conn.execute("ALTER TABLE users ADD COLUMN home_lon REAL")
            self.conn.commit()

        cur = self.conn.execute("PRAGMA table_info(source_stats)")
        ss_cols = {row["name"] for row in cur.fetchall()}
        if "duration_seconds" not in ss_cols:
            self.conn.execute("ALTER TABLE source_stats ADD COLUMN duration_seconds REAL")
            self.conn.commit()

        # impressions table
        try:
            self.conn.execute("SELECT 1 FROM impressions LIMIT 1")
        except Exception:
            self.conn.execute("""CREATE TABLE IF NOT EXISTS impressions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 1,
                event_id TEXT NOT NULL,
                run_id INTEGER NOT NULL,
                channel TEXT NOT NULL DEFAULT 'calendar',
                shown_at TEXT NOT NULL,
                clicked INTEGER DEFAULT 0,
                acted INTEGER DEFAULT 0,
                FOREIGN KEY (run_id) REFERENCES runs(id)
            )""")
            self.conn.commit()

        # steering table
        try:
            self.conn.execute("SELECT 1 FROM steering LIMIT 1")
        except Exception:
            self.conn.execute("""CREATE TABLE IF NOT EXISTS steering (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 1,
                target_type TEXT NOT NULL,
                target_value TEXT NOT NULL,
                action TEXT NOT NULL,
                expires_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, target_type, target_value)
            )""")
            self.conn.commit()

        # travel_plans table
        try:
            self.conn.execute("SELECT 1 FROM travel_plans LIMIT 1")
        except Exception:
            self.conn.execute("""CREATE TABLE IF NOT EXISTS travel_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 1,
                city TEXT NOT NULL,
                lat REAL,
                lon REAL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, city, start_date)
            )""")
            self.conn.commit()

        # spent_amount on attended
        cur = self.conn.execute("PRAGMA table_info(attended)")
        att_cols = {row["name"] for row in cur.fetchall()}
        if "spent_amount" not in att_cols:
            self.conn.execute("ALTER TABLE attended ADD COLUMN spent_amount REAL")
            self.conn.commit()

        # source_cache table
        try:
            self.conn.execute("SELECT 1 FROM source_cache LIMIT 1")
        except Exception:
            self.conn.execute("""CREATE TABLE IF NOT EXISTS source_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL UNIQUE,
                last_scraped TEXT NOT NULL,
                events_count INTEGER DEFAULT 0,
                refresh_interval_hours REAL DEFAULT 24.0
            )""")
            self.conn.commit()

        # app_settings table
        try:
            self.conn.execute("SELECT 1 FROM app_settings LIMIT 1")
        except Exception:
            self.conn.execute("""CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )""")
            self.conn.commit()

        # welcome_sent column on users
        cur = self.conn.execute("PRAGMA table_info(users)")
        user_cols3 = {row["name"] for row in cur.fetchall()}
        if "welcome_sent" not in user_cols3:
            self.conn.execute("ALTER TABLE users ADD COLUMN welcome_sent INTEGER DEFAULT 0")
            # Mark existing users as already welcomed (don't retroactively send)
            self.conn.execute("UPDATE users SET welcome_sent = 1")
            self.conn.commit()

        # notes column on runs (stores step timings JSON)
        cur = self.conn.execute("PRAGMA table_info(runs)")
        run_cols = {row["name"] for row in cur.fetchall()}
        if "notes" not in run_cols:
            self.conn.execute("ALTER TABLE runs ADD COLUMN notes TEXT")
            self.conn.commit()

        # ping_log table for group pings from email
        try:
            self.conn.execute("SELECT 1 FROM ping_log LIMIT 1")
        except Exception:
            self.conn.execute("""CREATE TABLE IF NOT EXISTS ping_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                group_id INTEGER NOT NULL,
                sent_at TEXT NOT NULL
            )""")
            self.conn.commit()

        # bucket list status column
        cur = self.conn.execute("PRAGMA table_info(user_bucket_list)")
        bl_cols = {row["name"] for row in cur.fetchall()}
        if "status" not in bl_cols:
            self.conn.execute("ALTER TABLE user_bucket_list ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
            self.conn.commit()
        if "completed_at" not in bl_cols:
            self.conn.execute("ALTER TABLE user_bucket_list ADD COLUMN completed_at TEXT")
            self.conn.commit()

        # groups: add display_name and invite_code columns
        cur = self.conn.execute("PRAGMA table_info(groups)")
        group_cols = {row["name"] for row in cur.fetchall()}
        if "invite_code" not in group_cols:
            self.conn.execute("ALTER TABLE groups ADD COLUMN invite_code TEXT")
            self.conn.commit()
            # Backfill existing groups with invite codes
            import secrets as _secrets
            for row in self.conn.execute("SELECT id FROM groups WHERE invite_code IS NULL").fetchall():
                self.conn.execute("UPDATE groups SET invite_code = ? WHERE id = ?",
                                  (_secrets.token_hex(3), row["id"]))
            self.conn.commit()
        if "display_name" not in group_cols:
            self.conn.execute("ALTER TABLE groups ADD COLUMN display_name TEXT")
            self.conn.commit()
            # Recreate groups table without UNIQUE on slug
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS groups_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    display_name TEXT,
                    slug TEXT DEFAULT '',
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (created_by) REFERENCES users(id)
                );
                INSERT INTO groups_new SELECT id, name, display_name, slug, created_by, created_at FROM groups;
                DROP TABLE groups;
                ALTER TABLE groups_new RENAME TO groups;
            """)
            self.conn.commit()

        # group_events table
        self.conn.execute("""CREATE TABLE IF NOT EXISTS group_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            created_by INTEGER NOT NULL,
            title TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            location TEXT DEFAULT '',
            url TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (group_id) REFERENCES groups(id),
            FOREIGN KEY (created_by) REFERENCES users(id)
        )""")
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
                   price, attendee_count, category, organizer, image_url, lat, lon, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, e.id, e.source.value, e.title, e.description, e.url,
                    e.start_time.isoformat() if e.start_time else None,
                    e.end_time.isoformat() if e.end_time else None,
                    e.location_name, e.location_address, int(e.is_online),
                    e.price, e.attendee_count, e.category, e.organizer, e.image_url,
                    e.lat, e.lon,
                    e.model_dump_json(),
                ),
            )
        self.conn.commit()

    def create_user_event(self, user_id: int, title: str, start_time: str,
                          location: str = "", url: str = "",
                          description: str = "") -> str:
        """Create a user-submitted event, auto-RSVP as going.
        Returns the event_id."""
        import hashlib
        event_id = f"user_{user_id}_{hashlib.md5(f'{title}{start_time}'.encode()).hexdigest()[:12]}"
        # Use the user's latest run_id (needed for event table FK)
        run = self.get_user_latest_run(user_id)
        run_id = run["id"] if run else 1
        self.conn.execute(
            """INSERT OR IGNORE INTO events
               (run_id, event_id, source, title, description, url,
                start_time, location_name, is_online, raw_json)
               VALUES (?, ?, 'user', ?, ?, ?, ?, ?, 0, '{}')""",
            (run_id, event_id, title, description, url, start_time, location),
        )
        # Auto-RSVP as going
        self.set_rsvp(user_id, event_id, run_id, "going")
        self.conn.commit()
        return event_id

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

    # --- Manual interests (DB-backed, replaces my_interests.txt) ---

    def get_user_manual_interests(self, user_id: int = 1) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM user_interests WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def add_user_manual_interest(self, keyword: str, user_id: int = 1, confidence: float = 0.9) -> bool:
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO user_interests (user_id, keyword, confidence, created_at) VALUES (?, ?, ?, ?)",
                (user_id, keyword.strip(), confidence, datetime.now().isoformat()),
            )
            self.conn.commit()
            return True
        except Exception:
            return False

    def delete_user_manual_interest(self, item_id: int, user_id: int = 1) -> bool:
        self.conn.execute(
            "DELETE FROM user_interests WHERE id = ? AND user_id = ?",
            (item_id, user_id),
        )
        self.conn.commit()
        return True

    def get_manual_interest_keywords(self, user_id: int = 1) -> list[str]:
        """Return just the keyword strings for use in the pipeline."""
        rows = self.conn.execute(
            "SELECT keyword FROM user_interests WHERE user_id = ? ORDER BY keyword",
            (user_id,),
        ).fetchall()
        return [r["keyword"] for r in rows]

    # --- Bucket list (DB-backed, replaces bucket_list.txt) ---

    def get_user_bucket_list(self, user_id: int = 1) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM user_bucket_list WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def add_bucket_item(self, activity: str, user_id: int = 1) -> bool:
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO user_bucket_list (user_id, activity, created_at) VALUES (?, ?, ?)",
                (user_id, activity.strip(), datetime.now().isoformat()),
            )
            self.conn.commit()
            return True
        except Exception:
            return False

    def delete_bucket_item(self, item_id: int, user_id: int = 1) -> bool:
        self.conn.execute(
            "DELETE FROM user_bucket_list WHERE id = ? AND user_id = ?",
            (item_id, user_id),
        )
        self.conn.commit()
        return True

    def get_bucket_list_activities(self, user_id: int = 1) -> list[str]:
        """Return just the activity strings for use in the pipeline."""
        rows = self.conn.execute(
            "SELECT activity FROM user_bucket_list WHERE user_id = ? ORDER BY activity",
            (user_id,),
        ).fetchall()
        return [r["activity"] for r in rows]

    def update_bucket_item_status(self, item_id: int, status: str, user_id: int = 1) -> bool:
        completed_at = datetime.now().isoformat() if status == "done" else None
        self.conn.execute(
            "UPDATE user_bucket_list SET status = ?, completed_at = ? WHERE id = ? AND user_id = ?",
            (status, completed_at, item_id, user_id),
        )
        self.conn.commit()
        return True

    # --- App settings (key-value store) ---

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        self.conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, datetime.now().isoformat()),
        )
        self.conn.commit()

    def save_source_stat(self, run_id: int, stat: SourceStat, duration_seconds: float | None = None):
        self.conn.execute(
            "INSERT INTO source_stats (run_id, source_name, events_found, error_message, duration_seconds) VALUES (?, ?, ?, ?, ?)",
            (run_id, stat.source_name, stat.events_found, stat.error_message, duration_seconds),
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

    def compute_daily_picks(self, run_id: int, user_id: int,
                            min_score: int = 40, max_per_day: int = 5,
                            max_per_vibe: int = 3) -> int:
        """Compute curated daily picks from a run and store in daily_picks table.
        Returns number of picks stored."""
        from collections import defaultdict
        # Clear old picks for this run
        self.conn.execute("DELETE FROM daily_picks WHERE run_id = ?", (run_id,))

        events = self.get_run_events(run_id)
        kept = [e for e in events if e.get("keep") and (e.get("score") or 0) >= min_score]

        # Group by day, sort by score
        day_groups: dict[str, list] = defaultdict(list)
        for e in sorted(kept, key=lambda x: -(x.get("score") or 0)):
            st = e.get("start_time")
            day = st[:10] if st else "9999-99-99"
            day_groups[day].append(e)

        total = 0
        for day in sorted(day_groups.keys()):
            picked = []
            vibe_counts: dict[str, int] = defaultdict(int)
            for e in day_groups[day]:
                if len(picked) >= max_per_day:
                    break
                v = e.get("vibe", "mixed")
                if vibe_counts[v] >= max_per_vibe:
                    continue
                picked.append(e)
                vibe_counts[v] += 1
            for rank, e in enumerate(picked, 1):
                self.conn.execute(
                    """INSERT OR REPLACE INTO daily_picks
                       (run_id, user_id, event_id, day, score, vibe, rank_in_day)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, user_id, e["event_id"], day,
                     int(e.get("score") or 0), e.get("vibe", "mixed"), rank),
                )
                total += 1
        self.conn.commit()
        return total

    def get_daily_picks(self, run_id: int) -> list[dict]:
        """Get curated daily picks for a run with full event data."""
        rows = self.conn.execute(
            """SELECT dp.day, dp.rank_in_day, e.*, rk.score, rk.vibe,
                      rk.match_reason, rk.keep, rk.event_type
               FROM daily_picks dp
               JOIN events e ON e.event_id = dp.event_id AND e.run_id = dp.run_id
               LEFT JOIN rankings rk ON rk.event_id = dp.event_id AND rk.run_id = dp.run_id
               WHERE dp.run_id = ?
               GROUP BY dp.event_id
               ORDER BY dp.day, dp.rank_in_day""",
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

    def get_source_health(self, last_n_runs: int = 10) -> list[dict]:
        """Return per-source health stats across recent runs.
        Success = no error AND events_found > 0. Zero events with no error = failure."""
        rows = self.conn.execute(
            """SELECT ss.source_name,
                      COUNT(*) as run_count,
                      SUM(CASE WHEN ss.error_message IS NULL AND ss.events_found > 0 THEN 1 ELSE 0 END) as successes,
                      SUM(CASE WHEN ss.error_message IS NOT NULL OR ss.events_found = 0 THEN 1 ELSE 0 END) as failures,
                      AVG(ss.events_found) as avg_events,
                      MAX(ss.events_found) as max_events,
                      MIN(ss.events_found) as min_events,
                      MAX(ss.error_message) as last_error,
                      GROUP_CONCAT(ss.events_found ORDER BY r.timestamp DESC) as event_history,
                      AVG(ss.duration_seconds) as avg_duration_s
               FROM source_stats ss
               JOIN runs r ON r.id = ss.run_id
               WHERE ss.run_id IN (
                   SELECT id FROM runs ORDER BY timestamp DESC LIMIT ?
               )
               GROUP BY ss.source_name
               ORDER BY avg_events DESC""",
            (last_n_runs,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_source_stats_by_run(self, last_n_runs: int = 10) -> list[dict]:
        """Return source stats grouped by run for per-run drill-down."""
        rows = self.conn.execute(
            """SELECT r.id as run_id, r.timestamp, ss.source_name,
                      ss.events_found, ss.error_message, ss.duration_seconds
               FROM source_stats ss
               JOIN runs r ON r.id = ss.run_id
               WHERE ss.run_id IN (
                   SELECT id FROM runs ORDER BY timestamp DESC LIMIT ?
               )
               ORDER BY r.timestamp DESC, ss.events_found DESC""",
            (last_n_runs,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_event_source_breakdown(self, run_id: int) -> list[dict]:
        """Return per-source event counts from events table (granular, e.g. 'mit', 'harvard')."""
        rows = self.conn.execute(
            """SELECT source, COUNT(*) as cnt
               FROM events WHERE run_id = ?
               GROUP BY source ORDER BY cnt DESC""",
            (run_id,),
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
            f"""SELECT r.event_id, r.user_id, u.name as user_name, u.email as user_email, r.status
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

    # --- Google Calendar ---

    def set_gcal_event(self, event_id: str, gcal_event_id: str, gcal_calendar_id: str):
        self.conn.execute(
            """INSERT INTO gcal_events (event_id, gcal_event_id, gcal_calendar_id, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(event_id) DO UPDATE SET gcal_event_id = ?, gcal_calendar_id = ?""",
            (event_id, gcal_event_id, gcal_calendar_id, datetime.now().isoformat(),
             gcal_event_id, gcal_calendar_id),
        )
        self.conn.commit()

    def get_gcal_event(self, event_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM gcal_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_gcal_events(self, event_ids: list[str]) -> dict[str, dict]:
        if not event_ids:
            return {}
        placeholders = ",".join("?" for _ in event_ids)
        rows = self.conn.execute(
            f"SELECT * FROM gcal_events WHERE event_id IN ({placeholders})",
            event_ids,
        ).fetchall()
        return {r["event_id"]: dict(r) for r in rows}

    # --- Groups ---

    def create_group(self, creator_id: int, display_name: str = "") -> int:
        import secrets as _secrets
        cur = self.conn.execute(
            "INSERT INTO groups (name, display_name, slug, created_by, created_at, invite_code) VALUES (?, ?, '', ?, ?, ?)",
            ("", display_name or None, creator_id, datetime.now().isoformat(), _secrets.token_hex(3)),
        )
        self.conn.commit()
        group_id = cur.lastrowid
        # Auto-generate name after creator is added
        return group_id

    def add_group_member(self, group_id: int, user_id: int):
        self.conn.execute(
            "INSERT OR IGNORE INTO group_members (group_id, user_id, joined_at) VALUES (?, ?, ?)",
            (group_id, user_id, datetime.now().isoformat()),
        )
        self.conn.commit()
        # Regenerate auto-name
        auto = self.generate_group_auto_name(group_id)
        self.conn.execute("UPDATE groups SET name = ? WHERE id = ?", (auto, group_id))
        self.conn.commit()

    def leave_group(self, group_id: int, user_id: int):
        self.conn.execute(
            "DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )
        self.conn.commit()
        # Regenerate auto-name
        auto = self.generate_group_auto_name(group_id)
        self.conn.execute("UPDATE groups SET name = ? WHERE id = ?", (auto, group_id))
        self.conn.commit()

    def delete_group(self, group_id: int, user_id: int) -> bool:
        """Delete a group. Only the creator can delete it."""
        row = self.conn.execute(
            "SELECT created_by FROM groups WHERE id = ?", (group_id,)
        ).fetchone()
        if not row or row["created_by"] != user_id:
            return False
        self.conn.execute("DELETE FROM group_members WHERE group_id = ?", (group_id,))
        self.conn.execute("DELETE FROM group_events WHERE group_id = ?", (group_id,))
        self.conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        self.conn.commit()
        return True

    def get_group_by_id(self, group_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM groups WHERE id = ?", (group_id,)
        ).fetchone()
        return dict(row) if row else None

    def generate_group_auto_name(self, group_id: int) -> str:
        members = self.get_group_members(group_id)
        names = [m.get("name", "").split()[0] or m.get("email", "").split("@")[0]
                 for m in members]
        if len(names) <= 3:
            return ", ".join(names)
        return f"{', '.join(names[:2])} +{len(names) - 2}"

    def update_group_display_name(self, group_id: int, display_name: str):
        self.conn.execute(
            "UPDATE groups SET display_name = ? WHERE id = ?", (display_name, group_id)
        )
        self.conn.commit()

    def get_group_display_name(self, group: dict) -> str:
        return group.get("display_name") or group.get("name") or ""

    def add_group_event(self, group_id: int, user_id: int, title: str,
                        start_time: str, end_time: str = "", location: str = "",
                        url: str = "", notes: str = "") -> int:
        cur = self.conn.execute(
            """INSERT INTO group_events (group_id, created_by, title, start_time, end_time,
               location, url, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (group_id, user_id, title, start_time, end_time or None, location, url, notes),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_group_user_events(self, group_id: int) -> list[dict]:
        rows = self.conn.execute(
            """SELECT ge.*, u.name as creator_name, u.email as creator_email
               FROM group_events ge
               JOIN users u ON u.id = ge.created_by
               WHERE ge.group_id = ?
               ORDER BY ge.start_time ASC""",
            (group_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_group_event(self, event_id: int, user_id: int):
        # Allow deletion if user created the event OR created the group
        self.conn.execute(
            """DELETE FROM group_events WHERE id = ? AND (
                created_by = ? OR group_id IN (
                    SELECT id FROM groups WHERE created_by = ?
                )
            )""",
            (event_id, user_id, user_id),
        )
        self.conn.commit()

    def get_group_members(self, group_id: int) -> list[dict]:
        rows = self.conn.execute(
            """SELECT u.* FROM users u
               JOIN group_members gm ON gm.user_id = u.id
               WHERE gm.group_id = ?""",
            (group_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_user_groups(self, user_id: int) -> list[dict]:
        rows = self.conn.execute(
            """SELECT g.*, COUNT(gm2.id) as member_count
               FROM groups g
               JOIN group_members gm ON gm.group_id = g.id AND gm.user_id = ?
               JOIN group_members gm2 ON gm2.group_id = g.id
               GROUP BY g.id""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_groups(self) -> list[dict]:
        rows = self.conn.execute(
            """SELECT g.*, COUNT(gm.id) as member_count
               FROM groups g
               LEFT JOIN group_members gm ON gm.group_id = g.id
               GROUP BY g.id
               ORDER BY g.created_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def is_group_member(self, group_id: int, user_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        ).fetchone()
        return row is not None

    def can_ping(self, user_id: int, event_id: str, group_id: int) -> bool:
        """Check rate limits: max 3 pings per user per day, max 1 ping per event per group."""
        # Check if this event was already pinged to this group
        row = self.conn.execute(
            "SELECT 1 FROM ping_log WHERE event_id = ? AND group_id = ?",
            (event_id, group_id),
        ).fetchone()
        if row:
            return False
        # Check daily limit: max 3 pings per user per day
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM ping_log WHERE user_id = ? AND sent_at > datetime('now', '-1 day')",
            (user_id,),
        ).fetchone()
        if row and row["cnt"] >= 3:
            return False
        return True

    def log_ping(self, user_id: int, event_id: str, group_id: int) -> None:
        """Record a ping in the log."""
        self.conn.execute(
            "INSERT INTO ping_log (user_id, event_id, group_id, sent_at) VALUES (?, ?, ?, ?)",
            (user_id, event_id, group_id, datetime.now().isoformat()),
        )
        self.conn.commit()

    def get_recent_friend_rsvps(self, user_id: int, hours: int = 48) -> list[dict]:
        rows = self.conn.execute(
            """SELECT u.name as user_name, r.status, r.created_at,
                      e.title as event_title, e.start_time, e.url as event_url
               FROM rsvps r
               JOIN users u ON u.id = r.user_id
               JOIN events e ON e.event_id = r.event_id AND e.run_id = r.run_id
               WHERE r.user_id != ?
                 AND r.status IN ('going', 'maybe')
                 AND r.created_at > datetime('now', '-' || ? || ' hours')
                 AND r.user_id IN (
                     SELECT gm2.user_id FROM group_members gm
                     JOIN group_members gm2 ON gm2.group_id = gm.group_id
                     WHERE gm.user_id = ? AND gm2.user_id != ?
                 )
               ORDER BY r.created_at DESC""",
            (user_id, hours, user_id, user_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_friend_rsvps_for_run(self, user_id: int, run_id: int) -> dict[str, list[str]]:
        """Return {event_id: [friend_names_going_or_maybe]} for all events in run,
        only counting group-mates of user_id."""
        rows = self.conn.execute(
            """SELECT r.event_id, r.status, u.name as user_name
               FROM rsvps r
               JOIN users u ON u.id = r.user_id
               WHERE r.run_id = ?
                 AND r.user_id != ?
                 AND r.status IN ('going', 'maybe')
                 AND r.user_id IN (
                     SELECT gm2.user_id FROM group_members gm
                     JOIN group_members gm2 ON gm2.group_id = gm.group_id
                     WHERE gm.user_id = ? AND gm2.user_id != ?
                 )""",
            (run_id, user_id, user_id, user_id),
        ).fetchall()
        result: dict[str, list[str]] = {}
        for row in rows:
            eid = row["event_id"]
            name = row["user_name"] or "friend"
            status = row["status"]
            key = f"{name}{'★' if status == 'going' else '?'}"
            result.setdefault(eid, []).append(key)
        return result

    def get_user_latest_run(self, user_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM runs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None

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

    # --- Guest RSVPs (for group planner) ---

    def set_guest_rsvp(self, group_id: int, event_id: str, guest_name: str, status: str):
        """Set or update a guest RSVP. Status '' or empty removes the RSVP."""
        if not status:
            self.conn.execute(
                "DELETE FROM guest_rsvps WHERE group_id = ? AND event_id = ? AND guest_name = ?",
                (group_id, event_id, guest_name),
            )
        else:
            self.conn.execute(
                """INSERT INTO guest_rsvps (group_id, event_id, guest_name, status, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(group_id, event_id, guest_name) DO UPDATE SET status = ?, created_at = ?""",
                (group_id, event_id, guest_name, status, datetime.now().isoformat(),
                 status, datetime.now().isoformat()),
            )
        self.conn.commit()

    def get_group_guest_rsvps(self, group_id: int, event_ids: list[str]) -> dict[str, list[dict]]:
        """Return {event_id: [{guest_name, status}]} for guest RSVPs in a group."""
        if not event_ids:
            return {}
        placeholders = ",".join("?" for _ in event_ids)
        rows = self.conn.execute(
            f"""SELECT event_id, guest_name, status FROM guest_rsvps
                WHERE group_id = ? AND event_id IN ({placeholders})""",
            [group_id] + event_ids,
        ).fetchall()
        result: dict[str, list[dict]] = {}
        for r in rows:
            r = dict(r)
            eid = r.pop("event_id")
            result.setdefault(eid, []).append(r)
        return result

    def get_group_guests(self, group_id: int) -> list[str]:
        """Return distinct guest names who have RSVPd in this group."""
        rows = self.conn.execute(
            "SELECT DISTINCT guest_name FROM guest_rsvps WHERE group_id = ?",
            (group_id,),
        ).fetchall()
        return [r["guest_name"] for r in rows]

    # --- Taste Elo ---

    SEED_TASTE_ITEMS = [
        ("Live music (small venue)", "music"),
        ("Music festival", "music"),
        ("Jazz / blues bar", "music"),
        ("Classical concert", "music"),
        ("Comedy show", "social"),
        ("Trivia night", "social"),
        ("Board game night", "social"),
        ("Karaoke", "social"),
        ("Art exhibition opening", "arts"),
        ("Photography walk", "arts"),
        ("Pottery / ceramics class", "arts"),
        ("Improv or theater", "arts"),
        ("Tech / AI talk", "intellectual"),
        ("Science lecture", "intellectual"),
        ("Philosophy discussion group", "intellectual"),
        ("Book club", "intellectual"),
        ("Outdoor hiking / nature", "active"),
        ("Rock climbing gym", "active"),
        ("Group fitness class", "active"),
        ("Yoga / meditation", "active"),
        ("Food pop-up / street market", "food"),
        ("Wine / cocktail tasting", "food"),
        ("Cooking class", "food"),
        ("Farmers market", "food"),
        ("Hackathon", "maker"),
        ("Maker / DIY workshop", "maker"),
        ("Startup networking", "maker"),
    ]

    def seed_taste_items(self, user_id: int = 1):
        """Seed the taste_items table with default activity archetypes if empty."""
        count = self.conn.execute(
            "SELECT COUNT(*) FROM taste_items WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        if count > 0:
            return
        now = datetime.now().isoformat()
        self.conn.executemany(
            "INSERT OR IGNORE INTO taste_items (user_id, label, category, elo_rating, matchup_count, created_at) VALUES (?, ?, ?, 1400, 0, ?)",
            [(user_id, label, cat, now) for label, cat in self.SEED_TASTE_ITEMS],
        )
        self.conn.commit()

    def get_taste_items(self, user_id: int = 1) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM taste_items WHERE user_id = ? ORDER BY elo_rating DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_taste_matchup_pair(self, user_id: int = 1) -> tuple[dict, dict] | None:
        """Return the two items with most similar Elo for maximum info gain."""
        items = self.get_taste_items(user_id)
        if len(items) < 2:
            return None
        # Sort by rating, find closest pair
        best_pair = None
        best_diff = float("inf")
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                diff = abs(items[i]["elo_rating"] - items[j]["elo_rating"])
                # Prefer items with fewer matchups
                recency_penalty = (items[i]["matchup_count"] + items[j]["matchup_count"]) * 10
                score = diff + recency_penalty
                if score < best_diff:
                    best_diff = score
                    best_pair = (items[i], items[j])
        return best_pair

    def record_taste_matchup(self, user_id: int, item_a_id: int, item_b_id: int, winner_id: int | None) -> None:
        """Record a matchup result and update Elo ratings."""
        K = 32
        row_a = self.conn.execute("SELECT * FROM taste_items WHERE id = ?", (item_a_id,)).fetchone()
        row_b = self.conn.execute("SELECT * FROM taste_items WHERE id = ?", (item_b_id,)).fetchone()
        if not row_a or not row_b:
            return
        ra, rb = float(row_a["elo_rating"]), float(row_b["elo_rating"])

        # Expected scores
        ea = 1 / (1 + 10 ** ((rb - ra) / 400))
        eb = 1 - ea

        if winner_id == item_a_id:
            sa, sb = 1.0, 0.0
        elif winner_id == item_b_id:
            sa, sb = 0.0, 1.0
        else:  # draw / equal
            sa, sb = 0.5, 0.5

        new_ra = ra + K * (sa - ea)
        new_rb = rb + K * (sb - eb)

        now = datetime.now().isoformat()
        self.conn.execute(
            "UPDATE taste_items SET elo_rating = ?, matchup_count = matchup_count + 1 WHERE id = ?",
            (round(new_ra, 1), item_a_id),
        )
        self.conn.execute(
            "UPDATE taste_items SET elo_rating = ?, matchup_count = matchup_count + 1 WHERE id = ?",
            (round(new_rb, 1), item_b_id),
        )
        self.conn.execute(
            "INSERT INTO taste_matchups (user_id, item_a_id, item_b_id, winner_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, item_a_id, item_b_id, winner_id, now),
        )
        self.conn.commit()

    def add_taste_item(self, label: str, category: str = "general", user_id: int = 1) -> bool:
        try:
            self.conn.execute(
                "INSERT INTO taste_items (user_id, label, category, elo_rating, matchup_count, created_at) VALUES (?, ?, ?, 1400, 0, ?)",
                (user_id, label.strip(), category, datetime.now().isoformat()),
            )
            self.conn.commit()
            return True
        except Exception:
            return False

    def delete_taste_item(self, item_id: int, user_id: int = 1) -> bool:
        self.conn.execute(
            "DELETE FROM taste_items WHERE id = ? AND user_id = ?", (item_id, user_id)
        )
        self.conn.commit()
        return True

    def get_taste_matchup_count(self, user_id: int = 1) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM taste_matchups WHERE user_id = ?", (user_id,)
        ).fetchone()[0]

    def record_impression(self, user_id: int, event_id: str, run_id: int, channel: str = "calendar") -> None:
        """Record that a user was shown an event."""
        now = datetime.now().isoformat()
        self.conn.execute(
            "INSERT OR IGNORE INTO impressions (user_id, event_id, run_id, channel, shown_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, event_id, run_id, channel, now),
        )
        self.conn.commit()

    def mark_impression_clicked(self, user_id: int, event_id: str) -> None:
        self.conn.execute(
            "UPDATE impressions SET clicked = 1 WHERE user_id = ? AND event_id = ? AND clicked = 0",
            (user_id, event_id),
        )
        self.conn.commit()

    def get_impression_count(self, user_id: int, event_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM impressions WHERE user_id = ? AND event_id = ?",
            (user_id, event_id),
        ).fetchone()
        return row[0] if row else 0

    def get_impression_counts_for_run(self, user_id: int, run_id: int) -> dict[str, int]:
        """Return {event_id: impression_count} for all events in a run."""
        rows = self.conn.execute(
            """SELECT event_id, COUNT(*) as cnt
               FROM impressions
               WHERE user_id = ? AND event_id IN (
                   SELECT event_id FROM events WHERE run_id = ?
               )
               GROUP BY event_id""",
            (user_id, run_id),
        ).fetchall()
        return {r["event_id"]: r["cnt"] for r in rows}

    def set_steering(self, user_id: int, target_type: str, target_value: str, action: str, expires_at: str | None = None) -> None:
        """Upsert a steering directive (more/less/block/pause/done)."""
        now = datetime.now().isoformat()
        self.conn.execute(
            """INSERT INTO steering (user_id, target_type, target_value, action, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, target_type, target_value) DO UPDATE SET
                   action = excluded.action,
                   expires_at = excluded.expires_at,
                   created_at = excluded.created_at""",
            (user_id, target_type, target_value, action, expires_at, now),
        )
        self.conn.commit()

    def get_steering(self, user_id: int) -> list[dict]:
        """Return all active steering directives for a user."""
        rows = self.conn.execute(
            """SELECT * FROM steering
               WHERE user_id = ?
                 AND (expires_at IS NULL OR expires_at > datetime('now'))
               ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_calendar_context(self, user_id: int) -> str:
        """Return a human-readable summary of user's upcoming RSVPs for calendar density awareness."""
        from datetime import datetime, timedelta
        now = datetime.now()
        week_end = now + timedelta(days=8)
        rows = self.conn.execute(
            """SELECT e.title, e.start_time, r.status
               FROM rsvps r
               JOIN events e ON e.event_id = r.event_id
               WHERE r.user_id = ?
                 AND r.status IN ('going', 'maybe')
                 AND e.start_time >= ? AND e.start_time <= ?
               ORDER BY e.start_time""",
            (user_id, now.isoformat(), week_end.isoformat()),
        ).fetchall()
        if not rows:
            return ""
        # Group by day
        by_day: dict[str, list[str]] = {}
        for r in rows:
            try:
                dt = datetime.fromisoformat(r["start_time"])
                day = dt.strftime("%A %b %-d")
                label = f"{r['title'][:40]} ({dt.strftime('%-I%p').lower()}, {r['status']})"
                by_day.setdefault(day, []).append(label)
            except Exception:
                pass
        if not by_day:
            return ""
        lines = []
        for day, items in by_day.items():
            count_str = f"{len(items)} plan{'s' if len(items) > 1 else ''}"
            lines.append(f"  {day}: {count_str} — {'; '.join(items[:3])}")
        return "\n".join(lines)

    def get_recent_ratings(self, user_id: int, days: int = 14) -> list[dict]:
        """Return recent event ratings for a user with event metadata."""
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            """SELECT a.title, a.rating, a.attended_at, a.event_id,
                      e.category, e.location_name,
                      rk.vibe
               FROM attended a
               LEFT JOIN events e ON e.event_id = a.event_id
               LEFT JOIN rankings rk ON rk.event_id = a.event_id AND rk.run_id = e.run_id
               WHERE a.user_id = ? AND a.rating IS NOT NULL AND a.rating > 0
                 AND a.attended_at > ?
               GROUP BY a.event_id
               ORDER BY a.rating DESC, a.attended_at DESC""",
            (user_id, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_rating_context(self, user_id: int) -> str:
        """Build a text summary of recent ratings for injection into ranking prompts."""
        ratings = self.get_recent_ratings(user_id, days=30)
        if not ratings:
            return ""
        lines = ["=== RECENT EVENT RATINGS (user feedback) ==="]
        high = [r for r in ratings if r["rating"] >= 4]
        low = [r for r in ratings if r["rating"] <= 2]
        for r in ratings[:8]:
            stars = r["rating"]
            vibe = r.get("vibe") or "mixed"
            cat = r.get("category") or ""
            lines.append(f"  - {r['title'][:50]} -> {stars}★ (vibe: {vibe}, cat: {cat})")
        if high:
            vibes = [r.get("vibe") or "mixed" for r in high]
            cats = [r.get("category") or "" for r in high if r.get("category")]
            lines.append(f"User prefers: vibes={', '.join(set(vibes))}" + (f", categories={', '.join(set(cats))}" if cats else ""))
        if low:
            vibes = [r.get("vibe") or "mixed" for r in low]
            cats = [r.get("category") or "" for r in low if r.get("category")]
            lines.append(f"User avoids: vibes={', '.join(set(vibes))}" + (f", categories={', '.join(set(cats))}" if cats else ""))
        lines.append("Boost events matching preferred vibes/categories. Reduce scores for avoided types.")
        return "\n".join(lines)

    def clear_steering(self, user_id: int, target_type: str, target_value: str) -> None:
        self.conn.execute(
            "DELETE FROM steering WHERE user_id = ? AND target_type = ? AND target_value = ?",
            (user_id, target_type, target_value),
        )
        self.conn.commit()

    def get_taste_streak(self, user_id: int = 1) -> dict:
        """Return current streak info: days_in_row and total_days with matchups."""
        rows = self.conn.execute(
            "SELECT DISTINCT date(created_at) as day FROM taste_matchups WHERE user_id = ? ORDER BY day DESC",
            (user_id,),
        ).fetchall()
        if not rows:
            return {"streak": 0, "total_days": 0, "today_done": False}

        from datetime import date, timedelta
        today = date.today()
        days = [date.fromisoformat(r["day"]) for r in rows]
        total_days = len(days)
        today_done = days[0] == today

        # Walk back counting consecutive days
        streak = 0
        check = today
        for d in days:
            if d == check:
                streak += 1
                check -= timedelta(days=1)
            elif d < check:
                # Gap — check if yesterday was done (allows today to still extend streak)
                break

        return {"streak": streak, "total_days": total_days, "today_done": today_done}

    def is_source_cache_fresh(self, source_name: str, max_age_hours: float | None = None) -> bool:
        """Return True if source was scraped recently (within its refresh interval)."""
        from datetime import datetime, timedelta
        row = self.conn.execute(
            "SELECT last_scraped, refresh_interval_hours FROM source_cache WHERE source_name = ?",
            (source_name,),
        ).fetchone()
        if not row:
            return False
        interval = max_age_hours if max_age_hours is not None else (row["refresh_interval_hours"] or 24.0)
        last = datetime.fromisoformat(row["last_scraped"])
        return (datetime.now() - last) < timedelta(hours=interval)

    def update_source_cache(self, source_name: str, events_count: int, refresh_interval_hours: float = 24.0) -> None:
        """Mark source as freshly scraped."""
        from datetime import datetime
        self.conn.execute(
            """INSERT INTO source_cache (source_name, last_scraped, events_count, refresh_interval_hours)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(source_name) DO UPDATE SET
                 last_scraped = excluded.last_scraped,
                 events_count = excluded.events_count,
                 refresh_interval_hours = excluded.refresh_interval_hours""",
            (source_name, datetime.now().isoformat(), events_count, refresh_interval_hours),
        )
        self.conn.commit()

    def get_source_cache_status(self) -> list[dict]:
        """Return all cached source freshness records."""
        from datetime import datetime
        rows = self.conn.execute(
            "SELECT *, (julianday('now') - julianday(last_scraped)) * 24 as age_hours FROM source_cache ORDER BY last_scraped DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_monthly_spend(self, user_id: int) -> dict:
        """Return spending stats for the current month."""
        from datetime import datetime
        now = datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        rows = self.conn.execute(
            """SELECT e.title, a.attended_at, a.spent_amount
               FROM attended a
               JOIN events e ON e.event_id = a.event_id
               WHERE a.user_id = ?
               ORDER BY a.attended_at DESC""",
            (user_id,),
        ).fetchall()
        rows = [dict(r) for r in rows]
        this_month = [r for r in rows if (r.get("attended_at") or "") >= month_start]
        total_spend = sum(r["spent_amount"] or 0 for r in this_month)
        free_count = sum(1 for r in this_month if not r.get("spent_amount"))
        paid_count = sum(1 for r in this_month if r.get("spent_amount"))
        return {
            "this_month": total_spend,
            "this_month_count": len(this_month),
            "free_count": free_count,
            "paid_count": paid_count,
            "recent": rows[:20],
        }

    def get_venue_profile(self, user_id: int) -> list[dict]:
        """Return venues the user has attended, with visit counts and avg ratings."""
        rows = self.conn.execute(
            """SELECT e.location_name as venue,
                      e.location_address as address,
                      COUNT(*) as visits,
                      AVG(a.rating) as avg_rating,
                      MAX(a.attended_at) as last_visited
               FROM attended a
               JOIN events e ON e.event_id = a.event_id
               WHERE a.user_id = ?
                 AND e.location_name IS NOT NULL
                 AND e.location_name != ''
               GROUP BY LOWER(e.location_name)
               ORDER BY visits DESC, avg_rating DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Retro log (search gap retrospective) ---

    def save_retro(self, user_id: int, query: str, db_count: int, web_count: int,
                   web_results_json: str = "", analysis: str = "", gap_reason: str = "") -> int:
        # Ensure retro_log table exists (migration for older DBs)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS retro_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL DEFAULT 1,
            query TEXT NOT NULL,
            db_result_count INTEGER NOT NULL DEFAULT 0,
            web_result_count INTEGER NOT NULL DEFAULT 0,
            web_results_json TEXT,
            analysis TEXT,
            gap_reason TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )""")
        cur = self.conn.execute(
            """INSERT INTO retro_log (user_id, query, db_result_count, web_result_count,
               web_results_json, analysis, gap_reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, query, db_count, web_count, web_results_json,
             analysis, gap_reason, datetime.now().isoformat()),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_retros(self, user_id: int | None = None, limit: int = 50) -> list[dict]:
        try:
            if user_id is not None:
                rows = self.conn.execute(
                    "SELECT * FROM retro_log WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM retro_log ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    # --- Analytics: north star metrics ---

    def get_north_star_metrics(self, user_id: int = 1, days: int = 90) -> dict:
        """Compute attend_rate × avg_rating and related metrics."""
        cutoff = f"datetime('now', '-{days} days')"

        shown = self.conn.execute(
            f"""SELECT COUNT(DISTINCT rk.event_id) FROM rankings rk
                JOIN runs r ON r.id = rk.run_id
                WHERE r.user_id = ? AND rk.keep = 1
                AND r.timestamp > {cutoff}""",
            (user_id,),
        ).fetchone()[0] or 1

        attended_rows = self.conn.execute(
            f"""SELECT a.rating FROM attended a
                WHERE a.user_id = ? AND a.attended_at > {cutoff}""",
            (user_id,),
        ).fetchall()
        attended = len(attended_rows)
        ratings = [r["rating"] for r in attended_rows if r["rating"] is not None]
        avg_rating = sum(ratings) / len(ratings) if ratings else 0
        attend_rate = attended / shown

        north_star = attend_rate * (avg_rating / 5.0) if avg_rating else 0

        rsvps_going = self.conn.execute(
            f"""SELECT COUNT(DISTINCT rv.event_id) FROM rsvps rv
                JOIN runs r ON r.id = rv.run_id
                WHERE rv.user_id = ? AND rv.status = 'going'
                AND r.timestamp > {cutoff}""",
            (user_id,),
        ).fetchone()[0] or 0

        discovery_attended = self.conn.execute(
            f"""SELECT COUNT(*) FROM attended a
                JOIN rankings rk ON rk.event_id = a.event_id
                JOIN runs r ON r.id = rk.run_id
                WHERE a.user_id = ? AND rk.keep = 1
                AND a.attended_at > {cutoff}
                AND rk.score < 70""",
            (user_id,),
        ).fetchone()[0] or 0
        discovery_rate = discovery_attended / max(attended, 1)

        run_stats = self.conn.execute(
            f"""SELECT r.id, r.timestamp,
                    COUNT(DISTINCT rk.event_id) as kept,
                    COUNT(DISTINCT a.event_id) as attended_count,
                    AVG(a.rating) as avg_rating
                FROM runs r
                LEFT JOIN rankings rk ON rk.run_id = r.id AND rk.keep = 1
                LEFT JOIN attended a ON a.run_id = r.id AND a.user_id = r.user_id
                WHERE r.user_id = ? AND r.timestamp > {cutoff}
                GROUP BY r.id ORDER BY r.timestamp DESC""",
            (user_id,),
        ).fetchall()

        return {
            "north_star": round(north_star * 100, 1),
            "attend_rate": round(attend_rate * 100, 1),
            "avg_rating": round(avg_rating, 2),
            "discovery_rate": round(discovery_rate * 100, 1),
            "total_shown": shown,
            "total_attended": attended,
            "total_rated": len(ratings),
            "rsvps_going": rsvps_going,
            "run_stats": [dict(r) for r in run_stats],
        }

    def get_ranking_analysis(self, user_id: int = 1, run_id: int | None = None) -> dict:
        """Score distribution and attended-vs-recommended overlap."""
        if run_id is None:
            row = self.conn.execute(
                "SELECT id FROM runs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            run_id = row["id"] if row else None

        if not run_id:
            return {"score_buckets": [], "dim_avgs": {}, "attended_scores": [], "top_events": [],
                    "total": 0, "kept": 0, "run_id": None}

        all_scores = self.conn.execute(
            "SELECT score, keep, vibe FROM rankings WHERE run_id = ?", (run_id,)
        ).fetchall()

        buckets = [0] * 11
        kept_buckets = [0] * 11
        for row in all_scores:
            s = row["score"] or 0
            b = min(int(s / 10), 10)
            buckets[b] += 1
            if row["keep"]:
                kept_buckets[b] += 1

        dim_avgs = self.conn.execute(
            """SELECT
                AVG(interest_score) as interest, AVG(social_score) as social,
                AVG(urgency_score) as urgency, AVG(logistics_score) as logistics,
                AVG(friend_score) as friend, AVG(discovery_score) as discovery,
                AVG(quality_score) as quality
               FROM rankings WHERE run_id = ? AND keep = 1""",
            (run_id,),
        ).fetchone()

        attended_scores = self.conn.execute(
            """SELECT rk.score, rk.interest_score, rk.social_score, rk.vibe, e.title
               FROM attended a
               JOIN rankings rk ON rk.event_id = a.event_id AND rk.run_id = ?
               JOIN events e ON e.event_id = a.event_id AND e.run_id = ?
               WHERE a.user_id = ?""",
            (run_id, run_id, user_id),
        ).fetchall()

        top_events = self.conn.execute(
            """SELECT e.title, rk.score, rk.interest_score, rk.social_score,
                      rk.urgency_score, rk.logistics_score, rk.friend_score,
                      rk.discovery_score, rk.quality_score, rk.vibe,
                      rk.match_reason, rk.keep
               FROM rankings rk
               JOIN events e ON e.event_id = rk.event_id AND e.run_id = rk.run_id
               WHERE rk.run_id = ? AND rk.keep = 1
               ORDER BY rk.score DESC LIMIT 20""",
            (run_id,),
        ).fetchall()

        return {
            "run_id": run_id,
            "score_buckets": buckets,
            "kept_buckets": kept_buckets,
            "bucket_labels": [f"{i*10}-{i*10+9}" for i in range(11)],
            "dim_avgs": dict(dim_avgs) if dim_avgs else {},
            "attended_scores": [dict(r) for r in attended_scores],
            "top_events": [dict(r) for r in top_events],
            "total": len(all_scores),
            "kept": sum(kept_buckets),
        }

    def get_backtest_data(self, user_id: int = 1) -> dict:
        """Signal attribution and precision/recall per run."""
        runs = self.conn.execute(
            """SELECT r.id, r.timestamp,
                      COUNT(DISTINCT rk.event_id) as kept_count
               FROM runs r
               LEFT JOIN rankings rk ON rk.run_id = r.id AND rk.keep = 1
               WHERE r.user_id = ?
               GROUP BY r.id ORDER BY r.timestamp DESC LIMIT 20""",
            (user_id,),
        ).fetchall()

        run_stats = []
        for run in runs:
            rid = run["id"]
            attended_ids = {
                r["event_id"] for r in self.conn.execute(
                    "SELECT event_id FROM attended WHERE user_id = ? AND run_id = ?",
                    (user_id, rid),
                ).fetchall()
            }
            kept_ids = {
                r["event_id"] for r in self.conn.execute(
                    "SELECT event_id FROM rankings WHERE run_id = ? AND keep = 1", (rid,)
                ).fetchall()
            }

            tp = len(attended_ids & kept_ids)
            fp = len(kept_ids - attended_ids)
            fn = len(attended_ids - kept_ids)
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0

            run_stats.append({
                "run_id": rid,
                "timestamp": (run["timestamp"] or "")[:10],
                "kept": run["kept_count"] or 0,
                "attended": len(attended_ids),
                "precision": round(precision * 100, 1),
                "recall": round(recall * 100, 1),
            })

        # Signal attribution: hit rate above/below median per dimension
        all_rankings = self.conn.execute(
            """SELECT rk.interest_score, rk.social_score, rk.urgency_score,
                      rk.logistics_score, rk.friend_score, rk.discovery_score,
                      rk.quality_score, rk.score,
                      CASE WHEN a.event_id IS NOT NULL THEN 1 ELSE 0 END as attended
               FROM rankings rk
               JOIN runs r ON r.id = rk.run_id
               LEFT JOIN attended a ON a.event_id = rk.event_id AND a.user_id = r.user_id
               WHERE r.user_id = ? AND rk.keep = 1""",
            (user_id,),
        ).fetchall()

        dim_lift = {}
        if all_rankings:
            dims = ["interest_score", "social_score", "urgency_score", "logistics_score",
                    "friend_score", "discovery_score", "quality_score"]
            for dim in dims:
                vals = sorted([r[dim] or 0 for r in all_rankings])
                med = vals[len(vals) // 2]
                high = [r["attended"] for r in all_rankings if (r[dim] or 0) >= med]
                low = [r["attended"] for r in all_rankings if (r[dim] or 0) < med]
                hr_high = sum(high) / len(high) if high else 0
                hr_low = sum(low) / len(low) if low else 0
                dim_lift[dim.replace("_score", "")] = {
                    "hr_high": round(hr_high * 100, 1),
                    "hr_low": round(hr_low * 100, 1),
                    "lift": round((hr_high - hr_low) * 100, 1),
                }

        return {
            "run_stats": run_stats,
            "dim_lift": dim_lift,
            "total_rankings": len(all_rankings),
        }
