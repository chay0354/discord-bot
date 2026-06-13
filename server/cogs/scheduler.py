# cogs/scheduler.py
# Purpose: Weekly time-based automation. On Monday 09:00 ET:
# - Arm Early Window (24h)
# - Push tickers from #pick-results to WEEKLY channels as "VOTING OPEN" + live countdown + voting buttons
# - Keep #pick-results showing this week's ballot during voting (cleared on Friday close)
# - Close ticker submission channels visually (post "Submissions Closed (x/20)" and remove bot messages)
# - Log a concise report to #mod
# Includes robust ET/DST fallback (works even without tzdata).

from __future__ import annotations

import asyncio
import unicodedata
from datetime import datetime, date, time as dtime, timedelta, timezone
from typing import Optional, List, Tuple

import discord
from discord.ext import commands, tasks

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
    ROLE_ADMIN,
    ROLE_NPC,
    ROLE_PLAYER,
    ROLE_WINNER,
    TICKER_LIMIT_PER_CATEGORY,
)
# --- Pull the primitives we already have in other cogs ---
# Early-window state + voting UI + leaderboards + helpers
from cogs.weekly_picks import (
    arm_early_window,
    clear_vote_runtime_state,
    disarm_early_window,
    hydrate_vote_state,
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
    _message_state_key,
    _persist_message_state,
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
    sync_pick_results_from_db,
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


def _friday_4pm_et_for_week(dt_utc: datetime) -> datetime:
    """Return this ISO week's Friday 16:00 ET as a UTC-aware datetime."""
    now_et = _to_et(dt_utc)
    monday_date = (now_et - timedelta(days=now_et.weekday())).date()
    friday_date = monday_date + timedelta(days=4)
    return _from_et_local_to_utc(datetime.combine(friday_date, dtime(16, 0)))


# --- Local helpers ---

def _find_text_channel(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    for ch in guild.text_channels:
        if ch.name.lower() == name.lower():
            return ch
    return None


def _normalize_channel_name(name: str) -> str:
    """Fold fancy Unicode (math-alphanumerics, full-width digits, emoji) to plain
    ascii letters/digits so channel matching is resilient to styled names."""
    folded = unicodedata.normalize("NFKC", name or "").lower()
    return "".join(ch for ch in folded if ch.isalnum())


def _find_winners_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Resolve the 1st-place WINNERS channel even when it uses styled Unicode.

    Tries the configured name first, then a normalized keyword match so a channel
    like '🏆１st-𝐑𝐀𝐍𝐊𝐄𝐃🏆' still resolves to keywords 1st / rank / winner.
    """
    exact = _find_text_channel(guild, CHANNEL_WINNERS)
    if exact:
        return exact
    target_norm = _normalize_channel_name(CHANNEL_WINNERS)
    keywords = ("1stranked", "1st", "ranked", "rank", "winner", "winners")
    for ch in guild.text_channels:
        norm = _normalize_channel_name(ch.name)
        if not norm:
            continue
        if norm == target_norm or any(kw in norm for kw in keywords):
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
            await asyncio.wait_for(guild.chunk(), timeout=30)
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
        self._winner_sync_loop.start()

    @commands.Cog.listener()
    async def on_ready(self):
        """Remove expired WINNER roles and back-fill winner history after restarts."""
        for guild in list(self.bot.guilds):
            try:
                await self._expire_winners(guild)
            except Exception as exc:
                print(f"[scheduler] winner expiry on_ready failed for {guild.id}: {exc!r}")
            try:
                await self._refresh_last_game_winners_from_db(guild)
            except Exception as exc:
                print(f"[scheduler] winner history sync on_ready failed for {guild.id}: {exc!r}")

    def cog_unload(self):
        self._winner_sync_loop.cancel()
        if self._task and not self._task.done():
            self._task.cancel()

    @tasks.loop(hours=1)
    async def _winner_sync_loop(self) -> None:
        for guild in list(self.bot.guilds):
            try:
                await self._expire_winners(guild)
            except Exception as exc:
                print(f"[scheduler] hourly winner sync failed for {guild.id}: {exc!r}", flush=True)

    @_winner_sync_loop.before_loop
    async def _before_winner_sync_loop(self) -> None:
        await self.bot.wait_until_ready()

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

    async def _monday_open_one_guild(
        self,
        guild: discord.Guild,
        *,
        manual: bool = False,
    ) -> Tuple[int, List[int]]:
        """
        Opens the voting stage for a single guild.

        Scheduled automation anchors the 24h early window to Monday 09:00 ET.
        Manual admin starts anchor the window to the moment the button is pressed.
        Returns (updated_weekly_count, per_category_counts).
        """
        now_utc = _now_utc()
        week_key = database.week_key_for(now_utc)
        title = (
            f"Vote Start (Manual) — Week {week_key} ({_format_et(now_utc)})"
            if manual
            else f"Monday Open — Week {week_key} ({_format_et(now_utc)})"
        )
        rpt = StepReport(title)

        # Remove any expired WINNER grants before opening a new voting week.
        try:
            expired = await self._expire_winners(guild)
            rpt.ok("Expired WINNER roles removed", f"{expired} member(s)")
        except Exception as exc:
            rpt.fail("Expired WINNER roles removed", repr(exc))

        # 1) Arm Early Window — 24h from manual press, or Monday 09:00 ET → Tuesday 09:00 ET.
        if manual:
            start_utc = now_utc
            end_utc = now_utc + timedelta(hours=24)
        else:
            start_utc = _monday_9am_et_for_week(now_utc)
            start_et = _to_et(start_utc)
            end_utc = _from_et_local_to_utc(
                datetime.combine(start_et.date() + timedelta(days=1), dtime(9, 0))
            )
        arm_early_window(start_utc)
        try:
            database.set_cycle_phase(
                guild.id,
                week_key,
                status="voting",
                ticker_selection_open=False,
                voting_open=True,
                early_window_open=True,
                monday_open_at=start_utc.isoformat(),
                early_window_end_at=end_utc.isoformat(),
            )
            window_detail = (
                f"24h window from now — ends {_format_et(end_utc)}"
                if manual
                else f"early window ends {_format_et(end_utc)}"
            )
            rpt.ok(
                "Voting window opened (24h early window armed)",
                window_detail if end_utc else "",
            )
        except Exception as exc:
            rpt.fail("Voting window opened (24h early window armed)", repr(exc))

        hydrate_vote_state(guild.id, week_key)

        # 2) Read ballot from Supabase (promote open pre-vote picks into this voting week).
        stored = database.ballot_tickers_for_voting_week(guild.id, week_key)
        lists: List[List[str]] = [stored["small"], stored["mid"], stored["blue"]]
        pr_msg = None
        pr_emb = None
        if not any(lists):
            try:
                pr_ch = await _get_pick_results_channel(guild)
                if pr_ch:
                    found = await _find_pick_results_message(pr_ch)
                    if found:
                        pr_msg, pr_emb = found
                        lists = _extract_lists_from_pick_results(pr_emb)
                        if any(lists):
                            database.seed_ticker_picks_from_lists(
                                guild.id,
                                week_key,
                                {
                                    "small": lists[0],
                                    "mid": lists[1],
                                    "blue": lists[2],
                                },
                            )
                            stored = database.list_tickers(guild.id, week_key)
                            lists = [stored["small"], stored["mid"], stored["blue"]]
            except Exception:
                pass
        try:
            closed_pre_vote = database.close_open_ticker_selection_cycles(guild.id)
            if closed_pre_vote:
                rpt.info(
                    "Parallel pre-vote cycles closed",
                    ", ".join(f"`{wk}`" for wk in closed_pre_vote),
                )
        except Exception as exc:
            rpt.fail("Parallel pre-vote cycles closed", repr(exc))
        total_moved = sum(len(lists[i]) for i in range(3))

        def _fmt_tickers(symbols: List[str]) -> str:
            if not symbols:
                return "none"
            shown = ", ".join(f"${s}" for s in symbols[:15])
            if len(symbols) > 15:
                shown += f", +{len(symbols) - 15} more"
            return shown

        rpt.info(
            "Tickers carried over from weekend selection to WEEKLY PICKS",
            f"{total_moved} ticker(s)\n"
            + "\n".join(
                f"• {_category_title(i)} ({len(lists[i])}): {_fmt_tickers(lists[i])}"
                for i in range(3)
            ),
        )

        # 3) Push to WEEKLY channels (clear old messages, post VOTING OPEN + buttons).
        updated = 0
        cleared = 0
        per_cat_counts: List[int] = [0, 0, 0]
        weekly_found = 0
        leaderboards_ok = 0
        opened_mentions: List[str] = []
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

            banner = _build_voting_open_embed(cat, end_utc, guild=guild)
            if not tickers:
                original = banner.description or ""
                parts = original.split("\n\n", 1)
                tail = parts[1] if len(parts) > 1 else ""
                banner.description = f"**NO TICKERS SELECTED THIS WEEK**\n\n{tail}"

            view = await build_weekly_voting_view(cat, tickers) if tickers else None
            if view is not None:
                # Register persistent handlers so vote buttons keep working after restart.
                self.bot.add_view(view)
            try:
                sent = await ch.send(embed=banner, view=view)
                await asyncio.to_thread(
                    _persist_message_state,
                    guild.id,
                    _message_state_key("voting_open", cat),
                    channel_id=ch.id,
                    message_id=sent.id,
                    payload={"week_key": week_key},
                )
                updated += 1
                opened_mentions.append(ch.mention)
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
            (", ".join(opened_mentions) if opened_mentions else f"{updated}/3 channels")
            + ("" if weekly_found == 3 else f" ({weekly_found} found)"),
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

        # 4) Publish this week's ballot on the PICK RESULTS board (weekend pre-vote selections).
        try:
            synced = await sync_pick_results_from_db(guild)
            if synced:
                rpt.ok("live-chosen-tickers board shows this week's ballot", "synced from ticker_picks")
            else:
                rpt.info("live-chosen-tickers board shows this week's ballot", "no board found")
        except Exception as exc:
            rpt.fail("live-chosen-tickers board shows this week's ballot", repr(exc))

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
                "tickers_by_category": {
                    "small": lists[0],
                    "mid": lists[1],
                    "blue": lists[2],
                },
                "channels_opened": opened_mentions,
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
        disarm_early_window()
        await self._announce_mod(
            guild,
            "Early Winner Window Closed",
            f"Early-vote eligibility ended at {_format_et(_now_utc())}. New votes still count for the weekly vote, but not winner eligibility.",
            discord.Color.orange(),
        )

    async def _tuesday_early_close_all_guilds(self) -> None:
        for guild in list(self.bot.guilds):
            await self._tuesday_early_close_one_guild(guild)

    async def _reopen_ticker_channels(
        self,
        guild: discord.Guild,
        *,
        selection_week_key: str | None = None,
    ) -> dict[str, int]:
        """Reopen the CHOOSE YOUR TICKER channels. Returns verifiable counts."""
        next_week_key = selection_week_key or database.ticker_selection_week_key_for(_now_utc())
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

    async def _restart_pre_voting_one_guild(
        self,
        guild: discord.Guild,
        actor_id: int | None = None,
        *,
        manual: bool = False,
    ) -> str:
        """Restart pre-vote ticker selection. Returns the active selection ``week_key``."""
        now_utc = _now_utc()
        if manual:
            week_key = database.week_key_for(now_utc)
        else:
            week_key = database.ticker_selection_week_key_for(now_utc)
            cycle = database.ensure_cycle(guild.id, week_key)
            if str(cycle.get("status") or "") == "closed":
                week_key = database.next_week_key_for(now_utc)
        database.ensure_cycle(guild.id, week_key)
        database.reset_week_game_data(guild.id, week_key)
        reset_picker_runtime_state()
        clear_vote_runtime_state()
        disarm_early_window()
        database.set_cycle_phase(
            guild.id,
            week_key,
            status="ticker_selection",
            ticker_selection_open=True,
            voting_open=False,
            early_window_open=False,
            clear_voting_schedule=True,
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

        await self._reopen_ticker_channels(guild, selection_week_key=week_key)
        database.log_event(
            guild.id,
            "manual_restart_pre_voting",
            {"week_key": week_key, "actor_id": actor_id, "manual": manual},
        )
        await self._announce_mod(
            guild,
            "Pre-Voting Restarted" if not manual else "Pre-Vote Started (Manual)",
            (
                f"Pre-vote opened for `{week_key}` from {_format_et(now_utc)}."
                if manual
                else f"Stopped any active game state and restarted Pre-Voting for `{week_key}`."
            ),
            discord.Color.blurple(),
        )
        return week_key

    async def _restore_npc_after_winner_removal(
        self,
        member: discord.Member,
        *,
        reason: str,
    ) -> None:
        npc_role = discord.utils.get(member.guild.roles, name=ROLE_NPC)
        player_role = discord.utils.get(member.guild.roles, name=ROLE_PLAYER)
        if (
            npc_role
            and npc_role not in member.roles
            and (not player_role or player_role not in member.roles)
        ):
            try:
                await member.add_roles(npc_role, reason=f"{reason} — restored NPC")
            except Exception:
                pass

    async def _sync_winner_roles(
        self,
        guild: discord.Guild,
        *,
        reason: str,
        announce: bool = True,
        dm_on_remove: bool = False,
        log_reason: str = "no_active_grant",
    ) -> int:
        """Remove WINNER from members who no longer have an active DB grant."""
        role = discord.utils.get(guild.roles, name=ROLE_WINNER)
        if not role:
            return 0
        active_ids = database.active_winner_user_ids(guild.id)
        player_mention = _player_channel_mention(guild)
        removed = 0
        for member in guild.members:
            if role not in member.roles or member.id in active_ids:
                continue
            try:
                await member.remove_roles(role, reason=reason)
                removed += 1
                database.log_event(
                    guild.id,
                    "winner_role_removed",
                    {
                        "discord_id": member.id,
                        "reason": log_reason,
                    },
                )
                await self._restore_npc_after_winner_removal(member, reason=reason)
                if dm_on_remove:
                    try:
                        await member.send(_winner_role_removed_dm(player_mention))
                    except Exception:
                        pass
            except Exception as exc:
                print(
                    f"[scheduler] failed to remove WINNER from {member.id} in {guild.id}: {exc!r}",
                    flush=True,
                )
        if removed and announce:
            await self._announce_mod(
                guild,
                "WINNER Roles Cleared",
                f"Removed **{ROLE_WINNER}** from **{removed}** member(s) ({reason}).",
                discord.Color.orange(),
            )
        return removed

    async def _revoke_winner_roles_for_users(
        self,
        guild: discord.Guild,
        user_ids: list[int],
        *,
        reason: str,
    ) -> int:
        """Remove WINNER and restore NPC for specific users (legacy helper)."""
        winner_role = discord.utils.get(guild.roles, name=ROLE_WINNER)
        if not winner_role or not user_ids:
            return 0
        removed = 0
        for user_id in user_ids:
            member = guild.get_member(user_id)
            if not member or winner_role not in member.roles:
                continue
            try:
                await member.remove_roles(winner_role, reason=reason)
                removed += 1
                await self._restore_npc_after_winner_removal(member, reason=reason)
            except Exception as exc:
                print(
                    f"[scheduler] failed to revoke WINNER for {user_id} in {guild.id}: {exc!r}",
                    flush=True,
                )
        return removed

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
                    database.log_event(
                        guild.id,
                        "winner_role_removed",
                        {
                            "discord_id": user_id,
                            "week_key": row.get("week_key"),
                            "reason": "expired",
                            "expires_at": row.get("expires_at"),
                        },
                    )
                    await self._restore_npc_after_winner_removal(
                        member, reason="WINNER role expired"
                    )
                    try:
                        await member.send(_winner_role_removed_dm(player_mention))
                    except Exception:
                        pass
                except Exception as exc:
                    print(
                        f"[scheduler] failed to expire WINNER for {user_id} in {guild.id}: {exc!r}",
                        flush=True,
                    )
            database.mark_winner_removed(int(row["id"]))
        orphan_removed = await self._sync_winner_roles(
            guild,
            reason="WINNER role expired (no active grant)",
            announce=False,
            dm_on_remove=False,
            log_reason="expired_or_revoked",
        )
        total = removed + orphan_removed
        if total:
            await self._announce_mod(
                guild,
                "WINNER Roles Expired",
                f"Removed **{ROLE_WINNER}** from **{total}** member(s) whose grant ended or was revoked.",
                discord.Color.orange(),
            )
        return total

    def _winners_week_state_key(self, week_key: str) -> str:
        return f"winners_week:{week_key}"

    def _build_winner_week_embed(
        self,
        *,
        week_key: str,
        winner_ids: list[int],
        valid_until_utc: datetime,
    ) -> discord.Embed:
        if winner_ids:
            mentions = "\n".join(f"<@{user_id}>" for user_id in winner_ids)
            return discord.Embed(
                title=f"Winners — Week {week_key}",
                description=(
                    f"Winner(s):\n{mentions}\n\n"
                    f"Role: **{ROLE_WINNER}** (one week)\n"
                    f"Valid until: **{_format_et(valid_until_utc)}**\n\n"
                    "**WINNER perks:** 5 votes per category, ticker-pick channels, live leaderboards "
                    "(same access as PLAYER for that week)."
                ),
                color=discord.Color.gold(),
            )
        return discord.Embed(
            title=f"Winners — Week {week_key}",
            description="No winner met all eligibility conditions.",
            color=discord.Color.dark_grey(),
        )

    async def _publish_last_game_winners(
        self,
        guild: discord.Guild,
        *,
        week_key: str,
        winner_ids: list[int],
        valid_until_utc: datetime,
    ) -> None:
        """Append (or update) one week's winner announcement — history is never cleared."""
        winner_channel = _find_winners_channel(guild)
        if not winner_channel:
            print(
                f"[scheduler] winners channel not found for guild {guild.id} "
                f"(looked for '{CHANNEL_WINNERS}' / 1st-ranked)",
                flush=True,
            )
            await self._announce_mod(
                guild,
                "Winners Channel Missing",
                (
                    f"Could not find the 1st-place winners channel (expected `{CHANNEL_WINNERS}`). "
                    "Winner announcement was not posted. Set `WINNERS_CHANNEL` or rename the channel."
                ),
                discord.Color.red(),
            )
            return

        embed = self._build_winner_week_embed(
            week_key=week_key,
            winner_ids=winner_ids,
            valid_until_utc=valid_until_utc,
        )
        state_key = self._winners_week_state_key(week_key)
        row = database.get_message_state(guild.id, state_key)
        cached_id = int(row["message_id"]) if row and row.get("message_id") else None
        if cached_id:
            try:
                msg = await winner_channel.fetch_message(cached_id)
                await msg.edit(embed=embed)
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        sent = await winner_channel.send(embed=embed)
        try:
            database.save_message_state(
                guild.id,
                state_key,
                channel_id=winner_channel.id,
                message_id=sent.id,
                payload={"week_key": week_key, "kind": "winners_history"},
            )
        except Exception as exc:
            print(f"[scheduler] save_message_state({state_key}) failed: {exc!r}", flush=True)

    def _expires_for_completed_game(self, game: dict) -> datetime:
        closed_raw = game.get("closed_at")
        try:
            closed_at = datetime.fromisoformat(str(closed_raw))
        except Exception:
            closed_at = _now_utc()
        if closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=UTC)
        return closed_at + timedelta(days=7)

    async def _refresh_last_game_winners_from_db(self, guild: discord.Guild) -> None:
        """Back-fill any missing winner-history posts from completed_games (oldest first)."""
        games = database.list_completed_games(guild.id, limit=50)
        if not games:
            return
        games_sorted = sorted(games, key=lambda row: str(row.get("closed_at") or ""))
        for game in games_sorted:
            week_key = str(game.get("week_key") or "")
            if not week_key:
                continue
            raw_ids = game.get("winner_ids") or []
            winner_ids = [int(uid) for uid in raw_ids] if isinstance(raw_ids, list) else []
            await self._publish_last_game_winners(
                guild,
                week_key=week_key,
                winner_ids=winner_ids,
                valid_until_utc=self._expires_for_completed_game(game),
            )

    async def _resolve_present_member(
        self, guild: discord.Guild, user_id: int
    ) -> Optional[discord.Member]:
        """Authoritatively resolve a member who is STILL in the guild.

        Returns the member if present, or None if they left/were banned. Uses the
        cache first, then a direct API fetch so a cache miss never wrongly drops a
        valid winner, and ``NotFound`` confirms the user is gone (so they are not a
        winner and receive no DM).
        """
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.NotFound:
            return None  # left or banned — definitively not in the server
        except (discord.HTTPException, discord.Forbidden) as exc:
            print(f"[scheduler] fetch_member({user_id}) failed: {exc!r}", flush=True)
            return None

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

        clear_vote_runtime_state()

        # 2) Purge WEEKLY PICKS channels (removes buttons) + post VOTING CLOSED.
        leaderboard_ch = _find_text_channel(guild, CHANNEL_FINAL_LEADERBOARD)
        leaderboard_mention = (
            leaderboard_ch.mention
            if leaderboard_ch
            else f"#{CHANNEL_FINAL_LEADERBOARD}"
        )
        closed = discord.Embed(
            title="VOTING CLOSED",
            description=(
                "Voting has ended for this week. "
                f"Final results are posted in {leaderboard_mention}.\n\n"
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
        eligible = list(report.get("eligible_winner_ids") or [])

        # Final guard: the ANNOUNCED winners must be EXACTLY the ones who will
        # actually receive the role now. Exclude anyone who already holds an
        # active WINNER grant or who is no longer in the guild (left/banned), so
        # the announcement can never name someone who doesn't get the role.
        winner_role = discord.utils.get(guild.roles, name=ROLE_WINNER)
        active_ids = database.active_winner_user_ids(guild.id)
        winners: list[int] = []
        drop_reasons: list[str] = []
        # Contract: ONLY a pure NPC can win. A member currently holding PLAYER,
        # ADMIN or WINNER is never awarded — this is the final guard that keeps
        # subscribers/staff out even if an old vote row is stale.
        blocking_roles = {ROLE_PLAYER.upper(), ROLE_ADMIN.upper(), ROLE_WINNER.upper()}
        present_members: dict[int, discord.Member] = {}
        for user_id in eligible:
            if user_id in active_ids:
                drop_reasons.append(f"{user_id}: active WINNER grant")
                continue
            member = await self._resolve_present_member(guild, user_id)
            if member is None:
                drop_reasons.append(f"{user_id}: left/banned")
                continue  # left or banned — never announce or award
            held = {r.name.upper() for r in member.roles} & blocking_roles
            if held:
                drop_reasons.append(f"{user_id}: holds {'/'.join(sorted(held))} (not a pure NPC)")
                continue
            present_members[user_id] = member
            winners.append(user_id)
        if drop_reasons:
            print(f"[scheduler] winners excluded at award: {drop_reasons}", flush=True)

        try:
            database.save_completed_game(
                guild.id, week_key, winner_ids=winners, closed_at=now_utc.isoformat()
            )
            detail = _format_winner_report(report)
            if drop_reasons:
                detail = (detail + "\n\nExcluded at award:\n• " + "\n• ".join(drop_reasons))[:1020]
            rpt.ok("Winners were calculated and saved", detail)
        except Exception as exc:
            rpt.fail("Winners were calculated and saved", repr(exc))

        # 6) Announce winners (only the validated list that will be awarded).
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

        # 7) Grant WINNER roles to the same validated list.
        expires_at = (now_utc + timedelta(days=7)).isoformat()
        granted = 0
        grant_errors = 0
        dm_sent = 0
        dm_failed: list[int] = []
        if winners and winner_role:
            for user_id in winners:
                # Re-confirm presence right before awarding so a member who left
                # between validation and award is never granted or DM'd.
                member = present_members.get(user_id) or await self._resolve_present_member(
                    guild, user_id
                )
                if not member:
                    drop_reasons.append(f"{user_id}: left before award")
                    continue
                try:
                    database.add_winner(
                        guild.id,
                        week_key,
                        user_id,
                        expires_at,
                        reason="npc_early_vote_all_categories",
                        winning_tickers=report.get("winning_tickers") or {},
                    )
                except Exception:
                    grant_errors += 1
                    continue
                database.upsert_user(user_id, str(member.display_name or member.name))
                try:
                    await member.add_roles(winner_role, reason="Weekly stock game winner")
                    granted += 1
                    database.log_event(
                        guild.id,
                        "winner_role_granted",
                        {
                            "discord_id": user_id,
                            "week_key": week_key,
                            "reason": "weekly_winner",
                            "expires_at": expires_at,
                        },
                    )
                    # Move the winner into WINNER for the week: drop NPC so they hold
                    # only the upgraded role. NPC is restored automatically when the
                    # WINNER grant expires (see _expire_winners).
                    npc_role = discord.utils.get(member.roles, name=ROLE_NPC)
                    if npc_role:
                        try:
                            await member.remove_roles(
                                npc_role, reason="Promoted to WINNER for the week"
                            )
                        except Exception as npc_exc:
                            print(
                                f"[scheduler] could not remove NPC from winner {user_id}: {npc_exc!r}",
                                flush=True,
                            )
                except Exception as role_exc:
                    print(f"[scheduler] WINNER role grant to {user_id} failed: {role_exc!r}", flush=True)
                    grant_errors += 1
                    continue
                # Send the winner DM only to a member who is in the server (already
                # confirmed above). A blocked/closed-DM failure is logged, not fatal.
                try:
                    await member.send(_winner_role_dm(now_utc + timedelta(days=7)))
                    dm_sent += 1
                    database.log_event(
                        guild.id,
                        "winner_dm_sent",
                        {"discord_id": user_id, "week_key": week_key},
                    )
                except (discord.Forbidden, discord.HTTPException) as dm_exc:
                    dm_failed.append(user_id)
                    print(f"[scheduler] winner DM to {user_id} failed: {dm_exc!r}", flush=True)
                    database.log_event(
                        guild.id,
                        "winner_dm_failed",
                        {"discord_id": user_id, "week_key": week_key, "error": repr(dm_exc)},
                    )
        if not winner_role:
            rpt.fail("WINNER roles were added to the winners", f"role '{ROLE_WINNER}' not found")
        else:
            rpt.check(
                "WINNER roles were added to the winners",
                grant_errors == 0,
                f"{granted} granted" + (f", {grant_errors} failed" if grant_errors else ""),
            )
            if winners:
                dm_detail = f"{dm_sent}/{len(winners)} delivered"
                if dm_failed:
                    dm_detail += " — failed (DMs closed): " + ", ".join(
                        f"<@{uid}>" for uid in dm_failed
                    )
                rpt.check(
                    "Winner DMs were delivered",
                    not dm_failed,
                    dm_detail,
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

    async def _reconcile_missed_events_one_guild(self, guild: discord.Guild) -> None:
        """Run any scheduled phase transition that was missed while the bot was offline.

        Order matters: Friday close ends the week; Monday open starts voting; Tuesday
        close ends the early window. Each step re-reads ``game_cycles`` when needed.
        """
        now_utc = _now_utc()
        week_key = database.week_key_for(now_utc)
        try:
            cycle = await asyncio.to_thread(database.ensure_cycle, guild.id, week_key)
        except Exception as exc:
            print(f"[scheduler] reconcile: cannot load cycle for {guild.id}: {exc!r}", flush=True)
            return

        mon_9 = _monday_9am_et_for_week(now_utc)
        tue_9 = mon_9 + timedelta(days=1)
        fri_4 = _friday_4pm_et_for_week(now_utc)

        voting_open = bool(cycle.get("voting_open"))
        early_open = bool(cycle.get("early_window_open"))
        status = str(cycle.get("status") or "")

        # 1) Missed Friday close — voting still open after market close.
        if now_utc >= fri_4 and voting_open and status != "closed":
            print(
                f"[scheduler] reconcile: missed Friday close for guild {guild.id} week {week_key}",
                flush=True,
            )
            await self._announce_mod(
                guild,
                "Scheduler Catch-Up — Friday Close",
                (
                    f"Bot was offline when voting should have closed for **{week_key}**. "
                    f"Running Friday close now ({_format_et(now_utc)})."
                ),
                discord.Color.orange(),
            )
            database.log_event(
                guild.id,
                "missed_friday_close_catchup",
                {"week_key": week_key, "at": now_utc.isoformat()},
            )
            await self._friday_close_one_guild(guild)
            return

        # 2) Missed Monday open — still the same trading week (before Friday close).
        if now_utc >= mon_9 and now_utc < fri_4 and not voting_open and status != "closed":
            print(
                f"[scheduler] reconcile: missed Monday open for guild {guild.id} week {week_key}",
                flush=True,
            )
            await self._announce_mod(
                guild,
                "Scheduler Catch-Up — Monday Open",
                (
                    f"Bot was offline when voting should have opened for **{week_key}**. "
                    f"Running Monday open now ({_format_et(now_utc)})."
                ),
                discord.Color.orange(),
            )
            database.log_event(
                guild.id,
                "missed_monday_open_catchup",
                {"week_key": week_key, "at": now_utc.isoformat()},
            )
            await self._monday_open_one_guild(guild)
            try:
                cycle = await asyncio.to_thread(database.ensure_cycle, guild.id, week_key)
            except Exception:
                return
            voting_open = bool(cycle.get("voting_open"))
            early_open = bool(cycle.get("early_window_open"))

        # 3) Re-arm in-memory early window when DB says it is still open.
        if voting_open and early_open and cycle.get("monday_open_at"):
            start_raw = cycle.get("monday_open_at")
            try:
                start_dt = datetime.fromisoformat(str(start_raw))
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=UTC)
                if now_utc < start_dt + timedelta(hours=24):
                    restore_early_window(start_dt)
            except Exception:
                pass

        # 4) Missed Tuesday early-window close.
        if now_utc >= tue_9 and now_utc < fri_4 and voting_open and early_open:
            print(
                f"[scheduler] reconcile: missed early-window close for guild {guild.id} week {week_key}",
                flush=True,
            )
            await self._announce_mod(
                guild,
                "Scheduler Catch-Up — Early Window Close",
                (
                    f"Bot was offline when the 24h early-vote window should have ended "
                    f"for **{week_key}**. Closing it now ({_format_et(now_utc)})."
                ),
                discord.Color.orange(),
            )
            database.log_event(
                guild.id,
                "missed_early_close_catchup",
                {"week_key": week_key, "at": now_utc.isoformat()},
            )
            await self._tuesday_early_close_one_guild(guild)

    async def _reconcile_missed_events_all_guilds(self) -> None:
        for guild in list(self.bot.guilds):
            try:
                await self._reconcile_missed_events_one_guild(guild)
            except Exception as exc:
                print(f"[scheduler] reconcile failed for {guild.id}: {exc!r}", flush=True)
                await self._announce_mod(
                    guild,
                    "Scheduler Catch-Up — Error",
                    f"Missed-event reconciliation failed: {exc!r}",
                    discord.Color.red(),
                )

    async def _runner(self):
        await self.bot.wait_until_ready()

        # Catch up any open/close the bot missed while offline.
        try:
            await self._reconcile_missed_events_all_guilds()
        except Exception as e:
            print(f"[scheduler] reconcile check error: {e!r}")

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
          - Publish this week's ballot on #pick-results
          - Close ticker submission channels visually
          - Log to #mod
        """
        if not ctx.guild:
            await ctx.send("This command must be run in a server.")
            return
        await ctx.send("Running Monday-open flow now…")
        try:
            updated, counts = await self._monday_open_one_guild(ctx.guild, manual=True)
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
