# cogs/scheduler.py
# Purpose: Weekly time-based automation. On Monday 09:00 ET:
# - Arm Early Window (24h)
# - Push tickers from #pick-results to WEEKLY channels as "VOTING OPEN" + live countdown + voting buttons
# - Reset #pick-results to 0/20
# - Close ticker submission channels visually (post "Submissions Closed (x/20)" and remove bot messages)
# - Log a concise report to #mod
# Includes robust ET/DST fallback (works even without tzdata).

from __future__ import annotations

import asyncio
from datetime import datetime, date, time as dtime, timedelta, timezone
from typing import Optional, List, Tuple

import discord
from discord.ext import commands

import database
from services.category_reconcile import reconcile_ticker_categories
from config import (
    CATEGORY_TITLES,
    CHANNEL_BLUE_LIVE,
    CHANNEL_BLUE_TICKER,
    CHANNEL_BLUE_VOTE,
    CHANNEL_FINAL_LEADERBOARD,
    CHANNEL_MID_LIVE,
    CHANNEL_MID_TICKER,
    CHANNEL_MID_VOTE,
    CHANNEL_SMALL_LIVE,
    CHANNEL_SMALL_TICKER,
    CHANNEL_SMALL_VOTE,
    CHANNEL_WINNERS,
    ROLE_WINNER,
    TICKER_LIMIT_PER_CATEGORY,
)
# --- Pull the primitives we already have in other cogs ---
# Early-window state + voting UI + leaderboards + helpers
from cogs.weekly_picks import (
    arm_early_window,
    is_early_window_active,
    build_weekly_voting_view,
    build_final_leaderboard_embeds,
    _post_or_update_leaderboard,
    _category_idx_to_weekly_name,
    _category_title,
    _delete_bot_messages,
    _purge_channel_messages,
    # NEW: use the same builder that includes the live T-minus line,
    # and the canonical end-of-window calculator
    _build_voting_open_embed,
    early_window_end_utc,
)

# Read/clear pick-results + build closed banner for ticker channels
from cogs.submission_ui import (
    OpenPickerView,
    _get_pick_results_channel,
    _find_pick_results_message,
    _extract_lists_from_pick_results,
    _closed_banner_embed,
    _clear_pick_results_message,
    reset_picker_runtime_state,
)

# --- TZ handling: try ZoneInfo; else robust manual ET with US DST rules ---
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None  # type: ignore

UTC = timezone.utc


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """weekday: Monday=0..Sunday=6. Return date of the n-th weekday in month."""
    first = date(year, month, 1)
    shift = (weekday - first.weekday()) % 7
    day = 1 + shift + 7 * (n - 1)
    return date(year, month, day)


def _us_dst_bounds_local(year: int) -> tuple[datetime, datetime]:
    """
    DST starts 02:00 local (EST) on 2nd Sunday in March,
    ends   02:00 local (EDT) on 1st  Sunday in November.
    """
    # Sunday = 6
    start_day = _nth_weekday_of_month(year, 3, 6, 2)
    end_day = _nth_weekday_of_month(year, 11, 6, 1)
    start_local = datetime(year, 3, start_day.day, 2, 0, 0)
    end_local = datetime(year, 11, end_day.day, 2, 0, 0)
    return start_local, end_local


def _us_dst_bounds_utc(year: int) -> tuple[datetime, datetime]:
    s_local, e_local = _us_dst_bounds_local(year)
    s_utc = s_local + timedelta(hours=5)  # EST -> UTC
    e_utc = e_local + timedelta(hours=4)  # EDT -> UTC
    return s_utc.replace(tzinfo=UTC), e_utc.replace(tzinfo=UTC)


def _is_dst_utc(dt_utc: datetime) -> bool:
    s_utc, e_utc = _us_dst_bounds_utc(dt_utc.year)
    return s_utc <= dt_utc < e_utc


def _is_dst_local(local_dt: datetime) -> bool:
    s_local, e_local = _us_dst_bounds_local(local_dt.year)
    return s_local <= local_dt < e_local


