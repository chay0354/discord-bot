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
from config import (
    CATEGORY_TITLES,
    CHANNEL_BLUE_LIVE,
    CHANNEL_BLUE_TICKER,
    CHANNEL_BLUE_VOTE,
    CHANNEL_FINAL_LEADERBOARD,
    CHANNEL_MID_LIVE,
    CHANNEL_MID_TICKER,
    CHANNEL_MID_VOTE,
    CHANNEL_PLAYER,
    CHANNEL_SMALL_LIVE,
    CHANNEL_SMALL_TICKER,
    CHANNEL_SMALL_VOTE,
    CHANNEL_WINNERS,
    PLAYER_CHANNEL_CANDIDATES,
    ROLE_WINNER,
    TICKER_LIMIT_PER_CATEGORY,
)
# --- Pull the primitives we already have in other cogs ---
# Early-window state + voting UI + leaderboards + helpers
from cogs.weekly_picks import (
    arm_early_window,
    restore_early_window,
    is_early_window_active,
    build_weekly_voting_view,
    build_final_leaderboard_embeds,
    _post_or_update_leaderboard,
    _category_idx_to_weekly_name,
    _category_title,
    _delete_bot_messages,
    _purge_channel_messages,
    _role_snapshot,
    # Voting banner builder (includes live Discord relative timestamp)
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
        "CONGRATULATIONS! 🎉 **YOU WON THE WINNER ROLE** 🎉\n\n"
        f"Your **WINNER** role is valid for one week, starting now until **{et}**.\n\n"
        "**The WINNER role gives you the same perks as a PLAYER subscription:**\n"
        "• **5 votes** per week in each category in the WEEKLY PICKS channels (instead of 1)\n"
        "• Access to subscriber-only channels:\n"
        "  • **CHOOSE YOUR TICKER** channels — pick stocks for next week's ballot\n"
        "  • **LIVE LEADERBOARD** channels — live vote counts during the week\n"
        "  • VIP chat for subscribers only\n\n"
        "⚠ This role will be removed automatically when the week ends."
    )


def _winner_role_removed_dm(player_mention: str) -> str:
    return (
        "**WINNER ROLE REMOVED**\n\n"
        "If you would like to continue enjoying WINNER perks, you can participate in next week's "
        f"competition or subscribe in {player_mention}."
    )


def _player_channel_mention(guild: discord.Guild) -> str:
    names = {CHANNEL_PLAYER.lower(), *(n.lower() for n in PLAYER_CHANNEL_CANDIDATES)}
    for ch in guild.text_channels:
        if ch.name.lower() in names:
            return ch.mention
    return f"#{CHANNEL_PLAYER}"


def _format_winner_report(report: dict) -> str:
    lines = [f"Week: `{report.get('week_key', '?')}`"]
    winning = report.get("winning_tickers") or {}
    if winning:
        lines.append("**Top tickers:**")
        for cat, tickers in winning.items():
            title = CATEGORY_TITLES.get(cat, cat)
            lines.append(f"• {title}: {', '.join(f'${t}' for t in tickers)}")
    winners = report.get("eligible_winner_ids") or []
    lines.append(f"**Eligible winners ({len(winners)}):** " + (", ".join(f"<@{uid}>" for uid in winners) or "none"))
    active = report.get("active_winner_user_ids") or []
    if active:
        lines.append(
            "**Skipped (active WINNER grant):** "
            + ", ".join(f"<@{uid}>" for uid in active)
        )
    exclusions = report.get("exclusions") or []
    if exclusions:
        by_reason: dict[str, int] = {}
        for row in exclusions:
            reason = row.get("reason", "?")
            by_reason[reason] = by_reason.get(reason, 0) + 1
        summary = ", ".join(f"{reason}={count}" for reason, count in sorted(by_reason.items()))
        lines.append(f"**Ineligible vote records:** {summary}")
    note = report.get("note")
    if note:
        lines.append(f"_{note}_")
    return "\n".join(lines)


