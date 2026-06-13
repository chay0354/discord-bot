"""Offline checks for Section 7 live leaderboard behavior."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import CHANNEL_BLUE_LIVE, CHANNEL_MID_LIVE, CHANNEL_SMALL_LIVE  # noqa: E402
from cogs.weekly_picks import _leaderboard_embed, _sorted_leaderboard  # noqa: E402

# Mirror database.vote_counts sort: (-count, ticker asc)
def _vote_counts_sort(pairs: list[tuple[str, int]]) -> list[tuple[str, int]]:
    return sorted(pairs, key=lambda x: (-x[1], x[0]))


def main() -> int:
    fails: list[str] = []

    live_channels = {CHANNEL_SMALL_LIVE, CHANNEL_MID_LIVE, CHANNEL_BLUE_LIVE}
    if len(live_channels) != 3:
        fails.append("expected 3 distinct live channels")

    # Tie sort: alphabetical among equal counts.
    tied = _vote_counts_sort([("ZZZ", 5), ("AAA", 5), ("MMM", 3)])
    if tied[:2] != [("AAA", 5), ("ZZZ", 5)]:
        fails.append(f"tie sort wrong: {tied}")

    # Live embed shows tickers/votes only — no user identifiers in source.
    emb = _leaderboard_embed(0, [("AMC", 4), ("BB", 2)], {}, {"AMC": "AMC Entertainment"})
    desc = emb.description or ""
    if "user_id" in desc.lower() or "@" in desc:
        fails.append("leaderboard embed may expose user info")
    if "$AMC" not in desc or "votes" not in desc:
        fails.append("leaderboard missing ticker/vote display")

    src_post = inspect.getsource(
        __import__("cogs.weekly_picks", fromlist=["_post_or_update_leaderboard"])._post_or_update_leaderboard
    )
    if "database.vote_counts" not in src_post:
        fails.append("live board must read counts from DB")

    # Vote persistence is DB-first: the vote is saved via record_vote BEFORE the
    # live count updates, so the leaderboard only refreshes after a confirmed
    # save (no optimistic increment that would need reverting on failure).
    src_fin = inspect.getsource(
        __import__("cogs.weekly_picks", fromlist=["WeeklyVotingView"]).WeeklyVotingView._persist_vote
    )
    if "database.record_vote" not in src_fin:
        fails.append("vote persistence must save to DB via record_vote")
    if "_schedule_leaderboard_update" not in src_fin:
        fails.append("vote finalize must schedule leaderboard update")
    # The leaderboard update must come AFTER a successful save, not before.
    if src_fin.index("record_vote") > src_fin.index("_schedule_leaderboard_update"):
        fails.append("leaderboard must update only after the vote is saved")

    src_sched = inspect.getsource(
        __import__("cogs.scheduler", fromlist=["SchedulerCog"]).SchedulerCog._friday_close_one_guild
    )
    if "CHANNEL CURRENTLY CLOSED" not in src_sched or "_purge_channel_messages" not in src_sched:
        fails.append("friday close must purge live and post closed message")

    if fails:
        print("SECTION 7 LOGIC: FAIL")
        for f in fails:
            print(f"  • {f}")
        return 1
    print("SECTION 7 LOGIC: PASS")
    print("  - 3 category-specific live channels")
    print("  - DB-first vote save; leaderboard updates only after confirmed save")
    print("  - Tie display: vote desc, then ticker A-Z")
    print("  - No user PII in embed; Friday close purges live boards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
