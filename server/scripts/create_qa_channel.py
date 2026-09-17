"""Create a bot-managed #q-and-a and post the FAQ embed."""
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
load_dotenv(ROOT.parent / ".env")

import discord

from cogs.qa_channel import QAView, _DEFAULT_ITEMS, qa_embed
from config import CHANNEL_QA, QA_CHANNEL_CANDIDATES
from discord_names import deny_pin_and_slowmode, find_game_role, find_text_channel


class Maker(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        super().__init__(intents=intents)

    async def on_ready(self) -> None:
        try:
            for guild in self.guilds:
                me = guild.me
                existing = find_text_channel(guild, CHANNEL_QA, *QA_CHANNEL_CANDIDATES)
                if existing and me and existing.permissions_for(me).send_messages:
                    print(f"already have #{existing.name}", flush=True)
                    continue
                no_pin = deny_pin_and_slowmode()
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(
                        view_channel=False, send_messages=False, **no_pin
                    ),
                }
                for key, send in (("NPC", False), ("PLAYER", False), ("WINNER", False), ("ADMIN", True)):
                    role = find_game_role(guild, key)
                    if role:
                        overwrites[role] = discord.PermissionOverwrite(
                            view_channel=True,
                            send_messages=send,
                            read_message_history=True,
                            **no_pin,
                        )
                if me:
                    overwrites[me] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, manage_messages=True
                    )
                parent = next(
                    (c.category for c in guild.text_channels if c.name.lower() == "admin-actions" and c.category),
                    None,
                )
                ch = await guild.create_text_channel(
                    CHANNEL_QA, overwrites=overwrites, category=parent, reason="Bot-managed Q&A"
                )
                await ch.send(embed=qa_embed(_DEFAULT_ITEMS), view=QAView(_DEFAULT_ITEMS))
                print(f"created #{ch.name} id={ch.id}", flush=True)
        finally:
            await self.close()


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN missing")
        return 1
    await Maker().start(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
