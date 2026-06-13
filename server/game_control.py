from __future__ import annotations

import json
import os
from typing import Any

import discord

import app_state
import database
from config import CATEGORY_TITLES, CATEGORIES, TICKER_LIMIT_PER_CATEGORY
from cogs.scheduler import SchedulerCog
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


def _find_scheduler_cog(bot: discord.Client) -> SchedulerCog | None:
    """Return the loaded scheduler cog without isinstance (avoids duplicate-class imports)."""
    cog = bot.get_cog("SchedulerCog")
    if cog is not None:
        return cog  # type: ignore[return-value]
    for loaded in bot.cogs.values():
        if type(loaded).__name__ == "SchedulerCog":
            return loaded  # type: ignore[return-value]
    return None


async def _scheduler() -> SchedulerCog:
    bot = app_state.bot
    if not bot:
        raise RuntimeError("Discord bot is not connected")
    if not app_state.bot_ready:
        raise RuntimeError("Discord bot is still starting; try again in a few seconds.")
    cog = _find_scheduler_cog(bot)
    if cog is not None:
        return cog
    raise RuntimeError(
        "SchedulerCog is not loaded. Check deploy logs for "
        "'[bot] Failed cogs.scheduler' and redeploy the bot."
    )


async def run_action(
    action: str,
    *,
    actor_id: int | None = None,
    guild: discord.Guild | None = None,
) -> dict[str, Any]:
    target = guild if guild is not None else _guild()
    scheduler = await _scheduler()
    week_key = database.week_key_for()

    if action in ("start_pre_vote", "start_pre_voting"):
        cycle = database.ensure_cycle(target.id, week_key)
        status = str(cycle.get("status") or "")
        if cycle.get("voting_open") or status == "voting":
            await scheduler._friday_close_one_guild(target)
        elif status != "closed":
            await scheduler._friday_close_one_guild(target)
        selection_week = await scheduler._restart_pre_voting_one_guild(
            target, actor_id=actor_id, manual=True
        )
        database.log_event(
            target.id,
            "start_pre_vote",
            {
                "ended_week": week_key,
                "pre_vote_week": selection_week,
                "actor_id": actor_id,
                "manual": True,
            },
        )
        return {
            "ok": True,
            "message": f"Week {week_key} ended. Pre-vote opened for {selection_week} (timer starts now).",
        }

    if action in ("start_vote", "start_voting", "end_pre_start_voting"):
        updated, counts = await scheduler._monday_open_one_guild(target, manual=True)
        database.log_event(
            target.id,
            "start_vote",
            {"updated": updated, "counts": counts, "actor_id": actor_id},
        )
        return {
            "ok": True,
            "message": "Vote stage started.",
            "updated": updated,
            "counts": counts,
        }

    if action == "close_early":
        await scheduler._tuesday_early_close_one_guild(target)
        database.log_event(target.id, "close_early", {"actor_id": actor_id})
        return {"ok": True, "message": "Early window closed."}

    if action == "end_competition":
        await scheduler._friday_close_one_guild(target)
        database.log_event(target.id, "end_competition", {"actor_id": actor_id})
        return {"ok": True, "message": "Vote stage ended."}

    if action in ("reset_winner_grants", "reset_winner"):
        revoked_ids = database.revoke_all_active_winner_grants(target.id)
        roles_removed = await scheduler._sync_winner_roles(
            target,
            reason="Manual admin reset of WINNER grants",
            announce=True,
            dm_on_remove=False,
            log_reason="manual_reset",
        )
        database.log_event(
            target.id,
            "reset_winner_grants",
            {
                "actor_id": actor_id,
                "grants_revoked": len(revoked_ids),
                "roles_removed": roles_removed,
                "user_ids": revoked_ids,
            },
        )
        if roles_removed == 0 and not revoked_ids:
            return {
                "ok": True,
                "message": "No active WINNER grants and no Discord WINNER roles to reset.",
            }
        if roles_removed == 0:
            return {
                "ok": True,
                "message": (
                    f"Revoked {len(revoked_ids)} active grant(s) in the database, "
                    "but no Discord WINNER roles were found."
                ),
            }
        return {
            "ok": True,
            "message": (
                f"Removed WINNER from {roles_removed} member(s)"
                + (
                    f" and revoked {len(revoked_ids)} active grant(s) in the database."
                    if revoked_ids
                    else " (database grants were already cleared)."
                )
                + " Normal expiry is one week from award until the next Friday close."
            ),
        }

    raise ValueError(f"Unknown action: {action}")


def get_game_status() -> dict[str, Any]:
    gid = _guild_id()
    week_key = database.week_key_for()
    selection_week = database.ticker_selection_week_key_for_guild(gid)
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


def _parse_audit_details(details: Any) -> dict[str, Any]:
    if details is None:
        return {}
    if isinstance(details, str):
        try:
            parsed = json.loads(details)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return dict(details) if isinstance(details, dict) else {}


