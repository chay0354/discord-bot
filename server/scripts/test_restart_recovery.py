"""Offline tests for restart / crash state recovery (report item #11).

Proves the pure, Discord-independent guarantees that make a restart safe:
  • The 24h early-vote window can be restored from persisted state, and an
    already-elapsed window is NOT treated as active.
  • Voting-button custom_ids are stable, so re-registering a view after a
    restart routes clicks to the right handler (no "This interaction failed").

Run:  python server/scripts/test_restart_recovery.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cogs.weekly_picks import (  # noqa: E402
    WeeklyVotingView,
    build_weekly_voting_view,
    is_early_window_active,
    restore_early_window,
    early_window_start_utc,
)

UTC = timezone.utc
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def custom_ids(view: WeeklyVotingView) -> list[str]:
    return [c.custom_id for c in view.children if getattr(c, "custom_id", None)]


def main() -> int:
    print("\nScenario 1: early window restored within 24h is active")
    start = datetime.now(tz=UTC) - timedelta(hours=2)
    restore_early_window(start)
    check("window active after restore", is_early_window_active() is True)
    check("start time persisted", early_window_start_utc() == start)

    print("\nScenario 2: elapsed early window is NOT active")
    restore_early_window(datetime.now(tz=UTC) - timedelta(hours=25))
    check("window inactive after 24h", is_early_window_active() is False)

    print("\nScenario 3: voting button custom_ids are stable across rebuilds")
    v1 = WeeklyVotingView(0, ["AAPL", "MSFT", "F"])
    v2 = WeeklyVotingView(0, ["AAPL", "MSFT", "F"])
    check("custom_ids present", custom_ids(v1) == ["vote:0:AAPL", "vote:0:MSFT", "vote:0:F"], str(custom_ids(v1)))
    check("custom_ids stable across rebuild", custom_ids(v1) == custom_ids(v2))

    print("\nScenario 4: categories produce distinct custom_id namespaces")
    small = WeeklyVotingView(0, ["AAPL"])
    mid = WeeklyVotingView(1, ["AAPL"])
    check("distinct per-category ids", custom_ids(small) != custom_ids(mid), f"{custom_ids(small)} vs {custom_ids(mid)}")

    print("\nScenario 5: recovery view routes clicks identically to Monday-open view")
    messy = ["$nvda", "amd ", "Pltr"]
    # Monday-open path (builds the message the user sees).
    built = asyncio.run(build_weekly_voting_view(2, messy, fetch_quotes=False))
    # Recovery path mirrors weekly_picks.on_ready normalization.
    norm = [str(t).strip().lstrip("$").upper() for t in messy if t]
    recovered = WeeklyVotingView(2, norm)
    check(
        "recovery ids match Monday-open ids",
        custom_ids(built) == custom_ids(recovered) == ["vote:2:NVDA", "vote:2:AMD", "vote:2:PLTR"],
        f"built={custom_ids(built)} recovered={custom_ids(recovered)}",
    )

    print("\n" + ("=" * 52))
    if failures:
        print(f"RESULT: FAILED ({len(failures)}): {', '.join(failures)}")
        return 1
    print("RESULT: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
