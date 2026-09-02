"""Offline checks for Part E features (stack votes, tie-break, extras, Q&A, incentives)."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cogs.weekly_picks import (  # noqa: E402
    _banner_description_with_timer,
    _can_stack_votes,
    _vote_confirmation_message,
    _vote_limit_for,
)
from config import PLAYER_VOTES_PER_CATEGORY, WINNER_BONUS_VOTES  # noqa: E402
from database import rank_leaderboard_with_tiebreak, winner_incentive_badge  # noqa: E402


class FakeRole:
    def __init__(self, name: str):
        self.name = name


class FakeMember:
    def __init__(self, roles: list[str]):
        self.roles = [FakeRole(r) for r in roles]
        self.guild = None
        self.id = 1
        self.guild_permissions = type("P", (), {"administrator": False, "manage_guild": False})()


def main() -> int:
    fails: list[str] = []

    if not _can_stack_votes(FakeMember(["PLAYER"])):
        fails.append("PLAYER must be allowed to stack votes")
    if not _can_stack_votes(FakeMember(["WINNER"])):
        fails.append("WINNER must be allowed to stack votes")
    if _can_stack_votes(FakeMember(["NPC"])):
        fails.append("NPC must not stack votes")
    if _vote_limit_for(FakeMember(["PLAYER"])) != PLAYER_VOTES_PER_CATEGORY:
        fails.append("PLAYER base limit must stay 5")

    msg = _vote_confirmation_message("AAPL", 2, 3, 5, 3)
    if "AAPL" not in msg or "3/5" not in msg:
        fails.append("confirmation must show ticker and count")

    desc = _banner_description_with_timer(0, None)
    if "same ticker" not in desc:
        fails.append("vote-open banner must mention stacking on the same ticker")

    counts = [("AAA", 10), ("BBB", 10), ("CCC", 4)]
    ranked_a = rank_leaderboard_with_tiebreak(counts, week_key="2026-W25", category="small")
    ranked_b = rank_leaderboard_with_tiebreak(counts, week_key="2026-W25", category="small")
    if ranked_a != ranked_b:
        fails.append("tie-break must be stable for the same week/category")
    if {r[1] for r in ranked_a[:2]} != {"AAA", "BBB"}:
        fails.append("tie-break must only shuffle equal vote totals")
    if ranked_a[0][0] != 1 or ranked_a[1][0] != 2:
        fails.append("tied tickers must receive sequential ranks")
    if not any(r[3] for r in ranked_a[:2]):
        fails.append("tied rows must be flagged")

    if winner_incentive_badge({"current_streak": 1}) != "🏆":
        fails.append("streak 1 badge")
    if winner_incentive_badge({"current_streak": 2}) != "🔥":
        fails.append("streak 2 badge")
    if winner_incentive_badge({"current_streak": 3}) != "💎":
        fails.append("streak 3 badge")
    if WINNER_BONUS_VOTES < 1:
        fails.append("winner bonus votes must be at least 1")

    extra_src = (ROOT / "cogs" / "extra_votes.py").read_text(encoding="utf-8")
    if "extra_votes:buy" not in extra_src or "create_extra_votes_checkout_session" not in extra_src:
        fails.append("extra-votes channel/button missing")
    billing_src = (ROOT / "cogs" / "billing.py").read_text(encoding="utf-8")
    if "kind" not in billing_src or "extra_votes" not in billing_src:
        fails.append("billing webhook must route extra_votes purchases")
    qa_src = (ROOT / "cogs" / "qa_channel.py").read_text(encoding="utf-8")
    if "qa:toggle:" not in qa_src or "admin_only" not in qa_src:
        fails.append("Q&A expand buttons / admin-only edit missing")
    sched_src = inspect.getsource(__import__("cogs.scheduler", fromlist=["SchedulerCog"]).SchedulerCog)
    if "record_winner_incentive" not in sched_src:
        fails.append("Friday close must record winner incentives")

    if fails:
        print("PART E LOGIC: FAIL")
        for f in fails:
            print(f"  • {f}")
        return 1
    print("PART E LOGIC: PASS")
    print("  • PLAYER/WINNER stack votes; NPC cannot")
    print("  • Final leaderboard random tie-break is stable")
    print("  • Extra-votes Stripe path + Q&A + winner streak present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