def _backfill_completed_games_from_audit(guild_id: int) -> None:
    """One-time style recovery when votes were cleared but friday_close audit rows exist."""
    existing = {r["week_key"] for r in database.list_completed_games(guild_id, limit=50)}
    logs = database._select(
        "audit_logs",
        f"?select=created_at,details&guild_id=eq.{guild_id}&event_type=eq.friday_close"
        f"&order=created_at.desc&limit=50",
    )
    for log in logs:
        details = _parse_audit_details(log.get("details"))
        wk = str(details.get("week_key") or "").strip()
        if not wk or wk in existing:
            continue
        raw_winners = details.get("winners") or []
        winner_ids = [int(x) for x in raw_winners] if isinstance(raw_winners, list) else []
        stocks = database.winning_stocks_for_week(guild_id, wk)
        totals = database.vote_totals_for_week(guild_id, wk)
        has_votes = any(totals.get(cat) for cat in CATEGORIES)
        database._request(
            "POST",
            "completed_games",
            query="?on_conflict=guild_id,week_key",
            json_body={
                "guild_id": guild_id,
                "week_key": wk,
                "closed_at": log.get("created_at") or database.utc_now_iso(),
                "winner_ids": winner_ids,
                "winning_stocks": stocks if has_votes else {},
                "vote_totals": totals if has_votes else {},
                "winners": database.winners_payload(winner_ids),
            },
            headers={"Prefer": "resolution=merge-duplicates"},
        )
        existing.add(wk)

    for row in database._select(
        "winners",
        f"?select=week_key,user_id,awarded_at&guild_id=eq.{guild_id}&order=awarded_at.desc&limit=200",
    ):
        wk = str(row["week_key"])
        if wk in existing:
            continue
        uid = int(row["user_id"])
        peers = database._select(
            "winners",
            f"?select=user_id&guild_id=eq.{guild_id}&week_key=eq.{database._eq(wk)}",
        )
        winner_ids = sorted({int(p["user_id"]) for p in peers})
        database.save_completed_game(
            guild_id,
            wk,
            winner_ids=winner_ids,
            closed_at=str(row.get("awarded_at") or database.utc_now_iso()),
        )
        existing.add(wk)


def _enrich_winners_from_discord(guild_id: int, winners: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bot = app_state.bot
    if not bot or not app_state.bot_ready:
        return winners
    guild = bot.get_guild(guild_id)
    if not guild:
        return winners
    out: list[dict[str, Any]] = []
    for row in winners:
        uid = int(row.get("user_id", 0))
        name = str(row.get("username") or "").strip()
        if name.startswith("Player ") or not name:
            member = guild.get_member(uid)
            if member:
                name = str(member.display_name or member.name)
                database.upsert_user(uid, name)
        out.append({"user_id": uid, "username": name or f"Player {uid}"})
    return out


def get_game_history(limit: int = 20) -> list[dict[str, Any]]:
    gid = _guild_id()
    cap = min(max(limit, 1), 50)
    rows = database.list_completed_games(gid, limit=cap)
    if not rows:
        _backfill_completed_games_from_audit(gid)
        rows = database.list_completed_games(gid, limit=cap)

    games: list[dict[str, Any]] = []
    for row in rows:
        raw_ids = row.get("winner_ids") or []
        winner_ids = sorted(int(x) for x in raw_ids) if isinstance(raw_ids, list) else []
        stocks = row.get("winning_stocks") or {}
        if not isinstance(stocks, dict):
            stocks = {}
        vote_totals = row.get("vote_totals") or {}
        if not isinstance(vote_totals, dict):
            vote_totals = {}
        winners = row.get("winners") or []
        if not isinstance(winners, list):
            winners = []
        wk = str(row["week_key"])
        if not any(vote_totals.get(cat) for cat in CATEGORIES):
            live_totals = database.vote_totals_for_week(gid, wk)
            if any(live_totals.get(cat) for cat in CATEGORIES):
                vote_totals = live_totals
        if not any(stocks.get(cat) for cat in CATEGORIES):
            live = database.winning_stocks_for_week(gid, wk)
            if any(live.get(cat) for cat in CATEGORIES):
                stocks = live
        if not winners and winner_ids:
            winners = database.winners_payload(winner_ids)
        elif winners and winner_ids:
            known = {int(w.get("user_id", 0)) for w in winners if isinstance(w, dict)}
            missing = [uid for uid in winner_ids if uid not in known]
            if missing:
                names = database.usernames_for_discord_ids(missing)
                for uid in missing:
                    winners.append({"user_id": uid, "username": names.get(uid) or f"Player {uid}"})
        winners = _enrich_winners_from_discord(gid, winners)
        games.append(
            {
                "week_key": wk,
                "closed_at": row.get("closed_at"),
                "winner_ids": winner_ids,
                "winners": winners,
                "category_titles": CATEGORY_TITLES,
                "winning_stocks": stocks,
                "vote_totals": vote_totals,
            }
        )
    return games


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
