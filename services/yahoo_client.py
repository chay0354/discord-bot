# services/yahoo_client.py
from __future__ import annotations

import requests
from typing import Optional, List, Dict, Any

# --- Yahoo Finance endpoints ---
YF_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
YF_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"

# Reuse one Session with headers so Yahoo doesn't block us
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
})

# NASDAQ/NYSE allowlist (Yahoo returns several codes/labels)
_ALLOWED_EX_CODES = {"NMS", "NCM", "NGM", "NYQ"}  # Nasdaq/NYSE families
# in exchDisp/fullExchangeName
_ALLOWED_EX_TEXT = ("NASDAQ", "NYSE")

# ---- helpers ---------------------------------------------------------------


def _is_allowed_exchange(q: Dict[str, Any]) -> bool:
    # e.g. NMS/NYQ/NCM/NGM
    exch = str(q.get("exchange") or "").upper()
    exd = str(q.get("exchDisp") or q.get("fullExchangeName") or "").upper()
    if exch in _ALLOWED_EX_CODES:
        return True
    return any(t in exd for t in _ALLOWED_EX_TEXT)


def _search_raw(term: str, count: int = 25) -> List[Dict[str, Any]]:
    """Call Yahoo /v1/finance/search and return 'quotes' filtered to equities and NASDAQ/NYSE."""
    params = {
        "q": term,
        "quotesCount": max(1, min(count, 50)),
        "newsCount": 0,
        "enableFuzzyQuery": "true",
        "lang": "en-US",
        "region": "US",
    }
    r = _SESSION.get(YF_SEARCH_URL, params=params, timeout=8)
    r.raise_for_status()
    data = r.json() or {}
    quotes = data.get("quotes") or []
    out: List[Dict[str, Any]] = []
    for q in quotes:
        qtype = (q.get("quoteType") or q.get("type") or "").upper()
        if qtype not in {"EQUITY", "S"}:
            continue
        if not _is_allowed_exchange(q):
            continue
        sym = (q.get("symbol") or "").upper()
        if not sym:
            continue
        out.append(q)
    return out


def _fetch_quote_batch(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch market data (incl. marketCap) for up to 20 symbols per request."""
    res: Dict[str, Dict[str, Any]] = {}
    if not symbols:
        return res
    # Yahoo allows ~20–50 per call; we chunk to 20 to be safe.
    CHUNK = 20
    for i in range(0, len(symbols), CHUNK):
        sy = ",".join(symbols[i:i+CHUNK])
        r = _SESSION.get(YF_QUOTE_URL, params={"symbols": sy}, timeout=8)
        r.raise_for_status()
        js = r.json() or {}
        arr = ((js.get("quoteResponse") or {}).get("result")) or []
        for it in arr:
            sym = (it.get("symbol") or "").upper()
            if sym:
                res[sym] = it
    return res


def _classify_by_cap(mcap: Optional[int]) -> Optional[str]:
    """Return 'blue' (>=10B), 'mid' (2B-10B), 'small' (<2B)."""
    if mcap is None:
        return None
    try:
        cap = int(mcap)
    except Exception:
        return None
    if cap >= 10_000_000_000:
        return "blue"
    if cap >= 2_000_000_000:
        return "mid"
    return "small"

# ---- public API ------------------------------------------------------------


def search_symbols_by_query(query: str, *, category: Optional[str] = None, limit: int = 25) -> List[Dict[str, Any]]:
    """
    Search Yahoo for 'query' and return up to 'limit' rows:
    {symbol, shortName, exchange, marketCap, category}
    If 'category' is provided ('small'|'mid'|'blue'), filter accordingly.
    """
    try:
        raw = _search_raw(query, count=max(limit * 3, 25)
                          )  # overfetch, we filter later
        symbols = [(q.get("symbol") or "").upper()
                   for q in raw if q.get("symbol")]
        quotes = _fetch_quote_batch(symbols)

        want = category.lower().strip() if category else None
        rows: List[Dict[str, Any]] = []
        seen: set[str] = set()

        for q in raw:
            sym = (q.get("symbol") or "").upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)

            info = quotes.get(sym, {})
            mcap = info.get("marketCap")
            cat = _classify_by_cap(mcap)
            if want and cat != want:
                continue

            rows.append({
                "symbol": sym,
                "shortName": info.get("shortName") or q.get("shortname") or q.get("longname") or "",
                "exchange": info.get("fullExchangeName") or q.get("exchDisp") or q.get("exchange"),
                "marketCap": mcap,
                "category": cat,
            })
            if len(rows) >= limit:
                break

        return rows
    except Exception as e:
        # return empty list on any error; print so you can see it in console
        print("[yahoo_client] search error:", repr(e))
        return []


def pick_for_channel_query(channel_name: str, term: str, limit: int = 25) -> List[str]:
    """
    Helper for the submission UI:
      - infers the desired category from channel name (small-cap[-ticker], mid-cap[-ticker], large-cap; also matches legacy *-caps* names)
      - searches Yahoo with 'term'
      - returns a list of SYMBOLS only (uppercase), up to 'limit'
    """
    ch = (channel_name or "").lower()
    if "small-cap" in ch:
        cat = "small"
    elif "mid-cap" in ch:
        cat = "mid"
    else:
        cat = "blue"
    rows = search_symbols_by_query(term, category=cat, limit=limit)
    return [r["symbol"].upper() for r in rows]


def category_for_channel(channel_name: str) -> str:
    ch = (channel_name or "").lower()
    if "small-cap" in ch:
        return "small"
    if "mid-cap" in ch:
        return "mid"
    return "blue"


def validate_symbol_for_category(symbol: str, category: str) -> Optional[Dict[str, Any]]:
    """
    Return a normalized Yahoo row when symbol is a common stock on NASDAQ/NYSE
    and currently belongs to the expected market-cap category.
    """
    normalized = (symbol or "").upper().strip().lstrip("$")
    if not normalized:
        return None
    rows = search_symbols_by_query(normalized, category=category, limit=10)
    for row in rows:
        if row["symbol"].upper() == normalized and row.get("category") == category:
            return row
    try:
        from services.finnhub_client import validate_symbol_for_category as _finnhub_validate

        return _finnhub_validate(normalized, category)
    except Exception:
        return None
