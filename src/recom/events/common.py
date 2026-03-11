"""Shared utilities for event scrapers."""

from __future__ import annotations

import hashlib


def make_event_id(source: str, title: str, date_str: str = "") -> str:
    """Generate a deterministic event ID from source, title, and optional date."""
    raw = f"{title.strip().lower()}|{date_str}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"{source}_{h}"
