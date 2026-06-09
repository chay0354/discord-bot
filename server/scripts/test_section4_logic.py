"""Offline checks for Section 4 week-open & voting rules."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import NPC_VOTES_PER_CATEGORY, PLAYER_VOTES_PER_CATEGORY  # noqa: E402
from cogs.scheduler import SchedulerCog, _monday_9am_et_for_week, _next_monday_9am_et  # noqa: E402
from cogs.weekly_picks import _can_vote, _vote_limit_for  # noqa: E402
from database import record_vote  # noqa: E402


class FakeRole:
    def __init__(self, name: str):
        self.name = name


class FakeMember:
    def __init__(self, roles: list[str]):
        self.roles = [FakeRole(r) for r in roles]
        self.guild_permissions = type("P", (), {"administrator": False, "manage_guild": False})()
        self.id = 1


def main() -> int:
    fails: list[str] = []

    # Scheduler automation exists (Monday 09:00 ET).
    src = inspect.getsource(SchedulerCog._runner)
    if "_monday_open_all_guilds" not in src or "_next_monday_9am_et" not in src:
        fails.append("scheduler missing Monday auto-open wiring")

    # Vote quotas.
    if _vote_limit_for(FakeMember(["NPC"])) != NPC_VOTES_PER_CATEGORY:
        fails.append("NPC vote limit wrong")
    if _vote_limit_for(FakeMember(["PLAYER"])) != PLAYER_VOTES_PER_CATEGORY:
        fails.append("PLAYER vote limit wrong")
    if _vote_limit_for(FakeMember(["WINNER"])) != PLAYER_VOTES_PER_CATEGORY:
        fails.append("WINNER vote limit wrong")

    # Roleless cannot vote.
    if _can_vote(FakeMember([])):
        fails.append("no-role user should not vote")

    # record_vote stores required fields.
    sig = inspect.signature(record_vote)
    for field in ("user_id", "role_at_vote", "category", "ticker"):
        if field not in sig.parameters:
            fails.append(f"record_vote missing {field}")
    src_vote = inspect.getsource(record_vote)
    if "created_at" not in src_vote:
        fails.append("record_vote missing created_at timestamp")

    if fails:
        print("SECTION 4 LOGIC: FAIL")
        for f in fails:
            print(f"  • {f}")
        return 1
    print("SECTION 4 LOGIC: PASS")
    print("  • Monday 09:00 ET scheduler wired")
    print(f"  • NPC={NPC_VOTES_PER_CATEGORY}, PLAYER/WINNER={PLAYER_VOTES_PER_CATEGORY} per category")
    print("  • record_vote persists user_id, role, ticker, category, timestamp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
