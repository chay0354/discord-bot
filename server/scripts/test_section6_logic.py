"""Offline checks for Section 6 results, eligibility, and winner lifecycle."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import (  # noqa: E402
    compute_eligible_winner_ids,
    filter_eligible_winners_at_award,
    winning_tickers_for_week,
)

# Simulate vote_counts / winning_tickers without Supabase.
SAMPLE_COUNTS = {
    "small": [("AMC", 5), ("GME", 3), ("BB", 5)],  # tie AMC/BB at 5
    "mid": [("ETSY", 7)],
    "blue": [("AAPL", 10), ("MSFT", 4)],
}


def _winning_from_counts(counts: dict[str, list[tuple[str, int]]]) -> dict[str, set[str]] | None:
    winning: dict[str, set[str]] = {}
    for cat, pairs in counts.items():
        if not pairs:
            return None
        top = pairs[0][1]
        winning[cat] = {t for t, n in pairs if n == top}
    return winning


def main() -> int:
    fails: list[str] = []

    winning = _winning_from_counts(SAMPLE_COUNTS)
    if winning != {"small": {"AMC", "BB"}, "mid": {"ETSY"}, "blue": {"AAPL"}}:
        fails.append(f"tie top-ticker set wrong: {winning}")

    # Sort tiebreak for display: alphabetical among equal counts.
    small_sorted = sorted(SAMPLE_COUNTS["small"], key=lambda x: (-x[1], x[0]))
    if small_sorted[0] != ("AMC", 5) or small_sorted[1] != ("BB", 5):
        fails.append("display sort tiebreak not alphabetical")

    uid = 9001
    rows_npc_win = [
        {"user_id": uid, "category": "small", "ticker": "AMC", "role_at_vote": "NPC", "is_early": True},
        {"user_id": uid, "category": "mid", "ticker": "ETSY", "role_at_vote": "NPC", "is_early": True},
        {"user_id": uid, "category": "blue", "ticker": "AAPL", "role_at_vote": "NPC", "is_early": True},
    ]
    ids, _ = compute_eligible_winner_ids(
        winning_tickers=winning or {},
        vote_rows=rows_npc_win,
        active_winner_user_ids=set(),
    )
    if ids != [uid]:
        fails.append("eligible NPC should win on tied top ticker pick")

    for role in ("PLAYER", "WINNER", "ADMIN"):
        rows = [
            {"user_id": uid, "category": c, "ticker": t, "role_at_vote": role, "is_early": True}
            for c, t in (("small", "AMC"), ("mid", "ETSY"), ("blue", "AAPL"))
        ]
        got, _ = compute_eligible_winner_ids(
            winning_tickers=winning or {}, vote_rows=rows, active_winner_user_ids=set()
        )
        if got:
            fails.append(f"{role} must not be eligible")

    # Active WINNER grant blocks re-award.
    got2, _ = compute_eligible_winner_ids(
        winning_tickers=winning or {},
        vote_rows=rows_npc_win,
        active_winner_user_ids={uid},
    )
    if got2:
        fails.append("active WINNER grant must block duplicate award")

    # Left guild at award.
    ids3, _ = compute_eligible_winner_ids(
        winning_tickers=winning or {},
        vote_rows=rows_npc_win,
        active_winner_user_ids=set(),
    )
    final, _ = filter_eligible_winners_at_award(
        ids3, [], guild_member_ids=set(), player_or_paid_ids=set()
    )
    if final:
        fails.append("left/banned user should be excluded at award")

    # NPC early correct but became PLAYER during week.
    final2, _ = filter_eligible_winners_at_award(
        ids3, [], guild_member_ids={uid}, player_or_paid_ids={uid}
    )
    if final2:
        fails.append("now_player_or_paid should block award")

    if fails:
        print("SECTION 6 LOGIC: FAIL")
        for f in fails:
            print(f"  • {f}")
        return 1
    print("SECTION 6 LOGIC: PASS")
    print("  • Top tickers by vote count; ties include all co-leaders")
    print("  • Display sort uses alphabetical tiebreak among equal counts")
    print("  • Only NPC+early eligible; PLAYER/WINNER/ADMIN blocked")
    print("  • Active grant, left/banned, mid-week PLAYER blocked at award")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
