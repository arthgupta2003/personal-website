"""ICS METHOD:REQUEST / CANCEL payload structure — drives instant gcal sync."""
from __future__ import annotations

import re

from calyx.email.invite import build_invite_ics


def _unfold(ics: str) -> str:
    """Reverse RFC 5545 line folding (CRLF + space) for substring assertions."""
    return ics.replace("\r\n ", "")


def test_request_payload_has_required_fields():
    ics = build_invite_ics(
        event_uid="grp_evt_99@calyx.local",
        title="Tide pooling + Birding!",
        start_time="2026-05-24T11:00:00",
        end_time="2026-05-24T15:00:00",
        location="Wonderland Trail",
        description="From acadia on Calyx",
        url="https://calyx.local/group/15",
        organizer_email="calyx@example.com",
        organizer_name="Arth via Calyx",
        attendee_email="friend@example.com",
        attendee_name="Friend",
        attendee_partstat="NEEDS-ACTION",
        method="REQUEST",
        sequence=0,
    )
    unfolded = _unfold(ics)
    assert "BEGIN:VCALENDAR" in unfolded
    assert "METHOD:REQUEST" in unfolded
    assert "UID:grp_evt_99@calyx.local" in unfolded
    assert "SEQUENCE:0" in unfolded
    assert "STATUS:CONFIRMED" in unfolded
    assert "ORGANIZER;CN=Arth via Calyx:mailto:calyx@example.com" in unfolded
    assert "ATTENDEE;CN=Friend;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:friend@example.com" in unfolded
    assert "SUMMARY:Tide pooling + Birding!" in unfolded
    # Time must have been converted from America/New_York local → UTC Z form.
    assert re.search(r"DTSTART:\d{8}T\d{6}Z", ics)
    assert re.search(r"DTEND:\d{8}T\d{6}Z", ics)


def test_cancel_payload_uses_status_cancelled_and_bumped_sequence():
    ics = build_invite_ics(
        event_uid="grp_evt_99@calyx.local",
        title="Tide pooling",
        start_time="2026-05-24T11:00:00",
        end_time=None,
        location="",
        description="",
        url="",
        organizer_email="calyx@example.com",
        attendee_email="friend@example.com",
        method="CANCEL",
        sequence=2,
    )
    unfolded = _unfold(ics)
    assert "METHOD:CANCEL" in unfolded
    assert "STATUS:CANCELLED" in unfolded
    assert "SEQUENCE:2" in unfolded
    assert "TRANSP:TRANSPARENT" in unfolded


def test_uid_stable_across_request_and_cancel():
    """Same UID across the lifecycle is what lets calendar clients update or remove
    the existing event instead of creating a duplicate."""
    common = dict(
        event_uid="grp_evt_42@calyx.local",
        title="Hike",
        start_time="2026-05-24T07:00:00",
        end_time="2026-05-24T15:00:00",
        location="",
        description="",
        url="",
        organizer_email="o@e.com",
        attendee_email="a@e.com",
    )
    req = build_invite_ics(method="REQUEST", sequence=0, **common)
    upd = build_invite_ics(method="REQUEST", sequence=1, **common)
    cnl = build_invite_ics(method="CANCEL", sequence=2, **common)
    for ics in (req, upd, cnl):
        assert "UID:grp_evt_42@calyx.local" in _unfold(ics)
