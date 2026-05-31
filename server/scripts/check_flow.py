from __future__ import annotations

import importlib
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

if load_dotenv:
    load_dotenv(ROOT / ".env")


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str


class FlowChecker:
    def __init__(self) -> None:
        self.results: list[CheckResult] = []

    def pass_(self, name: str, detail: str = "ok") -> None:
        self.results.append(CheckResult(name, "PASS", detail))

    def fail(self, name: str, detail: str) -> None:
        self.results.append(CheckResult(name, "FAIL", detail))

    def skip(self, name: str, detail: str) -> None:
        self.results.append(CheckResult(name, "SKIP", detail))

    def run(self, name: str, fn: Callable[[], str | None]) -> None:
        try:
            detail = fn() or "ok"
            self.pass_(name, detail)
        except SkipCheck as exc:
            self.skip(name, str(exc))
        except Exception as exc:
            self.fail(name, f"{exc.__class__.__name__}: {exc}")
            traceback.print_exc()

    def print_report(self) -> int:
        print("\nStock Bot Flow Check")
        print("=" * 60)
        for result in self.results:
            print(f"[{result.status}] {result.name}: {result.detail}")
        print("=" * 60)
        failed = [r for r in self.results if r.status == "FAIL"]
        skipped = [r for r in self.results if r.status == "SKIP"]
        print(f"Summary: {len(self.results) - len(failed) - len(skipped)} passed, {len(skipped)} skipped, {len(failed)} failed")
        return 1 if failed else 0


class SkipCheck(RuntimeError):
    pass


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SkipCheck(f"{name} is not set")
    return value


def check_imports() -> str:
    modules = [
        "config",
        "database",
        "services.yahoo_client",
        "services.finnhub_client",
        "services.stripe_client",
        "cogs.admin_tools",
        "cogs.submission_ui",
        "cogs.weekly_picks",
        "cogs.scheduler",
        "cogs.billing",
    ]
    for module in modules:
        importlib.import_module(module)
    return f"imported {len(modules)} modules"


def check_required_env_shape() -> str:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SkipCheck("DISCORD_TOKEN is not set")
    configured = ["DISCORD_TOKEN"]
    if os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        configured.append("SUPABASE_SERVICE_ROLE_KEY")
    if os.getenv("FINNHUB_API_KEY"):
        configured.append("FINNHUB_API_KEY")
    if os.getenv("STRIPE_SECRET_KEY") and os.getenv("STRIPE_MONTHLY_PRICE_ID"):
        configured.append("STRIPE")
    return "configured: " + ", ".join(configured)


def check_supabase_schema() -> str:
    require_env("SUPABASE_SERVICE_ROLE_KEY")
    import database

    database.init_db()
    database._request("GET", "users", query="?select=discord_id&limit=1")
    database._request("GET", "subscriptions", query="?select=discord_id&limit=1")
    database._request("GET", "game_cycles", query="?select=id&limit=1")
    database._request("GET", "ticker_picks", query="?select=id&limit=1")
    database._request("GET", "votes", query="?select=id&limit=1")
    database._request("GET", "winners", query="?select=id&limit=1")
    database._request("GET", "audit_logs", query="?select=id&limit=1")
    return "Supabase REST schema reachable"


