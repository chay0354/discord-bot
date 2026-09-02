"""Apply Part D UX/UI names + permission overwrites on the live guild.

Renames ticker / vote / live / pick-results channels and NPC/PLAYER/WINNER
roles to the buyer emoji + styled LIVE/TICKER names, then reapplies
overwrites so a brand-new member sees only RULES.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ensure_game_permissions import main as ensure_main


if __name__ == "__main__":
    if "--channels-only" not in sys.argv:
        sys.argv.append("--channels-only")
    raise SystemExit(asyncio.run(ensure_main()))
