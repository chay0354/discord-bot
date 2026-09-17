"""Live check: Discord names/roles/channels + whether new cogs left traces."""
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

from config import (
    CHANNEL_BLUE_LIVE,
    CHANNEL_BLUE_TICKER,
    CHANNEL_BLUE_VOTE,
    CHANNEL_EXTRA_VOTES,
    CHANNEL_MID_LIVE,
    CHANNEL_MID_TICKER,
    CHANNEL_MID_VOTE,
    CHANNEL_PICK_RESULTS,
    CHANNEL_QA,
    CHANNEL_SMALL_LIVE,
    CHANNEL_SMALL_TICKER,
    CHANNEL_SMALL_VOTE,
    EXTRA_VOTES_CHANNEL_CANDIDATES,
    PICK_RESULTS_CHANNEL_CANDIDATES,
    QA_CHANNEL_CANDIDATES,
    ROLE_NPC,
    ROLE_PLAYER,
    ROLE_WINNER,
)
from discord_names import find_game_role, find_text_channel, names_match


class Checker(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents)
        self.fails = 0

    def ok(self, cond: bool, label: str, detail: str = "") -> None:
        mark = "PASS" if cond else "FAIL"
        if not cond:
            self.fails += 1
        extra = f" — {detail}" if detail else ""
        print(f"[{mark}] {label}{extra}", flush=True)

    async def on_ready(self) -> None:
        try:
            me = self.user
            print(f"Logged in as {me} latency={self.latency*1000:.0f}ms", flush=True)
            for guild in self.guilds:
                print(f"Guild {guild.name} ({guild.id})", flush=True)
                bot = guild.me
                self.ok(bot is not None, "bot in guild")
                self.ok(bot.top_role.name == "stock-bot", "bot top role is stock-bot", bot.top_role.name)

                for key, want in (("NPC", ROLE_NPC), ("PLAYER", ROLE_PLAYER), ("WINNER", ROLE_WINNER)):
                    role = find_game_role(guild, key)
                    self.ok(role is not None, f"role {key} exists", role.name if role else "missing")
                    if role:
                        self.ok(names_match(role.name, want) or key in role.name.upper(), f"role {key} has emoji name", role.name)

                expected = [
                    ("small ticker", CHANNEL_SMALL_TICKER),
                    ("mid ticker", CHANNEL_MID_TICKER),
                    ("large ticker", CHANNEL_BLUE_TICKER),
                    ("small vote", CHANNEL_SMALL_VOTE),
                    ("mid vote", CHANNEL_MID_VOTE),
                    ("large vote", CHANNEL_BLUE_VOTE),
                    ("small live", CHANNEL_SMALL_LIVE),
                    ("mid live", CHANNEL_MID_LIVE),
                    ("large live", CHANNEL_BLUE_LIVE),
                    ("pick results", CHANNEL_PICK_RESULTS),
                ]
                for label, name in expected:
                    ch = find_text_channel(guild, name)
                    self.ok(ch is not None, f"channel {label}", ch.name if ch else f"missing {name}")

                pr = find_text_channel(guild, CHANNEL_PICK_RESULTS, *PICK_RESULTS_CHANNEL_CANDIDATES)
                self.ok(
                    pr is not None and names_match(pr.name, CHANNEL_PICK_RESULTS),
                    "pick-results renamed",
                    pr.name if pr else "missing",
                )

                ev = find_text_channel(guild, CHANNEL_EXTRA_VOTES, *EXTRA_VOTES_CHANNEL_CANDIDATES)
                self.ok(ev is not None, "extra-votes channel", ev.name if ev else "missing")
                if ev:
                    posted = False
                    async for msg in ev.history(limit=10):
                        if msg.author == guild.me and msg.embeds:
                            posted = True
                            custom = [c.custom_id for row in (msg.components or []) for c in row.children if getattr(c, "custom_id", None)]
                            self.ok("extra_votes:buy" in custom, "extra-votes button on message", str(custom))
                            break
                    self.ok(posted, "extra-votes embed posted by bot")

                qa = find_text_channel(guild, CHANNEL_QA, *QA_CHANNEL_CANDIDATES)
                usable = qa and qa.permissions_for(guild.me).send_messages
                self.ok(bool(usable), "q-and-a usable by bot", qa.name if qa else "missing")
                if qa and usable:
                    posted = False
                    async for msg in qa.history(limit=10):
                        if msg.author == guild.me and msg.embeds and (msg.embeds[0].title or "") == "Q&A":
                            posted = True
                            custom = [c.custom_id for row in (msg.components or []) for c in row.children if getattr(c, "custom_id", None)]
                            self.ok(any(str(x).startswith("qa:toggle:") for x in custom), "Q&A toggle buttons", str(custom))
                            break
                    self.ok(posted, "Q&A embed posted by bot")

                votes = [
                    find_text_channel(guild, CHANNEL_SMALL_VOTE),
                    find_text_channel(guild, CHANNEL_MID_VOTE),
                    find_text_channel(guild, CHANNEL_BLUE_VOTE),
                ]
                for ch in votes:
                    if not ch:
                        continue
                    view = ch.overwrites_for(guild.default_role).view_channel
                    self.ok(view is False, f"#{ch.name} @everyone hidden", f"view={view}")
            print("=" * 40, flush=True)
            print("LIVE CHECK FAIL" if self.fails else "LIVE CHECK PASS", flush=True)
        finally:
            await self.close()


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN missing")
        return 1
    client = Checker()
    await client.start(token)
    return 1 if client.fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
