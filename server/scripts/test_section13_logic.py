"""Offline checks for Section 13 admin reports, logs, and #mod."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cogs.scheduler import SchedulerCog, StepReport  # noqa: E402
from cogs import billing, submission_ui  # noqa: E402
import database  # noqa: E402


def main() -> int:
    fails: list[str] = []
    warns: list[str] = []

    sched_src = (ROOT / "cogs" / "scheduler.py").read_text(encoding="utf-8")
    billing_src = (ROOT / "cogs" / "billing.py").read_text(encoding="utf-8")
    sub_src = (ROOT / "cogs" / "submission_ui.py").read_text(encoding="utf-8")
    admin_src = (ROOT / "cogs" / "admin_tools.py").read_text(encoding="utf-8")
    db_src = (ROOT / "database.py").read_text(encoding="utf-8")

    monday = inspect.getsource(SchedulerCog._monday_open_one_guild)
    friday = inspect.getsource(SchedulerCog._friday_close_one_guild)
    early = inspect.getsource(SchedulerCog._tuesday_early_close_one_guild)

    # 1) Week open logged to #mod with channels + tickers
    if "_announce_report" not in monday or "StepReport" not in monday:
        fails.append("monday open must post StepReport to #mod")
    if "carried over from weekend selection" not in monday:
        fails.append("monday open report must mention tickers carried over")
    if "per_cat_counts" not in monday and "lists[cat]" not in monday:
        fails.append("monday open must track per-category ticker counts")
    # Individual ticker symbols are not listed in the #mod report today.
    if "_fmt_tickers" not in monday or "opened_mentions" not in monday:
        fails.append("monday open #mod must list ticker symbols and channel mentions")

    # 2) Week close logged to #mod
    if "_announce_report" not in friday or "friday_close" not in friday:
        fails.append("friday close must log to #mod and audit_logs")

    # 3) Early window end logged to #mod
    if "_announce_mod" not in early or "Early Winner Window Closed" not in early:
        fails.append("early window close must post to #mod")

    # 4) Weekend ticker picks logged
    if "ticker_pick" not in sub_src or "database.log_event" not in sub_src:
        fails.append("ticker picks must be audit-logged")

    # 5) Weekly votes persisted (DB acceptable)
    vote_src = inspect.getsource(database.record_vote)
    for field in ("ticker", "category", "user_id", "role_at_vote", "is_early", "created_at"):
        if field not in vote_src:
            fails.append(f"vote persistence missing {field}")

    # 6) PLAYER/WINNER grants logged
    for evt in ("player_role_granted", "winner_role_granted"):
        if evt not in billing_src and evt not in sched_src:
            fails.append(f"missing audit event {evt}")
    if "_mod_log" not in billing_src:
        fails.append("PLAYER stripe updates should also post to #mod")

    # 7) Role removals logged
    for evt in ("player_role_removed", "winner_role_removed"):
        if evt not in billing_src and evt not in sched_src:
            fails.append(f"missing audit event {evt}")

    # 8) Integration errors logged clearly
    for needle in ("stripe_webhook_error", "Stripe Webhook Error"):
        if needle not in billing_src:
            fails.append(f"billing missing stripe error logging: {needle}")
    for needle in ("PLAYER role permission error", "PLAYER role update failed"):
        if needle not in billing_src:
            fails.append(f"billing missing discord role error #mod posts: {needle}")
    # Market data: Finnhub is the source of truth for this bot.
    finnhub_src = (ROOT / "services" / "finnhub_client.py").read_text(encoding="utf-8")
    if "pop_last_error" not in finnhub_src or "_LAST_ERROR" not in finnhub_src:
        fails.append("finnhub_client must track API failures (pop_last_error)")
    if "market_data_api_error" not in sub_src:
        fails.append("ticker resolution must log Finnhub API failures to audit_logs")
    if "_post_mod_log_market_api_error" not in sub_src:
        fails.append("Finnhub API failures must be surfaced to #mod")
    if '"api_error"' not in sub_src and "api_error" not in sub_src:
        fails.append("ticker resolution must distinguish api_error from not_found")

    # 9) Week-close itemized step report
    if "class StepReport" not in sched_src:
        fails.append("StepReport missing")
    embed_src = inspect.getsource(StepReport.to_embed)
    if "failed step" not in embed_src and "fail" not in embed_src:
        fails.append("StepReport must itemize pass/fail steps")
    for step in (
        "Buttons were removed",
        "Leaderboard tables were posted",
        "Winners were announced",
        "WINNER roles were added",
    ):
        if step not in friday:
            fails.append(f"friday close report missing step: {step}")

    # 10) #mod admin-only
    if "mod_overwrites" not in admin_src or "CHANNEL_MOD" not in admin_src:
        fails.append("admin_tools must configure #mod overwrites")
    if "view_channel=False" not in admin_src:
        fails.append("#mod must hide channel from non-admins")

    # 11) Edge cases explained in reports
    if "NO TICKERS SELECTED" not in monday:
        fails.append("monday open must explain insufficient tickers in channel banner")
    if "_format_winner_report" not in sched_src:
        fails.append("winner report formatter missing")
    if '"no eligible winners"' not in friday and "no eligible winners" not in friday:
        fails.append("friday close must explain no-winner weeks")

    print("SECTION 13 — ADMIN REPORTS, LOGS & #mod")
    print("=" * 50)
    if fails:
        print("FAILURES:")
        for item in fails:
            print(f"  [FAIL] {item}")
    if warns:
        print("WARNINGS:")
        for item in warns:
            print(f"  [WARN] {item}")
    if not fails:
        print("All structural checks passed.")
        if warns:
            print("(See warnings for behavioral gaps that may still fail live criteria.)")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
