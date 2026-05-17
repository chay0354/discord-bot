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
from discord.ext import commands

import database
from cogs.admin_actions import AdminActionsView, admin_actions_embed
from cogs.scheduler import SchedulerCog
from cogs.weekly_picks import WeeklyPicksCog, WeeklyVotingView
from config import CHANNEL_ADMIN_ACTIONS, ROLE_ADMIN


def _find_text_channel(guild: discord.Guild, name: str) -> discord.TextChannel | None:
    for channel in guild.text_channels:
        if channel.name.lower() == name.lower():
            return channel
    return None


class AdminActionsPanelBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.did_setup = False

    async def setup_hook(self) -> None:
        await self.add_cog(WeeklyPicksCog(self))
        await self.add_cog(SchedulerCog(self))
        self.add_view(AdminActionsView(self))

    async def on_ready(self) -> None:
        if self.did_setup:
            return
        self.did_setup = True
        assert self.user is not None
        print(f"Logged in as {self.user} ({self.user.id})", flush=True)
        if not self.guilds:
            print("No guilds found.", flush=True)
            await self.close()
            return

        for guild in self.guilds:
            me = guild.me
            if not me:
                continue
            admin_role = discord.utils.get(guild.roles, name=ROLE_ADMIN)
            overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                me: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    embed_links=True,
                    read_message_history=True,
                    manage_messages=True,
                ),
            }
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    embed_links=True,
                )

            channel = _find_text_channel(guild, CHANNEL_ADMIN_ACTIONS)
            if channel:
                await channel.edit(overwrites=overwrites, reason="Stock bot admin actions setup")
            else:
                channel = await guild.create_text_channel(
                    CHANNEL_ADMIN_ACTIONS,
                    overwrites=overwrites,
                    reason="Stock bot admin actions setup",
                )

            async for message in channel.history(limit=50):
                if message.author == guild.me:
                    await message.delete()
            await channel.send(embed=admin_actions_embed(), view=AdminActionsView(self))
            print(f"Admin actions panel posted in #{CHANNEL_ADMIN_ACTIONS}", flush=True)

            scheduler = self.get_cog("SchedulerCog")
            if isinstance(scheduler, SchedulerCog):
                await scheduler._refresh_last_game_winners_from_db(guild)
                week_key = database.week_key_for()
                cycle = database.ensure_cycle(guild.id, week_key)
                if cycle.get("voting_open"):
                    selected = database.list_tickers(guild.id, week_key)
                    for idx, category in enumerate(("small", "mid", "blue")):
                        tickers = selected.get(category, [])
                        if tickers:
                            self.add_view(WeeklyVotingView(category_idx=idx, tickers=tickers))
                    print(f"Voting button handlers registered in {guild.name}", flush=True)
                else:
                    await scheduler._reopen_ticker_channels(guild)
                    print(f"Pre-Voting picker messages reposted in {guild.name}", flush=True)


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN is missing.")
        return 1
    if not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        print("SUPABASE_SERVICE_ROLE_KEY is missing.")
        return 1
    bot = AdminActionsPanelBot()
    await bot.start(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
