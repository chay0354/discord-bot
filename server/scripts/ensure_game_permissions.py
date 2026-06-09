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

from channel_permissions import entry_view_overwrites, role_gated_view_overwrites
from config import (
    CHANNEL_ADMIN_ACTIONS,
    CHANNEL_BLUE_LIVE,
    CHANNEL_BLUE_TICKER,
    CHANNEL_BLUE_VOTE,
    CHANNEL_FINAL_LEADERBOARD,
    CHANNEL_MID_LIVE,
    CHANNEL_MID_TICKER,
    CHANNEL_MID_VOTE,
    CHANNEL_MOD,
    CHANNEL_PICK_RESULTS,
    CHANNEL_RULES,
    CHANNEL_SMALL_LIVE,
    CHANNEL_SMALL_TICKER,
    CHANNEL_SMALL_VOTE,
    CHANNEL_WINNERS,
    CHANNEL_SUBSCRIBE,
    CHANNEL_MANAGE_SUBSCRIPTION,
    RULES_CHANNEL_CANDIDATES,
    SUBSCRIBE_CHANNEL_CANDIDATES,
    ROLE_ADMIN,
    ROLE_NPC,
    ROLE_PLAYER,
    ROLE_WINNER,
)


def _find_channel(guild: discord.Guild, name: str) -> discord.TextChannel | None:
    for channel in guild.text_channels:
        if channel.name.lower() == name.lower():
            return channel
    return None


async def _ensure_role(
    guild: discord.Guild,
    name: str,
    permissions: discord.Permissions,
) -> tuple[discord.Role, bool]:
    role = discord.utils.get(guild.roles, name=name)
    if role:
        try:
            await role.edit(permissions=permissions, reason="Stock bot permission verification")
        except discord.Forbidden:
            print(f"[WARN] Cannot edit role @{name}; check bot role hierarchy.", flush=True)
        return role, False
    role = await guild.create_role(
        name=name,
        permissions=permissions,
        reason="Stock bot permission verification",
    )
    return role, True


async def _ensure_channel(
    guild: discord.Guild,
    name: str,
    overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite],
) -> None:
    channel = _find_channel(guild, name)
    if not channel:
        channel = await guild.create_text_channel(
            name,
            overwrites=overwrites,
            reason="Stock bot permission verification",
        )
        print(f"[CREATE] #{name}", flush=True)
        return
    await channel.edit(overwrites=overwrites, reason="Stock bot permission verification")
    print(f"[UPDATE] #{name}", flush=True)


class PermissionEnsurer(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents)

    async def on_ready(self) -> None:
        assert self.user is not None
        print(f"Logged in as {self.user}", flush=True)
        for guild in self.guilds:
            me = guild.me
            if not me:
                continue
            print(f"Ensuring permissions in {guild.name}", flush=True)

            admin_perms = discord.Permissions(
                manage_channels=True,
                manage_roles=True,
                manage_messages=True,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )
            subscriber_perms = discord.Permissions(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
                use_external_emojis=True,
            )
            npc_role, _ = await _ensure_role(guild, ROLE_NPC, discord.Permissions.none())
            player_role, _ = await _ensure_role(guild, ROLE_PLAYER, subscriber_perms)
            winner_role, created_winner = await _ensure_role(guild, ROLE_WINNER, subscriber_perms)
            admin_role, _ = await _ensure_role(guild, ROLE_ADMIN, admin_perms)
            if created_winner:
                print(f"[CREATE] @{ROLE_WINNER}", flush=True)

            everyone = guild.default_role

            def role_gated() -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
                return role_gated_view_overwrites(guild, npc_role, player_role, winner_role, admin_role, me)

            def entry_public() -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
                return entry_view_overwrites(guild, npc_role, player_role, winner_role, admin_role, me)

            def subscriber_overwrites() -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
                return {
                    everyone: discord.PermissionOverwrite(view_channel=False),
                    npc_role: discord.PermissionOverwrite(view_channel=False),
                    player_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
                    winner_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
                    admin_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                    me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True, read_message_history=True, embed_links=True),
                }

            def mod_overwrites() -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
                return {
                    everyone: discord.PermissionOverwrite(view_channel=False),
                    npc_role: discord.PermissionOverwrite(view_channel=False),
                    player_role: discord.PermissionOverwrite(view_channel=False),
                    winner_role: discord.PermissionOverwrite(view_channel=False),
                    admin_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                    me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True, read_message_history=True, embed_links=True),
                }

            channel_specs = {
                CHANNEL_SMALL_TICKER: subscriber_overwrites(),
                CHANNEL_MID_TICKER: subscriber_overwrites(),
                CHANNEL_BLUE_TICKER: subscriber_overwrites(),
                CHANNEL_PICK_RESULTS: subscriber_overwrites(),
                CHANNEL_SMALL_LIVE: subscriber_overwrites(),
                CHANNEL_MID_LIVE: subscriber_overwrites(),
                CHANNEL_BLUE_LIVE: subscriber_overwrites(),
                CHANNEL_RULES: role_gated(),
                CHANNEL_SMALL_VOTE: entry_public(),
                CHANNEL_MID_VOTE: entry_public(),
                CHANNEL_BLUE_VOTE: entry_public(),
                CHANNEL_MOD: mod_overwrites(),
                CHANNEL_ADMIN_ACTIONS: mod_overwrites(),
                CHANNEL_FINAL_LEADERBOARD: entry_public(),
                CHANNEL_WINNERS: entry_public(),
                CHANNEL_MANAGE_SUBSCRIPTION: entry_public(),
            }
            for name, overwrites in channel_specs.items():
                await _ensure_channel(guild, name, overwrites)

            for name in RULES_CHANNEL_CANDIDATES:
                rules_ch = _find_channel(guild, name)
                if rules_ch:
                    await rules_ch.edit(overwrites=role_gated(), reason="Stock bot permission verification")
                    print(f"[UPDATE] #{rules_ch.name} (rules)", flush=True)
                    break

            subscribe_ch = None
            for name in SUBSCRIBE_CHANNEL_CANDIDATES:
                subscribe_ch = _find_channel(guild, name)
                if subscribe_ch:
                    break
            if subscribe_ch:
                await subscribe_ch.edit(overwrites=entry_public(), reason="Stock bot permission verification")
                print(f"[UPDATE] #{subscribe_ch.name} (subscribe)", flush=True)
            else:
                await _ensure_channel(guild, CHANNEL_SUBSCRIBE, entry_public())
        await self.close()


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN is missing.", flush=True)
        return 1
    client = PermissionEnsurer()
    await client.start(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
