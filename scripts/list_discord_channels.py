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


def _safe(text: str) -> str:
    return text.encode("ascii", "backslashreplace").decode("ascii")


class ChannelLister(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        super().__init__(intents=intents)

    async def on_ready(self) -> None:
        for guild in self.guilds:
            print(f"Guild: {_safe(guild.name)} ({guild.id})", flush=True)
            channels = await guild.fetch_channels()
            categories = {channel.id: channel.name for channel in channels if isinstance(channel, discord.CategoryChannel)}
            for channel in channels:
                if not isinstance(channel, discord.TextChannel):
                    continue
                category = categories.get(channel.category_id or 0, "NO CATEGORY")
                print(f"[{_safe(category)}] #{_safe(channel.name)} ({channel.id})", flush=True)
        await self.close()


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN is missing.", flush=True)
        return 1
    client = ChannelLister()
    await client.start(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
