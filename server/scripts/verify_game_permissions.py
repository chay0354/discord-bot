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

from config import (
    CHANNEL_BLUE_LIVE,
    CHANNEL_BLUE_TICKER,
    CHANNEL_MID_LIVE,
    CHANNEL_MID_TICKER,
    CHANNEL_PICK_RESULTS,
    CHANNEL_SMALL_LIVE,
    CHANNEL_SMALL_TICKER,
    NPC_VOTES_PER_CATEGORY,
    PLAYER_VOTES_PER_CATEGORY,
    ROLE_ADMIN,
    ROLE_NPC,
    ROLE_PLAYER,
    ROLE_WINNER,
)
from cogs.weekly_picks import _can_vote, _vote_limit_for


SUBSCRIBER_ONLY_CHANNELS = (
    CHANNEL_SMALL_TICKER,
    CHANNEL_MID_TICKER,
    CHANNEL_BLUE_TICKER,
    CHANNEL_PICK_RESULTS,
    CHANNEL_SMALL_LIVE,
    CHANNEL_MID_LIVE,
    CHANNEL_BLUE_LIVE,
    "👑𝙑𝙄𝙋-𝗖𝗵𝗮𝘁👑",
)


def _role(guild: discord.Guild, name: str) -> discord.Role | None:
    return discord.utils.get(guild.roles, name=name)


def _channel(guild: discord.Guild, name: str) -> discord.TextChannel | None:
    return discord.utils.get(guild.text_channels, name=name)


class GamePermissionVerifier(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents)
        self.exit_code = 0

    def _fail(self, message: str) -> None:
        self.exit_code = 1
        print(f"[FAIL] {message}", flush=True)

    def _pass(self, message: str) -> None:
        print(f"[PASS] {message}", flush=True)

    async def on_ready(self) -> None:
        print("Game Permission Verification", flush=True)
        print("=" * 60, flush=True)
        for guild in self.guilds:
            print(f"Guild: {guild.name} ({guild.id})", flush=True)
            roles = {
                ROLE_NPC: _role(guild, ROLE_NPC),
                ROLE_PLAYER: _role(guild, ROLE_PLAYER),
                ROLE_WINNER: _role(guild, ROLE_WINNER),
                ROLE_ADMIN: _role(guild, ROLE_ADMIN),
            }
            for name, role in roles.items():
                if role:
                    self._pass(f"Role exists: @{name}")
                else:
                    self._fail(f"Missing role: @{name}")
            if any(role is None for role in roles.values()):
                continue

            npc = roles[ROLE_NPC]
            player = roles[ROLE_PLAYER]
            winner = roles[ROLE_WINNER]

            # Vote limits use a minimal object with a .roles list, matching discord.Member shape used by the helper.
            class FakeMember:
                def __init__(self, role: discord.Role):
                    self.roles = [role]

            class NoRoleMember:
                roles: list = []

            vote_checks = (
                ("NPC vote limit", _vote_limit_for(FakeMember(npc)), NPC_VOTES_PER_CATEGORY),
                ("PLAYER vote limit", _vote_limit_for(FakeMember(player)), PLAYER_VOTES_PER_CATEGORY),
                ("WINNER vote limit", _vote_limit_for(FakeMember(winner)), PLAYER_VOTES_PER_CATEGORY),
                ("No-role vote limit", _vote_limit_for(NoRoleMember()), 0),
                ("No-role cannot vote", _can_vote(NoRoleMember()), False),
                ("NPC can vote", _can_vote(FakeMember(npc)), True),
            )
            for label, actual, expected in vote_checks:
                if actual == expected:
                    self._pass(f"{label}: {actual}")
                else:
                    self._fail(f"{label}: expected {expected}, got {actual}")

            for channel_name in SUBSCRIBER_ONLY_CHANNELS:
                channel = _channel(guild, channel_name)
                if not channel:
                    self._fail(f"Missing subscriber-only channel: #{channel_name}")
                    continue
                everyone_can_view = channel.permissions_for(guild.default_role).view_channel
                npc_can_view = channel.permissions_for(npc).view_channel
                player_can_view = channel.permissions_for(player).view_channel
                winner_can_view = channel.permissions_for(winner).view_channel
                if not everyone_can_view and not npc_can_view and player_can_view and winner_can_view:
                    self._pass(f"Subscriber visibility correct: #{channel_name}")
                else:
                    self._fail(
                        f"Subscriber visibility wrong for #{channel_name}: "
                        f"everyone={everyone_can_view}, NPC={npc_can_view}, PLAYER={player_can_view}, WINNER={winner_can_view}"
                    )

            for role_name, role in ((ROLE_PLAYER, player), (ROLE_WINNER, winner)):
                perms = role.permissions
                required = {
                    "attach_files": perms.attach_files,
                    "embed_links": perms.embed_links,
                    "use_external_emojis": perms.use_external_emojis,
                    "send_messages": perms.send_messages,
                }
                missing = [name for name, ok in required.items() if not ok]
                if missing:
                    self._fail(f"@{role_name} missing media/chat permissions: {', '.join(missing)}")
                else:
                    self._pass(f"@{role_name} has media/chat permissions available")
        print("=" * 60, flush=True)
        await self.close()


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN is missing.", flush=True)
        return 1
    client = GamePermissionVerifier()
    await client.start(token)
    return client.exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
