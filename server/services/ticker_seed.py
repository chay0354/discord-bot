from __future__ import annotations

from config import CATEGORIES, TICKER_LIMIT_PER_CATEGORY
from services.example_stocks import EXAMPLE_STOCKS
from services.finnhub_client import classify_market_cap_usd, get_company_profile


def _market_cap_usd_from_profile(profile: dict) -> int | None:
    millions = profile.get("marketCapitalization")
    if millions is None:
        return None
    try:
        return int(float(millions) * 1_000_000)
    except (TypeError, ValueError):
        return None


def validated_tickers_for_category(
    category: str,
    pool: list[str] | None = None,
    *,
    limit: int = TICKER_LIMIT_PER_CATEGORY,
) -> list[tuple[str, int, str]]:
    """Return up to `limit` tickers that Finnhub classifies into `category` (small/mid/blue)."""
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category: {category}")
    symbols = pool if pool is not None else EXAMPLE_STOCKS.get(category, [])
    out: list[tuple[str, int, str]] = []
    seen: set[str] = set()
    for raw in symbols:
        sym = str(raw).upper().strip().lstrip("$")
        if not sym or sym in seen:
            continue
        profile = get_company_profile(sym)
        if not profile:
            continue
        market_cap = _market_cap_usd_from_profile(profile)
        if classify_market_cap_usd(market_cap) != category:
            continue
        exchange = str(profile.get("exchange") or "NASDAQ").upper()
        out.append((sym, int(market_cap or 0), exchange))
        seen.add(sym)
        if len(out) >= limit:
            break
    return out


def manual_ballot_tickers() -> dict[str, list[tuple[str, int, str]]]:
    """20 validated tickers per category for manual / test voting phases."""
    ballot: dict[str, list[tuple[str, int, str]]] = {}
    for cat in CATEGORIES:
        ballot[cat] = validated_tickers_for_category(cat)
        if len(ballot[cat]) < TICKER_LIMIT_PER_CATEGORY:
            raise RuntimeError(
                f"Could only validate {len(ballot[cat])}/{TICKER_LIMIT_PER_CATEGORY} "
                f"tickers for {cat}; check Finnhub or expand EXAMPLE_STOCKS pool"
            )
    return ballot
