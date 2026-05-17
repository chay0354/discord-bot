from __future__ import annotations

import os
from typing import Any

import discord

import app_state
import database
from config import CATEGORY_TITLES, CATEGORIES, TICKER_LIMIT_PER_CATEGORY
from cogs.scheduler import SchedulerCog
from services.category_reconcile import reconcile_ticker_categories
from services.finnhub_client import format_quote, quote_and_names_for_symbols


def _guild_id() -> int:
    raw = os.getenv("DISCORD_GUILD_ID", "").strip()
    if raw:
        return int(raw)
    bot = app_state.bot
    if bot and app_state.bot_ready and bot.guilds:
        return bot.guilds[0].id
    raise RuntimeError(
        "DISCORD_GUILD_ID is not set and the bot is not connected to a guild yet"
    )


def _guild() -> discord.Guild:
    bot = app_state.bot
    if not bot or not app_state.bot_ready:
        raise RuntimeError("Discord bot is not connected")
    gid = _guild_id()
    guild = bot.get_guild(gid)
    if not guild:
        raise RuntimeError(f"Bot is not in guild {gid}")
    return guild


async def _scheduler() -> SchedulerCog:
    bot = app_state.bot
    if not bot:
        raise RuntimeError("Discord bot is not connected")
    cog = bot.get_cog("SchedulerCog")
    if isinstance(cog, SchedulerCog):
        return cog
    cog = SchedulerCog(bot)
    await bot.add_cog(cog)
    return cog


async def run_action(action: str, *, actor_id: int | None = None) -> dict[str, Any]:
    guild = _guild()
    scheduler = await _scheduler()
    week_key = database.week_key_for()

    if action == "start_voting":
        updated, counts = await scheduler._monday_open_one_guild(guild)
        database.log_event(guild.id, "crm_start_voting", {"updated": updated, "counts": counts})
        return {"ok": True, "message": "Vote stage started", "updated": updated, "counts": counts}

    if action == "close_early":
        await scheduler._tuesday_early_close_one_guild(guild)
        database.log_event(guild.id, "crm_close_early", {})
        return {"ok": True, "message": "Early window closed"}

    if action == "end_competition":
        await scheduler._friday_close_one_guild(guild)
        database.log_event(guild.id, "crm_end_competition", {})
        return {"ok": True, "message": "Vote stage ended"}

    if action == "start_pre_voting":
        await scheduler._restart_pre_voting_one_guild(guild, actor_id=actor_id)
        database.log_event(guild.id, "crm_start_pre_voting", {"actor_id": actor_id})
        return {"ok": True, "message": "Pre-voting restarted"}

    if action == "end_pre_start_voting":
        updated, counts = await scheduler._monday_open_one_guild(guild)
        database.log_event(
            guild.id,
            "crm_end_pre_start_voting",
            {"updated": updated, "counts": counts},
        )
        return {
            "ok": True,
            "message": "Pre-voting ended and vote stage started",
            "updated": updated,
            "counts": counts,
        }

    raise ValueError(f"Unknown action: {action}")


def get_game_status() -> dict[str, Any]:
    gid = _guild_id()
    week_key = database.week_key_for()
    selection_week = database.ticker_selection_week_key_for()
    cycle = database.ensure_cycle(gid, week_key)
    tickers = database.list_tickers(gid, week_key)
    counts = {cat: len(tickers[cat]) for cat in CATEGORIES}
    vote_totals = {cat: len(database.vote_counts(gid, week_key, cat)) for cat in CATEGORIES}
    winners = database.latest_winners_for_guild(gid)
    return {
        "guild_id": gid,
        "week_key": week_key,
        "selection_week_key": selection_week,
        "cycle": cycle,
        "ticker_counts": counts,
        "ticker_limit": TICKER_LIMIT_PER_CATEGORY,
        "vote_entry_counts": vote_totals,
        "category_titles": CATEGORY_TITLES,
        "latest_winners": winners,
        "bot_connected": app_state.bot_ready,
    }


def get_tickers() -> dict[str, Any]:
    gid = _guild_id()
    week_key = database.week_key_for()
    reconcile_ticker_categories(gid, week_key)
    rows = database.list_ticker_pick_rows(gid, week_key)
    return {"week_key": week_key, "picks": rows, "by_category": database.list_tickers(gid, week_key)}


def get_votes() -> dict[str, Any]:
    gid = _guild_id()
    week_key = database.week_key_for()
    return {
        "week_key": week_key,
        "counts": {
            cat: [{"ticker": t, "votes": c} for t, c in database.vote_counts(gid, week_key, cat)]
            for cat in CATEGORIES
        },
    }


def get_leaderboards() -> dict[str, Any]:
    gid = _guild_id()
    week_key = database.week_key_for()
    reconcile_ticker_categories(gid, week_key)
    out: dict[str, list[dict[str, Any]]] = {}
    all_syms: list[str] = []
    for cat in CATEGORIES:
        all_syms.extend(t for t, _ in database.vote_counts(gid, week_key, cat))
    quotes, names = quote_and_names_for_symbols(all_syms)
    for cat in CATEGORIES:
        rows = []
        for rank, (ticker, total) in enumerate(database.vote_counts(gid, week_key, cat), start=1):
            rows.append(
                {
                    "rank": rank,
                    "ticker": ticker,
                    "votes": total,
                    "name": names.get(ticker, ""),
                    "quote": format_quote(ticker, quotes.get(ticker)),
                }
            )
        out[cat] = rows
    return {"week_key": week_key, "leaderboards": out, "category_titles": CATEGORY_TITLES}


def get_audit_logs(limit: int = 50) -> list[dict[str, Any]]:
    gid = _guild_id()
    return database._select(
        "audit_logs",
        f"?select=*&guild_id=eq.{gid}&order=created_at.desc&limit={limit}",
    )


def get_subscriptions(limit: int = 100) -> list[dict[str, Any]]:
    return database._select(
        "subscriptions",
        f"?select=*&order=updated_at.desc&limit={limit}",
    )
