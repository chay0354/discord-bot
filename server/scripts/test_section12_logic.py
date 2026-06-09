"""Offline checks for Section 12 CRM/DB persistence."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database  # noqa: E402


def main() -> int:
    fails: list[str] = []

    db_src = (ROOT / "database.py").read_text(encoding="utf-8")
    appendix = (ROOT / "docs" / "APPENDIX_C.md").read_text(encoding="utf-8")
    restart_doc = (ROOT / "docs" / "RESTART_AND_STATE.md").read_text(encoding="utf-8")
    api_src = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
    weekly_src = (ROOT / "cogs" / "weekly_picks.py").read_text(encoding="utf-8")

    # 1) Users keyed by Discord ID
    if "discord_id" not in db_src or "upsert_user" not in db_src:
        fails.append("users table / upsert_user missing")

    # 2) Subscription persistence
    for field in ("status", "current_period_end", "canceled_at", "stripe_subscription_id"):
        if field not in db_src:
            fails.append(f"subscription field missing: {field}")

    # 3) Vote row fields
    vote_src = inspect.getsource(database.record_vote)
    for field in ("ticker", "category", "created_at", "role_at_vote"):
        if field not in vote_src:
            fails.append(f"vote persistence missing {field}")

    # 4) Early votes verifiable
    if "is_early" not in vote_src:
        fails.append("votes.is_early missing")

    # 5) Winners validity window
    if "add_winner" not in db_src or "expires_at" not in db_src:
        fails.append("winners validity persistence missing")
    winner_fn = db_src.split("def add_winner", 1)[1][:600]
    if "reason" not in winner_fn or "winning_tickers" not in winner_fn:
        fails.append("add_winner must persist reason and winning_tickers")

    # 6) Role validity
    if "removed_at" not in db_src or "active_winner_grants" not in db_src:
        fails.append("WINNER role validity tracking missing")

    # 7) Message IDs — DB helper exists but must be used OR recovery documented
    if "save_message_state" not in db_src:
        fails.append("message_state helper missing")
    sub_src = (ROOT / "cogs" / "submission_ui.py").read_text(encoding="utf-8")
    sched_src = (ROOT / "cogs" / "scheduler.py").read_text(encoding="utf-8")
    if "save_message_state" not in weekly_src and "save_message_state" not in sub_src:
        fails.append("message_state must be written from weekly_picks or submission_ui")
    if "_restore_message_ids_from_db" not in weekly_src:
        fails.append("restart must reload message_state from DB")
    if "list_message_states" not in db_src or "get_message_state" not in db_src:
        fails.append("message_state read helpers missing")

    # 8) Weekly schedule state
    if "game_cycles" not in db_src or "set_cycle_phase" not in db_src:
        fails.append("game_cycles schedule state missing")

    # 9) Restart docs + recovery
    if "hydrate_vote_state" not in weekly_src or "on_ready" not in weekly_src:
        fails.append("restart vote rehydration missing")
    if "Supabase" not in restart_doc:
        fails.append("restart persistence doc missing")

    # 10) CRM inspection path
    for path in ("/api/game/status", "/api/subscriptions", "/api/game/audit"):
        if path not in api_src:
            fails.append(f"CRM API path missing: {path}")

    # 11-12) Backup / restore documentation
    if "backup" not in appendix.lower() or "שחזור" not in appendix:
        fails.append("backup/restore documentation missing in APPENDIX_C")
    if "supabase db dump" not in appendix.lower() and "backup" not in appendix.lower():
        warnings.append("manual backup command should be documented")

    if fails:
        print("SECTION 12 LOGIC: FAIL")
        for f in fails:
            print(f"  • {f}")
        return 1

    print("SECTION 12 LOGIC: PASS")
    print("  • Supabase tables: users, subscriptions, votes, winners, game_cycles")
    print("  • CRM API + audit_logs; restart recovery documented")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
