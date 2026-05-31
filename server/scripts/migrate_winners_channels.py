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

from config import CHANNEL_FINAL_LEADERBOARD, CHANNEL_WINNERS


OLD_TO_NEW = {
    "leaderboard": CHANNEL_FINAL_LEADERBOARD,
    "1-ranked": CHANNEL_WINNERS,
}


def _find_text_channel(guild: discord.Guild, name: str) -> discord.TextChannel | None:
    for channel in guild.text_channels:
        if channel.name.lower() == name.lower():
            return channel
    return None


def _safe(text: str) -> str:
    return text.encode("ascii", "backslashreplace").decode("ascii")


async def _copy_recent_content(source: discord.TextChannel, target: discord.TextChannel) -> None:
    messages: list[discord.Message] = []
    async for message in source.history(limit=10, oldest_first=False):
        if message.content or message.embeds:
            messages.append(message)
    for message in reversed(messages):
        content = message.content or None
        embeds = message.embeds[:10]
        if content and embeds:
            await target.send(content=content, embeds=embeds)
        elif embeds:
            await target.send(embeds=embeds)
        elif content:
            await target.send(content)


class WinnersChannelMigrator(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.message_content = True
        super().__init__(intents=intents)

    async def on_ready(self) -> None:
        for guild in self.guilds:
            print(f"Guild: {_safe(guild.name)}", flush=True)
            fetched = await guild.fetch_channels()
            text_channels = [channel for channel in fetched if isinstance(channel, discord.TextChannel)]
            for old_name, new_name in OLD_TO_NEW.items():
                old_channel = next((channel for channel in text_channels if channel.name.lower() == old_name.lower()), None)
                new_channel = next((channel for channel in text_channels if channel.name.lower() == new_name.lower()), None)
                if not new_channel:
                    print(f"[WARN] Target channel missing: {_safe(new_name)!r}", flush=True)
                    continue
                if not old_channel:
                    print(f"[OK] Old channel already absent: #{old_name}", flush=True)
                    continue
                await _copy_recent_content(old_channel, new_channel)
                await old_channel.delete(reason="Using WINNERS category channel instead")
                print(f"[MOVED+DELETED] #{old_name} -> #{_safe(new_channel.name)}", flush=True)
        await self.close()


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN is missing.", flush=True)
        return 1
    client = WinnersChannelMigrator()
    await client.start(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
