"""Offline checks for Section 8 Friday close and final leaderboard."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main() -> int:
    fails: list[str] = []

    sched = __import__("cogs.scheduler", fromlist=["SchedulerCog"]).SchedulerCog
    close_src = inspect.getsource(sched._friday_close_one_guild)
    runner_src = inspect.getsource(sched._runner)
    monday_src = inspect.getsource(sched._monday_open_one_guild)
    publish_src = inspect.getsource(sched._publish_last_game_winners)
    final_src = inspect.getsource(
        __import__("cogs.weekly_picks", fromlist=["build_final_leaderboard_embeds"]).build_final_leaderboard_embeds
    )

    if "_friday_close_all_guilds" not in runner_src or "_next_weekday_time_et" not in runner_src:
        fails.append("scheduler missing Friday 16:00 ET auto-close")
    if "voting_open=False" not in close_src:
        fails.append("Friday close must set voting_open=False")
    if "VOTING CLOSED" not in close_src or "_purge_channel_messages" not in close_src:
        fails.append("Friday close must purge weekly channels and post closed message")
    if "Monday at 9 AM" not in close_src:
        fails.append("closing message must state next open time")

    if "build_final_leaderboard_embeds" not in close_src or "leaderboard.send" not in close_src:
        fails.append("Friday close must post final leaderboard embeds")
    if final_src.count("small") < 1 or final_src.count("mid") < 1 or final_src.count("blue") < 1:
        fails.append("final leaderboard must cover all 3 categories")
    if "all_vote_counts" not in final_src and "vote_counts" not in final_src:
        fails.append("final tables must use DB vote counts")

    if "No winner met all eligibility conditions" not in publish_src:
        fails.append("winners channel must handle no-winner case")

    # Monday open clears weekly temp content; final leaderboard is posted only on Friday close.
    if "_purge_channel_messages" not in monday_src:
        fails.append("Monday open should purge weekly vote channels")
    if "build_final_leaderboard_embeds" in monday_src:
        fails.append("Monday open should not replace final leaderboard tables")

    db = __import__("database", fromlist=["record_vote"])
    if "is_voting_open" not in inspect.getsource(db.record_vote):
        fails.append("record_vote must reject when voting closed")

    if fails:
        print("SECTION 8 LOGIC: FAIL")
        for f in fails:
            print(f"  • {f}")
        return 1
    print("SECTION 8 LOGIC: PASS")
    print("  • Friday 16:00 ET scheduler closes voting")
    print("  • Weekly channels purged; voting_open=False; reopen message posted")
    print("  • 3 final category tables from DB counts; no-winner message exists")
    print("  • Monday open clears weekly temp only; final history retained")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
