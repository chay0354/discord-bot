"""Finish leftover Part D/E Discord work: vote @everyone deny, QA access, bot-admin perms."""
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

from config import CHANNEL_BLUE_VOTE, CHANNEL_MID_VOTE, CHANNEL_SMALL_VOTE, CHANNEL_QA
from discord_names import deny_pin_and_slowmode, find_game_role, find_text_channel, normalize_discord_name


VOTE_NAMES = (CHANNEL_SMALL_VOTE, CHANNEL_MID_VOTE, CHANNEL_BLUE_VOTE)


class Completer(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        super().__init__(intents=intents)

    async def on_ready(self) -> None:
        try:
            for guild in self.guilds:
                await self._run(guild)
        finally:
            await self.close()

    async def _run(self, guild: discord.Guild) -> None:
        me = guild.me
        print(f"Guild {guild.name} admin={me.guild_permissions.administrator} "
              f"mch={me.guild_permissions.manage_channels} mroles={me.guild_permissions.manage_roles}",
              flush=True)
        await self._restore_bot_admin(guild, me)
        await self._hide_vote_channels(guild, me)
        await self._fix_qa(guild, me)

    async def _restore_bot_admin(self, guild: discord.Guild, me: discord.Member) -> None:
        role = discord.utils.get(guild.roles, name="bot-admin")
        if role is None:
            print("[bot-admin] not found", flush=True)
            return
        # Grant every permission this bot is allowed to assign (cannot grant Administrator).
        allowed = discord.Permissions(me.guild_permissions.value)
        allowed.administrator = False
        try:
            await role.edit(permissions=allowed, reason="Restore bot-admin usable permissions")
            print(f"[bot-admin] restored permissions value={allowed.value}", flush=True)
        except discord.HTTPException as exc:
            print(f"[bot-admin] permission restore failed: {exc}", flush=True)

    async def _hide_vote_channels(self, guild: discord.Guild, me: discord.Member) -> None:
        npc = find_game_role(guild, "NPC")
        player = find_game_role(guild, "PLAYER")
        winner = find_game_role(guild, "WINNER")
        admin = find_game_role(guild, "ADMIN")
        no_pin = deny_pin_and_slowmode()

        vote_channels: list[discord.TextChannel] = []
        for name in VOTE_NAMES:
            ch = find_text_channel(guild, name)
            if ch:
                vote_channels.append(ch)
                print(
                    f"[vote] #{ch.name} synced={ch.permissions_synced} "
                    f"everyone_ow={ch.overwrites_for(guild.default_role).pair()}",
                    flush=True,
                )

        category = next((ch.category for ch in vote_channels if ch.category), None)
        if category:
            print(f"[vote] category {category.name} mch={category.permissions_for(me).manage_channels}", flush=True)
            try:
                await category.set_permissions(
                    guild.default_role,
                    view_channel=False,
                    send_messages=False,
                    reason="New user sees only RULES — hide WEEKLY PICKS from @everyone",
                )
                print(f"[vote] category @{category.name} @everyone deny OK", flush=True)
            except discord.HTTPException as exc:
                print(f"[vote] category @everyone deny failed: {exc}", flush=True)
            for role, allow_send in ((npc, False), (player, False), (winner, False), (admin, True)):
                if not role:
                    continue
                try:
                    await category.set_permissions(
                        role,
                        view_channel=True,
                        send_messages=allow_send,
                        read_message_history=True,
                        **no_pin,
                        reason="WEEKLY PICKS visible to game roles",
                    )
                    print(f"[vote] category set {role.name}", flush=True)
                except discord.HTTPException as exc:
                    print(f"[vote] category set {role.name} failed: {exc}", flush=True)

        for ch in vote_channels:
            try:
                ow = ch.overwrites_for(guild.default_role)
                print(f"[vote] #{ch.name} current everyone view={ow.view_channel}", flush=True)
                await ch.set_permissions(
                    guild.default_role,
                    view_channel=False,
                    send_messages=False,
                    reason="Hide vote channel from roleless members",
                )
                print(f"[vote] #{ch.name} @everyone deny OK", flush=True)
            except discord.HTTPException as exc:
                print(f"[vote] #{ch.name} @everyone deny failed: {exc}", flush=True)

    async def _fix_qa(self, guild: discord.Guild, me: discord.Member) -> None:
        ch = find_text_channel(guild, CHANNEL_QA, "qa", "faq", "q-and-a")
        if ch is None:
            for c in guild.text_channels:
                if normalize_discord_name(c.name) == "qa":
                    ch = c
                    break
        if ch is None:
            print("[qa] channel not found", flush=True)
            return
        print(f"[qa] #{ch.name} view={ch.permissions_for(me).view_channel} "
              f"mch={ch.permissions_for(me).manage_channels} cat={ch.category}", flush=True)

        if ch.category:
            try:
                await ch.category.set_permissions(
                    me,
                    view_channel=True,
                    send_messages=True,
                    manage_channels=True,
                    manage_messages=True,
                    read_message_history=True,
                    reason="Give bot access to STARTING / Q&A",
                )
                print(f"[qa] category bot allow OK", flush=True)
            except discord.HTTPException as exc:
                print(f"[qa] category bot allow failed: {exc}", flush=True)

        no_pin = deny_pin_and_slowmode()
        try:
            await ch.set_permissions(
                me,
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                manage_messages=True,
                read_message_history=True,
                reason="Bot access to Q&A",
            )
            print(f"[qa] bot overwrite OK on #{ch.name}", flush=True)
        except discord.HTTPException as exc:
            print(f"[qa] bot overwrite failed: {exc}", flush=True)

        # Hide from roleless members; show to NPC+
        try:
            await ch.set_permissions(
                guild.default_role,
                view_channel=False,
                send_messages=False,
                **no_pin,
                reason="Q&A hidden until NPC",
            )
            print("[qa] @everyone deny OK", flush=True)
        except discord.HTTPException as exc:
            print(f"[qa] @everyone deny failed: {exc}", flush=True)

        for key, can_send in (("NPC", False), ("PLAYER", False), ("WINNER", False), ("ADMIN", True)):
            role = find_game_role(guild, key)
            if not role:
                continue
            try:
                await ch.set_permissions(
                    role,
                    view_channel=True,
                    send_messages=can_send,
                    read_message_history=True,
                    **no_pin,
                    reason="Q&A game-role visibility",
                )
                print(f"[qa] set {role.name}", flush=True)
            except discord.HTTPException as exc:
                print(f"[qa] set {role.name} failed: {exc}", flush=True)


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN missing", flush=True)
        return 1
    await Completer().start(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
