from __future__ import annotations

import os
from dataclasses import dataclass


TICKER_LIMIT_PER_CATEGORY = 20
NPC_VOTES_PER_CATEGORY = 1
PLAYER_VOTES_PER_CATEGORY = 5
# One Stripe pack grants this many extra votes per category for the active week.
EXTRA_VOTE_PACK_SIZE = int(os.getenv("EXTRA_VOTE_PACK_SIZE", "1"))
EXTRA_VOTE_PACK_CENTS = int(os.getenv("EXTRA_VOTE_PACK_CENTS", "299"))
WINNER_BONUS_VOTES = int(os.getenv("WINNER_BONUS_VOTES", "1"))

# Logical role keys stay NPC/PLAYER/WINNER; display names include the buyer icons.
ROLE_NPC = os.getenv("ROLE_NPC", "🤖 NPC")
ROLE_PLAYER = os.getenv("ROLE_PLAYER", "💎 PLAYER")
ROLE_WINNER = os.getenv("ROLE_WINNER", "🏆 WINNER")
ROLE_ADMIN = os.getenv("ROLE_ADMIN", "ADMIN")

# Mathematical sans-serif LIVE / TICKER (NFKC-folds back to ascii for lookups).
_STYLE_TICKER = "\U0001d5b3\U0001d5a8\U0001d5a2\U0001d5aa\U0001d5a4\U0001d5b1"  # 𝖳𝖨𝖢𝖪𝖤𝖱
_STYLE_LIVE = "\U0001d5ab\U0001d5a8\U0001d5b5\U0001d5a4"  # 𝖫𝖨𝖵𝖤

CHANNEL_SMALL_TICKER = os.getenv("CHANNEL_SMALL_TICKER", f"📊small-cap-{_STYLE_TICKER}")
CHANNEL_MID_TICKER = os.getenv("CHANNEL_MID_TICKER", f"📈mid-cap-{_STYLE_TICKER}")
CHANNEL_BLUE_TICKER = os.getenv("CHANNEL_BLUE_TICKER", f"🏦large-cap-{_STYLE_TICKER}")
CHANNEL_PICK_RESULTS = os.getenv("PICK_RESULTS_CHANNEL", "✅live-chosen-tickers")
PICK_RESULTS_CHANNEL_CANDIDATES = tuple(
    dict.fromkeys(
        n.strip()
        for n in os.getenv(
            "PICK_RESULTS_CHANNEL_CANDIDATES",
            f"{CHANNEL_PICK_RESULTS},live-chosen-tickers,pick-results",
        ).split(",")
        if n.strip()
    )
)

CHANNEL_SMALL_VOTE = os.getenv("CHANNEL_SMALL_VOTE", "🗳️small-cap")
CHANNEL_MID_VOTE = os.getenv("CHANNEL_MID_VOTE", "🗳️mid-cap")
CHANNEL_BLUE_VOTE = os.getenv("CHANNEL_BLUE_VOTE", "🗳️large-cap")

CHANNEL_SMALL_LIVE = os.getenv("CHANNEL_SMALL_LIVE", f"🔴small-cap-{_STYLE_LIVE}")
CHANNEL_MID_LIVE = os.getenv("CHANNEL_MID_LIVE", f"🔴mid-cap-{_STYLE_LIVE}")
CHANNEL_BLUE_LIVE = os.getenv("CHANNEL_BLUE_LIVE", f"🔴large-cap-{_STYLE_LIVE}")

CHANNEL_MOD = "mod"
CHANNEL_ADMIN_ACTIONS = "admin-actions"
# Rules channel — holds the "react to get NPC" onboarding gate (🔥 / 🚀)
CHANNEL_RULES = os.getenv("RULES_CHANNEL", "𝖱𝖴𝖫𝖤𝖲📜")
RULES_CHANNEL_CANDIDATES = tuple(
    dict.fromkeys(
        n.strip()
        for n in os.getenv("RULES_CHANNEL_CANDIDATES", f"rules,{CHANNEL_RULES}").split(",")
        if n.strip()
    )
)
# Emojis a new member reacts with to receive the NPC role (comma-separated)
NPC_GATE_EMOJIS = tuple(
    dict.fromkeys(
        e.strip()
        for e in os.getenv("NPC_GATE_EMOJIS", "🔥,🚀").split(",")
        if e.strip()
    )
)
# Subscribe (new PLAYER checkout) — first matching channel name in guild wins
CHANNEL_SUBSCRIBE = os.getenv("SUBSCRIBE_CHANNEL", "subscribe")
SUBSCRIBE_CHANNEL_CANDIDATES = tuple(
    dict.fromkeys(
        n.strip()
        for n in os.getenv(
            "SUBSCRIBE_CHANNEL_CANDIDATES",
            "subscribe,player,registration,register,𝐏𝐋𝐀𝐘𝐄𝐑",
        ).split(",")
        if n.strip()
    )
)
# Buy extra votes (one-time Stripe pack)
CHANNEL_EXTRA_VOTES = os.getenv("EXTRA_VOTES_CHANNEL", "extra-votes")
EXTRA_VOTES_CHANNEL_CANDIDATES = tuple(
    dict.fromkeys(
        n.strip()
        for n in os.getenv(
            "EXTRA_VOTES_CHANNEL_CANDIDATES",
            f"{CHANNEL_EXTRA_VOTES},buy-votes,extra-vote",
        ).split(",")
        if n.strip()
    )
)
# Public Q&A / FAQ
CHANNEL_QA = os.getenv("QA_CHANNEL", "q-and-a")
QA_CHANNEL_CANDIDATES = tuple(
    dict.fromkeys(
        n.strip()
        for n in os.getenv("QA_CHANNEL_CANDIDATES", f"ℚ＆𝗔,{CHANNEL_QA},q-and-a,faq").split(",")
        if n.strip()
    )
)
# Manage existing Stripe subscription (billing portal)
CHANNEL_MANAGE_SUBSCRIPTION = os.getenv("MANAGE_SUBSCRIPTION_CHANNEL", "manage-subscription")
MANAGE_SUBSCRIPTION_CHANNEL_CANDIDATES = tuple(
    dict.fromkeys(
        n.strip()
        for n in os.getenv(
            "MANAGE_SUBSCRIPTION_CHANNEL_CANDIDATES",
            "manage-subscription,manage-billing,billing",
        ).split(",")
        if n.strip()
    )
)
# Legacy alias (subscribe channel)
CHANNEL_PLAYER = os.getenv("PLAYER_CHANNEL", CHANNEL_SUBSCRIBE)
PLAYER_CHANNEL_CANDIDATES = SUBSCRIBE_CHANNEL_CANDIDATES
CHANNEL_FINAL_LEADERBOARD = os.getenv(
    "FINAL_LEADERBOARD_CHANNEL",
    "\U0001f947\U0001d40b\U0001d404\U0001d400\U0001d403\U0001d404\U0001d411\U0001d401\U0001d40e\U0001d400\U0001d411\U0001d403\U0001f947",
)
CHANNEL_WINNERS = os.getenv(
    "WINNERS_CHANNEL",
    "\U0001f3c6\uff11st-\U0001d479\U0001d468\U0001d475\U0001d472\U0001d46c\U0001d46b\U0001f3c6",
)

