from __future__ import annotations

import asyncio
import os
import sys
import traceback
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import discord
from discord.ext import commands

import database
from cogs.scheduler import SchedulerCog, _from_et_local_to_utc, _to_et
from cogs.weekly_picks import WeeklyPicksCog


TEST_TICKERS = {
    "small": [("SOUN", 1_500_000_000, "NASDAQ"), ("OPEN", 1_300_000_000, "NASDAQ")],
    "mid": [("CROX", 5_000_000_000, "NASDAQ"), ("ETSY", 6_000_000_000, "NASDAQ")],
    "blue": [("AAPL", 3_000_000_000_000, "NASDAQ"), ("MSFT", 3_000_000_000_000, "NASDAQ")],
}


def _next_friday_close_utc(now_utc: datetime) -> datetime:
    now_et = _to_et(now_utc)
    days = (4 - now_et.weekday()) % 7
    target_date = (now_et + timedelta(days=days)).date()
    local_target = datetime.combine(target_date, dtime(16, 0))
    target_utc = _from_et_local_to_utc(local_target)
    if target_utc <= now_utc:
        target_utc = _from_et_local_to_utc(local_target + timedelta(days=7))
    return target_utc


class LiveTestGameBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.did_setup = False
        self.scheduler_helper: SchedulerCog | None = None

    async def setup_hook(self) -> None:
        await self.add_cog(WeeklyPicksCog(self))
        self.scheduler_helper = SchedulerCog(self)

    async def on_ready(self) -> None:
        try:
            if self.did_setup:
                return
            self.did_setup = True
            assert self.user is not None
            print(f"Logged in as {self.user} ({self.user.id})", flush=True)
            if not self.guilds:
                print("No guilds found.", flush=True)
                await self.close()
                return

            guild = self.guilds[0]
            print(f"Setting up live test game in {guild.name} ({guild.id})", flush=True)
            now_utc = datetime.now(timezone.utc)
            week_key = database.week_key_for(now_utc)
            database.init_db()
            print("Supabase connection OK", flush=True)

            # Reset this week's test game state so repeated runs are predictable.
            print("Clearing current test game state...", flush=True)
            database._request("DELETE", "votes", query=f"?guild_id=eq.{guild.id}&week_key=eq.{week_key}")
            database._request("DELETE", "ticker_picks", query=f"?guild_id=eq.{guild.id}&week_key=eq.{week_key}")
            database._request("DELETE", "winners", query=f"?guild_id=eq.{guild.id}&week_key=eq.{week_key}")
            database._request("DELETE", "game_cycles", query=f"?guild_id=eq.{guild.id}&week_key=eq.{week_key}")

            print("Creating cycle...", flush=True)
            database.ensure_cycle(guild.id, week_key)
            database.set_cycle_phase(
                guild.id,
                week_key,
                status="ticker_selection",
                ticker_selection_open=True,
                voting_open=False,
                early_window_open=False,
            )

            fake_user_base = int(self.user.id)
            offset = 1
            print("Seeding tickers...", flush=True)
            for category, rows in TEST_TICKERS.items():
                for ticker, market_cap, exchange in rows:
                    submitter_id = fake_user_base + offset
                    offset += 1
                    database.upsert_user(submitter_id, username=f"test-seed-{ticker}")
                    ok, reason = database.add_ticker_pick(
                        guild.id,
                        week_key,
                        category,
                        ticker,
                        submitter_id,
                        market_cap=market_cap,
                        exchange=exchange,
                    )
                    if not ok:
                        print(f"Seed warning for {category} {ticker}: {reason}", flush=True)

            if not self.scheduler_helper:
                print("Scheduler helper was not initialized.", flush=True)
                await self.close()
                return

            print("Posting voting messages...", flush=True)
            updated, counts = await self.scheduler_helper._monday_open_one_guild(guild)
            close_utc = _next_friday_close_utc(now_utc)
            print(f"Live test game started. Weekly channels updated: {updated}; counts={counts}", flush=True)
            print(f"Scheduled test close at {close_utc:%Y-%m-%d %H:%M:%S} UTC", flush=True)
            self.loop.create_task(self._close_later(guild, close_utc), name="live_test_friday_close")
        except Exception:
            traceback.print_exc()
            await self.close()

    async def _close_later(self, guild: discord.Guild, close_utc: datetime) -> None:
        seconds = max(1, int((close_utc - datetime.now(timezone.utc)).total_seconds()))
        await asyncio.sleep(seconds)
        if self.scheduler_helper:
            await self.scheduler_helper._friday_close_one_guild(guild)
        await self.close()


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN is missing.")
        return 1
    if not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        print("SUPABASE_SERVICE_ROLE_KEY is missing.")
        return 1
    bot = LiveTestGameBot()
    await bot.start(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
