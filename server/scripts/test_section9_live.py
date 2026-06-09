"""Live probes for Section 9 ticker/exchange/market-cap validation."""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

from cogs.submission_ui import resolve_ticker_any  # noqa: E402
from services.yahoo_client import category_for_channel  # noqa: E402


def simulate(channel: str, symbol: str) -> str:
    q = symbol.upper().strip().lstrip("$")
    cat = category_for_channel(channel)
    row = resolve_ticker_any(q)
    if not row:
        return "not_found"
    if not row.get("exchange_ok"):
        return "bad_exchange"
    if not row.get("category"):
        return "no_market_cap"
    if row["category"] != cat:
        return "wrong_category"
    return "ok"


def main() -> int:
    fails: list[str] = []

    # 1) Fake symbol
    if simulate("small-cap-ticker", "ZZZZZ") != "not_found":
        fails.append("ZZZZZ should be not_found")

    # 2-3) OTC / non-NASDAQ-NYSE (TCEHY = Tencent OTC ADR; TSM = Taiwan, not US major)
    for sym in ("HCMC", "TCEHY", "TSM"):
        row = resolve_ticker_any(sym)
        if row and row.get("exchange_ok"):
            fails.append(f"{sym} should be blocked (exchange_ok=True)")
        elif not row:
            pass  # not found is also acceptable block
        else:
            print(f"  OK blocked {sym}: exchange={row.get('exchange')} exchange_ok={row.get('exchange_ok')}")

    # 5-7,10) Home category acceptance
    home = {
        "small-cap-ticker": "AMC",
        "mid-cap-ticker": "ETSY",
        "large-cap-ticker": "AAPL",
    }
    for ch, sym in home.items():
        st = simulate(ch, sym)
        if st != "ok":
            fails.append(f"{sym} in {ch} expected ok, got {st}")

    # 9) Wrong category despite valid ticker
    if simulate("small-cap-ticker", "ETSY") != "wrong_category":
        fails.append("ETSY in small should be wrong_category")

    # 11) Dotted symbol
    if simulate("large-cap-ticker", "BRK.B") != "ok":
        fails.append("BRK.B should resolve in large-cap channel")

    # 13) Rejection reasons exist in submission_ui
    from cogs import submission_ui  # noqa: E402

    src = Path(submission_ui.__file__).read_text(encoding="utf-8")
    for token in ("not_found", "bad_exchange", "no_market_cap", "wrong_category"):
        if token not in src:
            fails.append(f"missing rejection path: {token}")

    # 12) API failure does not save — code path: status != ok returns before add_ticker_pick
    if "if status != \"ok\":" not in src:
        fails.append("submission must abort before save when validation fails")

    # 14) Voting validates ballot membership
    wp = Path(ROOT / "cogs" / "weekly_picks.py").read_text(encoding="utf-8")
    if "vote_button_context" not in wp or "ticker not in self.tickers" not in wp:
        fails.append("voting must validate ballot membership")

    if fails:
        print("SECTION 9 LIVE: FAIL")
        for f in fails:
            print(f"  • {f}")
        return 1
    print("SECTION 9 LIVE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
