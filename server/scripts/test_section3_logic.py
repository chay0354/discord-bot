"""Offline checks for Section 3 ticker-selection rules (no Discord/Supabase)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import TICKER_LIMIT_PER_CATEGORY  # noqa: E402
import database  # noqa: E402
from cogs.submission_ui import _can_choose_weekly_ticker  # noqa: E402


class FakeRole:
    def __init__(self, name: str):
        self.name = name


class FakeMember:
    def __init__(self, roles: list[str]):
        self.roles = [FakeRole(r) for r in roles]
        self.guild_permissions = type("P", (), {"administrator": False, "manage_guild": False})()


def main() -> int:
    fails: list[str] = []

    if TICKER_LIMIT_PER_CATEGORY != 20:
        fails.append(f"TICKER_LIMIT_PER_CATEGORY={TICKER_LIMIT_PER_CATEGORY}, expected 20")

    # Weekend picks target the upcoming Monday week; Monday open reads that same key.
    sat = datetime(2026, 6, 6, 15, 0, tzinfo=timezone.utc)  # Saturday
    mon = datetime(2026, 6, 8, 13, 0, tzinfo=timezone.utc)  # Monday 09:00 ET ≈ 13:00 UTC (EDT)
    wk_sat = database.ticker_selection_week_key_for(sat)
    wk_mon = database.week_key_for(mon)
    if wk_sat != wk_mon:
        fails.append(f"week key mismatch: weekend pick {wk_sat} vs monday open {wk_mon}")

    # Role gate: NPC blocked, PLAYER/WINNER allowed.
    if _can_choose_weekly_ticker(FakeMember(["NPC"])):
        fails.append("NPC should not choose tickers")
    if not _can_choose_weekly_ticker(FakeMember(["PLAYER"])):
        fails.append("PLAYER should choose tickers")
    if not _can_choose_weekly_ticker(FakeMember(["WINNER"])):
        fails.append("WINNER should choose tickers")

    if fails:
        print("SECTION 3 LOGIC: FAIL")
        for f in fails:
            print(f"  • {f}")
        return 1
    print("SECTION 3 LOGIC: PASS")
    print(f"  • 20-ticker limit constant OK")
    print(f"  • weekend→monday week key aligned ({wk_sat})")
    print(f"  • NPC blocked / PLAYER+WINNER allowed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
