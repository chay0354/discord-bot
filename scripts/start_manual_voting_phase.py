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
from discord.ext import commands

import database
from cogs.scheduler import SchedulerCog
from cogs.weekly_picks import WeeklyPicksCog


MANUAL_TICKERS: dict[str, list[tuple[str, int, str]]] = {
    "small": [
        ("PRTS", 450_000_000, "NASDAQ"),
        ("BLNK", 250_000_000, "NASDAQ"),
        ("RZLV", 300_000_000, "NASDAQ"),
        ("SSTI", 500_000_000, "NASDAQ"),
        ("SPCB", 40_000_000, "NASDAQ"),
        ("KULR", 90_000_000, "NYSE"),
        ("GPRO", 280_000_000, "NASDAQ"),
        ("BB", 1_900_000_000, "NYSE"),
        ("VUZI", 80_000_000, "NASDAQ"),
        ("MVIS", 220_000_000, "NASDAQ"),
        ("DDD", 400_000_000, "NYSE"),
        ("VLD", 120_000_000, "NYSE"),
        ("NNDM", 380_000_000, "NASDAQ"),
        ("WULF", 1_800_000_000, "NASDAQ"),
        ("HUT", 1_600_000_000, "NASDAQ"),
        ("BTBT", 500_000_000, "NASDAQ"),
        ("BITF", 1_000_000_000, "NASDAQ"),
        ("SDIG", 70_000_000, "NASDAQ"),
        ("CAN", 400_000_000, "NASDAQ"),
        ("HIVE", 500_000_000, "NASDAQ"),
    ],
    "mid": [
        ("CROX", 6_000_000_000, "NASDAQ"),
        ("ETSY", 7_000_000_000, "NASDAQ"),
        ("FIVE", 5_000_000_000, "NASDAQ"),
        ("WING", 10_000_000_000, "NASDAQ"),
        ("SHAK", 4_000_000_000, "NYSE"),
        ("CHWY", 12_000_000_000, "NYSE"),
        ("BROS", 5_000_000_000, "NYSE"),
        ("OLLI", 7_000_000_000, "NASDAQ"),
        ("WEN", 4_000_000_000, "NASDAQ"),
        ("DIN", 1_000_000_000, "NYSE"),
        ("TXRH", 12_000_000_000, "NASDAQ"),
        ("CAKE", 2_000_000_000, "NASDAQ"),
        ("PLAY", 1_500_000_000, "NASDAQ"),
        ("DRI", 23_000_000_000, "NYSE"),
        ("YETI", 3_500_000_000, "NYSE"),
        ("COLM", 5_000_000_000, "NASDAQ"),
        ("DECK", 25_000_000_000, "NYSE"),
        ("SKX", 10_000_000_000, "NYSE"),
        ("BOOT", 4_000_000_000, "NYSE"),
        ("SIG", 4_000_000_000, "NYSE"),
    ],
    "blue": [
        ("AAPL", 3_000_000_000_000, "NASDAQ"),
        ("MSFT", 3_000_000_000_000, "NASDAQ"),
        ("NVDA", 3_000_000_000_000, "NASDAQ"),
        ("GOOGL", 2_000_000_000_000, "NASDAQ"),
        ("AMZN", 2_000_000_000_000, "NASDAQ"),
        ("META", 1_000_000_000_000, "NASDAQ"),
        ("TSLA", 800_000_000_000, "NASDAQ"),
        ("AVGO", 1_000_000_000_000, "NASDAQ"),
        ("JPM", 600_000_000_000, "NYSE"),
        ("V", 600_000_000_000, "NYSE"),
        ("LLY", 800_000_000_000, "NYSE"),
        ("UNH", 500_000_000_000, "NYSE"),
        ("MA", 450_000_000_000, "NYSE"),
        ("XOM", 500_000_000_000, "NYSE"),
        ("COST", 400_000_000_000, "NASDAQ"),
        ("WMT", 700_000_000_000, "NYSE"),
        ("PG", 400_000_000_000, "NYSE"),
        ("HD", 350_000_000_000, "NYSE"),
        ("ORCL", 400_000_000_000, "NYSE"),
        ("JNJ", 350_000_000_000, "NYSE"),
    ],
}


class ManualVotingBot(commands.Bot):
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
        week_key = database.week_key_for(datetime.now(timezone.utc))
        database.init_db()

        scheduler = self.get_cog("SchedulerCog")
        if not isinstance(scheduler, SchedulerCog):
            print("Scheduler cog did not load.", flush=True)
            await self.close()
            return

        for guild in self.guilds:
            print(f"Starting manual voting phase in {guild.name} ({guild.id}) for {week_key}", flush=True)
            now_iso = database.utc_now_iso()
            database.reset_week_game_data(guild.id, week_key)
            database._request("DELETE", "winners", query=f"?guild_id=eq.{guild.id}&week_key=eq.{database._eq(week_key)}")
            database.ensure_cycle(guild.id, week_key)
            database.set_cycle_phase(
                guild.id,
                week_key,
                status="ticker_selection",
                ticker_selection_open=True,
                voting_open=False,
                early_window_open=False,
            )
            database._request(
                "PATCH",
                "game_cycles",
                query=f"?guild_id=eq.{guild.id}&week_key=eq.{database._eq(week_key)}",
                json_body={"friday_close_at": None},
            )
            print("Cleared existing picks/votes and opened temporary seed phase.", flush=True)

            # Stay within PostgreSQL bigint range while keeping seeded users distinct.
            submitter_id = int(self.user.id)
            users: list[dict[str, object]] = []
            picks: list[dict[str, object]] = []
            seeded: dict[str, int] = {}
            for category, tickers in MANUAL_TICKERS.items():
                seeded[category] = len(tickers)
                for ticker, market_cap, exchange in tickers:
                    submitter_id += 1
                    users.append(
                        {
                            "discord_id": submitter_id,
                            "username": f"manual-seed-{category}-{ticker}",
                            "created_at": now_iso,
                            "updated_at": now_iso,
                        }
                    )
                    picks.append(
                        {
                            "guild_id": guild.id,
                            "week_key": week_key,
                            "category": category,
                            "ticker": ticker,
                            "market_cap": market_cap,
                            "exchange": exchange,
                            "submitted_by": submitter_id,
                            "submitted_at": now_iso,
                        }
                    )

            database._request(
                "POST",
                "users",
                query="?on_conflict=discord_id",
                json_body=users,
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )
            database._request(
                "POST",
                "ticker_picks",
                json_body=picks,
                headers={"Prefer": "return=minimal"},
            )
            print(f"Seeded ticker rows: {seeded}", flush=True)

            updated, counts = await scheduler._monday_open_one_guild(guild)
            database.log_event(
                guild.id,
                "manual_seeded_voting_phase",
                {"week_key": week_key, "seeded": seeded, "updated_channels": updated, "counts": counts},
            )
            print(f"Manual voting phase live: updated={updated}; counts={counts}; seeded={seeded}", flush=True)

        await self.close()


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN is missing.", flush=True)
        return 1
    if not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        print("SUPABASE_SERVICE_ROLE_KEY is missing.", flush=True)
        return 1
    bot = ManualVotingBot()
    await bot.start(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
