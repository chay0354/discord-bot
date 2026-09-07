"""Offline checks for Section 10 user messages, buttons & basic UX."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cogs.scheduler import SchedulerCog  # noqa: E402
import game_copy  # noqa: E402
from cogs.weekly_picks import (  # noqa: E402
    WeeklyVotingView,
    _banner_description_with_timer,
    _build_voting_open_embed,
    _weekly_closed_banner,
)


def main() -> int:
    fails: list[str] = []

    wp = (ROOT / "cogs" / "weekly_picks.py").read_text(encoding="utf-8")
    sub = (ROOT / "cogs" / "submission_ui.py").read_text(encoding="utf-8")
    sched = inspect.getsource(SchedulerCog._monday_open_one_guild)
    close = inspect.getsource(SchedulerCog._friday_close_one_guild)

    # 1) Vote-open message
    emb = _build_voting_open_embed(0, None)
    if (emb.title or "").upper() != "VOTING OPEN":
        fails.append("vote-open embed title must be VOTING OPEN")

    # 2) 24h early window in open banner
    desc = _banner_description_with_timer(0, __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    if "Early WINNER window" not in desc and "<t:" not in desc:
        fails.append("vote-open must show early-window deadline")

    # 3) Friday close time in vote-open message
    if "Friday" not in desc or "4 PM EST" not in desc:
        fails.append("vote-open banner must state Friday 4 PM EST close")

    # 4-5) Vote confirmation copy
    if "YOU PICKED" not in game_copy.vote_picked_line("AAPL", "small", 1, 5):
        fails.append("vote confirmation message missing")
    if "_vote_confirmation_message" not in wp:
        fails.append("vote confirmation helper missing")

    # 6) Vote-limit message
    limit_msg = game_copy.vote_limit_message("small", is_npc=True)
    if "Voting Limit" not in limit_msg:
        fails.append("vote-limit message missing")

    # 7) No-permission messages
    if "You need a game role before you can vote" not in wp:
        fails.append("vote no-role message missing")
    if "Only PLAYER subscribers" not in sub:
        fails.append("pre-vote no-permission message missing")

    # 8) Friday close message
    if "VOTING CLOSED" not in close:
        fails.append("Friday close must post VOTING CLOSED")
    closed_desc = game_copy.voting_closed_description()
    if "Monday at 9 AM" not in closed_desc:
        fails.append("Friday close must state next reopen time")
    if "top-ranked tickers and winners" not in closed_desc:
        fails.append("Friday close must point users to results channels")

    # 9-10) Buttons stay active: persistent views + restart recovery
    if "timeout=None" not in inspect.getsource(WeeklyVotingView.__init__):
        fails.append("WeeklyVotingView must be persistent (timeout=None)")
    if "add_view" not in sched:
        fails.append("Monday open must register persistent vote views")
    if "add_view" not in wp or "recovery" not in wp:
        fails.append("on_ready must re-register vote views after restart")
    if "defer(ephemeral=True)" not in wp:
        fails.append("vote handler must defer to avoid interaction timeout")
    if 'custom_id="pre_voting:open_picker"' not in sub:
        fails.append("OpenPickerView must use stable custom_id for persistence")

    # 11) Clickable channel links where required
    if "_channel_mention_or_text" not in wp or ".mention" not in wp:
        fails.append("vote UX must resolve channel mentions")
    if "return ch.mention" not in sub:
        fails.append("wrong-category redirect should use channel mention when channel exists")
    if "mention_live" not in (ROOT / "game_copy.py").read_text(encoding="utf-8"):
        fails.append("vote-open banner must resolve live channel mention")

    if fails:
        print("SECTION 10 LOGIC: FAIL")
        for f in fails:
            print(f"  • {f}")
        return 1

    print("SECTION 10 LOGIC: PASS")
    print("  • VOTING OPEN + early-window countdown on Monday")
    print("  • Vote confirm/limit/no-role messages present")
    print("  • Friday VOTING CLOSED + persistent buttons + restart recovery")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
