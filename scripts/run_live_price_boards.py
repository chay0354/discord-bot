from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import discord

import database
from config import (
    CHANNEL_BLUE_TICKER,
    CHANNEL_MID_TICKER,
    CHANNEL_SMALL_TICKER,
    CATEGORY_TITLES,
)
from services.finnhub_client import format_quote, get_quotes


PRICE_CHANNELS = {
    "small": CHANNEL_SMALL_TICKER,
    "mid": CHANNEL_MID_TICKER,
    "blue": CHANNEL_BLUE_TICKER,
}

FALLBACK_TICKERS = {
    "small": ["SOUN", "OPEN"],
    "mid": ["CROX", "ETSY"],
    "blue": ["AAPL", "MSFT"],
}


def _find_text_channel(guild: discord.Guild, name: str) -> discord.TextChannel | None:
    for channel in guild.text_channels:
        if channel.name.lower() == name.lower():
            return channel
    return None


def _price_embed(category: str, tickers: list[str]) -> discord.Embed:
    quotes = get_quotes(tickers)
    lines = [format_quote(ticker, quotes.get(ticker)) for ticker in tickers]
    embed = discord.Embed(
        title=f"LIVE STOCK PRICES — {CATEGORY_TITLES[category]}",
        description="\n".join(lines) if lines else "No stocks selected.",
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"Prices by Finnhub. Last refresh: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC")
    return embed


class LivePriceBoardBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        super().__init__(intents=intents)
        self.message_ids: dict[tuple[int, str], int] = {}

    async def on_ready(self) -> None:
        assert self.user is not None
        print(f"Logged in as {self.user} ({self.user.id})", flush=True)
        if not self.guilds:
            print("No guilds found.", flush=True)
            await self.close()
            return
        await self._setup_once()
        self.loop.create_task(self._refresh_loop(), name="live_price_boards_refresh")

    async def _setup_once(self) -> None:
        for guild in self.guilds:
            week_key = database.week_key_for()
            stored = database.list_tickers(guild.id, week_key)
            for category, channel_name in PRICE_CHANNELS.items():
                channel = _find_text_channel(guild, channel_name)
                if not channel:
                    print(f"Missing channel #{channel_name}", flush=True)
                    continue
                try:
                    async for message in channel.history(limit=100):
                        if message.author == guild.me:
                            await message.delete()
                except Exception as exc:
                    print(f"Could not clear #{channel_name}: {exc}", flush=True)
                tickers = stored.get(category) or FALLBACK_TICKERS[category]
                sent = await channel.send(embed=await asyncio.to_thread(_price_embed, category, tickers))
                self.message_ids[(guild.id, category)] = sent.id
                print(f"Posted live prices in #{channel_name}: {', '.join(tickers)}", flush=True)

    async def _refresh_loop(self) -> None:
        while not self.is_closed():
            await asyncio.sleep(60)
            for guild in self.guilds:
                week_key = database.week_key_for()
                stored = database.list_tickers(guild.id, week_key)
                for category, channel_name in PRICE_CHANNELS.items():
                    channel = _find_text_channel(guild, channel_name)
                    if not channel:
                        continue
                    message_id = self.message_ids.get((guild.id, category))
                    if not message_id:
                        continue
                    tickers = stored.get(category) or FALLBACK_TICKERS[category]
                    try:
                        message = await channel.fetch_message(message_id)
                        await message.edit(embed=await asyncio.to_thread(_price_embed, category, tickers))
                    except Exception as exc:
                        print(f"Could not refresh #{channel_name}: {exc}", flush=True)


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN is missing.")
        return 1
    if not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        print("SUPABASE_SERVICE_ROLE_KEY is missing.")
        return 1
    bot = LivePriceBoardBot()
    await bot.start(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
