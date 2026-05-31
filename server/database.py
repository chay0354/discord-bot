from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import requests

from config import (
    CATEGORIES,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
    TICKER_LIMIT_PER_CATEGORY,
)


class SupabaseError(RuntimeError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    if not SUPABASE_SERVICE_ROLE_KEY:
        raise SupabaseError("SUPABASE_SERVICE_ROLE_KEY is required for the bot database connection")
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    headers.update(extra or {})
    return headers


def _url(table: str, query: str = "") -> str:
    base = SUPABASE_URL.rstrip("/")
    return f"{base}/rest/v1/{table}{query}"


def _request(method: str, table: str, *, query: str = "", json_body: Any = None, headers: dict[str, str] | None = None) -> Any:
    response = requests.request(
        method,
        _url(table, query),
        headers=_headers(headers),
        json=json_body,
        timeout=12,
    )
    if response.status_code >= 400:
        try:
            payload = response.json()
        except Exception:
            payload = response.text
        raise SupabaseError(f"Supabase {method} {table} failed: {response.status_code} {payload}")
    if not response.text:
        return None
    try:
        return response.json()
    except Exception:
        return None


def _select(table: str, query: str) -> list[dict[str, Any]]:
    return _request("GET", table, query=query) or []


def _single(table: str, query: str) -> dict[str, Any] | None:
    rows = _select(table, query)
    return rows[0] if rows else None


def _eq(value: Any) -> str:
    return quote(str(value), safe="")


def init_db() -> None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise SupabaseError("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY before starting the bot")
    _request("GET", "game_cycles", query="?select=id&limit=1")
    _request("GET", "completed_games", query="?select=id&limit=1")


def reset_week_game_data(guild_id: int, week_key: str) -> None:
    """Clear selected tickers and votes for a weekly game restart."""
    query = f"?guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}"
    _request("DELETE", "votes", query=query)
    _request("DELETE", "ticker_picks", query=query)


def winning_stocks_for_week(guild_id: int, week_key: str) -> dict[str, list[dict[str, Any]]]:
    """Top-voted ticker(s) per category for a completed week."""
    out: dict[str, list[dict[str, Any]]] = {}
    for cat in CATEGORIES:
        counts = vote_counts(guild_id, week_key, cat)
        if not counts:
            out[cat] = []
            continue
        top_votes = counts[0][1]
        winners_at_top = [(ticker, total) for ticker, total in counts if total == top_votes]
        tied = len(winners_at_top) > 1
        out[cat] = [
            {"ticker": ticker, "votes": total, "tied": tied}
            for ticker, total in winners_at_top
        ]
    return out


def vote_totals_for_week(guild_id: int, week_key: str) -> dict[str, list[dict[str, Any]]]:
    """All tickers and vote counts per category (sorted high → low)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for cat in CATEGORIES:
        out[cat] = [
            {"ticker": ticker, "votes": total}
            for ticker, total in vote_counts(guild_id, week_key, cat)
        ]
    return out


def usernames_for_discord_ids(discord_ids: list[int]) -> dict[int, str]:
    if not discord_ids:
        return {}
    ids_csv = ",".join(str(int(i)) for i in discord_ids)
    rows = _select("users", f"?select=discord_id,username&discord_id=in.({ids_csv})")
    out: dict[int, str] = {}
    for row in rows:
        uid = int(row["discord_id"])
        name = str(row.get("username") or "").strip()
        if name:
            out[uid] = name
    return out


def winners_payload(winner_ids: list[int]) -> list[dict[str, Any]]:
    names = usernames_for_discord_ids(winner_ids)
    return [
        {
            "user_id": uid,
            "username": names.get(uid) or f"User {uid}",
        }
        for uid in winner_ids
    ]


def save_completed_game(
    guild_id: int,
    week_key: str,
    *,
    winner_ids: list[int],
    closed_at: str | None = None,
) -> dict[str, Any]:
    """Persist a finished week snapshot for the CRM history panel."""
    stocks = winning_stocks_for_week(guild_id, week_key)
    payload = {
        "guild_id": guild_id,
        "week_key": week_key,
        "closed_at": closed_at or utc_now_iso(),
        "winner_ids": winner_ids,
        "winning_stocks": stocks,
        "vote_totals": vote_totals_for_week(guild_id, week_key),
        "winners": winners_payload(winner_ids),
    }
    rows = _request(
        "POST",
        "completed_games",
        query="?on_conflict=guild_id,week_key",
        json_body=payload,
        headers={"Prefer": "resolution=merge-duplicates,return=representation"},
    ) or []
    return rows[0] if rows else payload


def list_completed_games(guild_id: int, limit: int = 20) -> list[dict[str, Any]]:
    cap = min(max(limit, 1), 50)
    return _select(
        "completed_games",
        f"?select=week_key,closed_at,winner_ids,winning_stocks,vote_totals,winners"
        f"&guild_id=eq.{guild_id}&order=closed_at.desc&limit={cap}",
    )


def week_key_for(dt: datetime | None = None) -> str:
    now = (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def next_week_key_for(dt: datetime | None = None) -> str:
    now = (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return week_key_for(now + timedelta(days=7))


def ticker_selection_week_key_for(dt: datetime | None = None) -> str:
    """
    Weekend ticker submissions belong to the upcoming Monday voting cycle.
    During the trading week they belong to the current cycle.
    """
    now = (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)
    # Approximate ET cutoff without importing the scheduler's DST helpers: after
    # Friday 20:00 UTC is always after Friday 16:00 ET during US market hours.
    if now.weekday() == 4 and now.hour >= 20:
        return next_week_key_for(now)
    if now.weekday() in {5, 6}:
        return next_week_key_for(now)
    return week_key_for(now)


def ensure_cycle(guild_id: int, week_key: str | None = None) -> dict[str, Any]:
    wk = week_key or week_key_for()
    row = _single("game_cycles", f"?select=*&guild_id=eq.{guild_id}&week_key=eq.{_eq(wk)}&limit=1")
    if row:
        return row
    payload = {
        "guild_id": guild_id,
        "week_key": wk,
        "status": "ticker_selection",
        "ticker_selection_open": True,
        "voting_open": False,
        "early_window_open": False,
        "started_at": utc_now_iso(),
    }
    rows = _request(
        "POST",
        "game_cycles",
        json_body=payload,
        headers={"Prefer": "return=representation"},
    ) or []
    return rows[0]


def set_cycle_phase(
    guild_id: int,
    week_key: str,
    *,
    status: str,
    ticker_selection_open: bool,
    voting_open: bool,
    early_window_open: bool,
    monday_open_at: str | None = None,
    early_window_end_at: str | None = None,
    friday_close_at: str | None = None,
) -> None:
    ensure_cycle(guild_id, week_key)
    payload: dict[str, Any] = {
        "status": status,
        "ticker_selection_open": ticker_selection_open,
        "voting_open": voting_open,
        "early_window_open": early_window_open,
    }
    if monday_open_at:
        payload["monday_open_at"] = monday_open_at
    if early_window_end_at:
        payload["early_window_end_at"] = early_window_end_at
    if friday_close_at:
        payload["friday_close_at"] = friday_close_at
    _request("PATCH", "game_cycles", query=f"?guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}", json_body=payload)


def is_ticker_selection_open(guild_id: int, week_key: str | None = None) -> bool:
    return bool(ensure_cycle(guild_id, week_key)["ticker_selection_open"])


def is_voting_open(guild_id: int, week_key: str | None = None) -> bool:
    return bool(ensure_cycle(guild_id, week_key)["voting_open"])


def upsert_user(discord_id: int, username: str | None = None, **fields: Any) -> None:
    now = utc_now_iso()
    payload = {
        "discord_id": discord_id,
        "username": username,
        "updated_at": now,
    }
    for key in ("full_name", "email", "phone", "marketing_consent"):
        if key in fields and fields[key] is not None:
            payload[key] = fields[key]
    if not _single("users", f"?select=discord_id&discord_id=eq.{discord_id}&limit=1"):
        payload["created_at"] = now
    _request(
        "POST",
        "users",
        query="?on_conflict=discord_id",
        json_body=payload,
        headers={"Prefer": "resolution=merge-duplicates"},
    )


def upsert_subscription(
    discord_id: int,
    *,
    status: str,
    payment_status: str | None = None,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    current_period_end: str | None = None,
    canceled_at: str | None = None,
    last_event_type: str | None = None,
    last_event_id: str | None = None,
) -> None:
    payload = {
        "discord_id": discord_id,
        "status": status,
        "updated_at": utc_now_iso(),
    }
    optional = {
        "payment_status": payment_status,
        "stripe_customer_id": stripe_customer_id,
        "stripe_subscription_id": stripe_subscription_id,
        "current_period_end": current_period_end,
        "canceled_at": canceled_at,
        "last_event_type": last_event_type,
        "last_event_id": last_event_id,
    }
    payload.update({k: v for k, v in optional.items() if v is not None})
    _request(
        "POST",
        "subscriptions",
        query="?on_conflict=discord_id",
        json_body=payload,
        headers={"Prefer": "resolution=merge-duplicates"},
    )


def get_subscription_by_customer(stripe_customer_id: str) -> dict[str, Any] | None:
    """Reverse lookup: which Discord user owns this Stripe customer.

    Used to resolve webhooks that don't carry discord metadata (e.g.
    subscription.updated/deleted) without ever guessing by email.
    """
    if not stripe_customer_id:
        return None
    return _single(
        "subscriptions",
        f"?select=*&stripe_customer_id=eq.{_eq(stripe_customer_id)}&limit=1",
    )


# --- Stripe webhook idempotency -----------------------------------------

def get_stripe_event(event_id: str) -> dict[str, Any] | None:
    if not event_id:
        return None
    return _single("stripe_events", f"?select=*&id=eq.{_eq(event_id)}&limit=1")


def claim_stripe_event(event_id: str, event_type: str, payload: Any = None) -> bool:
    """Record a webhook event id. Returns True if newly claimed (not seen before).

    The primary key on `id` guarantees that concurrent or retried deliveries
    of the same Stripe event can never be processed twice.
    """
    if not event_id:
        return True
    rows = _request(
        "POST",
        "stripe_events",
        query="?on_conflict=id",
        json_body={
            "id": event_id,
            "type": event_type,
            "payload": payload,
            "processed": False,
            "received_at": utc_now_iso(),
        },
        headers={"Prefer": "resolution=ignore-duplicates,return=representation"},
    ) or []
    return bool(rows)


def mark_stripe_event_processed(
    event_id: str,
    *,
    discord_id: int | None = None,
    status: str | None = None,
    error: str | None = None,
) -> None:
    if not event_id:
        return
    payload: dict[str, Any] = {
        "processed": error is None,
        "processed_at": utc_now_iso(),
    }
    if discord_id is not None:
        payload["discord_id"] = discord_id
    if status is not None:
        payload["status"] = status
    if error is not None:
        payload["error"] = error[:500]
    _request("PATCH", "stripe_events", query=f"?id=eq.{_eq(event_id)}", json_body=payload)


def recent_stripe_events(limit: int = 25) -> list[dict[str, Any]]:
    cap = min(max(limit, 1), 100)
    return _select(
        "stripe_events",
        f"?select=id,type,discord_id,status,processed,error,received_at,processed_at"
        f"&order=received_at.desc&limit={cap}",
    )


def is_paid_member(discord_id: int) -> bool:
    row = _single("subscriptions", f"?select=status&discord_id=eq.{discord_id}&limit=1")
    return bool(row and row["status"] in {"active", "trialing", "active_until_period_end", "past_due_grace"})


def get_subscription(discord_id: int) -> dict[str, Any] | None:
    return _single(
        "subscriptions",
        f"?select=*&discord_id=eq.{discord_id}&limit=1",
    )


def count_tickers(guild_id: int, week_key: str, category: str) -> int:
    rows = _select(
        "ticker_picks",
        f"?select=id&guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}&category=eq.{category}",
    )
    return len(rows)


def user_has_ticker_pick(guild_id: int, week_key: str, category: str, user_id: int) -> bool:
    rows = _select(
        "ticker_picks",
        (
            f"?select=id&guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}"
            f"&category=eq.{category}&submitted_by=eq.{user_id}&limit=1"
        ),
    )
    return bool(rows)


def list_tickers(guild_id: int, week_key: str, category: str | None = None) -> dict[str, list[str]]:
    query = f"?select=category,ticker&guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}&order=submitted_at.asc,ticker.asc"
    if category:
        query += f"&category=eq.{category}"
    rows = _select("ticker_picks", query)
    out = {cat: [] for cat in CATEGORIES}
    for row in rows:
        out[row["category"]].append(row["ticker"])
    return out


def list_ticker_pick_rows(guild_id: int, week_key: str) -> list[dict[str, Any]]:
    return _select(
        "ticker_picks",
        (
            f"?select=id,category,ticker,market_cap,submitted_by"
            f"&guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}"
            f"&order=ticker.asc"
        ),
    )


def ticker_in_category(guild_id: int, week_key: str, category: str, ticker: str) -> bool:
    sym = ticker.upper().strip().lstrip("$")
    rows = _select(
        "ticker_picks",
        (
            f"?select=id&guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}"
            f"&category=eq.{category}&ticker=eq.{sym}&limit=1"
        ),
    )
    return bool(rows)


def update_ticker_pick_category(
    pick_id: int,
    category: str,
    market_cap: int | None = None,
) -> None:
    body: dict[str, Any] = {"category": category}
    if market_cap is not None:
        body["market_cap"] = market_cap
    _request("PATCH", "ticker_picks", query=f"?id=eq.{pick_id}", json_body=body)


def update_ticker_pick_market_cap(pick_id: int, market_cap: int) -> None:
    _request(
        "PATCH",
        "ticker_picks",
        query=f"?id=eq.{pick_id}",
        json_body={"market_cap": market_cap},
    )


def delete_ticker_pick(pick_id: int) -> None:
    _request("DELETE", "ticker_picks", query=f"?id=eq.{pick_id}")


def move_votes_for_ticker(
    guild_id: int,
    week_key: str,
    ticker: str,
    from_category: str,
    to_category: str,
) -> None:
    if from_category == to_category:
        return
    sym = ticker.upper().strip().lstrip("$")
    _request(
        "PATCH",
        "votes",
        query=(
            f"?guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}"
            f"&ticker=eq.{sym}&category=eq.{from_category}"
        ),
        json_body={"category": to_category},
    )


def ticker_pick_category(guild_id: int, week_key: str, ticker: str) -> str | None:
    sym = ticker.upper().strip().lstrip("$")
    rows = _select(
        "ticker_picks",
        (
            f"?select=category&guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}"
            f"&ticker=eq.{sym}&limit=1"
        ),
    )
    return rows[0]["category"] if rows else None


def vote_button_context(
    guild_id: int,
    week_key: str,
    category: str,
    user_id: int,
    ticker: str,
) -> dict[str, Any]:
    """Parallel Supabase reads for vote validation (used after instant UI ack)."""
    sym = ticker.upper().strip().lstrip("$")
    cycle = ensure_cycle(guild_id, week_key)
    voting_open = bool(cycle.get("voting_open"))
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_cat = pool.submit(ticker_pick_category, guild_id, week_key, sym)
        fut_count = pool.submit(user_vote_count, guild_id, week_key, category, user_id)
        fut_prior = pool.submit(user_voted_ticker, guild_id, week_key, user_id, sym)
        return {
            "voting_open": voting_open,
            "actual_category": fut_cat.result(),
            "vote_count": fut_count.result(),
            "prior_vote_category": fut_prior.result(),
        }


def fetch_week_vote_rows(guild_id: int, week_key: str) -> list[dict[str, Any]]:
    return _select(
        "votes",
        (
            f"?select=category,ticker,user_id"
            f"&guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}"
        ),
    )


def user_voted_ticker(guild_id: int, week_key: str, user_id: int, ticker: str) -> str | None:
    """Category where this user already voted for ticker, if any."""
    sym = ticker.upper().strip().lstrip("$")
    rows = _select(
        "votes",
        (
            f"?select=category&guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}"
            f"&user_id=eq.{user_id}&ticker=eq.{sym}&limit=1"
        ),
    )
    return rows[0]["category"] if rows else None


def add_ticker_pick(
    guild_id: int,
    week_key: str,
    category: str,
    ticker: str,
    user_id: int,
    *,
    market_cap: int | None,
    exchange: str | None,
) -> tuple[bool, str]:
    ensure_cycle(guild_id, week_key)
    if not is_ticker_selection_open(guild_id, week_key):
        return False, "closed"
    if category not in CATEGORIES:
        return False, "bad_category"
    if count_tickers(guild_id, week_key, category) >= TICKER_LIMIT_PER_CATEGORY:
        return False, "full"
    upsert_user(user_id)
    payload = {
        "guild_id": guild_id,
        "week_key": week_key,
        "category": category,
        "ticker": ticker.upper(),
        "market_cap": market_cap,
        "exchange": exchange,
        "submitted_by": user_id,
        "submitted_at": utc_now_iso(),
    }
    try:
        _request("POST", "ticker_picks", json_body=payload)
        return True, "ok"
    except SupabaseError as exc:
        msg = str(exc)
        if "ticker_picks_guild_id_week_key_category_submitted_by_key" in msg:
            return False, "user_already_picked"
        if "ticker_picks_guild_id_week_key_category_ticker_key" in msg:
            return False, "duplicate"
        if "23505" in msg:
            return False, "duplicate"
        raise


def record_vote(
    guild_id: int,
    week_key: str,
    category: str,
    ticker: str,
    user_id: int,
    role_at_vote: str,
    is_early: bool,
) -> tuple[bool, str]:
    ensure_cycle(guild_id, week_key)
    if not is_voting_open(guild_id, week_key):
        return False, "closed"
    upsert_user(user_id)
    payload = {
        "guild_id": guild_id,
        "week_key": week_key,
        "category": category,
        "ticker": ticker.upper(),
        "user_id": user_id,
        "role_at_vote": role_at_vote,
        "is_early": is_early,
        "created_at": utc_now_iso(),
    }
    try:
        _request("POST", "votes", json_body=payload)
        return True, "ok"
    except SupabaseError as exc:
        if "23505" in str(exc):
            return False, "duplicate"
        raise


def user_vote_count(guild_id: int, week_key: str, category: str, user_id: int) -> int:
    rows = _select(
        "votes",
        f"?select=id&guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}&category=eq.{category}&user_id=eq.{user_id}",
    )
    return len(rows)


def vote_counts(guild_id: int, week_key: str, category: str) -> list[tuple[str, int]]:
    rows = _select(
        "votes",
        f"?select=ticker&guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}&category=eq.{category}",
    )
    counts: dict[str, int] = {}
    for row in rows:
        ticker = row["ticker"]
        counts[ticker] = counts.get(ticker, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def all_vote_counts(guild_id: int, week_key: str) -> dict[str, list[tuple[str, int]]]:
    return {cat: vote_counts(guild_id, week_key, cat) for cat in CATEGORIES}


def winning_tickers_for_week(guild_id: int, week_key: str) -> dict[str, set[str]] | None:
    """Top-voted ticker(s) per category. None if any category has no votes."""
    winning: dict[str, set[str]] = {}
    for cat in CATEGORIES:
        counts = vote_counts(guild_id, week_key, cat)
        if not counts:
            return None
        top_count = counts[0][1]
        winning[cat] = {ticker for ticker, total in counts if total == top_count}
    return winning


def compute_eligible_winner_ids(
    *,
    winning_tickers: dict[str, set[str]],
    vote_rows: list[dict[str, Any]],
    active_winner_user_ids: set[int],
) -> tuple[list[int], list[dict[str, Any]]]:
    """Pure eligibility logic for unit tests.

    A user wins only if:
      • every vote counted toward eligibility was cast as **NPC** (role_at_vote)
      • every such vote was in the **early 24h window** (is_early)
      • they picked a top ticker in **each** category
      • they do not already hold an **active WINNER grant**
    """
    by_user: dict[int, dict[str, set[str]]] = {}
    exclusions: list[dict[str, Any]] = []

    for row in vote_rows:
        user_id = int(row["user_id"])
        category = str(row["category"])
        ticker = str(row["ticker"]).upper()
        role = str(row.get("role_at_vote") or "NPC").upper()
        is_early = bool(row.get("is_early"))

        if role != "NPC":
            exclusions.append(
                {
                    "user_id": user_id,
                    "reason": "not_npc_at_vote",
                    "detail": f"role_at_vote={role}",
                    "category": category,
                    "ticker": ticker,
                }
            )
            continue
        if not is_early:
            exclusions.append(
                {
                    "user_id": user_id,
                    "reason": "not_early_window",
                    "detail": "vote after 24h early window",
                    "category": category,
                    "ticker": ticker,
                }
            )
            continue
        by_user.setdefault(user_id, {}).setdefault(category, set()).add(ticker)

    eligible: list[int] = []
    for user_id, picks in by_user.items():
        if user_id in active_winner_user_ids:
            exclusions.append(
                {
                    "user_id": user_id,
                    "reason": "active_winner_grant",
                    "detail": "already holds WINNER role for a prior week",
                }
            )
            continue
        missing = [cat for cat in CATEGORIES if not (picks.get(cat, set()) & winning_tickers.get(cat, set()))]
        if missing:
            exclusions.append(
                {
                    "user_id": user_id,
                    "reason": "wrong_picks",
                    "detail": f"did not pick a top ticker in: {', '.join(missing)}",
                    "picks": {cat: sorted(picks.get(cat, set())) for cat in CATEGORIES},
                }
            )
            continue
        eligible.append(user_id)
    return sorted(eligible), exclusions


def expired_winner_grants(
    guild_id: int | None = None,
    now_iso: str | None = None,
) -> list[dict[str, Any]]:
    """Winner rows whose validity period has ended and should be removed."""
    now = quote(now_iso or utc_now_iso(), safe="")
    query = f"?select=*&removed_at=is.null&expires_at=lte.{now}"
    if guild_id is not None:
        query += f"&guild_id=eq.{guild_id}"
    return _select("winners", query)


def active_winner_grants(
    guild_id: int | None = None,
    now_iso: str | None = None,
) -> list[dict[str, Any]]:
    """Winner rows still within their one-week validity window."""
    now = quote(now_iso or utc_now_iso(), safe="")
    query = f"?select=*&removed_at=is.null&expires_at=gt.{now}"
    if guild_id is not None:
        query += f"&guild_id=eq.{guild_id}"
    return _select("winners", query)


def active_winner_user_ids(guild_id: int, now_iso: str | None = None) -> set[int]:
    return {int(row["user_id"]) for row in active_winner_grants(guild_id, now_iso)}


def eligible_winners(guild_id: int, week_key: str) -> list[int]:
    winning = winning_tickers_for_week(guild_id, week_key)
    if not winning:
        return []
    rows = _select(
        "votes",
        (
            f"?select=user_id,category,ticker,role_at_vote,is_early"
            f"&guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}"
        ),
    )
    active = active_winner_user_ids(guild_id)
    ids, _ = compute_eligible_winner_ids(
        winning_tickers=winning,
        vote_rows=rows,
        active_winner_user_ids=active,
    )
    return ids


def eligible_winners_report(guild_id: int, week_key: str) -> dict[str, Any]:
    """Detailed winner calculation for #mod logs and admin review."""
    winning = winning_tickers_for_week(guild_id, week_key)
    rows = _select(
        "votes",
        (
            f"?select=user_id,category,ticker,role_at_vote,is_early,created_at"
            f"&guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}"
        ),
    )
    active = active_winner_user_ids(guild_id)
    if not winning:
        return {
            "week_key": week_key,
            "winning_tickers": {},
            "eligible_winner_ids": [],
            "exclusions": [],
            "active_winner_user_ids": sorted(active),
            "note": "no votes in one or more categories",
        }
    ids, exclusions = compute_eligible_winner_ids(
        winning_tickers=winning,
        vote_rows=rows,
        active_winner_user_ids=active,
    )
    return {
        "week_key": week_key,
        "winning_tickers": {cat: sorted(tickers) for cat, tickers in winning.items()},
        "eligible_winner_ids": ids,
        "exclusions": exclusions,
        "active_winner_user_ids": sorted(active),
    }


