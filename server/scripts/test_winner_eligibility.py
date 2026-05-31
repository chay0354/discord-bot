"""Offline acceptance tests for WINNER role eligibility (report item #9).

Proves:
  • Only NPC votes in the early 24h window can win.
  • PLAYER and WINNER votes never win, even with correct picks.
  • An active WINNER grant blocks a second WINNER award.
  • Late NPC votes do not count toward winning picks.

Run:  python server/scripts/test_winner_eligibility.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import compute_eligible_winner_ids  # noqa: E402

WINNING = {
    "small": {"AAPL"},
    "mid": {"MSFT"},
    "blue": {"GOOG"},
}

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def rows(*entries):
    out = []
    for user_id, category, ticker, role, is_early in entries:
        out.append(
            {
                "user_id": user_id,
                "category": category,
                "ticker": ticker,
                "role_at_vote": role,
                "is_early": is_early,
            }
        )
    return out


def main() -> int:
    print("\nScenario 1: NPC with early votes on all top tickers wins")
    ids, ex = compute_eligible_winner_ids(
        winning_tickers=WINNING,
        vote_rows=rows(
            (101, "small", "AAPL", "NPC", True),
            (101, "mid", "MSFT", "NPC", True),
            (101, "blue", "GOOG", "NPC", True),
        ),
        active_winner_user_ids=set(),
    )
    check("npc early winner eligible", ids == [101], str(ids))

    print("\nScenario 2: PLAYER with correct picks cannot win")
    ids, ex = compute_eligible_winner_ids(
        winning_tickers=WINNING,
        vote_rows=rows(
            (202, "small", "AAPL", "PLAYER", True),
            (202, "mid", "MSFT", "PLAYER", True),
            (202, "blue", "GOOG", "PLAYER", True),
        ),
        active_winner_user_ids=set(),
    )
    check("player excluded", ids == [], str(ids))
    check("player exclusion logged", any(r["reason"] == "not_npc_at_vote" for r in ex))

    print("\nScenario 3: existing WINNER cannot win again")
    ids, ex = compute_eligible_winner_ids(
        winning_tickers=WINNING,
        vote_rows=rows(
            (303, "small", "AAPL", "NPC", True),
            (303, "mid", "MSFT", "NPC", True),
            (303, "blue", "GOOG", "NPC", True),
        ),
        active_winner_user_ids={303},
    )
    check("active winner grant blocks win", ids == [], str(ids))
    check("active grant reason logged", any(r["reason"] == "active_winner_grant" for r in ex))

    print("\nScenario 4: WINNER role at vote time cannot win")
    ids, _ = compute_eligible_winner_ids(
        winning_tickers=WINNING,
        vote_rows=rows(
            (404, "small", "AAPL", "WINNER", True),
            (404, "mid", "MSFT", "WINNER", True),
            (404, "blue", "GOOG", "WINNER", True),
        ),
        active_winner_user_ids=set(),
    )
    check("winner-at-vote excluded", ids == [], str(ids))

    print("\nScenario 5: NPC late votes (after 24h) cannot win")
    ids, ex = compute_eligible_winner_ids(
        winning_tickers=WINNING,
        vote_rows=rows(
            (505, "small", "AAPL", "NPC", False),
            (505, "mid", "MSFT", "NPC", False),
            (505, "blue", "GOOG", "NPC", False),
        ),
        active_winner_user_ids=set(),
    )
    check("late npc votes excluded", ids == [], str(ids))
    check("late vote reason logged", any(r["reason"] == "not_early_window" for r in ex))

    print("\nScenario 6: mixed early NPC + late NPC only early picks count")
    ids, _ = compute_eligible_winner_ids(
        winning_tickers=WINNING,
        vote_rows=rows(
            (606, "small", "AAPL", "NPC", True),
            (606, "mid", "MSFT", "NPC", True),
            (606, "blue", "GOOG", "NPC", False),
        ),
        active_winner_user_ids=set(),
    )
    check("missing early blue vote blocks win", ids == [], str(ids))

    print("\nScenario 7: wrong picks excluded")
    ids, ex = compute_eligible_winner_ids(
        winning_tickers=WINNING,
        vote_rows=rows(
            (707, "small", "AAPL", "NPC", True),
            (707, "mid", "MSFT", "NPC", True),
            (707, "blue", "TSLA", "NPC", True),
        ),
        active_winner_user_ids=set(),
    )
    check("wrong ticker excluded", ids == [], str(ids))
    check("wrong picks reason logged", any(r["reason"] == "wrong_picks" for r in ex))

    print("\n" + ("=" * 52))
    if failures:
        print(f"RESULT: FAILED ({len(failures)}): {', '.join(failures)}")
        return 1
    print("RESULT: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
