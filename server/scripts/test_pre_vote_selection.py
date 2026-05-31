"""Simulate pre-vote ticker selection for every category (live Finnhub/Yahoo).

Runs the same validation path as ``StockPickerView._resolve_ticker`` for each
symbol against each channel (small / mid / large cap). Reports which tickers
would be **accepted** vs rejected and why.

Usage:
  python server/scripts/test_pre_vote_selection.py           # example lists, 30 per category
  python server/scripts/test_pre_vote_selection.py --all     # full EXAMPLE_STOCKS pools
  python server/scripts/test_pre_vote_selection.py --symbols AAPL,PLTR,GOOG

Exit code 1 if any symbol that should work in its home category is rejected.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

from config import CATEGORY_TITLES, CATEGORIES  # noqa: E402
from cogs.submission_ui import resolve_ticker_any  # noqa: E402
from services.example_stocks import EXAMPLE_STOCKS  # noqa: E402
from services.yahoo_client import category_for_channel  # noqa: E402

# Channel names used in Discord (must match config / guild).
CHANNEL_BY_CATEGORY = {
    "small": "small-cap-ticker",
    "mid": "mid-cap-ticker",
    "blue": "large-cap-ticker",
}

# Tickers verified live against current Finnhub market-cap tiers (May 2026).
# Note: F, A, PLTR, RIVN, SOFI, etc. moved to Large Cap — they belong in #large-cap-ticker.
MUST_ACCEPT = {
    "small": ["AMC", "GREE", "PRTS", "BLNK", "KULR", "GPRO"],
    "mid": ["ETSY", "CROX", "WING", "SHAK", "CHWY", "BROS"],
    "blue": ["AAPL", "MSFT", "NVDA", "GOOG", "GOOGL", "BRK.B", "AMD", "SHOP"],
}

# Former small/mid picks that grew — must resolve but fail in the wrong channel.
MUST_ROUTE_ELSEWHERE = [
    ("PLTR", "blue"),
    ("F", "blue"),
    ("GME", "mid"),
    ("SOFI", "blue"),
]

# Should always fail regardless of category.
MUST_REJECT = ["ZVZZT", "NOTAREALTICKER", ""]


def simulate_resolve(channel_name: str, query: str) -> tuple[str, str, dict | None]:
    """Mirror ``StockPickerView._resolve_ticker`` without Discord."""
    q = query.upper().strip().lstrip("$")
    category = category_for_channel(channel_name)
    if not q:
        return "not_found", q, None

    row = resolve_ticker_any(q)
    if not row:
        return "not_found", q, None
    if not row.get("exchange_ok"):
        return "bad_exchange", q, row
    actual_category = row.get("category")
    if not actual_category:
        return "no_market_cap", q, row
    if actual_category != category:
        return "wrong_category", q, row
    return "ok", q, {**row, "category": category}


def _fmt_row(sym: str, status: str, row: dict | None, expected_cat: str) -> str:
    if status == "ok":
        cap = row.get("marketCap") if row else "?"
        exch = (row or {}).get("exchange", "?")
        name = (row or {}).get("shortName", "")[:28]
        return f"  OK   ${sym:8}  {exch:12}  cap={cap}  {name}"
    detail = ""
    if row:
        actual = row.get("category")
        if status == "wrong_category" and actual:
            detail = f" -> actually {CATEGORY_TITLES.get(actual, actual)}"
        elif status == "bad_exchange":
            detail = f" -> {row.get('exchange', '?')}"
    return f"  FAIL ${sym:8}  {status:16}{detail}"


def run_category(
    category: str,
    symbols: list[str],
    *,
    delay_s: float,
) -> tuple[int, int, list[str]]:
    channel = CHANNEL_BY_CATEGORY[category]
    title = CATEGORY_TITLES[category]
    print(f"\n{'=' * 72}")
    print(f"CHANNEL #{channel}  ({title})  —  {len(symbols)} symbol(s)")
    print(f"{'=' * 72}")

    ok_count = 0
    fail_lines: list[str] = []
    for raw in symbols:
        sym = str(raw).upper().strip().lstrip("$")
        if not sym:
            continue
        status, _, row = simulate_resolve(channel, sym)
        line = _fmt_row(sym, status, row, category)
        if status == "ok":
            ok_count += 1
            print(line)
        else:
            fail_lines.append(line)
            print(line)
        if delay_s:
            time.sleep(delay_s)

    fail_count = len(symbols) - ok_count
    rate = (100.0 * ok_count / len(symbols)) if symbols else 0.0
    print(f"\n  Summary: {ok_count}/{len(symbols)} accepted ({rate:.0f}%)")
    return ok_count, fail_count, fail_lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate pre-vote ticker selection per category")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Test full EXAMPLE_STOCKS lists (slow; many Finnhub calls)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Max symbols per category from EXAMPLE_STOCKS when not using --all (default 30)",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="Comma-separated extra symbols to test in their natural category channel",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.12,
        help="Seconds between API calls (default 0.12)",
    )
    parser.add_argument(
        "--regression-only",
        action="store_true",
        help="Skip EXAMPLE_STOCKS pools (faster; core resolver checks only)",
    )
    args = parser.parse_args()

    failures: list[str] = []
    total_ok = 0
    total_tested = 0

    # 1) Regression: MUST_ACCEPT in home category
    print("\n### REGRESSION — must accept in home category ###")
    for cat in CATEGORIES:
        syms = MUST_ACCEPT[cat]
        ok, _, fails = run_category(cat, syms, delay_s=args.delay)
        total_ok += ok
        total_tested += len(syms)
        for line in fails:
            failures.append(f"{cat}: {line.strip()}")

    # 2) Symbols that resolve but belong in another channel (buyer typing in wrong room)
    print("\n### REGRESSION — reroute to correct category ###")
    for sym, home in MUST_ROUTE_ELSEWHERE:
        row = resolve_ticker_any(sym)
        if not row or not row.get("category"):
            msg = f"  FAIL ${sym} — expected to resolve to {home}, got not_found"
            print(msg)
            failures.append(msg)
            continue
        actual = row["category"]
        if actual != home:
            msg = f"  FAIL ${sym} — cap tier changed: expected {home}, now {actual}"
            print(msg)
            failures.append(msg)
            continue
        for cat in CATEGORIES:
            if cat == home:
                continue
            status, _, _ = simulate_resolve(CHANNEL_BY_CATEGORY[cat], sym)
            if status == "ok":
                msg = f"  FAIL ${sym} wrongly accepted in {cat}"
                print(msg)
                failures.append(msg)
            else:
                print(f"  OK   ${sym} rejected in #{CHANNEL_BY_CATEGORY[cat]} ({status})")

    # 3) MUST_REJECT
    print("\n### REGRESSION — must reject junk symbols ###")
    for sym in MUST_REJECT:
        if not sym:
            continue
        for cat in CATEGORIES:
            status, _, _ = simulate_resolve(CHANNEL_BY_CATEGORY[cat], sym)
            if status == "ok":
                msg = f"  FAIL ${sym} accepted in {cat} (should reject)"
                print(msg)
                failures.append(msg)
            else:
                print(f"  OK   ${sym or '(empty)'} rejected in {cat} ({status})")

    # 4) EXAMPLE_STOCKS pools (static lists may be stale vs live market caps)
    if not args.regression_only:
        print("\n### EXAMPLE_STOCKS pools — simulate pre-vote picks (labeled category) ###")
        for cat in CATEGORIES:
            pool = EXAMPLE_STOCKS.get(cat, [])
            if not args.all:
                pool = pool[: max(0, args.limit)]
            ok, _, fails = run_category(cat, pool, delay_s=args.delay)
            total_ok += ok
            total_tested += len(pool)
            for line in fails:
                failures.append(f"{cat}/examples: {line.strip()}")

    # 5) Optional extra symbols (user-supplied)
    if args.symbols.strip():
        print("\n### CUSTOM symbols ###")
        extra = [s.strip() for s in args.symbols.split(",") if s.strip()]
        for sym in extra:
            row = resolve_ticker_any(sym)
            if not row or not row.get("category"):
                print(f"  ??   ${sym.upper()} — could not resolve; skipping channel routing")
                continue
            cat = row["category"]
            ok, _, fails = run_category(cat, [sym], delay_s=0)
            total_ok += ok
            total_tested += 1
            for line in fails:
                failures.append(f"custom/{sym}: {line.strip()}")

    # 6) Cross-check: correct-category symbols must fail in wrong channels
    print("\n### SANITY — home tickers rejected in wrong channels ###")
    cross_cases = [("AAPL", "blue", "small"), ("AMC", "small", "blue"), ("ETSY", "mid", "small")]
    for sym, home, wrong in cross_cases:
        status, _, _ = simulate_resolve(CHANNEL_BY_CATEGORY[home], sym)
        if status != "ok":
            failures.append(f"cross-setup: ${sym} should ok in {home}, got {status}")
            print(f"  FAIL ${sym} in home #{CHANNEL_BY_CATEGORY[home]} -> {status}")
        wrong_status, _, _ = simulate_resolve(CHANNEL_BY_CATEGORY[wrong], sym)
        if wrong_status == "ok":
            failures.append(f"cross: ${sym} wrongly accepted in {wrong}")
        else:
            print(f"  OK   ${sym} in #{CHANNEL_BY_CATEGORY[wrong]} -> {wrong_status}")

    print("\n" + "=" * 72)
    print(f"OVERALL: {total_ok}/{total_tested} accepted when submitted in labeled category")
    print("(Low example-pool % usually means EXAMPLE_STOCKS lists are stale, not a resolver bug.)")

    # Regression failures (must-accept + cross + reroute) vs example-list drift
    reg_failures = [f for f in failures if not f.startswith(("small/examples", "mid/examples", "blue/examples"))]
    example_failures = [f for f in failures if f.startswith(("small/examples", "mid/examples", "blue/examples"))]

    if reg_failures:
        print(f"\nREGRESSION FAILURES ({len(reg_failures)}):")
        for f in reg_failures[:20]:
            print(f"  • {f}")

    if example_failures:
        print(f"\nEXAMPLE_STOCKS DRIFT ({len(example_failures)} symbols not accepted in labeled channel):")
        for f in example_failures[:15]:
            print(f"  • {f}")
        if len(example_failures) > 15:
            print(f"  … and {len(example_failures) - 15} more")

    if reg_failures:
        print("\nRESULT: FAIL (resolver regression)")
        return 1
    if example_failures:
        print("\nRESULT: PASS (resolver OK; example lists need refresh)")
        return 0
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
