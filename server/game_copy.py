"""Buyer-requested user-facing copy for Discord game messages.

Keep wording here so WEEKLY PICKS, ticker channels, leaderboards, and DMs
stay in one place and match the acceptance text.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from config import (
    CATEGORIES,
    CATEGORY_TITLES,
    WINNER_BONUS_VOTES,
    CHANNEL_BLUE_LIVE,
    CHANNEL_BLUE_TICKER,
    CHANNEL_BLUE_VOTE,
    CHANNEL_FINAL_LEADERBOARD,
    CHANNEL_MID_LIVE,
    CHANNEL_MID_TICKER,
    CHANNEL_MID_VOTE,
    CHANNEL_PICK_RESULTS,
    CHANNEL_PLAYER,
    CHANNEL_SMALL_LIVE,
    CHANNEL_SMALL_TICKER,
    CHANNEL_SMALL_VOTE,
    CHANNEL_WINNERS,
    PICK_RESULTS_CHANNEL_CANDIDATES,
    PLAYER_CHANNEL_CANDIDATES,
)
from discord_names import find_text_channel

# Not legal terms — a short game disclaimer until the buyer supplies full copy.
NOT_INVESTMENT_ADVICE = (
    "Tickers, votes, rankings, and results are a game and are **not investment advice**."
)

CATEGORY_HEADLINES = {
    "small": "**SMALL CAP — Companies Worth up to $2B**",
    "mid": "**MID CAP — Companies Worth Between $2B–$10B**",
    "blue": "**LARGE CAP — Companies Worth Over $10B**",
}

CATEGORY_PLAIN = {
    "small": "Small Cap",
    "mid": "Mid Cap",
    "blue": "Large Cap",
}

CHOOSE_TICKER_BUTTON = "CHOOSE TICKER"
CLICK_HERE_HINT = (
    "Press the **CLICK HERE** button and type the **full ticker symbol** in the field "
    "(with or without the $ symbol).\n"
    "Make sure the ticker belongs to the correct market cap category."
)


def cat_key(cat: int | str) -> str:
    if isinstance(cat, int):
        return CATEGORIES[cat]
    key = str(cat).lower()
    if key in ("large", "large-cap", "blue-cap"):
        return "blue"
    return key


def mention(guild, names: Iterable[str], fallback: str) -> str:
    if guild is not None:
        ch = find_text_channel(guild, *list(names))
        if ch:
            return ch.mention
    return fallback


def mention_player(guild) -> str:
    return mention(guild, (CHANNEL_PLAYER, *PLAYER_CHANNEL_CANDIDATES), "#💎𝐏𝐋𝐀𝐘𝐄𝐑💎")


def mention_pick_results(guild) -> str:
    return mention(
        guild,
        (CHANNEL_PICK_RESULTS, *PICK_RESULTS_CHANNEL_CANDIDATES),
        "#live-chosen-tickers",
    )


def mention_vote(guild, cat: int | str) -> str:
    key = cat_key(cat)
    name = {
        "small": CHANNEL_SMALL_VOTE,
        "mid": CHANNEL_MID_VOTE,
        "blue": CHANNEL_BLUE_VOTE,
    }[key]
    return mention(guild, (name,), f"#{name}")


def mention_live(guild, cat: int | str) -> str:
    key = cat_key(cat)
    name = {
        "small": CHANNEL_SMALL_LIVE,
        "mid": CHANNEL_MID_LIVE,
        "blue": CHANNEL_BLUE_LIVE,
    }[key]
    return mention(guild, (name,), f"#{name}")


def mention_ticker(guild, cat: int | str) -> str:
    key = cat_key(cat)
    name = {
        "small": CHANNEL_SMALL_TICKER,
        "mid": CHANNEL_MID_TICKER,
        "blue": CHANNEL_BLUE_TICKER,
    }[key]
    return mention(guild, (name,), f"#{name}")


def mention_leaderboard(guild) -> str:
    return mention(guild, (CHANNEL_FINAL_LEADERBOARD,), f"#{CHANNEL_FINAL_LEADERBOARD}")


def mention_winners(guild) -> str:
    return mention(guild, (CHANNEL_WINNERS,), f"#{CHANNEL_WINNERS}")


def _t_minus_hours(end_utc: datetime | None) -> str:
    if end_utc is None:
        return "24 hours"
    now = datetime.now(tz=timezone.utc)
    if end_utc.tzinfo is None:
        end_utc = end_utc.replace(tzinfo=timezone.utc)
    remaining = end_utc - now
    hours = max(0, int(remaining.total_seconds() // 3600))
    minutes = max(0, int((remaining.total_seconds() % 3600) // 60))
    if remaining.total_seconds() <= 0:
        return "0 hours"
    if hours <= 0:
        return f"{minutes} minutes"
    if minutes:
        return f"{hours} hours {minutes} minutes"
    return f"{hours} hours"


def voting_open_description(
    cat: int | str,
    end_utc: datetime | None,
    guild=None,
) -> str:
    key = cat_key(cat)
    headline = CATEGORY_HEADLINES[key]
    if end_utc is None:
        early_line = "Early WINNER window ends in: Tuesday, 9 AM EST — T-minus: 24 hours"
        timer = ""
    else:
        unix = int(end_utc.timestamp())
        now = datetime.now(tz=timezone.utc)
        aware = end_utc if end_utc.tzinfo else end_utc.replace(tzinfo=timezone.utc)
        if now >= aware:
            early_line = "Early WINNER window is closed. You can still vote, but new votes do not qualify for WINNER."
            timer = f"(ended <t:{unix}:F>)"
        else:
            early_line = (
                f"Early WINNER window ends in: Tuesday, 9 AM EST — T-minus: {_t_minus_hours(aware)}"
            )
            timer = f"<t:{unix}:R> (<t:{unix}:F>)"
    parts = [
        headline,
        "What to do: Choose your favorite stocks by pressing the buttons below.",
        "NPC: 1 vote in this category",
        "PLAYER/WINNER: Up to 5 votes in this category (Multiple votes per ticker allowed — you may put more than one vote on the same ticker.)",
        early_line,
    ]
    if timer:
        parts.append(timer)
    parts.append("Voting closes: Friday, 4 PM EST.")
    parts.append(NOT_INVESTMENT_ADVICE)
    return "\n".join(parts)


def vote_picked_line(
    ticker: str,
    cat: int | str,
    count: int,
    limit: int,
    guild=None,
    *,
    is_npc: bool = False,
) -> str:
    live = mention_live(guild, cat)
    line = f"YOU PICKED ${ticker} — You Have {count}/{limit} Picks. Watch results live in {live}"
    if is_npc:
        title = CATEGORY_PLAIN[cat_key(cat)]
        player = mention_player(guild)
        line += (
            f"\nTo get access to the {title} live leaderboard and vote up to 5 times, "
            f"subscribe in {player}."
        )
    return line


def vote_limit_message(cat: int | str, *, is_npc: bool, guild=None) -> str:
    title = CATEGORY_PLAIN[cat_key(cat)]
    lb = mention_leaderboard(guild)
    winners = mention_winners(guild)
    lines = [
        f"You Have Reached Your Voting Limit in {title}. Next voting opens Monday at 9 AM EST.",
        "Results will be shown in:",
        lb,
        winners,
        "Friday at 4 PM EST.",
    ]
    if is_npc:
        player = mention_player(guild)
        lines.append(
            f"To get access to the {title} live leaderboard and vote up to 5 times, subscribe in {player}."
        )
    return "\n".join(lines)


def voting_closed_description(guild=None) -> str:
    lb = mention_leaderboard(guild)
    winners = mention_winners(guild)
    player = mention_player(guild)
    return (
        "Voting will resume Monday at 9 AM EST.\n"
        "To review this week’s top-ranked tickers and winners:\n"
        f"{lb}\n"
        f"{winners}\n"
        f"CHOOSE YOUR TICKER is now open for {player} and WINNER roles.\n"
        f"{NOT_INVESTMENT_ADVICE}"
    )


def live_channel_closed_description(cat: int | str) -> str:
    title = CATEGORY_PLAIN[cat_key(cat)]
    return (
        "The channel will reopen next Monday at 9 AM EST.\n"
        f"Here you can track live voting results for the {title} category.\n"
        f"{NOT_INVESTMENT_ADVICE}"
    )


def live_leaderboard_line(rank: int, ticker: str, votes: int, *, top3: bool) -> str:
    text = f"${ticker} - {votes} VOTES"
    if rank == 1:
        return f"# 🥇 {text}"
    if rank == 2:
        return f"## 🥈 **{text}**"
    if rank == 3:
        return f"## 🥉 **{text}**"
    return text


def midweek_leaderboard_description(guild=None) -> str:
    small = mention_vote(guild, "small")
    mid = mention_vote(guild, "mid")
    large = mention_vote(guild, "blue")
    player = mention_player(guild)
    return (
        "Voting is still open in the WEEKLY PICKS channels:\n"
        f"{small}\n"
        f"{mid}\n"
        f"{large}\n"
        "This week’s results will be shown in this channel when voting closes on Friday at 4PM EST.\n"
        f"To get access to the live leaderboards and more, join {player}.\n"
        f"{NOT_INVESTMENT_ADVICE}"
    )


def final_leaderboard_header(
    date_label: str,
    tops: dict[str, str],
) -> str:
    small = tops.get("small") or "—"
    mid = tops.get("mid") or "—"
    blue = tops.get("blue") or "—"
    return (
        f"TOP PICKS BY CATEGORY — ({date_label})\n"
        f"SMALL CAP — ${small}\n"
        f"MID CAP — ${mid}\n"
        f"LARGE CAP — ${blue}\n"
        "You can view the full tables with total vote counts below ⬇️"
    )


def ticker_channel_open_description(cat: int | str, guild=None) -> str:
    title = CATEGORY_PLAIN[cat_key(cat)]
    results = mention_pick_results(guild)
    return (
        f"Here you choose the ticker you want for the {title} category.\n"
        "Each PLAYER subscriber can choose only one ticker per category to participate in the weekly competition.\n"
        f"Press the **{CHOOSE_TICKER_BUTTON}** button.\n"
        f"When the window opens, type the **full ticker symbol** you want from the {title} category and press Enter.\n"
        f"Your ticker will be registered automatically and updated in the table in {results}.\n"
        f"{NOT_INVESTMENT_ADVICE}"
    )


def already_submitted_ticker(guild=None) -> str:
    results = mention_pick_results(guild)
    return (
        "You have already submitted a ticker for this category.\n"
        "You can now choose a ticker in the other categories.\n"
        f"View your selected ticker in {results}."
    )


def ticker_already_selected(guild=None) -> str:
    results = mention_pick_results(guild)
    return (
        f"That ticker has already been selected (shown in {results}).\n"
        "Please choose a different ticker."
    )


def ticker_channel_closed(cat: int | str, guild=None) -> str:
    title = CATEGORY_PLAIN[cat_key(cat)]
    results = mention_pick_results(guild)
    return (
        f"The {title} channel has reached the maximum limit of 20/20 different tickers.\n"
        "Try the other CHOOSE YOUR TICKER channels.\n"
        "The channel will reopen next Friday at 4PM EST.\n"
        f"View the results tables in {results}."
    )


def live_chosen_tickers_description(guild=None) -> str:
    small = mention_ticker(guild, "small")
    mid = mention_ticker(guild, "mid")
    large = mention_ticker(guild, "blue")
    return (
        "Here you can see the tickers selected by subscribers in:\n"
        f"{small} • {mid} • {large}\n"
        "Each category closes after reaching 20 different tickers.\n"
        "On Monday at 9 AM EST, the selected tickers will move to the WEEKLY PICKS voting channels.\n"
        "Vote for your favorite ticker before the categories fill up.\n"
        f"{NOT_INVESTMENT_ADVICE}"
    )


def live_chosen_tickers_closed_for_voting(guild=None) -> str:
    small = mention_vote(guild, "small")
    mid = mention_vote(guild, "mid")
    large = mention_vote(guild, "blue")
    return (
        "Voting is now open. This table is closed for the week.\n"
        "Vote for this week's tickers in the WEEKLY PICKS channels:\n"
        f"{small}\n"
        f"{mid}\n"
        f"{large}"
    )


def winner_incentive_lines(stats: dict | None = None) -> str:
    streak = int((stats or {}).get("current_streak") or 1)
    bonus = int((stats or {}).get("bonus_votes") or WINNER_BONUS_VOTES)
    if streak >= 3:
        badge = "💎"
    elif streak >= 2:
        badge = "🔥"
    else:
        badge = "🏆"
    return (
        f"Winner streak: {badge} **{streak}** week(s)\n"
        f"Bonus: **+{bonus} extra vote** in each WEEKLY PICKS category next week."
    )


def winner_role_dm(
    valid_until_label: str,
    *,
    stats: dict | None = None,
) -> str:
    return (
        "CONGRATULATIONS!🎉 YOU WON THE WINNER ROLE🎉\n\n"
        f"Your WINNER role is valid for one week, starting now until next Friday ({valid_until_label}) at 4PM EST.\n\n"
        f"{winner_incentive_lines(stats)}\n\n"
        "The WINNER role gives you the same perks as a PLAYER subscription:\n\n"
        "5 votes per week in each category in the WEEKLY PICKS channels (instead of 1)\n\n"
        "Access to subscriber-only channels:\n\n"
        "CHOOSE YOUR TICKER channels — choose which stocks will appear in the WEEKLY PICKS voting during the week\n\n"
        "LIVE LEADERBOARD channels — track live vote updates and see how many votes each ticker receives in real time\n\n"
        "VIP chat for subscribers only\n\n"
        "⚠️ This role will be removed automatically when the week ends."
    )


def winner_role_removed_dm(player_mention: str | None = None) -> str:
    player = player_mention or mention_player(None)
    return (
        "WINNER ROLE REMOVED\n\n"
        "If you would like to continue enjoying the WINNER perks, you can participate "
        f"in next week’s competition or click here > {player} to purchase a subscription."
    )


def friday_et_label(dt_utc: datetime) -> str:
    try:
        from zoneinfo import ZoneInfo

        local = dt_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        local = dt_utc
    return f"{local:%B %d, %Y}"
