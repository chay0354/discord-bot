from __future__ import annotations

import os
from dataclasses import dataclass


TICKER_LIMIT_PER_CATEGORY = 20
NPC_VOTES_PER_CATEGORY = 1
PLAYER_VOTES_PER_CATEGORY = 5

ROLE_NPC = os.getenv("ROLE_NPC", "NPC")
ROLE_PLAYER = os.getenv("ROLE_PLAYER", "PLAYER")
ROLE_WINNER = os.getenv("ROLE_WINNER", "WINNER")
ROLE_ADMIN = os.getenv("ROLE_ADMIN", "ADMIN")

CHANNEL_SMALL_TICKER = "small-cap-ticker"
CHANNEL_MID_TICKER = "mid-cap-ticker"
CHANNEL_BLUE_TICKER = "large-cap-ticker"
CHANNEL_PICK_RESULTS = os.getenv("PICK_RESULTS_CHANNEL", "pick-results")

CHANNEL_SMALL_VOTE = "small-cap"
CHANNEL_MID_VOTE = "mid-cap"
CHANNEL_BLUE_VOTE = "large-cap"

CHANNEL_SMALL_LIVE = "small-cap-live"
CHANNEL_MID_LIVE = "mid-cap-live"
CHANNEL_BLUE_LIVE = "large-cap-live"

CHANNEL_MOD = "mod"
CHANNEL_ADMIN_ACTIONS = "admin-actions"
# Subscribe / PLAYER registration channel (first existing name in guild wins)
CHANNEL_PLAYER = os.getenv("PLAYER_CHANNEL", "player")
PLAYER_CHANNEL_CANDIDATES = tuple(
    n.strip()
    for n in os.getenv(
        "PLAYER_CHANNEL_CANDIDATES",
        "player,subscribe,registration,register,𝐏𝐋𝐀𝐘𝐄𝐑",
    ).split(",")
    if n.strip()
)
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
)


@dataclass(frozen=True)
class StripeSettings:
    secret_key: str | None = os.getenv("STRIPE_SECRET_KEY")
    webhook_secret: str | None = os.getenv("STRIPE_WEBHOOK_SECRET")
    price_id: str | None = os.getenv("STRIPE_MONTHLY_PRICE_ID")
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