def add_winner(guild_id: int, week_key: str, user_id: int, expires_at: str) -> None:
    upsert_user(user_id)
    _request(
        "POST",
        "winners",
        query="?on_conflict=guild_id,week_key,user_id",
        json_body={
            "guild_id": guild_id,
            "week_key": week_key,
            "user_id": user_id,
            "awarded_at": utc_now_iso(),
            "expires_at": expires_at,
        },
        headers={"Prefer": "resolution=ignore-duplicates"},
    )


def latest_winners_for_guild(guild_id: int) -> dict[str, Any] | None:
    rows = _select(
        "winners",
        f"?select=week_key,user_id,expires_at&guild_id=eq.{guild_id}&order=awarded_at.desc&limit=50",
    )
    if not rows:
        return None
    week_key = rows[0]["week_key"]
    latest_rows = [row for row in rows if row["week_key"] == week_key]
    return {
        "week_key": week_key,
        "winner_ids": [int(row["user_id"]) for row in latest_rows],
        "expires_at": latest_rows[0].get("expires_at"),
    }


def active_winners(now_iso: str | None = None) -> list[dict[str, Any]]:
    """Backward-compatible alias: rows due for role removal."""
    return expired_winner_grants(now_iso=now_iso)


def mark_winner_removed(winner_id: int) -> None:
    _request("PATCH", "winners", query=f"?id=eq.{winner_id}", json_body={"removed_at": utc_now_iso()})


