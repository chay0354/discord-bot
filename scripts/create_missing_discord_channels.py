from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
import discord

from config import (
    CHANNEL_FINAL_LEADERBOARD,
    CHANNEL_PICK_RESULTS,
    CHANNEL_WINNERS,
)


load_dotenv(ROOT / ".env")

MISSING_CHANNELS = (
    CHANNEL_PICK_RESULTS,
    CHANNEL_FINAL_LEADERBOARD,
    CHANNEL_WINNERS,
)


class MissingChannelCreator(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        super().__init__(intents=intents)
        self.exit_code = 0

    async def on_ready(self) -> None:
        assert self.user is not None
        print(f"Logged in as {self.user} ({self.user.id})")
        if not self.guilds:
            print("[FAIL] Bot is not connected to any guild.")
            self.exit_code = 1
            await self.close()
            return

        for guild in self.guilds:
            print(f"Guild: {guild.name} ({guild.id})")
            me = guild.me
            if not me:
                print("[FAIL] Could not resolve bot member.")
                self.exit_code = 1
                continue
            if not me.guild_permissions.manage_channels:
                print("[FAIL] Bot does not have Manage Channels, so it cannot create channels.")
                self.exit_code = 1
                continue

            existing = {channel.name for channel in guild.text_channels}
            for channel_name in MISSING_CHANNELS:
                if channel_name in existing:
                    print(f"[PASS] #{channel_name} already exists")
                    continue
                try:
                    channel = await guild.create_text_channel(
                        name=channel_name,
                        reason="Stock bot required channel setup",
                    )
                    print(f"[CREATED] #{channel.name}")
                except discord.Forbidden:
                    print(f"[FAIL] Forbidden creating #{channel_name}; missing Manage Channels or role hierarchy.")
                    self.exit_code = 1
                except discord.HTTPException as exc:
                    print(f"[FAIL] Discord API error creating #{channel_name}: {exc}")
                    self.exit_code = 1

        await self.close()


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("[FAIL] DISCORD_TOKEN is not set.")
        return 1
    client = MissingChannelCreator()
    try:
        await client.start(token)
    except discord.LoginFailure:
        print("[FAIL] Discord rejected the token.")
        return 1
    return client.exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
