from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import discord
from discord.ext import commands

import database
from cogs.scheduler import SchedulerCog, winner_award_filter_sets
from config import ROLE_WINNER
from discord_names import member_role_keys


class WinnerRepairBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.did_run = False

    async def setup_hook(self) -> None:
        await self.add_cog(SchedulerCog(self))

    async def on_ready(self) -> None:
        if self.did_run:
            return
        self.did_run = True
        scheduler = self.get_cog("SchedulerCog")
        if not isinstance(scheduler, SchedulerCog):
            print("Scheduler cog did not load.", flush=True)
            await self.close()
            return

        week_key = database.week_key_for(datetime.now(timezone.utc))
        expires_at_utc = datetime.now(timezone.utc) + timedelta(days=7)
        expires_at = expires_at_utc.isoformat()
        week_start_iso = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        blocking_roles = {"PLAYER", "ADMIN", "WINNER"}
        for guild in self.guilds:
            await scheduler._expire_winners(guild, end_eligibility_week=True)
            member_ids, player_or_paid = await winner_award_filter_sets(
                guild, week_start_iso=week_start_iso
            )
            winners = database.eligible_winners(
                guild.id,
                week_key,
                guild_member_ids=member_ids,
                player_or_paid_ids=player_or_paid,
            )
            winner_role = discord.utils.get(guild.roles, name=ROLE_WINNER)
            print(f"{guild.name}: eligible winners for {week_key}: {winners}", flush=True)
            awarded: list[int] = []
            for user_id in winners:
                member = guild.get_member(user_id)
                if not member:
                    print(f"skip {user_id}: not in guild", flush=True)
                    continue
                held = member_role_keys(member) & blocking_roles
                if held:
                    print(f"skip {user_id}: holds {'/'.join(sorted(held))}", flush=True)
                    continue
                database.add_winner(guild.id, week_key, user_id, expires_at)
                awarded.append(user_id)
                if winner_role:
                    if winner_role not in member.roles:
                        try:
                            await member.add_roles(winner_role, reason="Repaired weekly stock game winner")
                        except Exception as exc:
                            print(f"Could not add WINNER role to {user_id}: {exc!r}", flush=True)
            await scheduler._publish_last_game_winners(
                guild,
                week_key=week_key,
                winner_ids=awarded,
                valid_until_utc=expires_at_utc,
            )
        await self.close()


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN is missing.", flush=True)
        return 1
    if not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        print("SUPABASE_SERVICE_ROLE_KEY is missing.", flush=True)
        return 1
    bot = WinnerRepairBot()
    await bot.start(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