class StepReport:
    """Collects per-step pass/fail results for a weekly-cycle operation.

    Produces a verified #mod checklist (report item #10): every step is recorded
    with an explicit ✅/❌ and, on failure, the reason — instead of a single
    generic "success" message.
    """

    _ICON = {"ok": "✅", "fail": "❌", "info": "•", "skip": "⏭️"}

    def __init__(self, title: str):
        self.title = title
        self.steps: list[tuple[str, str, str]] = []

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        self.steps.append((label, "ok" if ok else "fail", detail))
        return ok

    def ok(self, label: str, detail: str = "") -> None:
        self.steps.append((label, "ok", detail))

    def fail(self, label: str, detail: str = "") -> None:
        self.steps.append((label, "fail", detail))

    def info(self, label: str, detail: str = "") -> None:
        self.steps.append((label, "info", detail))

    @property
    def failures(self) -> list[tuple[str, str, str]]:
        return [s for s in self.steps if s[1] == "fail"]

    @property
    def any_failed(self) -> bool:
        return bool(self.failures)

    def to_embed(self) -> discord.Embed:
        lines: list[str] = []
        for label, status, detail in self.steps:
            line = f"{self._ICON.get(status, '•')} {label}"
            if detail:
                line += f" — {detail}"
            lines.append(line)
        if self.any_failed:
            color = discord.Color.red()
            header = f"⚠️ Completed with {len(self.failures)} failed step(s)"
        else:
            color = discord.Color.green()
            header = "All steps verified successfully"
        desc = header + "\n\n" + "\n".join(lines)
        return discord.Embed(title=self.title, description=desc[:4090], color=color)

    def summary_dict(self) -> dict:
        return {
            "title": self.title,
            "failed": self.any_failed,
            "steps": [
                {"label": label, "status": status, "detail": detail}
                for label, status, detail in self.steps
            ],
        }


