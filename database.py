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


def reset_week_game_data(guild_id: int, week_key: str) -> None:
    """Clear selected tickers and votes for a weekly game restart."""
    query = f"?guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}"
    _request("DELETE", "votes", query=query)
    _request("DELETE", "ticker_picks", query=query)


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
    }
    payload.update({k: v for k, v in optional.items() if v is not None})
    _request(
        "POST",
        "subscriptions",
        query="?on_conflict=discord_id",
        json_body=payload,
        headers={"Prefer": "resolution=merge-duplicates"},
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
            f"?select=id,category,ticker,market_cap"
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


def eligible_winners(guild_id: int, week_key: str) -> list[int]:
    winning_tickers: dict[str, set[str]] = {}
    for cat in CATEGORIES:
        counts = vote_counts(guild_id, week_key, cat)
        if not counts:
            return []
        top_count = counts[0][1]
        winning_tickers[cat] = {ticker for ticker, total in counts if total == top_count}

    rows = _select(
        "votes",
        f"?select=user_id,category,ticker&guild_id=eq.{guild_id}&week_key=eq.{_eq(week_key)}",
    )
    by_user: dict[int, dict[str, set[str]]] = {}
    for row in rows:
        user_id = int(row["user_id"])
        by_user.setdefault(user_id, {}).setdefault(row["category"], set()).add(row["ticker"])
    return sorted(
        user_id for user_id, picks in by_user.items()
        if all(picks.get(cat, set()) & winning_tickers[cat] for cat in CATEGORIES)
    )


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
    now = quote(now_iso or utc_now_iso(), safe="")
    return _select("winners", f"?select=*&removed_at=is.null&expires_at=lte.{now}")


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


def dump_json(data: Any) -> str:
    return json.dumps(data, default=str, ensure_ascii=False)
