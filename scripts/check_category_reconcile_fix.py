#!/usr/bin/env python3
"""
Verify the category-reconcile fix for Start pre-vote / Friday close.

The bug: when the same user has picks in multiple cap categories and reconcile
tries to move one ticker into a category where they already submitted another,
Supabase raised duplicate key on ticker_picks (guild_id, week_key, category, submitted_by).

Run from repo root or server/:
  python server/scripts/check_category_reconcile_fix.py
  python server/scripts/check_category_reconcile_fix.py --live
  python server/scripts/check_category_reconcile_fix.py --probe-guild
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

if load_dotenv:
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT.parent / ".env")


@dataclass
class Result:
    name: str
    status: str
    detail: str


class Reporter:
    def __init__(self) -> None:
        self.results: list[Result] = []

    def ok(self, name: str, detail: str = "ok") -> None:
        self.results.append(Result(name, "PASS", detail))

    def fail(self, name: str, detail: str) -> None:
        self.results.append(Result(name, "FAIL", detail))

    def skip(self, name: str, detail: str) -> None:
        self.results.append(Result(name, "SKIP", detail))

    def exit_code(self) -> int:
        print("\nCategory reconcile fix check")
        print("=" * 60)
        for r in self.results:
            print(f"[{r.status}] {r.name}: {r.detail}")
        print("=" * 60)
        failed = sum(1 for r in self.results if r.status == "FAIL")
        print(
            f"Summary: {len(self.results) - failed} passed, "
            f"{sum(1 for r in self.results if r.status == 'SKIP')} skipped, {failed} failed"
        )
        return 1 if failed else 0


GUILD_TEST = 999_000_444_001
USER_TEST = 1506773090510831676  # same id from production error log
WEEK_TEST = "reconcile-fix-W00"


def test_mocked_user_conflict_drops_pick() -> None:
    """Same user: mid AAA + small BBB; BBB reclassifies to mid -> delete BBB, no PATCH."""
    import database
    from services import category_reconcile as cr

    rows = [
        {"id": 101, "category": "mid", "ticker": "AAA", "market_cap": None, "submitted_by": USER_TEST},
        {"id": 102, "category": "small", "ticker": "BBB", "market_cap": None, "submitted_by": USER_TEST},
    ]

    delete_calls: list[int] = []
    update_calls: list[int] = []
    move_calls: list[tuple[str, str, str]] = []

    def fake_delete(pick_id: int) -> None:
        delete_calls.append(pick_id)

    def fake_update(pick_id: int, category: str, market_cap: int | None = None) -> None:
        update_calls.append(pick_id)

    def fake_move(guild_id: int, week_key: str, ticker: str, old: str, new: str) -> None:
        move_calls.append((ticker, old, new))

    with (
        patch.object(database, "list_ticker_pick_rows", return_value=rows),
        patch.object(cr, "_current_category_for_ticker", side_effect=lambda t: ("mid", 5_000_000_000) if t == "BBB" else ("mid", 50_000_000_000)),
        patch.object(database, "ticker_in_category", return_value=False),
        patch.object(database, "user_has_ticker_pick", return_value=True),
        patch.object(database, "delete_ticker_pick", side_effect=fake_delete),
        patch.object(database, "update_ticker_pick_category", side_effect=fake_update),
        patch.object(database, "update_ticker_pick_market_cap"),
        patch.object(database, "move_votes_for_ticker", side_effect=fake_move),
    ):
        moves = cr.reconcile_ticker_categories(GUILD_TEST, WEEK_TEST)

    assert 102 in delete_calls, f"expected delete of pick 102, got {delete_calls}"
    assert 102 not in update_calls, f"PATCH must not run on conflicting pick, got {update_calls}"
    assert ("BBB", "small", "mid") in move_calls, f"votes should still move: {move_calls}"
    assert any(m.ticker == "BBB" and m.from_category == "small" for m in moves)


def test_mocked_patch_when_no_conflict() -> None:
    """Different users -> normal PATCH is allowed."""
    import database
    from services import category_reconcile as cr

    rows = [
        {"id": 201, "category": "small", "ticker": "CCC", "market_cap": None, "submitted_by": 111},
    ]
    update_calls: list[int] = []
    delete_calls: list[int] = []

    with (
        patch.object(database, "list_ticker_pick_rows", return_value=rows),
        patch.object(cr, "_current_category_for_ticker", return_value=("mid", 5_000_000_000)),
        patch.object(database, "ticker_in_category", return_value=False),
        patch.object(database, "user_has_ticker_pick", return_value=False),
        patch.object(database, "delete_ticker_pick", side_effect=lambda i: delete_calls.append(i)),
        patch.object(database, "update_ticker_pick_category", side_effect=lambda i, *a: update_calls.append(i)),
        patch.object(database, "move_votes_for_ticker"),
    ):
        cr.reconcile_ticker_categories(GUILD_TEST, WEEK_TEST)

    assert update_calls == [201]
    assert not delete_calls


def test_list_ticker_pick_rows_includes_submitted_by() -> None:
    import database

    query = ""
    captured: dict[str, Any] = {}

    def fake_select(table: str, q: str) -> list[dict]:
        nonlocal query
        query = q
        return []

    with patch.object(database, "_select", side_effect=fake_select):
        database.list_ticker_pick_rows(GUILD_TEST, WEEK_TEST)

    assert "submitted_by" in query, f"select must include submitted_by: {query}"


def run_supabase_integration(reporter: Reporter) -> None:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not key:
        reporter.skip("Supabase integration", "SUPABASE_SERVICE_ROLE_KEY not set")
        return

    import database
    from services import category_reconcile as cr

    database.init_db()
    database._request("DELETE", "votes", query=f"?guild_id=eq.{GUILD_TEST}&week_key=eq.{WEEK_TEST}")
    database._request("DELETE", "ticker_picks", query=f"?guild_id=eq.{GUILD_TEST}&week_key=eq.{WEEK_TEST}")
    database._request("DELETE", "game_cycles", query=f"?guild_id=eq.{GUILD_TEST}&week_key=eq.{WEEK_TEST}")

    database.ensure_cycle(GUILD_TEST, WEEK_TEST)
    database.set_cycle_phase(
        GUILD_TEST,
        WEEK_TEST,
        status="ticker_selection",
        ticker_selection_open=True,
        voting_open=True,
        early_window_open=False,
    )
    database.upsert_user(USER_TEST, "reconcile-test-user")

    ok, reason = database.add_ticker_pick(
        GUILD_TEST, WEEK_TEST, "mid", "ZZMID", USER_TEST,
        market_cap=5_000_000_000, exchange="NASDAQ",
    )
    if not ok:
        raise RuntimeError(f"mid pick failed: {reason}")

    ok, reason = database.add_ticker_pick(
        GUILD_TEST, WEEK_TEST, "small", "ZZSML", USER_TEST,
        market_cap=500_000_000, exchange="NASDAQ",
    )
    if not ok:
        raise RuntimeError(f"small pick failed: {reason}")

    database.record_vote(GUILD_TEST, WEEK_TEST, "small", "ZZSML", USER_TEST, "NPC", False)

    with patch.object(
        cr,
        "_current_category_for_ticker",
        side_effect=lambda t: ("mid", 5_000_000_000) if t == "ZZSML" else ("mid", 5_000_000_000),
    ):
        moves = cr.reconcile_ticker_categories(GUILD_TEST, WEEK_TEST)

    mid_picks = database._select(
        "ticker_picks",
        f"?select=id,ticker,submitted_by&guild_id=eq.{GUILD_TEST}&week_key=eq.{WEEK_TEST}&category=eq.mid",
    )
    small_picks = database._select(
        "ticker_picks",
        f"?select=id,ticker&guild_id=eq.{GUILD_TEST}&week_key=eq.{WEEK_TEST}&category=eq.small",
    )
    votes = database._select(
        "votes",
        f"?select=category,ticker&guild_id=eq.{GUILD_TEST}&week_key=eq.{WEEK_TEST}&ticker=eq.ZZSML",
    )

    user_mid_rows = [r for r in mid_picks if int(r.get("submitted_by", 0)) == USER_TEST]
    if len(user_mid_rows) != 1:
        raise RuntimeError(f"expected 1 mid pick for user, got {user_mid_rows}")
    if small_picks:
        raise RuntimeError(f"small pick should be dropped after reconcile, got {small_picks}")
    if not votes or votes[0]["category"] != "mid":
        raise RuntimeError(f"ZZSML votes should be in mid, got {votes}")
    if not any(m.ticker == "ZZSML" for m in moves):
        raise RuntimeError(f"expected move for ZZSML, got {moves}")

    reporter.ok(
        "Supabase integration",
        f"reconcile ok; mid picks={len(mid_picks)}, votes moved to mid, moves={len(moves)}",
    )

    database._request("DELETE", "votes", query=f"?guild_id=eq.{GUILD_TEST}&week_key=eq.{WEEK_TEST}")
    database._request("DELETE", "ticker_picks", query=f"?guild_id=eq.{GUILD_TEST}&week_key=eq.{WEEK_TEST}")
    database._request("DELETE", "game_cycles", query=f"?guild_id=eq.{GUILD_TEST}&week_key=eq.{WEEK_TEST}")


def probe_production_guild(reporter: Reporter) -> None:
    """Read-only: report picks that would have caused the duplicate-key PATCH."""
    gid_raw = os.getenv("DISCORD_GUILD_ID", "").strip()
    if not gid_raw:
        reporter.skip("Production probe", "DISCORD_GUILD_ID not set")
        return
    if not os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip():
        reporter.skip("Production probe", "SUPABASE_SERVICE_ROLE_KEY not set")
        return

    import database
    from services import category_reconcile as cr

    guild_id = int(gid_raw)
    week_key = os.getenv("PROBE_WEEK_KEY", "").strip() or database.week_key_for()
    database.init_db()

    rows = database.list_ticker_pick_rows(guild_id, week_key)
    if not rows:
        reporter.ok("Production probe", f"no ticker_picks for {week_key}")
        return

    conflicts: list[str] = []
    for row in rows:
        ticker = str(row["ticker"]).upper()
        old_cat = row["category"]
        submitted_by = row.get("submitted_by")
        new_cat, _ = cr._current_category_for_ticker(ticker)
        if new_cat not in ("small", "mid", "blue") or new_cat == old_cat:
            continue
        if submitted_by is None:
            continue
        uid = int(submitted_by)
        if database.user_has_ticker_pick(guild_id, week_key, new_cat, uid):
            conflicts.append(f"${ticker} {old_cat}->{new_cat} user={uid} pick_id={row['id']}")

    if conflicts:
        reporter.ok(
            "Production probe",
            f"week {week_key}: {len(conflicts)} conflict(s) — fix would DROP pick (not PATCH): "
            + "; ".join(conflicts[:5])
            + (" ..." if len(conflicts) > 5 else ""),
        )
    else:
        reporter.ok("Production probe", f"week {week_key}: no user/category conflicts in {len(rows)} picks")


def run_live_reconcile_on_probe_week(reporter: Reporter) -> None:
    """Run real reconcile on production guild/week (mutates picks/votes for that week)."""
    gid_raw = os.getenv("DISCORD_GUILD_ID", "").strip()
    if not gid_raw:
        reporter.skip("Live reconcile", "DISCORD_GUILD_ID not set")
        return
    if not os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip():
        reporter.skip("Live reconcile", "SUPABASE_SERVICE_ROLE_KEY not set")
        return

    import database
    from services.category_reconcile import reconcile_ticker_categories

    guild_id = int(gid_raw)
    week_key = os.getenv("PROBE_WEEK_KEY", "").strip() or database.week_key_for()
    database.init_db()
    moves = reconcile_ticker_categories(guild_id, week_key)
    reporter.ok("Live reconcile", f"week {week_key}: completed with {len(moves)} move(s), no Supabase error")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run Supabase integration test (isolated test guild)",
    )
    parser.add_argument(
        "--probe-guild",
        action="store_true",
        help="Read-only scan of DISCORD_GUILD_ID for would-be conflicts",
    )
    parser.add_argument(
        "--live-reconcile",
        action="store_true",
        help="Run reconcile on real guild/week (mutates DB; use PROBE_WEEK_KEY)",
    )
    args = parser.parse_args()
    reporter = Reporter()

    for name, fn in [
        ("Mock: user conflict drops pick", test_mocked_user_conflict_drops_pick),
        ("Mock: patch when no conflict", test_mocked_patch_when_no_conflict),
        ("list_ticker_pick_rows includes submitted_by", test_list_ticker_pick_rows_includes_submitted_by),
    ]:
        try:
            fn()
            reporter.ok(name)
        except Exception as exc:
            reporter.fail(name, f"{exc.__class__.__name__}: {exc}")
            traceback.print_exc()

    if args.live:
        try:
            run_supabase_integration(reporter)
        except Exception as exc:
            reporter.fail("Supabase integration", f"{exc.__class__.__name__}: {exc}")
            traceback.print_exc()
    else:
        reporter.skip("Supabase integration", "pass --live to run")

    if args.probe_guild:
        try:
            probe_production_guild(reporter)
        except Exception as exc:
            reporter.fail("Production probe", f"{exc.__class__.__name__}: {exc}")
            traceback.print_exc()

    if args.live_reconcile:
        try:
            run_live_reconcile_on_probe_week(reporter)
        except Exception as exc:
            reporter.fail("Live reconcile", f"{exc.__class__.__name__}: {exc}")
            traceback.print_exc()

    return reporter.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
