from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

import requests

from config import FINNHUB_API_KEY


FINNHUB_API = "https://finnhub.io/api/v1"
_CACHE_TTL_SECONDS = 45
_QUOTE_CACHE: dict[str, tuple[float, "FinnhubQuote | None"]] = {}
_PROFILE_CACHE: dict[str, tuple[float, dict | None]] = {}

# Most recent transport/API failure (network, timeout, 4xx/5xx, rate limit).
# Lets callers distinguish "ticker does not exist" from "the API call failed".
_LAST_ERROR: str | None = None


def pop_last_error() -> str | None:
    """Return and clear the most recent Finnhub API failure, if any."""
    global _LAST_ERROR
    err, _LAST_ERROR = _LAST_ERROR, None
    return err


@dataclass(frozen=True)
class FinnhubQuote:
    symbol: str
    current_price: float | None
    change: float | None
    percent_change: float | None
    previous_close: float | None


def _get(path: str, params: dict[str, str]) -> dict:
    global _LAST_ERROR
    if not FINNHUB_API_KEY:
        _LAST_ERROR = "FINNHUB_API_KEY not configured"
        return {}
    merged = {**params, "token": FINNHUB_API_KEY}
    try:
        response = requests.get(f"{FINNHUB_API}/{path}", params=merged, timeout=8)
        response.raise_for_status()
    except requests.RequestException as exc:
        _LAST_ERROR = f"finnhub {path}: {exc!r}"
        print(f"[finnhub_client] {path} request failed: {exc!r}", flush=True)
        raise
    return response.json() or {}


def get_quote(symbol: str) -> FinnhubQuote | None:
    normalized = symbol.upper().strip().lstrip("$")
    now = time.time()
    cached = _QUOTE_CACHE.get(normalized)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        data = _get("quote", {"symbol": normalized})
        quote = FinnhubQuote(
            symbol=normalized,
            current_price=data.get("c") or None,
            change=data.get("d") or None,
            percent_change=data.get("dp") or None,
            previous_close=data.get("pc") or None,
        )
        if quote.current_price is None:
            quote = None
    except Exception:
        quote = None

    _QUOTE_CACHE[normalized] = (now, quote)
    return quote


def get_quotes(symbols: Iterable[str]) -> dict[str, FinnhubQuote]:
    quotes: dict[str, FinnhubQuote] = {}
    for symbol in symbols:
        quote = get_quote(symbol)
        if quote:
            quotes[quote.symbol] = quote
    return quotes


def get_company_profile(symbol: str) -> dict | None:
    normalized = symbol.upper().strip().lstrip("$")
    now = time.time()
    cached = _PROFILE_CACHE.get(normalized)
    if cached and now - cached[0] < 3600:
        return cached[1]
    try:
        data = _get("stock/profile2", {"symbol": normalized})
        profile = data if data.get("ticker") else None
    except Exception:
        profile = None
    _PROFILE_CACHE[normalized] = (now, profile)
    return profile


def classify_market_cap_usd(market_cap_usd: float | int | None) -> str | None:
    if market_cap_usd is None:
        return None
    cap = float(market_cap_usd)
    if cap >= 10_000_000_000:
        return "blue"
    if cap >= 2_000_000_000:
        return "mid"
    return "small"


def _exchange_is_us_major(exchange: str) -> bool:
    """True when the exchange string denotes NASDAQ or NYSE (any Finnhub variant)."""
    up = str(exchange or "").upper()
    if "NASDAQ" in up or "NYSE" in up:
        return True
    # Finnhub sometimes spells these out.
    return "NEW YORK STOCK EXCHANGE" in up or "NASDAQ NMS" in up


def resolve_symbol(symbol: str) -> dict | None:
    """Authoritative exact-symbol lookup (no category filter).

    Returns the company's real exchange + market-cap category so the caller can
    give a precise message (wrong exchange / wrong category) instead of a vague
    rejection. Returns None only when the symbol does not exist at all.
    """
    normalized = symbol.upper().strip().lstrip("$")
    if not normalized:
        return None
    profile = get_company_profile(normalized)
    if not profile and ("-" in normalized or "." in normalized):
        # Try the alternate class-share separator (users type BRK.B / BRK-B / BRKB).
        alt = normalized.replace("-", ".") if "-" in normalized else normalized.replace(".", "-")
        profile = get_company_profile(alt)
    if not profile:
        return None
    # NOTE: We intentionally do NOT require profile["ticker"] == typed symbol.
    # For dual-class shares Finnhub returns the primary class (e.g. GOOG -> GOOGL,
    # BRK.B -> BRK.A); rejecting on that mismatch wrongly blocked valid US stocks.
    # An unknown symbol returns an empty profile (None above), so junk is still rejected.
    exchange = str(profile.get("exchange") or "")
    market_cap_millions = profile.get("marketCapitalization")
    market_cap_usd = float(market_cap_millions) * 1_000_000 if market_cap_millions else None
    return {
        "symbol": normalized,
        "shortName": profile.get("name") or "",
        "exchange": exchange,
        "marketCap": int(market_cap_usd) if market_cap_usd is not None else None,
        "category": classify_market_cap_usd(market_cap_usd),
        "exchange_ok": _exchange_is_us_major(exchange),
        "source": "finnhub",
    }


def validate_symbol_for_category(symbol: str, category: str) -> dict | None:
    row = resolve_symbol(symbol)
    if not row or not row.get("exchange_ok"):
        return None
    if row.get("category") != category:
        return None
    return {**row, "category": category}


def format_quote(symbol: str, quote: FinnhubQuote | None) -> str:
    if not quote or quote.current_price is None:
        return f"${symbol}"
    sign = "+" if (quote.change or 0) >= 0 else ""
    if quote.percent_change is None:
        return f"${symbol} @ ${quote.current_price:.2f}"
    return f"${symbol} @ ${quote.current_price:.2f} ({sign}{quote.percent_change:.2f}%)"


def quote_and_names_for_symbols(symbols: Iterable[str]) -> tuple[dict[str, FinnhubQuote], dict[str, str]]:
    """
    Batch-fetch quotes and company display names for UI (leaderboards, voting roster).
    Returns (quotes_by_symbol, short_name_by_symbol).
    """
    from concurrent.futures import ThreadPoolExecutor

    normalized = []
    seen: set[str] = set()
    for raw in symbols:
        s = str(raw).upper().strip().lstrip("$")
        if s and s not in seen:
            seen.add(s)
            normalized.append(s)
    if not normalized:
        return {}, {}
    quotes: dict[str, FinnhubQuote] = {}
    names: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=12) as executor:
        quote_results = list(executor.map(get_quote, normalized))
        profile_results = list(executor.map(get_company_profile, normalized))
    for sym, q, prof in zip(normalized, quote_results, profile_results):
        if q:
            quotes[sym] = q
        if prof and str(prof.get("name") or "").strip():
            names[sym] = str(prof["name"]).strip()
        else:
            names[sym] = ""
    return quotes, names
