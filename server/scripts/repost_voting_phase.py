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

from cogs.scheduler import SchedulerCog
from cogs.weekly_picks import WeeklyPicksCog


class VotingPhaseReposter(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.did_run = False

    async def setup_hook(self) -> None:
        await self.add_cog(WeeklyPicksCog(self))
        await self.add_cog(SchedulerCog(self))

    async def on_ready(self) -> None:
        if self.did_run:
            return
        self.did_run = True
        assert self.user is not None
        print(f"Logged in as {self.user} ({self.user.id})", flush=True)

        scheduler = self.get_cog("SchedulerCog")
        if not isinstance(scheduler, SchedulerCog):
            print("Scheduler cog did not load.", flush=True)
            await self.close()
            return

        for guild in self.guilds:
            updated, counts = await scheduler._monday_open_one_guild(guild)
            print(
                f"Reposted voting phase in {guild.name}: updated={updated}, selected_context_counts={counts}",
                flush=True,
            )
        await self.close()


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN is missing.")
        return 1
    if not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        print("SUPABASE_SERVICE_ROLE_KEY is missing.")
        return 1
    bot = VotingPhaseReposter()
    await bot.start(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
