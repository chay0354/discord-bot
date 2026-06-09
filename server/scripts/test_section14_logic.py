"""Offline checks for Section 14 restart, continuity, and scheduling catch-up."""
from __future__ import annotations

import inspect
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cogs.scheduler import (  # noqa: E402
    SchedulerCog,
    _friday_4pm_et_for_week,
    _monday_9am_et_for_week,
    _to_et,
)
from cogs.weekly_picks import (  # noqa: E402
    disarm_early_window,
    is_early_window_active,
    restore_early_window,
)

UTC = timezone.utc


def main() -> int:
    fails: list[str] = []

    sched_src = (ROOT / "cogs" / "scheduler.py").read_text(encoding="utf-8")
    restart_doc = (ROOT / "docs" / "RESTART_AND_STATE.md").read_text(encoding="utf-8")
    weekly_src = (ROOT / "cogs" / "weekly_picks.py").read_text(encoding="utf-8")

    # 1) Documented simple restart path
    if "python run.py" not in restart_doc:
        fails.append("RESTART_AND_STATE.md must document python run.py entry point")

    # 2–5) Persistence hooks present
    for needle in ("hydrate_vote_state", "list_tickers", "ensure_cycle", "_expire_winners"):
        if needle not in sched_src and needle not in weekly_src:
            fails.append(f"missing restart persistence hook: {needle}")

    # 6) Missed-event catch-up on startup
    reconcile = inspect.getsource(SchedulerCog._reconcile_missed_events_one_guild)
    for needle in (
        "missed_friday_close_catchup",
        "missed_monday_open_catchup",
        "missed_early_close_catchup",
        "_friday_close_one_guild",
        "_monday_open_one_guild",
        "_tuesday_early_close_one_guild",
    ):
        if needle not in reconcile:
            fails.append(f"reconcile missing catch-up step: {needle}")
    if "_reconcile_missed_events_all_guilds" not in inspect.getsource(SchedulerCog._runner):
        fails.append("scheduler runner must call reconcile on startup")

    # 7) NY time, not host local
    if "America/New_York" not in sched_src and "ET_TZ" not in sched_src:
        fails.append("scheduler must pin to America/New_York / ET")

    # 8) DST helpers + Friday 16:00 ET anchor
    fri_probe = datetime(2025, 6, 11, 20, 0, tzinfo=UTC)  # Wed Jun 11 2025
    fri_4 = _friday_4pm_et_for_week(fri_probe)
    fri_et = _to_et(fri_4)
    if fri_et.weekday() != 4 or fri_et.hour != 16:
        fails.append(f"Friday 4pm ET wrong for mid-week probe: {fri_et}")

    mon_probe = datetime(2025, 1, 8, 18, 0, tzinfo=UTC)  # Wed in EST week
    mon_9 = _monday_9am_et_for_week(mon_probe)
    mon_et = _to_et(mon_9)
    if mon_et.weekday() != 0 or mon_et.hour != 9:
        fails.append(f"Monday 9am ET wrong: {mon_et}")

    # Early window disarm after Tuesday close path
    if "disarm_early_window" not in sched_src:
        fails.append("Tuesday close must disarm in-memory early window")
    restore_src = inspect.getsource(
        __import__("cogs.weekly_picks", fromlist=["WeeklyPicksCog"]).WeeklyPicksCog._restore_early_window_from_cycle
    )
    if "early_window_open" not in restore_src:
        fails.append("early window restore must respect DB early_window_open flag")

    start = datetime.now(tz=UTC) - timedelta(hours=2)
    restore_early_window(start)
    disarm_early_window()
    if is_early_window_active():
        fails.append("disarm_early_window must clear active early window")

    print("SECTION 14 — RESTART, CONTINUITY & SCHEDULING")
    print("=" * 50)
    if fails:
        for item in fails:
            print(f"  [FAIL] {item}")
        return 1
    print("All structural checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
