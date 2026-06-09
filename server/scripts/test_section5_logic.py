"""Offline checks for Section 5 early-window timing and eligibility."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cogs.scheduler import (  # noqa: E402
    _format_et,
    _from_et_local_to_utc,
    _monday_9am_et_for_week,
    _next_weekday_time_et,
    _to_et,
)
from cogs.weekly_picks import is_early_window_active, restore_early_window  # noqa: E402
from database import compute_eligible_winner_ids  # noqa: E402

UTC = timezone.utc
WINNING = {"small": {"AAPL"}, "mid": {"MSFT"}, "blue": {"GOOG"}}


def _tuesday_9am_after_monday(monday_start_utc: datetime) -> datetime:
    start_et = _to_et(monday_start_utc)
    from datetime import time as dtime

    return _from_et_local_to_utc(
        datetime.combine(start_et.date() + timedelta(days=1), dtime(9, 0))
    )


def main() -> int:
    fails: list[str] = []

    # Representative Mondays in EST and EDT weeks.
    for label, monday_utc in (
        ("EST week", datetime(2025, 1, 6, 14, 0, tzinfo=UTC)),   # Mon Jan 6 2025 09:00 ET (EST)
        ("EDT week", datetime(2025, 6, 9, 13, 0, tzinfo=UTC)),  # Mon Jun 9 2025 09:00 ET (EDT)
    ):
        start = _monday_9am_et_for_week(monday_utc)
        end = _tuesday_9am_after_monday(start)
        start_et = _to_et(start)
        end_et = _to_et(end)
        if start_et.hour != 9 or start_et.minute != 0:
            fails.append(f"{label}: start not 09:00 ET ({_format_et(start)})")
        if end_et.weekday() != 1 or end_et.hour != 9:
            fails.append(f"{label}: end not Tuesday 09:00 ET ({_format_et(end)})")
        if end <= start:
            fails.append(f"{label}: end before start")

    # Scheduler Tuesday close target matches Tuesday 09:00 ET.
    probe = datetime(2025, 6, 9, 13, 0, tzinfo=UTC)
    tue_fire = _next_weekday_time_et(probe, 1, 9, 0)
    tue_et = _to_et(tue_fire)
    if tue_et.weekday() != 1 or tue_et.hour != 9:
        fails.append(f"Tuesday scheduler fire wrong: {_format_et(tue_fire)}")

    # In-memory early flag: inside window vs after cutoff.
    start = _monday_9am_et_for_week(datetime(2025, 6, 9, 14, 0, tzinfo=UTC))
    end = _tuesday_9am_after_monday(start)
    restore_early_window(start)
    if not is_early_window_active(start + timedelta(hours=1)):
        fails.append("vote at Mon 10:00 ET should be early")
    if is_early_window_active(end + timedelta(minutes=1)):
        fails.append("vote at Tue 09:01 ET should NOT be early")

    # Eligibility uses is_early column (rows 13/14 scenarios).
    uid = 42
    partial = [
        {"user_id": uid, "category": "small", "ticker": "AAPL", "role_at_vote": "NPC", "is_early": True},
        {"user_id": uid, "category": "mid", "ticker": "MSFT", "role_at_vote": "NPC", "is_early": False},
        {"user_id": uid, "category": "blue", "ticker": "GOOG", "role_at_vote": "NPC", "is_early": False},
    ]
    ids, _ = compute_eligible_winner_ids(
        winning_tickers=WINNING, vote_rows=partial, active_winner_user_ids=set()
    )
    if ids:
        fails.append("partial early+late should not win")

    all_late = [
        {"user_id": uid, "category": c, "ticker": t, "role_at_vote": "NPC", "is_early": False}
        for c, t in (("small", "AAPL"), ("mid", "MSFT"), ("blue", "GOOG"))
    ]
    ids2, _ = compute_eligible_winner_ids(
        winning_tickers=WINNING, vote_rows=all_late, active_winner_user_ids=set()
    )
    if ids2:
        fails.append("all-late votes should not win")

    all_early = [
        {"user_id": uid, "category": c, "ticker": t, "role_at_vote": "NPC", "is_early": True}
        for c, t in (("small", "AAPL"), ("mid", "MSFT"), ("blue", "GOOG"))
    ]
    ids3, _ = compute_eligible_winner_ids(
        winning_tickers=WINNING, vote_rows=all_early, active_winner_user_ids=set()
    )
    if ids3 != [uid]:
        fails.append("all-early correct NPC should win eligibility")

    if fails:
        print("SECTION 5 LOGIC: FAIL")
        for f in fails:
            print(f"  • {f}")
        return 1
    print("SECTION 5 LOGIC: PASS")
    print("  • Monday 09:00 ET → Tuesday 09:00 ET window (EST + EDT weeks)")
    print("  • Tuesday 09:01 ET is outside early window")
    print("  • is_early drives eligibility (partial/late/all-early cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
