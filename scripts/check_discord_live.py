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
    ALL_REQUIRED_CHANNELS,
    ROLE_ADMIN,
    ROLE_NPC,
    ROLE_PLAYER,
    ROLE_WINNER,
)


load_dotenv(ROOT / ".env")


REQUIRED_ROLES = (ROLE_NPC, ROLE_PLAYER, ROLE_WINNER, ROLE_ADMIN)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


class LiveDiscordCheck(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.exit_code = 0

    async def on_ready(self) -> None:
        assert self.user is not None
        print("Discord Live Check")
        print("=" * 60)
        print(f"Logged in as: {self.user} ({self.user.id})")
        print(f"Connected guilds: {len(self.guilds)}")
        if not self.guilds:
            print("[FAIL] Bot is not in any Discord server.")
            self.exit_code = 1
            await self.close()
            return

        for guild in self.guilds:
            print("-" * 60)
            print(f"Guild: {guild.name} ({guild.id})")
            me = guild.me
            if not me:
                print("[FAIL] Could not resolve bot member in guild.")
                self.exit_code = 1
                continue

            guild_perms = me.guild_permissions
            permission_checks = {
                "manage_roles": guild_perms.manage_roles,
                "manage_channels": guild_perms.manage_channels,
                "manage_messages": guild_perms.manage_messages,
                "send_messages": guild_perms.send_messages,
                "embed_links": guild_perms.embed_links,
                "read_message_history": guild_perms.read_message_history,
            }
            missing_perms = [name for name, ok in permission_checks.items() if not ok]
            if missing_perms:
                print(f"[WARN] Missing guild-level permissions: {', '.join(missing_perms)}")
            else:
                print("[PASS] Required guild-level permissions are present.")

            role_names = {role.name for role in guild.roles}
            missing_roles = [name for name in REQUIRED_ROLES if name not in role_names]
            if missing_roles:
                print(f"[WARN] Missing roles: {', '.join(missing_roles)}")
            else:
                print("[PASS] Required roles exist.")

            channel_names = {channel.name for channel in guild.text_channels}
            missing_channels = [name for name in ALL_REQUIRED_CHANNELS if name not in channel_names]
            if missing_channels:
                print(f"[WARN] Missing channels: {', '.join(missing_channels)}")
            else:
                print("[PASS] Required channels exist.")

            writable_required = []
            for channel_name in ALL_REQUIRED_CHANNELS:
                channel = discord.utils.get(guild.text_channels, name=channel_name)
                if not channel:
                    continue
                perms = channel.permissions_for(me)
                if not (perms.view_channel and perms.send_messages and perms.embed_links):
                    writable_required.append(
                        f"#{channel_name}(view={_yes_no(perms.view_channel)},send={_yes_no(perms.send_messages)},embed={_yes_no(perms.embed_links)})"
                    )
            if writable_required:
                print(f"[WARN] Bot cannot fully write/embed in: {', '.join(writable_required)}")
            else:
                print("[PASS] Bot can view/send/embed in required existing channels.")

        print("=" * 60)
        print("Live Discord API check complete.")
        await self.close()


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("[FAIL] DISCORD_TOKEN is not set.")
        return 1
    client = LiveDiscordCheck()
    try:
        await client.start(token)
    except discord.LoginFailure:
        print("[FAIL] Discord rejected the token.")
        return 1
    return client.exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
