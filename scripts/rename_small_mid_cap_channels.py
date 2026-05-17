from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import discord

from config import (
    CHANNEL_MID_LIVE,
    CHANNEL_MID_TICKER,
    CHANNEL_MID_VOTE,
    CHANNEL_SMALL_LIVE,
    CHANNEL_SMALL_TICKER,
    CHANNEL_SMALL_VOTE,
)

RENAMES = {
    "small-caps-ticker": CHANNEL_SMALL_TICKER,
    "mid-caps-ticker": CHANNEL_MID_TICKER,
    "small-caps": CHANNEL_SMALL_VOTE,
    "mid-caps": CHANNEL_MID_VOTE,
    "small-caps-live": CHANNEL_SMALL_LIVE,
    "mid-caps-live": CHANNEL_MID_LIVE,
}


class ChannelRenamer(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        super().__init__(intents=intents)

    async def on_ready(self) -> None:
        for guild in self.guilds:
            print(f"Guild: {guild.name}", flush=True)
            for old_name, new_name in RENAMES.items():
                old_channel = discord.utils.get(guild.text_channels, name=old_name)
                existing_new = discord.utils.get(guild.text_channels, name=new_name)
                if old_channel:
                    await old_channel.edit(
                        name=new_name,
                        reason="Rename *-caps* channels to *-cap*",
                    )
                    print(f"Renamed #{old_name} -> #{new_name}", flush=True)
                elif existing_new:
                    print(f"Already renamed: #{new_name}", flush=True)
                else:
                    print(f"Missing: #{old_name} / #{new_name}", flush=True)
        await self.close()


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN is missing.", flush=True)
        return 1
    await ChannelRenamer().start(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
