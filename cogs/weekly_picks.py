# cogs/weekly_picks.py
# Purpose: WEEKLY PICKS (voting, live leaderboards). Submission flow remains in submission_ui.py.
# Adds "Early Window" tracking + live countdown updater that edits ONLY the embed text (not the buttons).

from __future__ import annotations

from typing import Dict, List, Set, Tuple, Optional
import asyncio
from datetime import datetime, timedelta, timezone, date, time as dtime

import discord
from discord.ext import commands

import database
from config import (
    CATEGORIES,
    CATEGORY_TITLES,
    CHANNEL_BLUE_LIVE,
    CHANNEL_BLUE_TICKER,
    CHANNEL_BLUE_VOTE,
    CHANNEL_MID_LIVE,
    CHANNEL_MID_TICKER,
    CHANNEL_MID_VOTE,
    CHANNEL_PICK_RESULTS,
    CHANNEL_SMALL_LIVE,
    CHANNEL_SMALL_TICKER,
    CHANNEL_SMALL_VOTE,
    NPC_VOTES_PER_CATEGORY,
    PLAYER_VOTES_PER_CATEGORY,
    ROLE_ADMIN,
    ROLE_NPC,
    ROLE_PLAYER,
    ROLE_WINNER,
    TICKER_LIMIT_PER_CATEGORY,
)
from services.finnhub_client import FinnhubQuote, format_quote, quote_and_names_for_symbols
# import OpenPickerView to re-open ticker channels without touching submission_ui.py
from cogs.submission_ui import OpenPickerView

UTC = timezone.utc

REQUIRED_WEEKLY_CHANNELS = [
    CHANNEL_SMALL_VOTE,
    CHANNEL_MID_VOTE,
    CHANNEL_BLUE_VOTE,
]

REQUIRED_LIVE_CHANNELS = [
    CHANNEL_SMALL_LIVE,
    CHANNEL_MID_LIVE,
    CHANNEL_BLUE_LIVE,
]

TICKER_CHANNELS = [
    CHANNEL_SMALL_TICKER,
    CHANNEL_MID_TICKER,
    CHANNEL_BLUE_TICKER,
]