async def winner_award_filter_sets(
    guild: discord.Guild,
    *,
    week_start_iso: str | None = None,
) -> tuple[set[int], set[int]]:
    """Member ids in guild, and ids blocked from WINNER award (PLAYER role or paid sub).

    The membership set drives the ban/leave exclusion (a user who left or was
    banned is no longer in ``guild.members``). We chunk the guild first so the
    member cache is complete; otherwise a still-present winner could be missing
    from the cache and wrongly excluded.

    The blocked set excludes anyone who is a PLAYER / active subscriber **now**,
    *or* who gained PLAYER access at any point during the week — so an NPC who
    became a PLAYER mid-week (even if they reverted to NPC) cannot win.
    """
    if not guild.chunked:
        try:
            await guild.chunk()
        except Exception as exc:  # noqa: BLE001
            print(f"[scheduler] guild.chunk() failed for {guild.id}: {exc!r}", flush=True)
    member_ids = {m.id for m in guild.members}
    player_or_paid: set[int] = set()
    for member in guild.members:
        if _role_snapshot(member) == "PLAYER":
            player_or_paid.add(member.id)
        elif database.is_paid_member(member.id):
            player_or_paid.add(member.id)
    # Anyone who became a PLAYER during the week is blocked, even if reverted.
    if week_start_iso:
        try:
            player_or_paid |= await asyncio.to_thread(
                database.player_grant_user_ids_since, week_start_iso
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[scheduler] player_grant lookup failed for {guild.id}: {exc!r}", flush=True)
    return member_ids, player_or_paid


class SchedulerCog(commands.Cog):
    """Automations for Monday 09:00 ET. Also exposes manual admin triggers."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._task: Optional[asyncio.Task] = None
        self._next_fire_utc: Optional[datetime] = None

    async def cog_load(self):
        self._task = asyncio.create_task(
            self._runner(), name="scheduler_runner")

    @commands.Cog.listener()
    async def on_ready(self):
        """Remove expired WINNER roles after restarts (not only on Friday close)."""
        for guild in list(self.bot.guilds):
            try:
                await self._expire_winners(guild)
            except Exception as exc:
                print(f"[scheduler] winner expiry on_ready failed for {guild.id}: {exc!r}")

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

    async def _announce_report(self, guild: discord.Guild, report: "StepReport") -> None:
        ch = _find_text_channel(guild, "mod")
        if not ch:
            return
        try:
            await ch.send(embed=report.to_embed())
        except Exception:
            pass

    # ---------------- Core Monday-open operation ----------------

    async def _monday_open_one_guild(self, guild: discord.Guild) -> Tuple[int, List[int]]:
        """
        Executes Monday 09:00 ET flow for a single guild.
        Returns (updated_weekly_count, per_category_counts).
        """
        now_utc = _now_utc()
        week_key = database.week_key_for(now_utc)
        rpt = StepReport(f"Monday Open — Week {week_key} ({_format_et(now_utc)})")

        # Remove any expired WINNER grants before opening a new voting week.
        try:
            expired = await self._expire_winners(guild)
            rpt.ok("Expired WINNER roles removed", f"{expired} member(s)")
        except Exception as exc:
            rpt.fail("Expired WINNER roles removed", repr(exc))

        # 1) Arm Early Window (start now) + set phase.
        arm_early_window(now_utc)
        end_utc = early_window_end_utc()
        try:
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
            rpt.ok(
                "Voting window opened (24h early window armed)",
                f"early window ends {_format_et(end_utc)}" if end_utc else "",
            )
        except Exception as exc:
            rpt.fail("Voting window opened (24h early window armed)", repr(exc))

        # 2) Read ballot from Supabase (tickers chosen during weekend selection).
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
        total_moved = sum(len(lists[i]) for i in range(3))
        rpt.info(
            "Tickers moved from weekend selection to WEEKLY PICKS",
            f"{total_moved} ticker(s) — "
            + " • ".join(f"{_category_title(i)}: {len(lists[i])}" for i in range(3)),
        )

        # 3) Push to WEEKLY channels (clear old messages, post VOTING OPEN + buttons).
        updated = 0
        cleared = 0
        per_cat_counts: List[int] = [0, 0, 0]
        weekly_found = 0
        leaderboards_ok = 0
        for cat in range(3):
            ch_name = _category_idx_to_weekly_name(cat)
            ch = _find_text_channel(guild, ch_name)
            if not ch:
                continue
            weekly_found += 1

            tickers = lists[cat] if cat < len(lists) else []
            per_cat_counts[cat] = len(tickers)

            try:
                await _purge_channel_messages(ch, guild, limit=500)
                cleared += 1
            except Exception:
                pass

            banner = _build_voting_open_embed(cat, end_utc)
            if not tickers:
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

            try:
                await _post_or_update_leaderboard(guild, cat)
                leaderboards_ok += 1
            except Exception:
                pass

        rpt.check(
            "WEEKLY PICKS channels opened with VOTING OPEN + buttons",
            updated == 3,
            f"{updated}/3 channels" + ("" if weekly_found == 3 else f" ({weekly_found} found)"),
        )
        rpt.check(
            "Previous WEEKLY PICKS messages removed",
            cleared == weekly_found and weekly_found > 0,
            f"{cleared}/{weekly_found or 3} channels",
        )
        rpt.check(
            "Live leaderboard tables updated",
            leaderboards_ok == 3,
            f"{leaderboards_ok}/3 channels",
        )

        # 4) Reset the live-chosen-tickers (PICK RESULTS) board.
        try:
            if pr_msg and pr_emb:
                await _clear_pick_results_message(pr_msg, pr_emb)
                rpt.ok("live-chosen-tickers board reset for next selection")
            else:
                rpt.info("live-chosen-tickers board reset for next selection", "no board found")
        except Exception as exc:
            rpt.fail("live-chosen-tickers board reset for next selection", repr(exc))

        # 5) Close CHOOSE YOUR TICKER channels visually.
        ticker_map = {
            CHANNEL_SMALL_TICKER: 0,
            CHANNEL_MID_TICKER: 1,
            CHANNEL_BLUE_TICKER: 2,
        }
        ticker_closed = 0
        ticker_found = 0
        for name, idx in ticker_map.items():
            tch = _find_text_channel(guild, name)
            if not tch:
                continue
            ticker_found += 1
            try:
                await _purge_channel_messages(tch, guild, limit=500)
            except Exception:
                pass
            try:
                emb = _closed_banner_embed(guild, count=per_cat_counts[idx])
                await tch.send(embed=emb)
                ticker_closed += 1
            except Exception:
                pass
        rpt.check(
            "CHOOSE YOUR TICKER channels closed for the week",
            ticker_closed == ticker_found and ticker_found > 0,
            f"{ticker_closed}/{ticker_found or 3} channels",
        )

        database.log_event(
            guild.id,
            "monday_open",
            {
                "week_key": week_key,
                "per_category_counts": per_cat_counts,
                "tickers_moved": total_moved,
                "report": rpt.summary_dict(),
            },
        )
        await self._announce_report(guild, rpt)

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

    async def _reopen_ticker_channels(self, guild: discord.Guild) -> dict[str, int]:
        """Reopen the CHOOSE YOUR TICKER channels. Returns verifiable counts."""
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
                "Click **Open Picker** and type the **full ticker symbol** "
                "(with or without `$`).\n\n"
                "The ticker must be a real **NASDAQ** or **NYSE** stock that fits this channel’s "
                "market-cap category."
            ),
            color=discord.Color.blurple(),
        )
        result = {"total": 0, "found": 0, "cleared": 0, "reopened": 0}
        for name in (CHANNEL_SMALL_TICKER, CHANNEL_MID_TICKER, CHANNEL_BLUE_TICKER):
            result["total"] += 1
            ch = _find_text_channel(guild, name)
            if not ch:
                continue
            result["found"] += 1
            try:
                await _purge_channel_messages(ch, guild, limit=500)
                result["cleared"] += 1
            except Exception:
                pass
            try:
                await ch.send(embed=opener, view=OpenPickerView(channel=ch, user_id=0))
                result["reopened"] += 1
            except Exception:
                pass
        return result

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

    async def _expire_winners(self, guild: discord.Guild) -> int:
        role = discord.utils.get(guild.roles, name=ROLE_WINNER)
        if not role:
            return 0
        player_mention = _player_channel_mention(guild)
        removed = 0
        for row in database.expired_winner_grants(guild.id):
            user_id = int(row["user_id"])
            member = guild.get_member(user_id)
            if member and role in member.roles:
                try:
                    await member.remove_roles(role, reason="WINNER role expired")
                    removed += 1
                    try:
                        await member.send(_winner_role_removed_dm(player_mention))
                    except Exception:
                        pass
                except Exception:
                    pass
            database.mark_winner_removed(int(row["id"]))
        if removed:
            await self._announce_mod(
                guild,
                "WINNER Roles Expired",
                f"Removed **{ROLE_WINNER}** from **{removed}** member(s) whose one-week grant ended.",
                discord.Color.orange(),
            )
        return removed

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

    async def _friday_close_one_guild(self, guild: discord.Guild) -> "StepReport":
        now_utc = _now_utc()
        week_key = database.week_key_for(now_utc)
        rpt = StepReport(f"Friday Close — Week {week_key} ({_format_et(now_utc)})")

        # 1) Mark the weekly cycle closed.
        try:
            database.set_cycle_phase(
                guild.id,
                week_key,
                status="closed",
                ticker_selection_open=False,
                voting_open=False,
                early_window_open=False,
                friday_close_at=now_utc.isoformat(),
            )
            rpt.ok("WEEKLY PICKS voting closed successfully")
        except Exception as exc:
            rpt.fail("WEEKLY PICKS voting closed successfully", repr(exc))

        # 2) Purge WEEKLY PICKS channels (removes buttons) + post VOTING CLOSED.
        closed = discord.Embed(
            title="VOTING CLOSED",
            description=(
                "Voting will resume **Monday at 9 AM EST**.\n"
                "CHOOSE YOUR TICKER is now open for PLAYER and WINNER roles."
            ),
            color=discord.Color.dark_grey(),
        )
        weekly_names = (CHANNEL_SMALL_VOTE, CHANNEL_MID_VOTE, CHANNEL_BLUE_VOTE)
        buttons_removed = 0
        closed_posted = 0
        weekly_found = 0
        for name in weekly_names:
            ch = _find_text_channel(guild, name)
            if not ch:
                continue
            weekly_found += 1
            try:
                await _purge_channel_messages(ch, guild, limit=500)
                buttons_removed += 1
            except Exception:
                pass
            try:
                await ch.send(embed=closed)
                closed_posted += 1
            except Exception:
                pass
        rpt.check(
            "Buttons were removed from the WEEKLY PICKS channels",
            buttons_removed == len(weekly_names),
            f"{buttons_removed}/{len(weekly_names)} channels",
        )
        rpt.check(
            "Channel closed message was posted successfully",
            closed_posted == len(weekly_names),
            f"{closed_posted}/{len(weekly_names)} channels"
            + ("" if weekly_found == len(weekly_names) else f" ({weekly_found} found)"),
        )

        # 3) Post final leaderboard tables.
        leaderboard = _find_text_channel(guild, CHANNEL_FINAL_LEADERBOARD)
        if leaderboard:
            try:
                final_embeds = await build_final_leaderboard_embeds(guild.id, week_key)
                posted = 0
                for emb in final_embeds:
                    await leaderboard.send(embed=emb)
                    posted += 1
                rpt.check("Leaderboard tables were posted successfully", posted > 0, f"{posted} table(s)")
            except Exception as exc:
                rpt.fail("Leaderboard tables were posted successfully", repr(exc))
        else:
            rpt.fail("Leaderboard tables were posted successfully", "leaderboard channel not found")

        # 4) Expire any WINNER grants whose week ended.
        try:
            expired = await self._expire_winners(guild)
            rpt.ok("Expired WINNER roles removed", f"{expired} member(s)")
        except Exception as exc:
            rpt.fail("Expired WINNER roles removed", repr(exc))

        # 5) Compute eligible winners (NPC + early-window only) and persist.
        week_start_iso = (now_utc - timedelta(days=7)).isoformat()
        member_ids, player_or_paid = await winner_award_filter_sets(
            guild, week_start_iso=week_start_iso
        )
        report = database.eligible_winners_report(
            guild.id,
            week_key,
            guild_member_ids=member_ids,
            player_or_paid_ids=player_or_paid,
        )
        winners = list(report.get("eligible_winner_ids") or [])
        try:
            database.save_completed_game(
                guild.id, week_key, winner_ids=winners, closed_at=now_utc.isoformat()
            )
            rpt.ok("Winners were calculated and saved", _format_winner_report(report))
        except Exception as exc:
            rpt.fail("Winners were calculated and saved", repr(exc))

        # 6) Announce winners.
        try:
            expires_at_utc = now_utc + timedelta(days=7)
            await self._publish_last_game_winners(
                guild, week_key=week_key, winner_ids=winners, valid_until_utc=expires_at_utc
            )
            rpt.check(
                "Winners were announced successfully",
                True,
                f"{len(winners)} winner(s)" if winners else "no eligible winners",
            )
        except Exception as exc:
            rpt.fail("Winners were announced successfully", repr(exc))

        # 7) Grant WINNER roles.
        winner_role = discord.utils.get(guild.roles, name=ROLE_WINNER)
        expires_at = (now_utc + timedelta(days=7)).isoformat()
        granted = 0
        grant_errors = 0
        if winners and winner_role:
            active_ids = database.active_winner_user_ids(guild.id)
            for user_id in winners:
                if user_id in active_ids:
                    continue
                member = guild.get_member(user_id)
                if not member:
                    continue
                if member and winner_role in member.roles:
                    continue
                try:
                    database.add_winner(guild.id, week_key, user_id, expires_at)
                except Exception:
                    grant_errors += 1
                    continue
                if member:
                    database.upsert_user(user_id, str(member.display_name or member.name))
                    try:
                        await member.add_roles(winner_role, reason="Weekly stock game winner")
                        granted += 1
                        try:
                            await member.send(_winner_role_dm(now_utc + timedelta(days=7)))
                        except Exception:
                            pass
                    except Exception:
                        grant_errors += 1
        if not winner_role:
            rpt.fail("WINNER roles were added to the winners", f"role '{ROLE_WINNER}' not found")
        else:
            rpt.check(
                "WINNER roles were added to the winners",
                grant_errors == 0,
                f"{granted} granted" + (f", {grant_errors} failed" if grant_errors else ""),
            )

        # 8) Close live-leaderboard channels: delete tables + post closing message.
        live_close = discord.Embed(
            title="CHANNEL CURRENTLY CLOSED",
            description=(
                "The channel will reopen next **Monday at 9 AM EST**.\n"
                "Here you can track live voting results during the week."
            ),
            color=discord.Color.dark_grey(),
        )
        live_names = (CHANNEL_SMALL_LIVE, CHANNEL_MID_LIVE, CHANNEL_BLUE_LIVE)
        live_deleted = 0
        live_closed = 0
        for name in live_names:
            ch = _find_text_channel(guild, name)
            if not ch:
                continue
            try:
                await _purge_channel_messages(ch, guild, limit=500)
                live_deleted += 1
            except Exception:
                pass
            try:
                await ch.send(embed=live_close)
                live_closed += 1
            except Exception:
                pass
        rpt.check(
            "Live leaderboard tables were deleted successfully",
            live_deleted == len(live_names),
            f"{live_deleted}/{len(live_names)} channels",
        )
        rpt.check(
            "Closing messages were posted in the live leaderboard channels",
            live_closed == len(live_names),
            f"{live_closed}/{len(live_names)} channels",
        )

        # 9) Reopen CHOOSE YOUR TICKER channels for next week.
        try:
            reopen = await self._reopen_ticker_channels(guild)
            rpt.check(
                "CHOOSE YOUR TICKER channels were reopened successfully",
                reopen["reopened"] == reopen["total"],
                f"{reopen['reopened']}/{reopen['total']} channels",
            )
            rpt.check(
                "Weekly bot messages in the CHOOSE YOUR TICKER channels were deleted successfully",
                reopen["cleared"] == reopen["total"],
                f"{reopen['cleared']}/{reopen['total']} channels",
            )
        except Exception as exc:
            rpt.fail("CHOOSE YOUR TICKER channels were reopened successfully", repr(exc))

        # 10) Reset the live-chosen-tickers (PICK RESULTS) board.
        try:
            pr_ch = await _get_pick_results_channel(guild)
            if pr_ch:
                found = await _find_pick_results_message(pr_ch)
                if found:
                    msg, emb = found
                    await _clear_pick_results_message(msg, emb)
                rpt.ok("live-chosen-tickers reopened and previous closed message cleared")
            else:
                rpt.fail("live-chosen-tickers reopened and previous closed message cleared", "channel not found")
        except Exception as exc:
            rpt.fail("live-chosen-tickers reopened and previous closed message cleared", repr(exc))

        # 11) PLAYER roles added during the week (best-effort stat).
        player_added = database.count_player_grants_since(week_start_iso)
        rpt.info("PLAYER roles were added during the week", f"{player_added} user(s)")

        database.log_event(
            guild.id,
            "friday_close",
            {
                "week_key": week_key,
                "winner_count": len(winners),
                "winners": winners,
                "winning_tickers": report.get("winning_tickers"),
                "winner_roles_granted": granted,
                "player_grants_during_week": player_added,
                "report": rpt.summary_dict(),
            },
        )
        await self._announce_report(guild, rpt)
        return rpt

    async def _friday_close_all_guilds(self) -> None:
        for guild in list(self.bot.guilds):
            try:
                await self._friday_close_one_guild(guild)
            except Exception as e:
                await self._announce_mod(guild, "Friday Close — Error", repr(e), discord.Color.red())

    async def _bootstrap_if_inside_window(self):
        """
        If bot starts within [Mon 09:00 ET, Tue 09:00 ET) run the Monday-open flow
        only if it has NOT already happened for this week. After a restart the
        in-memory early window is gone, so we consult the DB cycle first and
        re-arm it instead of re-running Monday-open (which would wipe the active
        voting channels). This makes restarts safe and idempotent.
        """
        now_utc = _now_utc()
        this_mon_9_utc = _monday_9am_et_for_week(now_utc)
        tue_9_utc = this_mon_9_utc + timedelta(days=1)
        if not (this_mon_9_utc <= now_utc < tue_9_utc):
            return

        week_key = database.week_key_for(now_utc)
        already_open = False
        for guild in list(self.bot.guilds):
            try:
                cycle = await asyncio.to_thread(database.ensure_cycle, guild.id, week_key)
            except Exception:
                continue
            if bool(cycle.get("voting_open")) and cycle.get("monday_open_at"):
                already_open = True
                # Re-arm the in-memory early window from persisted state.
                start_raw = cycle.get("monday_open_at")
                try:
                    start_dt = datetime.fromisoformat(str(start_raw))
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=UTC)
                    if now_utc < start_dt + timedelta(hours=24):
                        restore_early_window(start_dt)
                except Exception:
                    pass

        if already_open or is_early_window_active(now_utc):
            return
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
            rpt = await self._friday_close_one_guild(ctx.guild)
            if rpt.any_failed:
                await ctx.send(
                    f"Friday-close completed with **{len(rpt.failures)}** failed step(s) — see #mod report."
                )
            else:
                await ctx.send("Friday-close flow completed — all steps verified. See #mod report.")
        except Exception as e:
            await ctx.send(f"Error: {e!r}")


# -------- Extension hook (required by discord.py to load this cog) --------
async def setup(bot: commands.Bot):
    await bot.add_cog(SchedulerCog(bot))