def _utc_to_et(dt_utc: datetime) -> datetime:
    """Return naive ET-local datetime from UTC, using US DST rules."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    offset_hours = -4 if _is_dst_utc(dt_utc) else -5
    return (dt_utc + timedelta(hours=offset_hours)).replace(tzinfo=None)


def _et_local_to_utc(local_dt: datetime) -> datetime:
    """Convert naive ET-local to UTC aware datetime using US DST rules."""
    offset_hours = -4 if _is_dst_local(local_dt) else -5
    return (local_dt - timedelta(hours=offset_hours)).replace(tzinfo=UTC)


# If ZoneInfo is available, prefer it; else use the manual converters above.
ET_TZ = None
if ZoneInfo is not None:
    try:
        ET_TZ = ZoneInfo("America/New_York")
    except Exception:
        ET_TZ = None  # fall back to manual


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _to_et(dt_utc: datetime) -> datetime:
    if ET_TZ:
        return dt_utc.astimezone(ET_TZ)
    return _utc_to_et(dt_utc)


def _from_et_local_to_utc(local_dt: datetime) -> datetime:
    if ET_TZ:
        return local_dt.replace(tzinfo=ET_TZ).astimezone(UTC)
    return _et_local_to_utc(local_dt)


def _monday_9am_et_for_week(dt_utc: datetime) -> datetime:
    """
    Given 'now' UTC, return THIS week's Monday 09:00 ET (as UTC tz-aware).
    If now is before that instant -> that's the next fire.
    """
    now_et = _to_et(dt_utc)
    weekday = now_et.weekday()  # Monday=0..Sunday=6
    monday_date = (now_et - timedelta(days=weekday)).date()
    local_monday_9 = datetime.combine(monday_date, dtime(9, 0))
    return _from_et_local_to_utc(local_monday_9)


def _next_monday_9am_et(dt_utc: datetime) -> datetime:
    """Return next occurrence of Monday 09:00 ET (UTC tz-aware)."""
    this_mon_9_utc = _monday_9am_et_for_week(dt_utc)
    if dt_utc < this_mon_9_utc:
        return this_mon_9_utc
    now_et = _to_et(dt_utc)
    weekday = now_et.weekday()
    monday_date = (now_et - timedelta(days=weekday)).date()
    next_monday_date = monday_date + timedelta(days=7)
    local_next_monday_9 = datetime.combine(next_monday_date, dtime(9, 0))
    return _from_et_local_to_utc(local_next_monday_9)


def _next_weekday_time_et(dt_utc: datetime, weekday: int, hour: int, minute: int = 0) -> datetime:
    now_et = _to_et(dt_utc)
    days = (weekday - now_et.weekday()) % 7
    target_date = (now_et + timedelta(days=days)).date()
    local_target = datetime.combine(target_date, dtime(hour, minute))
    target_utc = _from_et_local_to_utc(local_target)
    if target_utc <= dt_utc:
        target_utc = _from_et_local_to_utc(local_target + timedelta(days=7))
    return target_utc


# --- Local helpers ---

def _find_text_channel(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    for ch in guild.text_channels:
        if ch.name.lower() == name.lower():
            return ch
    return None


def _format_et(dt_utc: datetime) -> str:
    """Return 'YYYY-MM-DD HH:MM ET' for display."""
    if ET_TZ:
        return f"{dt_utc.astimezone(ET_TZ):%Y-%m-%d %H:%M} ET"
    return f"{_utc_to_et(dt_utc):%Y-%m-%d %H:%M} ET"


# kept for completeness; no longer used to build the banner
def _timer_lines(end_utc: datetime) -> str:
    unix = int(end_utc.timestamp())
    return f"**Early Winners Window ends:** {_format_et(end_utc)} — <t:{unix}:F> • <t:{unix}:R>"


def _winner_role_dm(valid_until_utc: datetime) -> str:
    et = _format_et(valid_until_utc)
    return (
        "You won this week's stock game and received the **WINNER** role for one week.\n\n"
        "**What WINNER gives you until then:**\n"
        "• **5 votes** per category each week (same as PLAYER)\n"
        "• Access to **ticker pick** channels during pre-vote\n"
        "• Access to **live leaderboard** channels during voting\n"
        "• Your votes count toward the weekly game like a subscriber\n\n"
        f"**Valid until:** {et}\n"
        "The role is removed automatically when that week ends."
    )


class SchedulerCog(commands.Cog):
    """Automations for Monday 09:00 ET. Also exposes manual admin triggers."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._task: Optional[asyncio.Task] = None
        self._next_fire_utc: Optional[datetime] = None

    async def cog_load(self):
        self._task = asyncio.create_task(
            self._runner(), name="scheduler_runner")

    def cog_unload(self):
        if self._task and not self._task.done():
            self._task.cancel()

    async def _announce_mod(self, guild: discord.Guild, title: str, desc: str, color: discord.Color):
        ch = _find_text_channel(guild, "mod")
        if not ch:
            return
        try:
            emb = discord.Embed(title=title, description=desc, color=color)
            await ch.send(embed=emb)
        except Exception:
            pass

    # ---------------- Core Monday-open operation ----------------

    async def _monday_open_one_guild(self, guild: discord.Guild) -> Tuple[int, List[int]]:
        """
        Executes Monday 09:00 ET flow for a single guild.
        Returns (updated_weekly_count, per_category_counts).
        """
        now_utc = _now_utc()

        # 1) Arm Early Window (start now)
        arm_early_window(now_utc)
        # canonical end instant (as computed by weekly_picks itself)
        end_utc = early_window_end_utc()
        week_key = database.week_key_for(now_utc)
        database.set_cycle_phase(
            guild.id,
            week_key,
            status="voting",
            ticker_selection_open=False,
            voting_open=True,
            early_window_open=True,
            monday_open_at=now_utc.isoformat(),
            early_window_end_at=end_utc.isoformat() if end_utc else None,
        )

        # 2) Read ballot from Supabase (#pic-results fallback). Do not reconcile here —
        # the ballot should stay 20 per category as picked. Live cap changes run during
        # voting via WeeklyPicksCog._category_reconcile_loop (every 5 min).
        stored = database.list_tickers(guild.id, week_key)
        lists: List[List[str]] = [stored["small"], stored["mid"], stored["blue"]]
        pr_msg = None
        pr_emb = None
        try:
            pr_ch = await _get_pick_results_channel(guild)
            if pr_ch:
                found = await _find_pick_results_message(pr_ch)
                if found:
                    pr_msg, pr_emb = found
                    if not any(lists):
                        lists = _extract_lists_from_pick_results(pr_emb)
        except Exception:
            pass

        # 3) Push to WEEKLY channels as active voting (VOTING OPEN + live T-minus + buttons)
        updated = 0
        per_cat_counts: List[int] = [0, 0, 0]
        for cat in range(3):
            ch_name = _category_idx_to_weekly_name(cat)
            ch = _find_text_channel(guild, ch_name)
            if not ch:
                continue

            tickers = lists[cat] if cat < len(lists) else []
            per_cat_counts[cat] = len(tickers)

            # Wipe everything so only the new voting banner remains
            try:
                await _purge_channel_messages(ch, guild, limit=500)
            except Exception:
                pass

            # Build the banner using the same builder as weekly_picks (includes ⏳ T-minus).
            banner = _build_voting_open_embed(cat, end_utc)
            if not tickers:
                # Voting is only from subscriber-selected tickers.
                original = banner.description or ""
                parts = original.split("\n\n", 1)
                tail = parts[1] if len(parts) > 1 else ""
                banner.description = f"**NO TICKERS SELECTED THIS WEEK**\n\n{tail}"

            view = await build_weekly_voting_view(cat, tickers) if tickers else None
            try:
                await ch.send(embed=banner, view=view)
                updated += 1
            except Exception:
                continue

            # Seed/refresh live leaderboard message
            try:
                await _post_or_update_leaderboard(guild, cat)
            except Exception:
                pass

        # 4) Reset #pic-results to empty display for the next ticker selection phase
        try:
            if pr_msg and pr_emb:
                await _clear_pick_results_message(pr_msg, pr_emb)
        except Exception:
            pass

        # 5) Close Ticker submission channels visually:
        ticker_map = {
            CHANNEL_SMALL_TICKER: 0,
            CHANNEL_MID_TICKER: 1,
            CHANNEL_BLUE_TICKER: 2,
        }
        for name, idx in ticker_map.items():
            tch = _find_text_channel(guild, name)
            if not tch:
                continue
            try:
                await _purge_channel_messages(tch, guild, limit=500)
            except Exception:
                pass
            try:
                emb = _closed_banner_embed(guild, count=per_cat_counts[idx])
                await tch.send(embed=emb)
            except Exception:
                pass

        # 6) Report to #mod
        try:
            lines = []
            for i in range(3):
                lines.append(
                    f"• {_category_title(i)}: {per_cat_counts[i]}/{TICKER_LIMIT_PER_CATEGORY} pushed")
            lines.append("")
            if end_utc:
                lines.append(f"Early window ends: {_format_et(end_utc)}")
            await self._announce_mod(
                guild,
                title="Monday Open — Completed",
                desc="\n".join(lines),
                color=discord.Color.green(),
            )
        except Exception:
            pass

        return updated, per_cat_counts

    async def _monday_open_all_guilds(self):
        for g in list(self.bot.guilds):
            try:
                await self._monday_open_one_guild(g)
            except Exception as e:
                await self._announce_mod(
                    g,
                    title="Monday Open — Error",
                    desc=f"An error occurred during Monday-open automation: {e!r}",
                    color=discord.Color.red(),
                )

    async def _tuesday_early_close_one_guild(self, guild: discord.Guild) -> None:
        week_key = database.week_key_for(_now_utc())
        database.set_cycle_phase(
            guild.id,
            week_key,
            status="voting",
            ticker_selection_open=False,
            voting_open=True,
            early_window_open=False,
        )
        await self._announce_mod(
            guild,
            "Early Winner Window Closed",
            f"Early-vote eligibility ended at {_format_et(_now_utc())}. New votes still count for the weekly vote, but not winner eligibility.",
            discord.Color.orange(),
        )

    async def _tuesday_early_close_all_guilds(self) -> None:
        for guild in list(self.bot.guilds):
            await self._tuesday_early_close_one_guild(guild)

    async def _reopen_ticker_channels(self, guild: discord.Guild) -> None:
        next_week_key = database.ticker_selection_week_key_for(_now_utc())
        database.ensure_cycle(guild.id, next_week_key)
        database.set_cycle_phase(
            guild.id,
            next_week_key,
            status="ticker_selection",
            ticker_selection_open=True,
            voting_open=False,
            early_window_open=False,
        )
        opener = discord.Embed(
            title="YOU CHOOSE YOUR TICKER",
            description=(
                "Click **Open Picker**, then use the **dropdown** or **Search symbol** "
                "Use the **dropdown** or **Search symbol** (type letters, then pick from the list).\n\n"
                "Need ideas? Click **Show 20 Examples** for twenty sample stocks (names and prices). "
                "Then use **Show 20 more** on that private message to load the next twenty, as many times as you like."
            ),
            color=discord.Color.blurple(),
        )
        for name in (CHANNEL_SMALL_TICKER, CHANNEL_MID_TICKER, CHANNEL_BLUE_TICKER):
            ch = _find_text_channel(guild, name)
            if not ch:
                continue
            await _purge_channel_messages(ch, guild, limit=500)
            await ch.send(embed=opener, view=OpenPickerView(channel=ch, user_id=0))

    async def _restart_pre_voting_one_guild(self, guild: discord.Guild, actor_id: int | None = None) -> None:
        now_utc = _now_utc()
        week_key = database.ticker_selection_week_key_for(now_utc)
        database.ensure_cycle(guild.id, week_key)
        cycle = database.ensure_cycle(guild.id, week_key)
        if str(cycle.get("status") or "") == "closed":
            week_key = database.next_week_key_for(now_utc)
            database.ensure_cycle(guild.id, week_key)
        database.reset_week_game_data(guild.id, week_key)
        reset_picker_runtime_state()
        database.set_cycle_phase(
            guild.id,
            week_key,
            status="ticker_selection",
            ticker_selection_open=True,
            voting_open=False,
            early_window_open=False,
        )

        stopped = discord.Embed(
            title="PRE-VOTING STAGE ACTIVE",
            description=(
                "Any previous Vote Stage was stopped. Subscribed users, WINNERS, and admins can now "
                "choose the 20 stocks for each category."
            ),
            color=discord.Color.blurple(),
        )
        for name in (CHANNEL_SMALL_VOTE, CHANNEL_MID_VOTE, CHANNEL_BLUE_VOTE):
            ch = _find_text_channel(guild, name)
            if ch:
                await _purge_channel_messages(ch, guild, limit=500)
                await ch.send(embed=stopped)

        for name in (CHANNEL_SMALL_LIVE, CHANNEL_MID_LIVE, CHANNEL_BLUE_LIVE):
            ch = _find_text_channel(guild, name)
            if ch:
                await _purge_channel_messages(ch, guild, limit=500)
        for cat in range(3):
            try:
                await _post_or_update_leaderboard(guild, cat)
            except Exception:
                pass

        pr_ch = await _get_pick_results_channel(guild)
        if pr_ch:
            found = await _find_pick_results_message(pr_ch)
            if found:
                msg, emb = found
                await _clear_pick_results_message(msg, emb)
            else:
                emb = discord.Embed(
                    title="PICK RESULTS",
                    description=(
                        f"Small / Mid / Blue weekly lists. Each category closes at {TICKER_LIMIT_PER_CATEGORY} tickers."
                    ),
                    color=discord.Color.gold(),
                )
                emb.add_field(name=f"{CATEGORY_TITLES['small']} (0/{TICKER_LIMIT_PER_CATEGORY})", value="—", inline=False)
                emb.add_field(name=f"{CATEGORY_TITLES['mid']} (0/{TICKER_LIMIT_PER_CATEGORY})", value="—", inline=False)
                emb.add_field(name=f"{CATEGORY_TITLES['blue']} (0/{TICKER_LIMIT_PER_CATEGORY})", value="—", inline=False)
                await pr_ch.send(embed=emb)

        await self._reopen_ticker_channels(guild)
        database.log_event(
            guild.id,
            "manual_restart_pre_voting",
            {"week_key": week_key, "actor_id": actor_id},
        )
        await self._announce_mod(
            guild,
            "Pre-Voting Restarted",
            f"Stopped any active game state and restarted Pre-Voting for `{week_key}`.",
            discord.Color.blurple(),
        )

    async def _expire_winners(self, guild: discord.Guild) -> None:
        role = discord.utils.get(guild.roles, name=ROLE_WINNER)
        if not role:
            return
        for row in database.active_winners():
            if int(row["guild_id"]) != guild.id:
                continue
            member = guild.get_member(int(row["user_id"]))
            if member and role in member.roles:
                try:
                    await member.remove_roles(role, reason="WINNER role expired")
                except Exception:
                    pass
            database.mark_winner_removed(int(row["id"]))

    async def _publish_last_game_winners(
        self,
        guild: discord.Guild,
        *,
        week_key: str,
        winner_ids: list[int],
        valid_until_utc: datetime,
    ) -> None:
        winner_channel = _find_text_channel(guild, CHANNEL_WINNERS)
        if not winner_channel:
            return

        await _delete_bot_messages(winner_channel, guild, limit=200)
        if winner_ids:
            mentions = "\n".join(f"<@{user_id}>" for user_id in winner_ids)
            embed = discord.Embed(
                title="LAST GAME WINNER",
                description=(
                    f"Week: **{week_key}**\n"
                    f"Winner(s):\n{mentions}\n\n"
                    f"Role: **{ROLE_WINNER}** (one week)\n"
                    f"Valid until: **{_format_et(valid_until_utc)}**\n\n"
                    "**WINNER perks:** 5 votes per category, ticker-pick channels, live leaderboards "
                    "(same access as PLAYER for that week)."
                ),
                color=discord.Color.gold(),
            )
        else:
            embed = discord.Embed(
                title="LAST GAME WINNER",
                description=f"Week: **{week_key}**\nNo winner met all eligibility conditions.",
                color=discord.Color.dark_grey(),
            )
        await winner_channel.send(embed=embed)

    async def _refresh_last_game_winners_from_db(self, guild: discord.Guild) -> None:
        latest = database.latest_winners_for_guild(guild.id)
        if not latest:
            return
        expires_raw = latest.get("expires_at")
        try:
            expires_at = datetime.fromisoformat(str(expires_raw))
        except Exception:
            expires_at = _now_utc()
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        await self._publish_last_game_winners(
            guild,
            week_key=str(latest["week_key"]),
            winner_ids=list(latest["winner_ids"]),
            valid_until_utc=expires_at,
        )

    async def _friday_close_one_guild(self, guild: discord.Guild) -> None:
        now_utc = _now_utc()
        week_key = database.week_key_for(now_utc)
        await asyncio.to_thread(reconcile_ticker_categories, guild.id, week_key)
        database.set_cycle_phase(
            guild.id,
            week_key,
            status="closed",
            ticker_selection_open=False,
            voting_open=False,
            early_window_open=False,
            friday_close_at=now_utc.isoformat(),
        )

        closed = discord.Embed(
            title="VOTING CLOSED",
            description="Voting is closed until Monday at 9:00 AM ET.",
            color=discord.Color.dark_grey(),
        )
        for name in (CHANNEL_SMALL_VOTE, CHANNEL_MID_VOTE, CHANNEL_BLUE_VOTE):
            ch = _find_text_channel(guild, name)
            if ch:
                await _purge_channel_messages(ch, guild, limit=500)
                await ch.send(embed=closed)

        leaderboard = _find_text_channel(guild, CHANNEL_FINAL_LEADERBOARD)
        if leaderboard:
            final_embeds = await build_final_leaderboard_embeds(guild.id, week_key)
            for emb in final_embeds:
                await leaderboard.send(embed=emb)

        await self._expire_winners(guild)

        winners = database.eligible_winners(guild.id, week_key)
        database.save_completed_game(
            guild.id,
            week_key,
            winner_ids=winners,
            closed_at=now_utc.isoformat(),
        )
        winner_role = discord.utils.get(guild.roles, name=ROLE_WINNER)
        expires_at_utc = now_utc + timedelta(days=7)
        expires_at = expires_at_utc.isoformat()
        if winners and winner_role:
            for user_id in winners:
                database.add_winner(guild.id, week_key, user_id, expires_at)
                member = guild.get_member(user_id)
                if member:
                    database.upsert_user(user_id, str(member.display_name or member.name))
                if member:
                    try:
                        await member.add_roles(winner_role, reason="Weekly stock game winner")
                        await member.send(_winner_role_dm(expires_at_utc))
                    except Exception:
                        pass
        await self._publish_last_game_winners(
            guild,
            week_key=week_key,
            winner_ids=winners,
            valid_until_utc=expires_at_utc,
        )

        await self._reopen_ticker_channels(guild)
        database.log_event(
            guild.id,
            "friday_close",
            {"week_key": week_key, "winner_count": len(winners), "winners": winners},
        )
        await self._announce_mod(
            guild,
            "Friday Close — Completed",
            f"Closed weekly voting, calculated {len(winners)} winner(s), and reopened ticker selection.",
            discord.Color.green(),
        )

    async def _friday_close_all_guilds(self) -> None:
        for guild in list(self.bot.guilds):
            try:
                await self._friday_close_one_guild(guild)
            except Exception as e:
                await self._announce_mod(guild, "Friday Close — Error", repr(e), discord.Color.red())

    async def _bootstrap_if_inside_window(self):
        """
        If bot starts within [Mon 09:00 ET, Tue 09:00 ET) and early window
        isn't armed yet, run the full Monday-open flow immediately.
        """
        now_utc = _now_utc()
        this_mon_9_utc = _monday_9am_et_for_week(now_utc)
        tue_9_utc = this_mon_9_utc + timedelta(days=1)
        if this_mon_9_utc <= now_utc < tue_9_utc:
            if not is_early_window_active(now_utc):
                await self._monday_open_all_guilds()

    async def _runner(self):
        await self.bot.wait_until_ready()

        # If we're already inside the window on startup, run now.
        try:
            await self._bootstrap_if_inside_window()
        except Exception as e:
            print(f"[scheduler] bootstrap check error: {e!r}")

        while not self.bot.is_closed():
            now_utc = _now_utc()
            monday_utc = _next_monday_9am_et(now_utc)
            tuesday_utc = _next_weekday_time_et(now_utc, 1, 9, 0)
            friday_utc = _next_weekday_time_et(now_utc, 4, 16, 0)
            target_utc = min(monday_utc, tuesday_utc, friday_utc)
            self._next_fire_utc = target_utc
            print(
                f"[scheduler] Next automation at (UTC): {target_utc:%Y-%m-%d %H:%M:%S}")
            # Sleep until fire
            seconds = max(1.0, (target_utc - now_utc).total_seconds())
            try:
                await asyncio.sleep(seconds)
            except asyncio.CancelledError:
                return

            try:
                if target_utc == monday_utc:
                    await self._monday_open_all_guilds()
                elif target_utc == tuesday_utc:
                    await self._tuesday_early_close_all_guilds()
                else:
                    await self._friday_close_all_guilds()
            except Exception as e:
                print(f"[scheduler] automation error: {e!r}")

    # ---------- Admin helpers ----------

    @commands.command(name="sched_status")
    @commands.has_role("ADMIN")
    async def sched_status(self, ctx: commands.Context):
        """ADMIN: show the next scheduled automation times."""
        now_utc = _now_utc()
        monday = _next_monday_9am_et(now_utc)
        tuesday = _next_weekday_time_et(now_utc, 1, 9, 0)
        friday = _next_weekday_time_et(now_utc, 4, 16, 0)
        desc = (
            f"Monday open: **{_format_et(monday)}**\n"
            f"Early window close: **{_format_et(tuesday)}**\n"
            f"Friday close: **{_format_et(friday)}**"
        )
        emb = discord.Embed(title="Scheduler — Status",
                            description=desc, color=discord.Color.blurple())
        await ctx.send(embed=emb)

    @commands.command(name="sched_monday_open_now")
    @commands.has_role("ADMIN")
    async def sched_monday_open_now(self, ctx: commands.Context):
        """
        ADMIN: run the full Monday-open automation NOW for this guild:
          - Arm 24h window
          - Push VOTING OPEN + buttons + countdown to WEEKLY channels
          - Reset #pick-results
          - Close ticker submission channels visually
          - Log to #mod
        """
        if not ctx.guild:
            await ctx.send("This command must be run in a server.")
            return
        await ctx.send("Running Monday-open flow now…")
        try:
            updated, counts = await self._monday_open_one_guild(ctx.guild)
            done = discord.Embed(
                title="Manual Monday Open — Done",
                description=(
                    f"Pushed in {updated} channel(s).\n"
                    f"Small: {counts[0]}/{TICKER_LIMIT_PER_CATEGORY} • "
                    f"Mid: {counts[1]}/{TICKER_LIMIT_PER_CATEGORY} • "
                    f"Blue: {counts[2]}/{TICKER_LIMIT_PER_CATEGORY}"
                ),
                color=discord.Color.green()
            )
            await ctx.send(embed=done)
        except Exception as e:
            await ctx.send(f"Error: {e!r}")

    @commands.command(name="sched_friday_close_now")
    @commands.has_role("ADMIN")
    async def sched_friday_close_now(self, ctx: commands.Context):
        if not ctx.guild:
            await ctx.send("This command must be run in a server.")
            return
        await ctx.send("Running Friday-close flow now…")
        try:
            await self._friday_close_one_guild(ctx.guild)
            await ctx.send("Friday-close flow completed.")
        except Exception as e:
            await ctx.send(f"Error: {e!r}")


# -------- Extension hook (required by discord.py to load this cog) --------
async def setup(bot: commands.Bot):
    await bot.add_cog(SchedulerCog(bot))
