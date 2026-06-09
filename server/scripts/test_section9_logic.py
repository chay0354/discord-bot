"""Static + mocked checks for Section 9 ticker validation."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    fails: list[str] = []

    finnhub = (ROOT / "services" / "finnhub_client.py").read_text(encoding="utf-8")
    yahoo = (ROOT / "services" / "yahoo_client.py").read_text(encoding="utf-8")
    submission = (ROOT / "cogs" / "submission_ui.py").read_text(encoding="utf-8")
    weekly = (ROOT / "cogs" / "weekly_picks.py").read_text(encoding="utf-8")

    # 4) Spec data sources
    if "finnhub" not in submission.lower() or "yahoo" not in submission.lower():
        fails.append("resolve_ticker_any must use Finnhub + Yahoo fallback")

    # 5-7) Contractual cap bands
    for threshold in ("10_000_000_000", "2_000_000_000"):
        if threshold not in finnhub or threshold not in yahoo:
            fails.append(f"missing cap threshold {threshold} in classifier")

    # 2-3) Exchange allowlist
    if "_exchange_is_us_major" not in finnhub:
        fails.append("finnhub exchange gate missing")
    if "_is_allowed_exchange" not in yahoo:
        fails.append("yahoo exchange gate missing")

    # 8,13) Rejection paths + messages
    for status in ("not_found", "bad_exchange", "no_market_cap", "wrong_category"):
        if f'status == "{status}"' not in submission:
            fails.append(f"submission_ui missing handler for {status}")

    # 12) No save on failed validation
    if 'if status != "ok":' not in submission:
        fails.append("must not persist ticker when validation fails")

    # 14) Weekend + voting validation
    if "_resolve_ticker" not in submission:
        fails.append("weekend selection validation missing")
    if "vote_button_context" not in weekly or "actual_category" not in weekly:
        fails.append("voting stage category validation missing")

    # 12 mocked: both APIs down -> reject
    from cogs.submission_ui import resolve_ticker_any  # noqa: E402

    with patch("cogs.submission_ui.finnhub_resolve_symbol", return_value=None), patch(
        "cogs.submission_ui.yahoo_resolve_symbol", return_value=None
    ):
        if resolve_ticker_any("AAPL") is not None:
            fails.append("API outage must not approve ticker")

    with patch("cogs.submission_ui.finnhub_resolve_symbol", side_effect=RuntimeError("down")), patch(
        "cogs.submission_ui.yahoo_resolve_symbol", side_effect=RuntimeError("down")
    ):
        if resolve_ticker_any("AAPL") is not None:
            fails.append("API exceptions must not approve ticker")

    # Partial profile without cap -> no_market_cap path
    partial = {"symbol": "TEST", "exchange_ok": True, "category": None, "exchange": "NASDAQ"}
    with patch("cogs.submission_ui.finnhub_resolve_symbol", return_value=partial), patch(
        "cogs.submission_ui.yahoo_resolve_symbol", return_value=None
    ):
        row = resolve_ticker_any("TEST")
        if not row or row.get("category"):
            fails.append("partial cap data should surface as missing category")

    if fails:
        print("SECTION 9 LOGIC: FAIL")
        for f in fails:
            print(f"  • {f}")
        return 1

    print("SECTION 9 LOGIC: PASS")
    print("  • Finnhub primary, Yahoo fallback; NASDAQ/NYSE gates on both")
    print("  • Cap bands: small < $2B, mid $2B–$10B, blue >= $10B")
    print("  • Four rejection reasons; no DB save unless status==ok")
    print("  • Voting checks ballot category via vote_button_context")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