def check_database_game_flow() -> str:
    require_env("SUPABASE_SERVICE_ROLE_KEY")
    import database

    guild_id = 999_000_111_222
    user_id = 999_000_111_223
    week_key = "flow-check-W00"
    ticker = "AAPL"

    # Clean stale rows from previous flow checks.
    database._request("DELETE", "votes", query=f"?guild_id=eq.{guild_id}&week_key=eq.{week_key}")
    database._request("DELETE", "ticker_picks", query=f"?guild_id=eq.{guild_id}&week_key=eq.{week_key}")
    database._request("DELETE", "winners", query=f"?guild_id=eq.{guild_id}&week_key=eq.{week_key}")
    database._request("DELETE", "game_cycles", query=f"?guild_id=eq.{guild_id}&week_key=eq.{week_key}")

    database.upsert_user(user_id, username="flow-check")
    database.ensure_cycle(guild_id, week_key)
    ok, reason = database.add_ticker_pick(
        guild_id,
        week_key,
        "blue",
        ticker,
        user_id,
        market_cap=3_000_000_000_000,
        exchange="NASDAQ",
    )
    if not ok:
        raise RuntimeError(f"ticker pick failed: {reason}")

    database.set_cycle_phase(
        guild_id,
        week_key,
        status="voting",
        ticker_selection_open=False,
        voting_open=True,
        early_window_open=True,
    )
    ok, reason = database.record_vote(guild_id, week_key, "blue", ticker, user_id, "NPC", True)
    if not ok:
        raise RuntimeError(f"vote failed: {reason}")
    counts = database.vote_counts(guild_id, week_key, "blue")
    if counts[:1] != [(ticker, 1)]:
        raise RuntimeError(f"unexpected vote counts: {counts}")

    database.set_cycle_phase(
        guild_id,
        week_key,
        status="closed",
        ticker_selection_open=False,
        voting_open=False,
        early_window_open=False,
        friday_close_at=datetime.now(timezone.utc).isoformat(),
    )

    # Clean test data, leaving the user row harmless for audit/debug correlation.
    database._request("DELETE", "votes", query=f"?guild_id=eq.{guild_id}&week_key=eq.{week_key}")
    database._request("DELETE", "ticker_picks", query=f"?guild_id=eq.{guild_id}&week_key=eq.{week_key}")
    database._request("DELETE", "winners", query=f"?guild_id=eq.{guild_id}&week_key=eq.{week_key}")
    database._request("DELETE", "game_cycles", query=f"?guild_id=eq.{guild_id}&week_key=eq.{week_key}")
    return "cycle -> ticker pick -> vote -> close works"


def check_yahoo_market_data() -> str:
    from services.yahoo_client import validate_symbol_for_category

    row = validate_symbol_for_category("AAPL", "blue")
    if not row:
        raise RuntimeError("AAPL did not validate as blue chip")
    return f"AAPL validated on {row.get('exchange')}"


def check_finnhub_quote() -> str:
    require_env("FINNHUB_API_KEY")
    from services.finnhub_client import get_quote

    quote = get_quote("AAPL")
    if not quote or quote.current_price is None:
        raise RuntimeError("no AAPL quote returned")
    return f"AAPL quote returned: ${quote.current_price:.2f}"


def check_scheduler_week_keys() -> str:
    import database

    friday_after_close = datetime(2026, 5, 15, 21, tzinfo=timezone.utc)
    current = database.week_key_for(friday_after_close)
    next_selection = database.ticker_selection_week_key_for(friday_after_close)
    if current == next_selection:
        raise RuntimeError("Friday after close did not map to next ticker-selection week")
    return f"Friday close maps {current} -> {next_selection}"


def check_stripe_config() -> str:
    if not os.getenv("STRIPE_SECRET_KEY") or not os.getenv("STRIPE_MONTHLY_PRICE_ID"):
        raise SkipCheck("Stripe env vars are not set")
    from services.stripe_client import _settings

    _settings()
    return "Stripe settings available"


def main() -> int:
    checker = FlowChecker()
    checker.run("Python imports", check_imports)
    checker.run("Required env shape", check_required_env_shape)
    checker.run("Supabase schema access", check_supabase_schema)
    checker.run("Supabase game flow", check_database_game_flow)
    checker.run("Yahoo validation", check_yahoo_market_data)
    checker.run("Finnhub quote", check_finnhub_quote)
    checker.run("Scheduler week-key logic", check_scheduler_week_keys)
    checker.run("Stripe config", check_stripe_config)
    return checker.print_report()


if __name__ == "__main__":
    raise SystemExit(main())