def save_message_state(
    guild_id: int,
    key: str,
    *,
    channel_id: int | None,
    message_id: int | None,
    payload: dict[str, Any] | None = None,
) -> None:
    _request(
        "POST",
        "message_state",
        query="?on_conflict=guild_id,key",
        json_body={
            "guild_id": guild_id,
            "key": key,
            "channel_id": channel_id,
            "message_id": message_id,
            "payload": payload or {},
            "updated_at": utc_now_iso(),
        },
        headers={"Prefer": "resolution=merge-duplicates"},
    )


def log_event(guild_id: int | None, event_type: str, details: dict[str, Any]) -> None:
    """Append an audit-log row. Never raises: logging must not break a flow."""
    try:
        _request(
            "POST",
            "audit_logs",
            json_body={
                "guild_id": guild_id,
                "event_type": event_type,
                "details": details,
                "created_at": utc_now_iso(),
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[audit] log_event({event_type}) failed: {exc!r}", flush=True)


# Subscription statuses that mean the user currently has PLAYER access.
_PLAYER_ACTIVE_STATUSES = {"active", "active_until_period_end", "trialing"}


def count_player_grants_since(since_iso: str) -> int:
    """Best-effort count of distinct users who gained PLAYER access since `since_iso`.

    Reads billing audit rows (`stripe_webhook`) and counts unique Discord IDs
    whose event moved them into an active state. Returns 0 on any error so the
    weekly report never fails because of this stat.
    """
    try:
        rows = _select(
            "audit_logs",
            (
                f"?select=details,created_at&event_type=eq.stripe_webhook"
                f"&created_at=gte.{quote(since_iso, safe='')}"
            ),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[audit] count_player_grants_since failed: {exc!r}", flush=True)
        return 0
    users: set[int] = set()
    for row in rows:
        details = row.get("details") or {}
        status = str(details.get("status") or "")
        discord_id = details.get("discord_id")
        if status in _PLAYER_ACTIVE_STATUSES and discord_id:
            try:
                users.add(int(discord_id))
            except (TypeError, ValueError):
                continue
    return len(users)


def dump_json(data: Any) -> str:
    return json.dumps(data, default=str, ensure_ascii=False)