def _find_text_channel(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    for ch in guild.text_channels:
        if ch.name.lower() == name.lower():
            return ch
    return None


def _category_idx_to_weekly_name(idx: int) -> str:
    return [CHANNEL_SMALL_VOTE, CHANNEL_MID_VOTE, CHANNEL_BLUE_VOTE][idx]


def _category_idx_to_live_name(idx: int) -> str:
    return [CHANNEL_SMALL_LIVE, CHANNEL_MID_LIVE, CHANNEL_BLUE_LIVE][idx]


def _category_title(idx: int) -> str:
    return [CATEGORY_TITLES["small"], CATEGORY_TITLES["mid"], CATEGORY_TITLES["blue"]][idx]


def _parse_tickers_field(val: Optional[str]) -> List[str]:
    """
    Expect a string like: "$AAA • $BBB • $CCC" (from the embed field "Tickers").
    Returns ["AAA", "BBB", ...]
    """
    if not val or val.strip() == "—":
        return []
    out: List[str] = []
    for part in val.split("•"):
        sym = part.strip().lstrip("$").upper()
        if sym:
            out.append(sym)
    return out


def _extract_tickers_from_components(msg: discord.Message) -> List[str]:
    """Fallback: read button labels from existing components."""
    tickers: List[str] = []
    try:
        for row in msg.components:
            if isinstance(row, discord.ActionRow):
                children = row.children
            else:
                children = getattr(row, "children", [])
            for c in children:
                if isinstance(c, discord.ui.Button) or getattr(c, "type", None) == 2:
                    label = getattr(c, "label", None)
                    if label:
                        t = str(label).strip().upper().lstrip("$")
                        if t and t not in tickers:
                            tickers.append(t)
    except Exception:
        pass
    return tickers


# ===================== In-memory voting state =====================

# per-category user votes: {category_idx: {user_id: set(tickers)}}
_user_votes: Dict[int, Dict[int, Set[str]]] = {0: {}, 1: {}, 2: {}}

# per-category vote counts: {category_idx: {ticker: count}}
_vote_counts: Dict[int, Dict[str, int]] = {0: {}, 1: {}, 2: {}}

# live leaderboard message ids per category (best-effort, not persisted)
_live_msg_ids: Dict[int, int] = {}
_leaderboard_update_tasks: Dict[Tuple[int, int], asyncio.Task] = {}

# track the message ids of "VOTING OPEN" per guild per category for countdown updates
# structure: {guild_id: {cat_idx: message_id}}
_voting_open_msg_ids: Dict[int, Dict[int, int]] = {}


# ===================== Early window (first 24h) tracking =====================

# When Monday-open occurs (manually now; automatically later via scheduler), we arm this.
_early_window_start_utc: Optional[datetime] = None

# For each category, which NPC user voted which tickers during the first 24h:
# {cat_idx: {user_id: set(tickers)}}
_early_votes: Dict[int, Dict[int, Set[str]]] = {0: {}, 1: {}, 2: {}}


def arm_early_window(start_utc: datetime) -> None:
    """
    Called at Monday 09:00 ET to start the 24h early window.
    Resets early votes tracking for a fresh week.
    """
    global _early_window_start_utc
    _early_window_start_utc = start_utc.astimezone(UTC)
    for cat in range(3):
        _early_votes[cat].clear()


def is_early_window_active(now_utc: Optional[datetime] = None) -> bool:
    """
    Returns True if we are within [start, start+24h).
    """
    if _early_window_start_utc is None:
        return False
    now = (now_utc or datetime.now(tz=UTC)).astimezone(UTC)
    return _early_window_start_utc <= now < (_early_window_start_utc + timedelta(hours=24))


def early_window_end_utc() -> Optional[datetime]:
    if _early_window_start_utc is None:
        return None
    return _early_window_start_utc + timedelta(hours=24)


def _record_early_vote_if_applicable(cat: int, member: discord.Member, ticker: str) -> None:
    """
    Only NPC votes during the first 24h are tracked here.
    """
    if not is_early_window_active():
        return
    role_names = {r.name.upper() for r in member.roles}
    if "NPC" not in role_names:
        return
    bucket = _early_votes[cat].setdefault(member.id, set())
    bucket.add(ticker)


def snapshot_early_votes() -> Dict[int, Dict[int, List[str]]]:
    """
    Returns a read-only snapshot {cat_idx: {user_id: [tickers...]}} for diagnostics / winners logic.
    """
    out: Dict[int, Dict[int, List[str]]] = {0: {}, 1: {}, 2: {}}
    for cat in range(3):
        for uid, s in _early_votes[cat].items():
            out[cat][uid] = sorted(s)
    return out


# ===================== ET display helpers (works with/without tzdata) =====================

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None  # type: ignore

ET_TZ = None
if ZoneInfo is not None:
    try:
        ET_TZ = ZoneInfo("America/New_York")
    except Exception:
        ET_TZ = None  # fallback to manual


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    shift = (weekday - first.weekday()) % 7
    day = 1 + shift + 7 * (n - 1)
    return date(year, month, day)


def _us_dst_bounds_local(year: int) -> tuple[datetime, datetime]:
    # Sunday=6; DST start: 2nd Sunday of March at 02:00; end: 1st Sunday of November at 02:00
    start_day = _nth_weekday_of_month(year, 3, 6, 2)
    end_day = _nth_weekday_of_month(year, 11, 6, 1)
    return datetime(year, 3, start_day.day, 2, 0, 0), datetime(year, 11, end_day.day, 2, 0, 0)


def _us_dst_bounds_utc(year: int) -> tuple[datetime, datetime]:
    s_local, e_local = _us_dst_bounds_local(year)
    s_utc = s_local + timedelta(hours=5)  # EST -> UTC
    e_utc = e_local + timedelta(hours=4)  # EDT -> UTC
    return s_utc.replace(tzinfo=UTC), e_utc.replace(tzinfo=UTC)


def _is_dst_utc(dt_utc: datetime) -> bool:
    s_utc, e_utc = _us_dst_bounds_utc(dt_utc.year)
    return s_utc <= dt_utc < e_utc


def _utc_to_et(dt_utc: datetime) -> datetime:
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    # EDT=UTC-4, EST=UTC-5
    offset_hours = -4 if _is_dst_utc(dt_utc) else -5
    return (dt_utc + timedelta(hours=offset_hours)).replace(tzinfo=None)


def _format_et(dt_utc: datetime) -> str:
    """Return 'YYYY-MM-DD HH:MM ET' for display."""
    if ET_TZ:
        return f"{dt_utc.astimezone(ET_TZ):%Y-%m-%d %H:%M} ET"
    # fallback manual
    return f"{_utc_to_et(dt_utc):%Y-%m-%d %H:%M} ET"


# ===================== Helpers for leaderboards =====================

def _vote_limit_for(member: discord.Member) -> int:
    """PLAYER/WINNER/ADMIN => 5; NPC/default => 1."""
    names = {r.name.upper() for r in member.roles}
    if ROLE_PLAYER.upper() in names or ROLE_WINNER.upper() in names or ROLE_ADMIN.upper() in names:
        return PLAYER_VOTES_PER_CATEGORY
    if ROLE_NPC.upper() in names:
        return NPC_VOTES_PER_CATEGORY
    return NPC_VOTES_PER_CATEGORY


def _role_snapshot(member: discord.Member) -> str:
    names = {r.name.upper() for r in member.roles}
    if ROLE_PLAYER.upper() in names:
        return "PLAYER"
    if ROLE_WINNER.upper() in names:
        return "WINNER"
    return "NPC"


def _ensure_user_slot(cat: int, user_id: int) -> Set[str]:
    return _user_votes[cat].setdefault(user_id, set())


def _inc_count(cat: int, ticker: str, delta: int = 1) -> int:
    d = _vote_counts[cat]
    d[ticker] = d.get(ticker, 0) + delta
    if d[ticker] < 0:
        d[ticker] = 0
    return d[ticker]


def _revert_optimistic_vote(cat: int, user_id: int, ticker: str) -> None:
    sym = ticker.upper()
    user_set = _user_votes[cat].get(user_id)
    if user_set:
        user_set.discard(sym)
    counts = _vote_counts[cat]
    if sym in counts:
        counts[sym] = max(0, counts[sym] - 1)
        if counts[sym] == 0:
            counts.pop(sym, None)


def hydrate_vote_state(guild_id: int, week_key: str | None = None) -> None:
    """Load this week's votes into memory so button replies can be instant."""
    wk = week_key or database.week_key_for()
    rows = database.fetch_week_vote_rows(guild_id, wk)
    cat_keys = ["small", "mid", "blue"]
    for idx in range(3):
        _vote_counts[idx].clear()
        _user_votes[idx].clear()
    for row in rows:
        cat = row.get("category")
        if cat not in cat_keys:
            continue
        idx = cat_keys.index(cat)
        sym = str(row["ticker"]).upper()
        uid = int(row["user_id"])
        _vote_counts[idx][sym] = _vote_counts[idx].get(sym, 0) + 1
        _user_votes[idx].setdefault(uid, set()).add(sym)


def _sorted_leaderboard(cat: int) -> List[Tuple[str, int]]:
    """Return [(ticker, count)] sorted by count desc, then ticker asc."""
    items = list(_vote_counts[cat].items())
    items.sort(key=lambda x: (-x[1], x[0]))
    return items


def _channel_mention_or_text(guild: discord.Guild, candidates: List[str], fallback_text: str) -> str:
    """Try to mention a channel by any of given names; else return plain fallback text."""
    for n in candidates:
        ch = _find_text_channel(guild, n)
        if ch:
            return ch.mention
    return fallback_text


def _truncate(text: str, max_len: int) -> str:
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


_ROSTER_COLORS = (
    discord.Color.from_rgb(32, 102, 92),   # small — deep teal
    discord.Color.from_rgb(41, 78, 128),   # mid — slate blue
    discord.Color.from_rgb(88, 52, 120),    # large cap — plum
)


def _leaderboard_line(
    rank: int,
    ticker: str,
    votes: int,
    quotes: dict[str, FinnhubQuote],
    names: dict[str, str],
    *,
    medal: str | None = None,
) -> str:
    nm = _truncate(names.get(ticker, "") or "", 34) or "—"
    q = format_quote(ticker, quotes.get(ticker))
    head = f"{medal} **{rank}.** **${ticker}**" if medal else f"**{rank}.** **${ticker}**"
    body = f"{nm}\n    {q} · **{votes}** votes"
    return f"{head}\n{body}"


def _leaderboard_embed(
    cat: int,
    pairs: List[Tuple[str, int]] | None = None,
    quotes: dict[str, FinnhubQuote] | None = None,
    names: dict[str, str] | None = None,
) -> discord.Embed:
    pairs = pairs if pairs is not None else _sorted_leaderboard(cat)
    quotes = quotes or {}
    names = names or {}

    title = f"🏆 LIVE LEADERBOARD — {_category_title(cat)} 🏆"
    if not pairs:
        desc = "No votes yet."
    else:
        lines: List[str] = []
        medals = ["🥇", "🥈", "🥉"]
        for i, (t, c) in enumerate(pairs[:3], start=1):
            lines.append(
                _leaderboard_line(i, t, c, quotes, names, medal=medals[i - 1])
            )
            lines.append("**━━━━━━━━━━━━━━━━**")
        for j, (t, c) in enumerate(pairs[3:], start=4):
            lines.append(_leaderboard_line(j, t, c, quotes, names))
            if j < len(pairs):
                lines.append("────────────────")
        desc = "\n".join(lines)

    emb = discord.Embed(
        title=title,
        description=desc,
        color=_ROSTER_COLORS[cat] if cat < len(_ROSTER_COLORS) else discord.Color.dark_teal(),
    )
    emb.set_footer(text="Name & price refresh with votes · Ties: alphabetical")
    return emb


def _schedule_leaderboard_update(guild: discord.Guild, cat: int) -> None:
    """Debounced live leaderboard refresh so votes confirm instantly."""
    key = (guild.id, cat)
    existing = _leaderboard_update_tasks.get(key)
    if existing and not existing.done():
        existing.cancel()

    async def _run() -> None:
        try:
            await asyncio.sleep(1.25)
            await _post_or_update_leaderboard(guild, cat)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    _leaderboard_update_tasks[key] = asyncio.create_task(_run())


async def _post_or_update_leaderboard(guild: discord.Guild, cat: int) -> None:
    """Post or edit the live leaderboard in the corresponding -live channel."""
    ch_name = _category_idx_to_live_name(cat)
    ch = _find_text_channel(guild, ch_name)
    if not ch:
        return
    week_key = database.week_key_for()
    pairs = database.vote_counts(guild.id, week_key, ["small", "mid", "blue"][cat])
    tickers = [ticker for ticker, _ in pairs]
    quotes, names = await asyncio.to_thread(quote_and_names_for_symbols, tickers)
    emb = _leaderboard_embed(cat, pairs, quotes, names)
    # edit existing if we have id
    msg_id = _live_msg_ids.get(cat)
    if msg_id:
        try:
            msg = await ch.fetch_message(msg_id)
            await msg.edit(embed=emb)
            return
        except Exception:
            pass  # fall through to send new

    # try find latest bot message with matching title
    try:
        async for msg in ch.history(limit=20):
            if msg.author == guild.me and msg.embeds:
                e = msg.embeds[0]
                if (e.title or "").startswith("🏆 LIVE LEADERBOARD —"):
                    await msg.edit(embed=emb)
                    _live_msg_ids[cat] = msg.id
                    return
    except Exception:
        pass

    sent = await ch.send(embed=emb)
    _live_msg_ids[cat] = sent.id


# ===================== Pick-Results helpers (local copy) =====================

def _fresh_pick_results_embed() -> discord.Embed:
    emb = discord.Embed(
        title="PICK RESULTS",
        description=(
            f"Small / Mid / Blue weekly lists. Each category closes at {TICKER_LIMIT_PER_CATEGORY} tickers."
        ),
        color=discord.Color.gold()
    )
    emb.add_field(name=f"{CATEGORY_TITLES['small']} (0/{TICKER_LIMIT_PER_CATEGORY})", value="—", inline=False)
    emb.add_field(name=f"{CATEGORY_TITLES['mid']} (0/{TICKER_LIMIT_PER_CATEGORY})", value="—", inline=False)
    emb.add_field(name=f"{CATEGORY_TITLES['blue']} (0/{TICKER_LIMIT_PER_CATEGORY})", value="—", inline=False)
    return emb


async def _get_pick_results_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    for ch in guild.text_channels:
        if ch.name.lower() in {CHANNEL_PICK_RESULTS.lower(), "pick-results"}:
            return ch
    return None


async def _find_pick_results_message(pr_ch: discord.TextChannel) -> Optional[Tuple[discord.Message, discord.Embed]]:
    async for msg in pr_ch.history(limit=50):
        if msg.author == pr_ch.guild.me and msg.embeds:
            emb = msg.embeds[0]
            title = (emb.title or "").lower()
            if "pick results" in title:
                return msg, emb
    return None


async def _reset_pick_results(guild: discord.Guild) -> None:
    pr_ch = await _get_pick_results_channel(guild)
    if not pr_ch:
        return
    found = await _find_pick_results_message(pr_ch)
    if not found:
        # create a fresh scaffold if none exists
        await pr_ch.send(embed=_fresh_pick_results_embed())
        return
    msg, emb = found
    new_emb = discord.Embed(
        title=emb.title, description=emb.description, color=emb.color)
    new_emb.add_field(name=f"{CATEGORY_TITLES['small']} (0/{TICKER_LIMIT_PER_CATEGORY})", value="—", inline=False)
    new_emb.add_field(name=f"{CATEGORY_TITLES['mid']} (0/{TICKER_LIMIT_PER_CATEGORY})", value="—", inline=False)
    new_emb.add_field(name=f"{CATEGORY_TITLES['blue']} (0/{TICKER_LIMIT_PER_CATEGORY})", value="—", inline=False)
    await msg.edit(embed=new_emb)


# ===================== Voting View =====================

def _vote_button_label(symbol: str, quote: FinnhubQuote | None) -> str:
    """Discord button label, e.g. $KULR @ $3.60 (max 80 chars)."""
    sym = symbol.upper().strip().lstrip("$")
    if quote and quote.current_price is not None:
        return f"${sym} @ ${quote.current_price:.2f}"[:80]
    return f"${sym}"[:80]


class WeeklyVotingView(discord.ui.View):
    """Active voting buttons for the tickers selected by subscribers."""

    def __init__(
        self,
        category_idx: int,
        tickers: List[str],
        quotes: dict[str, FinnhubQuote] | None = None,
    ):
        # persistent during runtime; not persisted across restarts
        super().__init__(timeout=None)
        self.category_idx = category_idx
        self.tickers = tickers[:20]  # Discord allows max 25 components; keep 20 game options.
        quotes = quotes or {}

        for t in self.tickers:
            btn = discord.ui.Button(
                label=_vote_button_label(t, quotes.get(t.upper())),
                style=discord.ButtonStyle.primary,
                custom_id=f"vote:{category_idx}:{t}",
            )

            async def _cb(interaction: discord.Interaction, ticker=t):
                try:
                    await interaction.response.defer(ephemeral=True)
                except discord.InteractionResponded:
                    pass
                await self._handle_vote(interaction, ticker, already_deferred=True)

            btn.callback = _cb  # type: ignore
            self.add_item(btn)

    async def _finalize_vote_background(
        self,
        interaction: discord.Interaction,
        *,
        guild: discord.Guild,
        cat: int,
        category_key: str,
        week_key: str,
        ticker: str,
        member: discord.Member,
        limit: int,
        role_at_vote: str,
    ) -> None:
        try:
            ctx = await asyncio.to_thread(
                database.vote_button_context,
                guild.id,
                week_key,
                category_key,
                member.id,
                ticker,
            )
            if not ctx["voting_open"]:
                _revert_optimistic_vote(cat, member.id, ticker)
                await interaction.followup.send(
                    "Voting is closed. Next voting opens Monday at 9:00 AM ET.",
                    ephemeral=True,
                )
                return

            actual_cat = ctx["actual_category"]
            if not actual_cat:
                _revert_optimistic_vote(cat, member.id, ticker)
                await interaction.followup.send(
                    f"${ticker} is not in this week's game lists.",
                    ephemeral=True,
                )
                return

            save_cat = cat
            save_key = category_key
            if actual_cat != category_key:
                save_key = actual_cat
                save_cat = CATEGORIES.index(actual_cat)

            if ctx["prior_vote_category"]:
                _revert_optimistic_vote(cat, member.id, ticker)
                await interaction.followup.send(
                    f"You already voted for ${ticker} this week.",
                    ephemeral=True,
                )
                return

            db_count = int(ctx["vote_count"])
            if db_count >= limit:
                _revert_optimistic_vote(cat, member.id, ticker)
                await interaction.followup.send(
                    "YOU HAVE REACHED THE LIMIT OF YOUR VOTES. NEXT VOTING OPENS MONDAY 9AM.",
                    ephemeral=True,
                )
                return

            ok, reason = await asyncio.to_thread(
                database.record_vote,
                guild.id,
                week_key,
                save_key,
                ticker,
                member.id,
                role_at_vote,
                is_early_window_active() and role_at_vote == "NPC",
            )
            if not ok:
                _revert_optimistic_vote(cat, member.id, ticker)
                if reason == "duplicate":
                    await interaction.followup.send(
                        f"You already voted for ${ticker}.",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        "Your vote could not be saved. Please try again.",
                        ephemeral=True,
                    )
                return

            if save_cat != cat:
                _revert_optimistic_vote(cat, member.id, ticker)
                _user_votes[save_cat].setdefault(member.id, set()).add(ticker)
                _inc_count(save_cat, ticker, +1)

            _record_early_vote_if_applicable(save_cat, member, ticker)
            _schedule_leaderboard_update(guild, save_cat)
        except Exception:
            _revert_optimistic_vote(cat, member.id, ticker)
            try:
                await interaction.followup.send(
                    "Your vote could not be saved. Please try again.",
                    ephemeral=True,
                )
            except Exception:
                pass

    async def _handle_vote(
        self,
        interaction: discord.Interaction,
        ticker: str,
        *,
        already_deferred: bool = False,
    ):
        if not already_deferred:
            try:
                await interaction.response.defer(ephemeral=True)
            except discord.InteractionResponded:
                pass

        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.followup.send("This can only be used in a server.", ephemeral=True)
            return

        guild = interaction.guild
        cat = self.category_idx
        category_key = ["small", "mid", "blue"][cat]
        week_key = database.week_key_for()
        member: discord.Member = interaction.user
        limit = _vote_limit_for(member)
        role_at_vote = _role_snapshot(member)
        ticker = ticker.upper().strip().lstrip("$")

        if ticker not in self.tickers:
            await interaction.followup.send(
                f"${ticker} is not on this channel's ballot.",
                ephemeral=True,
            )
            return

        user_set = _ensure_user_slot(cat, member.id)
        mem_count = len(user_set)

        if ticker in user_set:
            await interaction.followup.send(
                f"You already voted for ${ticker} in this category.",
                ephemeral=True,
            )
            return

        # ---- NPC-special UX: limit==1 ----
        if limit == 1:
            if mem_count >= 1:
                reg_mention = _channel_mention_or_text(
                    guild,
                    ["𝐏𝐋𝐀𝐘𝐄𝐑", "player", "register", "registration", "subscribe"],
                    "#PLAYER"
                )
                await interaction.followup.send(
                    "YOU HAVE REACHED THE LIMIT OF YOUR VOTES. "
                    "NEXT VOTING OPENS MONDAY 9AM. "
                    f"IF YOU WANT TO GET MORE VOTES AND EXTRA PRESS HERE TO SUBSCRIBE: {reg_mention}",
                    ephemeral=True
                )
                return

            reg_mention = _channel_mention_or_text(
                guild,
                ["𝐏𝐋𝐀𝐘𝐄𝐑", "player", "register", "registration", "subscribe"],
                "#PLAYER"
            )
            await interaction.followup.send(
                f"YOU HAVE PICKED ${ticker}\n"
                f"YOU HAVE 1/{limit} PICKS\n"
                f"Join {reg_mention} to get 5 weekly votes and see live results in real time.",
                ephemeral=True,
            )
            user_set.add(ticker)
            _inc_count(cat, ticker, +1)
            asyncio.create_task(
                self._finalize_vote_background(
                    interaction,
                    guild=guild,
                    cat=cat,
                    category_key=category_key,
                    week_key=week_key,
                    ticker=ticker,
                    member=member,
                    limit=limit,
                    role_at_vote=role_at_vote,
                )
            )
            return

        # ---- Default (PLAYER/ADMIN) behavior ----
        if mem_count >= limit:
            await interaction.followup.send(
                "YOU HAVE REACHED THE LIMIT OF YOUR VOTES. NEXT VOTING OPENS MONDAY 9AM.",
                ephemeral=True
            )
            return

        new_count = mem_count + 1
        await interaction.followup.send(
            f"YOU HAVE PICKED ${ticker}\nYOU HAVE {new_count}/{limit} PICKS",
            ephemeral=True,
        )
        user_set.add(ticker)
        _inc_count(cat, ticker, +1)
        asyncio.create_task(
            self._finalize_vote_background(
                interaction,
                guild=guild,
                cat=cat,
                category_key=category_key,
                week_key=week_key,
                ticker=ticker,
                member=member,
                limit=limit,
                role_at_vote=role_at_vote,
            )
        )


async def build_weekly_voting_view(
    category_idx: int,
    tickers: List[str],
    *,
    fetch_quotes: bool = True,
) -> WeeklyVotingView:
    """Build voting buttons with live prices on each label."""
    tix = [str(t).strip().lstrip("$").upper() for t in tickers if t][:20]
    quotes: dict[str, FinnhubQuote] = {}
    if fetch_quotes and tix:
        quotes, _ = await asyncio.to_thread(quote_and_names_for_symbols, tix)
    return WeeklyVotingView(category_idx, tix, quotes)


# ===================== Utility: cleanup helpers =====================

async def _delete_bot_messages(ch: discord.TextChannel, guild: discord.Guild, limit: int = 100) -> int:
    """Delete recent messages authored by this bot in the channel."""
    count = 0
    try:
        async for msg in ch.history(limit=limit):
            if msg.author == guild.me:
                try:
                    await msg.delete()
                    count += 1
                except Exception:
                    pass
    except Exception:
        pass
    return count


async def _purge_channel_messages(
    ch: discord.TextChannel,
    guild: discord.Guild,
    limit: int = 400,
) -> int:
    """Wipe every message (bot or user) in a channel.

    Uses bulk delete for messages younger than 14 days, then falls back to
    one-by-one deletes for anything older. Falls back to bot-only deletes if
    the bot lacks the Manage Messages permission.
    """
    me = guild.me
    can_manage = bool(
        me
        and ch.permissions_for(me).manage_messages
        and ch.permissions_for(me).read_message_history
    )
    if not can_manage:
        return await _delete_bot_messages(ch, guild, limit=limit)

    deleted_total = 0
    try:
        purged = await ch.purge(limit=limit, bulk=True, reason="Weekly cycle reset")
        deleted_total += len(purged)
    except discord.Forbidden:
        return await _delete_bot_messages(ch, guild, limit=limit)
    except discord.HTTPException:
        pass

    # purge() skips messages >14 days old; clean them up individually.
    try:
        async for msg in ch.history(limit=limit):
            try:
                await msg.delete()
                deleted_total += 1
            except discord.Forbidden:
                break
            except Exception:
                continue
    except Exception:
        pass
    return deleted_total


def _weekly_closed_banner() -> discord.Embed:
    emb = discord.Embed(
        title="VOTING CLOSED",
        description="Voting channels are closed and will reopen on Monday at 9:00 AM ET.",
        color=discord.Color.dark_grey()
    )
    return emb


def _open_picker_embed() -> discord.Embed:
    return discord.Embed(
        title="CHOOSE YOUR TICKER",
        description=(
            "Click **Open Picker**, then **Try Ticker** and type part or all of a ticker "
            "(with or without `$`). The system auto-completes it to the best valid match.\n\n"
            "Need ideas? Click **Show 20 Examples** for twenty sample stocks (names and prices). "
            "Then use **Show 20 more** on that private message to load the next twenty, as many times as you like."
        ),
        color=discord.Color.blurple()
    )


# ===================== Countdown banner helpers =====================

def _format_tminus(end_utc: datetime, now_utc: Optional[datetime] = None) -> str:
    now = (now_utc or datetime.now(tz=UTC)).astimezone(UTC)
    secs = max(0, int((end_utc - now).total_seconds()))
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _banner_description_with_timer(cat: int, end_utc: Optional[datetime]) -> str:
    live_ch = _category_idx_to_live_name(cat)
    base = (
        f"This week’s **{_category_title(cat)}** game is in the **vote stage**.\n\n"
        "**What to do:** press a **button below** to vote for that stock. "
        "Each button shows the ticker and its current price.\n\n"
        f"**Live leaderboard:** #{live_ch}\n"
        "• **NPC** — 1 vote in this category\n"
        "• **PLAYER / WINNER** — up to 5 votes (different tickers)"
    )
    if end_utc is None:
        return base
    et_str = _format_et(end_utc)
    unix = int(end_utc.timestamp())
    return (
        f"{base}\n\n"
        f"**Early winner window ends:** {et_str} — <t:{unix}:F> • <t:{unix}:R>\n"
        f"⏳ **T‑minus:** {_format_tminus(end_utc)}"
    )


def _build_voting_open_embed(cat: int, end_utc: Optional[datetime]) -> discord.Embed:
    color = _ROSTER_COLORS[cat] if cat < len(_ROSTER_COLORS) else discord.Color.blue()
    emb = discord.Embed(
        title="VOTING OPEN",
        description=_banner_description_with_timer(cat, end_utc),
        color=color,
    )
    emb.set_author(name=_category_title(cat))
    return emb


async def build_final_leaderboard_embeds(guild_id: int, week_key: str) -> list[discord.Embed]:
    """End-of-week board: one embed (and Discord message) per cap category."""
    counts = database.all_vote_counts(guild_id, week_key)
    all_syms: list[str] = []
    for cat in ("small", "mid", "blue"):
        all_syms.extend(t for t, _ in counts[cat])
    quotes, names = await asyncio.to_thread(quote_and_names_for_symbols, all_syms)

    embeds: list[discord.Embed] = []
    for cat_idx, cat_key in enumerate(("small", "mid", "blue")):
        title = CATEGORY_TITLES[cat_key]
        color = (
            _ROSTER_COLORS[cat_idx]
            if cat_idx < len(_ROSTER_COLORS)
            else discord.Color.gold()
        )
        rows = counts[cat_key]
        if not rows:
            embeds.append(
                discord.Embed(
                    title=f"FINAL WEEKLY LEADERBOARD — {title}",
                    description="No votes this week.",
                    color=color,
                )
            )
            continue

        lines: list[str] = []
        for rank, (ticker, total) in enumerate(rows, start=1):
            nm = _truncate(names.get(ticker, "") or "", 30) or "—"
            q = format_quote(ticker, quotes.get(ticker))
            lines.append(f"**{rank}.** `${ticker}` · {nm}\n    {q} · **{total}**")

        desc = "\n\n".join(lines)
        if len(desc) > 4000:
            desc = "\n\n".join(lines[:12]) + "\n\n… *list truncated*"

        embeds.append(
            discord.Embed(
                title=f"FINAL WEEKLY LEADERBOARD — {title}",
                description=(
                    "Results for this week — **symbol**, **company**, **price**, **votes**.\n\n"
                    f"{desc}"
                ),
                color=color,
            )
        )
    return embeds


async def _get_or_cache_voting_open_message(guild: discord.Guild, cat: int) -> Optional[discord.Message]:
    """
    Return the VOTING OPEN message for the given category in this guild.
    Prefer cached id; else search last ~50 messages for our banner and cache it.
    """
    ch_name = _category_idx_to_weekly_name(cat)
    ch = _find_text_channel(guild, ch_name)
    if not ch:
        return None

    # cached?
    mid = _voting_open_msg_ids.get(guild.id, {}).get(cat)
    if mid:
        try:
            return await ch.fetch_message(mid)
        except Exception:
            # drop cache and try locate again
            _voting_open_msg_ids.setdefault(guild.id, {}).pop(cat, None)

    # search recent bot messages
    try:
        async for msg in ch.history(limit=50):
            if msg.author != guild.me or not msg.embeds:
                continue
            e = msg.embeds[0]
            if (e.title or "").strip().upper() == "VOTING OPEN":
                _voting_open_msg_ids.setdefault(guild.id, {})[cat] = msg.id
                return msg
    except Exception:
        pass
    return None


# ===================== Cog =====================

class WeeklyPicksCog(commands.Cog):
    """All 'Weekly Picks' logic (buttons, voting, live leaderboards, winners) lives here."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._timer_task: Optional[asyncio.Task] = None

    async def cog_load(self):
        self._timer_task = asyncio.create_task(
            self._countdown_updater(), name="weekly_countdown_updater")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        week_key = database.week_key_for()
        for guild in self.bot.guilds:
            open_ = await asyncio.to_thread(
                database.is_voting_open, guild.id, week_key
            )
            if open_:
                await asyncio.to_thread(hydrate_vote_state, guild.id, week_key)

    def cog_unload(self):
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()

    # --- ADMIN helper: sanity-check layout; tiny, non-invasive test command ---
    @commands.command(name="weekly_status")
    @commands.has_role("ADMIN")
    async def weekly_status(self, ctx: commands.Context):
        """
        ADMIN (run in #mod): quick layout check for WEEKLY PICKS and LIVE channels.
        Does not change any state.
        """
        if "mod" not in ctx.channel.name.lower():
            await ctx.send("Please run this in the **#mod** channel.")
            return

        if not ctx.guild:
            await ctx.send("This command must be run in a server.")
            return

        found_weekly = []
        missing_weekly = []
        for name in REQUIRED_WEEKLY_CHANNELS:
            ch = _find_text_channel(ctx.guild, name)
            (found_weekly if ch else missing_weekly).append(name)

        found_live = []
        missing_live = []
        for name in REQUIRED_LIVE_CHANNELS:
            ch = _find_text_channel(ctx.guild, name)
            (found_live if ch else missing_live).append(name)

        emb = discord.Embed(
            title="Weekly Picks — Status",
            description="Sanity check for required channels. No changes were made.",
            color=discord.Color.blurple(),
        )
        emb.add_field(
            name="Weekly Picks channels",
            value=(
                ("✅ Found: " +
                 ", ".join(f"#{n}" for n in found_weekly)) if found_weekly else "—"
            ) + "\n" +
            (
                ("❌ Missing: " + ", ".join(f"#{n}" for n in missing_weekly)
                 ) if missing_weekly else "All present."
            ),
            inline=False
        )
        emb.add_field(
            name="Live Leaderboard channels",
            value=(
                ("✅ Found: " +
                 ", ".join(f"#{n}" for n in found_live)) if found_live else "—"
            ) + "\n" +
            (
                ("❌ Missing: " + ", ".join(f"#{n}" for n in missing_live)
                 ) if missing_live else "All present."
            ),
            inline=False
        )
        await ctx.send(embed=emb)

    # --- ADMIN: enable voting on existing WEEKLY PICKS messages pushed earlier ---
    @commands.command(name="enable_voting")
    @commands.has_role("ADMIN")
    async def enable_voting(self, ctx: commands.Context):
        """
        ADMIN: scans the three WEEKLY PICKS channels for the most recent bot message
        titled 'WEEKLY PICKS — <Category>', parses tickers from its 'Tickers' field
        (or from button labels), deletes old bot messages in that channel, and posts a fresh
        'VOTING OPEN' banner with active voting buttons + (if armed) a live countdown line.
        Also primes live leaderboards.
        """
        if not ctx.guild:
            await ctx.send("This command must be run in a server.")
            return

        # compute early window end if armed
        end_utc = early_window_end_utc()
        week_key = database.week_key_for()
        database.set_cycle_phase(
            ctx.guild.id,
            week_key,
            status="voting",
            ticker_selection_open=False,
            voting_open=True,
            early_window_open=is_early_window_active(),
            monday_open_at=datetime.now(tz=UTC).isoformat(),
            early_window_end_at=end_utc.isoformat() if end_utc else None,
        )

        await ctx.send("Enabling voting… scanning channels, cleaning up, and preparing buttons.")
        updated = 0
        for cat in range(3):
            ch_name = _category_idx_to_weekly_name(cat)
            ch = _find_text_channel(ctx.guild, ch_name)
            if not ch:
                await ctx.send(f"Channel **#{ch_name}** not found.")
                continue

            # locate pushed tickers
            target_msg = None
            tickers: List[str] = []
            try:
                async for msg in ch.history(limit=50):
                    if msg.author != ctx.guild.me or not msg.embeds:
                        continue
                    e = msg.embeds[0]
                    if (e.title or "").strip().upper().startswith(f"WEEKLY PICKS — {_category_title(cat).upper()}"):
                        # parse tickers from the "Tickers" field if present
                        field_val = None
                        for f in e.fields:
                            if (f.name or "").strip().lower() == "tickers":
                                field_val = f.value
                                break
                        tickers = _parse_tickers_field(field_val)
                        if not tickers:
                            # fallback: from existing buttons/labels
                            tickers = _extract_tickers_from_components(msg)
                        target_msg = msg
                        break
            except Exception:
                pass

            if not target_msg or not tickers:
                await ctx.send(f"No WEEKLY PICKS message with tickers found in **#{ch_name}**.")
                continue

            # Prime in-memory counts (start from 0 for each ticker)
            counts = _vote_counts[cat] = {}
            for t in tickers:
                counts[t] = counts.get(t, 0)

            # Build view + VOTING OPEN banner + roster card (names & prices)
            banner = _build_voting_open_embed(cat, end_utc)
            view = await build_weekly_voting_view(cat, tickers)

            try:
                # Clean old bot messages first so only the new banner remains
                await _delete_bot_messages(ch, ctx.guild, limit=400)
                sent = await ch.send(embed=banner, view=view)
                _voting_open_msg_ids.setdefault(
                    ctx.guild.id, {})[cat] = sent.id
                updated += 1
            except Exception as e:
                await ctx.send(f"Failed to enable voting in **#{ch_name}**: {e}")
                continue

            # Seed live leaderboard
            try:
                await _post_or_update_leaderboard(ctx.guild, cat)
            except Exception:
                pass

        await ctx.send(f"Enabled voting on {updated} WEEKLY PICKS message(s).")

    # --- ADMIN: reset everything for a fresh cycle ---
    @commands.command(name="reset_content")
    @commands.has_role("ADMIN")
    async def reset_content(self, ctx: commands.Context):
        """
        ADMIN (run in #mod): reset Weekly Picks, Live Leaderboards, Pick Results,
        and reopen the three ticker channels with Open Picker.
        """
        if "mod" not in ctx.channel.name.lower():
            await ctx.send("Please run this in the **#mod** channel.")
            return
        if not ctx.guild:
            await ctx.send("This command must be run in a server.")
            return

        guild = ctx.guild
        week_key = database.week_key_for()
        database.set_cycle_phase(
            guild.id,
            week_key,
            status="ticker_selection",
            ticker_selection_open=True,
            voting_open=False,
            early_window_open=False,
        )
        summary_lines: List[str] = []

        # 1) WEEKLY PICKS: wipe bot messages + post "VOTING CLOSED"
        closed = _weekly_closed_banner()
        for name in REQUIRED_WEEKLY_CHANNELS:
            ch = _find_text_channel(guild, name)
            if not ch:
                summary_lines.append(f"• Missing #{name}")
                continue
            deleted = await _delete_bot_messages(ch, guild, limit=200)
            try:
                await ch.send(embed=closed)
            except Exception:
                pass
            summary_lines.append(
                f"• #{name}: cleared {deleted} and posted CLOSED")

        # 2) LIVE LEADERBOARDS: clear + reset counts + seed fresh 'No votes yet'
        for cat in range(3):
            _user_votes[cat].clear()
            _vote_counts[cat].clear()
        _live_msg_ids.clear()
        # clear cached IDs for this guild
        _voting_open_msg_ids.pop(guild.id, None)

        for cat in range(3):
            live_name = _category_idx_to_live_name(cat)
            ch = _find_text_channel(guild, live_name)
            if not ch:
                summary_lines.append(f"• Missing #{live_name}")
                continue
            deleted = await _delete_bot_messages(ch, guild, limit=200)
            try:
                await _post_or_update_leaderboard(guild, cat)
            except Exception:
                pass
            summary_lines.append(
                f"• #{live_name}: cleared {deleted} and reset leaderboard")

        # 3) PICK RESULTS: reset all three fields to 0/20
        await _reset_pick_results(guild)
        summary_lines.append(
            "• #pick-results: reset to (0/20) for all categories")

        # 4) TICKER CHANNELS: clear bot messages and post Open Picker embed+view
        opener = _open_picker_embed()
        for name in TICKER_CHANNELS:
            ch = _find_text_channel(guild, name)
            if not ch:
                summary_lines.append(f"• Missing #{name}")
                continue
            deleted = await _delete_bot_messages(ch, guild, limit=200)
            try:
                await ch.send(embed=opener, view=OpenPickerView(channel=ch, user_id=ctx.author.id))
                summary_lines.append(
                    f"• #{name}: cleared {deleted} and posted Open Picker")
            except Exception as e:
                summary_lines.append(
                    f"• #{name}: failed to post Open Picker ({e})")

        # Report summary back to #mod
        report = discord.Embed(
            title="Reset Content — Summary",
            description="\n".join(summary_lines),
            color=discord.Color.teal()
        )
        await ctx.send(embed=report)

    # --- ADMIN: early-window testing helpers (no UX change for users) ---
    @commands.command(name="early_arm_now")
    @commands.has_role("ADMIN")
    async def early_arm_now(self, ctx: commands.Context):
        """ADMIN: start the 24h early window now (UTC-based)."""
        now = datetime.now(tz=UTC)
        arm_early_window(now)
        until = now + timedelta(hours=24)
        # Update existing VOTING OPEN banners to start showing timer immediately
        try:
            await self._update_all_guild_banners_once()
        except Exception:
            pass
        emb = discord.Embed(
            title="Early Window Armed",
            description=f"Start (UTC): **{now:%Y-%m-%d %H:%M}**\nEnd (UTC): **{until:%Y-%m-%d %H:%M}**",
            color=discord.Color.green()
        )
        await ctx.send(embed=emb)

    @commands.command(name="early_status")
    @commands.has_role("ADMIN")
    async def early_status(self, ctx: commands.Context):
        """ADMIN: show early-window status and a small diagnostic."""
        now = datetime.now(tz=UTC)
        active = is_early_window_active(now)
        if _early_window_start_utc is None:
            desc = "Early window is **not armed**."
        else:
            until = _early_window_start_utc + timedelta(hours=24)
            left = max(0, int((until - now).total_seconds()))
            h, m = left // 3600, (left % 3600) // 60
            desc = (
                f"Start (UTC): **{_early_window_start_utc:%Y-%m-%d %H:%M}**\n"
                f"End   (UTC): **{until:%Y-%m-%d %H:%M}**\n"
                f"Active: **{active}** — time left: ~{h}h {m}m"
            )
        emb = discord.Embed(title="Early Window — Status",
                            description=desc, color=discord.Color.blurple())
        await ctx.send(embed=emb)

    # ---------------- Countdown updater (runs every ~60s during early window) ----------------

    async def _update_all_guild_banners_once(self):
        """Refresh the timer on the VOTING OPEN instruction card."""
        end = early_window_end_utc()
        if end is None:
            return
        for guild in list(self.bot.guilds):
            for cat in range(3):
                msg = await _get_or_cache_voting_open_message(guild, cat)
                if not msg:
                    continue
                try:
                    await msg.edit(embed=_build_voting_open_embed(cat, end))
                except Exception:
                    pass

    async def _countdown_updater(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                # only update when early window is active
                if is_early_window_active():
                    await self._update_all_guild_banners_once()
                # gentle, avoids ratelimits; still looks “alive”
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return
            except Exception:
                # swallow errors to keep the loop alive
                await asyncio.sleep(60)


# -------- Extension hook (required by discord.py to load this cog) --------

async def setup(bot: commands.Bot):
    await bot.add_cog(WeeklyPicksCog(bot))