CATEGORIES = ("small", "mid", "blue")
CATEGORY_TITLES = {
    "small": "Small Cap",
    "mid": "Mid Cap",
    "blue": "Large Cap",
}

CATEGORY_FROM_TICKER_CHANNEL = {
    CHANNEL_SMALL_TICKER: "small",
    CHANNEL_MID_TICKER: "mid",
    CHANNEL_BLUE_TICKER: "blue",
}

CATEGORY_FROM_VOTE_CHANNEL = {
    CHANNEL_SMALL_VOTE: "small",
    CHANNEL_MID_VOTE: "mid",
    CHANNEL_BLUE_VOTE: "blue",
}

TICKER_CHANNEL_BY_CATEGORY = {
    "small": CHANNEL_SMALL_TICKER,
    "mid": CHANNEL_MID_TICKER,
    "blue": CHANNEL_BLUE_TICKER,
}

VOTE_CHANNEL_BY_CATEGORY = {
    "small": CHANNEL_SMALL_VOTE,
    "mid": CHANNEL_MID_VOTE,
    "blue": CHANNEL_BLUE_VOTE,
}

LIVE_CHANNEL_BY_CATEGORY = {
    "small": CHANNEL_SMALL_LIVE,
    "mid": CHANNEL_MID_LIVE,
    "blue": CHANNEL_BLUE_LIVE,
}

ALL_REQUIRED_CHANNELS = (
    CHANNEL_SMALL_TICKER,
    CHANNEL_MID_TICKER,
    CHANNEL_BLUE_TICKER,
    CHANNEL_PICK_RESULTS,
    CHANNEL_SMALL_VOTE,
    CHANNEL_MID_VOTE,
    CHANNEL_BLUE_VOTE,
    CHANNEL_SMALL_LIVE,
    CHANNEL_MID_LIVE,
    CHANNEL_BLUE_LIVE,
    CHANNEL_MOD,
    CHANNEL_ADMIN_ACTIONS,
    CHANNEL_FINAL_LEADERBOARD,
    CHANNEL_WINNERS,
    CHANNEL_SUBSCRIBE,
    CHANNEL_MANAGE_SUBSCRIPTION,
    CHANNEL_EXTRA_VOTES,
    CHANNEL_QA,
)


@dataclass(frozen=True)
class StripeSettings:
    secret_key: str | None = os.getenv("STRIPE_SECRET_KEY")
    webhook_secret: str | None = os.getenv("STRIPE_WEBHOOK_SECRET")
    price_id: str | None = os.getenv("STRIPE_MONTHLY_PRICE_ID")
    extra_votes_price_id: str | None = os.getenv("STRIPE_EXTRA_VOTES_PRICE_ID")
    success_url: str = os.getenv("STRIPE_SUCCESS_URL", "https://discord.com/channels/@me")
    cancel_url: str = os.getenv("STRIPE_CANCEL_URL", "https://discord.com/channels/@me")
    portal_return_url: str = os.getenv("STRIPE_PORTAL_RETURN_URL", "https://discord.com/channels/@me")


STRIPE_WEBHOOK_HOST = os.getenv("STRIPE_WEBHOOK_HOST", "0.0.0.0")
STRIPE_WEBHOOK_PORT = int(os.getenv("STRIPE_WEBHOOK_PORT", "8081"))


DB_PATH = os.getenv("STOCK_BOT_DB", "stock_bot.sqlite3")

SUPABASE_PROJECT_REF = os.getenv("SUPABASE_PROJECT_REF", "hxyixnwdfwffqmzcvvlx")
SUPABASE_URL = os.getenv("SUPABASE_URL", f"https://{SUPABASE_PROJECT_REF}.supabase.co")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
