"""Live check of the pre-vote ticker resolver against real market data.

Verifies that valid NASDAQ/NYSE tickers resolve (including ones that are NOT in
any example list), that fake symbols are rejected, and that the single-letter
auto-pick bug is gone (a single letter only resolves if it is a real 1-letter
ticker like F, not by prefix-matching an example).
"""
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

from config import CATEGORY_TITLES
from cogs.submission_ui import resolve_ticker_any

# (symbol, should_resolve)  — should_resolve=False means "must be rejected as not found"
CASES = [
    ("F", True),        # Ford — single-letter NYSE ticker
    ("AAPL", True),     # Apple
    ("PLTR", True),     # Palantir — not in example lists
    ("RIVN", True),     # Rivian — not in example lists
    ("SHOP", True),     # Shopify (NYSE)
    ("AMD", True),
    ("ZVZZT", False),   # Nasdaq TEST ticker / not a tradable equity
    ("NOTAREALTICKER", False),
    ("A", True),        # Agilent — real single-letter ticker (must resolve to A, not auto-pick)
]

print(f"{'SYMBOL':16} {'RESULT':10} {'EXCH_OK':8} {'CATEGORY':9} EXCHANGE / NAME")
print("-" * 90)
ok = True
for sym, should in CASES:
    row = resolve_ticker_any(sym)
    if row:
        cat = row.get("category")
        cat_title = CATEGORY_TITLES.get(cat, cat or "?")
        exch_ok = row.get("exchange_ok")
        resolved = row.get("symbol")
        print(f"{sym:16} {'FOUND':10} {str(exch_ok):8} {str(cat_title):9} {row.get('exchange','')} | {row.get('shortName','')}")
        # Anti-auto-pick: the resolved symbol must equal what we typed.
        if resolved != sym.upper():
            print(f"   !! resolved to {resolved}, expected {sym.upper()} (auto-pick bug)")
            ok = False
        if not should:
            print(f"   !! expected NOT FOUND for {sym}")
            ok = False
    else:
        print(f"{sym:16} {'NOT FOUND':10}")
        if should:
            print(f"   !! expected to resolve {sym}")
            ok = False

print("-" * 90)
print("RESULT:", "PASS" if ok else "FAIL (see !! lines)")
sys.exit(0 if ok else 1)
