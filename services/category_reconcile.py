from __future__ import annotations

from dataclasses import dataclass

import database
from config import CATEGORIES
from services.finnhub_client import classify_market_cap_usd, get_company_profile


@dataclass(frozen=True)
class CategoryMove:
    ticker: str
    from_category: str
    to_category: str


def _market_cap_usd_from_profile(profile: dict) -> int | None:
    millions = profile.get("marketCapitalization")
    if millions is None:
        return None
    try:
        return int(float(millions) * 1_000_000)
    except (TypeError, ValueError):
        return None


def _current_category_for_ticker(ticker: str) -> tuple[str | None, int | None]:
    profile = get_company_profile(ticker)
    if not profile:
        return None, None
    market_cap = _market_cap_usd_from_profile(profile)
    return classify_market_cap_usd(market_cap), market_cap


def reconcile_ticker_categories(guild_id: int, week_key: str) -> list[CategoryMove]:
    """
    Reclassify weekly tickers by live market cap and move votes with them.
    Returns moves applied (empty if nothing changed).
    """
    rows = database.list_ticker_pick_rows(guild_id, week_key)
    if not rows:
        return []

    by_ticker: dict[str, list[dict]] = {}
    for row in rows:
        sym = str(row["ticker"]).upper()
        by_ticker.setdefault(sym, []).append(row)

    moves: list[CategoryMove] = []
    for ticker, pick_rows in by_ticker.items():
        new_cat, market_cap = _current_category_for_ticker(ticker)
        if new_cat not in CATEGORIES:
            continue

        for row in pick_rows:
            old_cat = row["category"]
            pick_id = int(row["id"])
            submitted_by = row.get("submitted_by")
            if old_cat == new_cat:
                if market_cap is not None:
                    database.update_ticker_pick_market_cap(pick_id, market_cap)
                continue

            drop_pick = False
            if database.ticker_in_category(guild_id, week_key, new_cat, ticker):
                drop_pick = True
            elif submitted_by is not None and database.user_has_ticker_pick(
                guild_id, week_key, new_cat, int(submitted_by)
            ):
                # Same user already has a different ticker in the target category.
                drop_pick = True

            if drop_pick:
                database.delete_ticker_pick(pick_id)
            else:
                database.update_ticker_pick_category(pick_id, new_cat, market_cap)

            database.move_votes_for_ticker(
                guild_id, week_key, ticker, old_cat, new_cat
            )
            moves.append(CategoryMove(ticker=ticker, from_category=old_cat, to_category=new_cat))

    return moves


def category_for_ticker(guild_id: int, week_key: str, ticker: str) -> str | None:
    return database.ticker_pick_category(guild_id, week_key, ticker)
