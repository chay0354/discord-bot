"""Part D: styled channel/role names still resolve to the same logical keys."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import (
    CHANNEL_PICK_RESULTS,
    CHANNEL_SMALL_LIVE,
    CHANNEL_SMALL_TICKER,
    CHANNEL_SMALL_VOTE,
    ROLE_NPC,
    ROLE_PLAYER,
    ROLE_WINNER,
)
from discord_names import logical_role_key, names_match, normalize_discord_name


def check(cond: bool, label: str) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise SystemExit(1)


def main() -> int:
    check(normalize_discord_name("📊small-cap-𝖳𝖨𝖢𝖪𝖤𝖱") == "smallcapticker", "ticker emoji+style folds")
    check(normalize_discord_name("small-cap-ticker") == "smallcapticker", "plain ticker folds")
    check(names_match(CHANNEL_SMALL_TICKER, "small-cap-ticker"), "small ticker alias")
    check(names_match(CHANNEL_SMALL_LIVE, "small-cap-live"), "small live alias")
    check(names_match(CHANNEL_SMALL_VOTE, "small-cap"), "small vote alias")
    check(names_match("📢small-cap", "🗳️small-cap"), "megaphone vote still matches ballot")
    check(names_match("💠📊small-cap-𝖳𝖨𝖢𝖪𝖤𝖱", "small-cap-ticker"), "diamond ticker prefix folds")
    check(names_match("pick-results", "pick-results"), "old pick-results still matches itself")
    check(not names_match(CHANNEL_PICK_RESULTS, "pick-results"), "new pick-results name is distinct")
    check("livechosentickers" in normalize_discord_name(CHANNEL_PICK_RESULTS), "live-chosen-tickers canonical")
    check(logical_role_key("NPC") == "NPC", "plain NPC")
    check(logical_role_key(ROLE_NPC) == "NPC", "emoji NPC")
    check(logical_role_key(ROLE_PLAYER) == "PLAYER", "emoji PLAYER")
    check(logical_role_key(ROLE_WINNER) == "WINNER", "emoji WINNER")
    check(logical_role_key("🤖 NPC") == "NPC", "robot NPC")
    check(logical_role_key("💎 PLAYER") == "PLAYER", "diamond PLAYER")
    check(logical_role_key("🏆 WINNER") == "WINNER", "trophy WINNER")
    check(logical_role_key("bot-admin") is None, "bot-admin is not ADMIN")
    check(logical_role_key("ADMIN") == "ADMIN", "plain ADMIN")
    print("Part D name tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
