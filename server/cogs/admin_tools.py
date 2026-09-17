# cogs/admin_tools.py
# Purpose: Small, focused admin utilities. Adds !clear_here (ADMIN only).
# Robust channel wipe: iterates history in chunks, deletes one-by-one,
# ignores 404s, handles very old messages and bot messages, and logs progress.

from __future__ import annotations

import asyncio
from typing import Optional, List

import discord
from discord.ext import commands

import database
from discord_names import find_game_role, find_text_channel
from config import (
    CHANNEL_BLUE_LIVE,
    CHANNEL_BLUE_TICKER,
    CHANNEL_BLUE_VOTE,
    CHANNEL_ADMIN_ACTIONS,
    CHANNEL_MANAGE_SUBSCRIPTION,
    CHANNEL_SUBSCRIBE,
    CHANNEL_FINAL_LEADERBOARD,
    CHANNEL_MID_LIVE,
    CHANNEL_MID_TICKER,
    CHANNEL_MID_VOTE,
    CHANNEL_MOD,
    CHANNEL_EXTRA_VOTES,
    CHANNEL_PICK_RESULTS,
    CHANNEL_QA,
    CHANNEL_RULES,
    CHANNEL_SMALL_LIVE,
    CHANNEL_SMALL_TICKER,
    CHANNEL_SMALL_VOTE,
    CHANNEL_WINNERS,
    EXTRA_VOTES_CHANNEL_CANDIDATES,
    MANAGE_SUBSCRIPTION_CHANNEL_CANDIDATES,
    PICK_RESULTS_CHANNEL_CANDIDATES,
    QA_CHANNEL_CANDIDATES,
    RULES_CHANNEL_CANDIDATES,
    SUBSCRIBE_CHANNEL_CANDIDATES,
    ROLE_ADMIN,
    ROLE_NPC,
    ROLE_PLAYER,
    ROLE_WINNER,
)


CHUNK_SIZE = 100           # how many messages to fetch per history chunk
# seconds between individual deletes (gentle on API)
PAUSE_BETWEEN_DELETES = 0.12
PAUSE_BETWEEN_CHUNKS = 0.35   # pause between history chunks
PROGRESS_EVERY = 100       # print progress to console every N deletions


class AdminToolsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _find_channel(self, guild: discord.Guild, name: str) -> discord.TextChannel | None:
        return find_text_channel(guild, name)

    async def _ensure_role(
        self,
        guild: discord.Guild,
        name: str,
        *,
        permissions: discord.Permissions | None = None,
    ) -> tuple[discord.Role, bool]:
        role = find_game_role(guild, name) or discord.utils.get(guild.roles, name=name)
        if role:
            if role.name != name:
                try:
                    await role.edit(name=name, reason="Stock bot infrastructure setup")
                except discord.Forbidden:
                    pass
            return role, False
        role = await guild.create_role(
            name=name,
            permissions=permissions or discord.Permissions.none(),
            reason="Stock bot infrastructure setup",
        )
        return role, True

    async def _ensure_text_channel(
        self,
        guild: discord.Guild,
        name: str,
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite],
        *aliases: str,
        rename: bool = True,
    ) -> tuple[discord.TextChannel, bool]:
        channel = find_text_channel(guild, name, *aliases)
        if channel:
            edits: dict = {"overwrites": overwrites}
            if rename and channel.name != name:
                edits["name"] = name
            await channel.edit(**edits, reason="Stock bot infrastructure setup")
            return channel, False
        channel = await guild.create_text_channel(
            name=name,
            overwrites=overwrites,
            reason="Stock bot infrastructure setup",
        )
        return channel, True

    @commands.command(name="setup_infrastructure")
    @commands.has_role("ADMIN")
    @commands.guild_only()
    async def setup_infrastructure(self, ctx: commands.Context):
        """
        ADMIN: Create/update required bot roles, channels, and channel permissions.
        Run once after inviting the bot, and again after server permission changes.
        """
        guild = ctx.guild
        if not guild or not guild.me:
            await ctx.send("This command must be used inside a server.")
            return

        # No manage_channels on human ADMIN — Discord bypasses channel view denies for that
        # permission, so #PLAYER would stay visible in the sidebar. The bot edits channels itself.
        admin_perms = discord.Permissions(
            manage_roles=True,
            manage_messages=True,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        )
        player_perms = discord.Permissions(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
            use_external_emojis=True,
        )
        player_perms.pin_messages = False
        player_perms.bypass_slowmode = False
        admin_perms.pin_messages = False
        admin_perms.bypass_slowmode = False
        winner_perms = player_perms

        created_roles: List[str] = []
        npc_role, created = await self._ensure_role(guild, ROLE_NPC)
        if created:
            created_roles.append(ROLE_NPC)
        player_role, created = await self._ensure_role(guild, ROLE_PLAYER, permissions=player_perms)
        if created:
            created_roles.append(ROLE_PLAYER)
        winner_role, created = await self._ensure_role(guild, ROLE_WINNER, permissions=winner_perms)
        if created:
            created_roles.append(ROLE_WINNER)
        admin_role, created = await self._ensure_role(guild, ROLE_ADMIN, permissions=admin_perms)
        if created:
            created_roles.append(ROLE_ADMIN)

        everyone = guild.default_role
        bot_member = guild.me
        no_pin = {"pin_messages": False, "bypass_slowmode": False}

        def public_overwrites() -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
            return {
                everyone: discord.PermissionOverwrite(view_channel=False, send_messages=False, **no_pin),
                npc_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                player_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                winner_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                admin_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, **no_pin),
                bot_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True, read_message_history=True),
            }

        def subscribe_funnel_overwrites() -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
            """Subscribe / become-PLAYER channel: NPCs only (hide from PLAYER and ADMIN)."""
            return {
                everyone: discord.PermissionOverwrite(view_channel=False, send_messages=False, **no_pin),
                npc_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                player_role: discord.PermissionOverwrite(view_channel=False, **no_pin),
                winner_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                admin_role: discord.PermissionOverwrite(view_channel=False, **no_pin),
                bot_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True, read_message_history=True),
            }

        def subscriber_overwrites() -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
            return {
                everyone: discord.PermissionOverwrite(view_channel=False, **no_pin),
                npc_role: discord.PermissionOverwrite(view_channel=False, **no_pin),
                player_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                winner_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                admin_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, **no_pin),
                bot_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True, read_message_history=True),
            }

        def mod_overwrites() -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
            return {
                everyone: discord.PermissionOverwrite(view_channel=False, **no_pin),
                npc_role: discord.PermissionOverwrite(view_channel=False, **no_pin),
                player_role: discord.PermissionOverwrite(view_channel=False, **no_pin),
                winner_role: discord.PermissionOverwrite(view_channel=False, **no_pin),
                admin_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, **no_pin),
                bot_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True, read_message_history=True),
            }

        def rules_overwrites() -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
            return {
                everyone: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                npc_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                player_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                winner_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                admin_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, **no_pin),
                bot_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True, read_message_history=True),
            }

        created_channels: List[str] = []
        updated_channels: List[str] = []
        channel_specs: list[tuple[str, dict, tuple[str, ...], bool]] = [
            (CHANNEL_SMALL_TICKER, subscriber_overwrites(), (), True),
            (CHANNEL_MID_TICKER, subscriber_overwrites(), (), True),
            (CHANNEL_BLUE_TICKER, subscriber_overwrites(), (), True),
            (CHANNEL_PICK_RESULTS, subscriber_overwrites(), PICK_RESULTS_CHANNEL_CANDIDATES, True),
            (CHANNEL_SMALL_VOTE, public_overwrites(), (), True),
            (CHANNEL_MID_VOTE, public_overwrites(), (), True),
            (CHANNEL_BLUE_VOTE, public_overwrites(), (), True),
            (CHANNEL_SMALL_LIVE, subscriber_overwrites(), (), True),
            (CHANNEL_MID_LIVE, subscriber_overwrites(), (), True),
            (CHANNEL_BLUE_LIVE, subscriber_overwrites(), (), True),
            (CHANNEL_MOD, mod_overwrites(), (), False),
            (CHANNEL_ADMIN_ACTIONS, mod_overwrites(), (), False),
            (CHANNEL_SUBSCRIBE, subscribe_funnel_overwrites(), SUBSCRIBE_CHANNEL_CANDIDATES, True),
            (CHANNEL_MANAGE_SUBSCRIPTION, public_overwrites(), MANAGE_SUBSCRIPTION_CHANNEL_CANDIDATES, True),
            (CHANNEL_FINAL_LEADERBOARD, public_overwrites(), (), False),
            (CHANNEL_WINNERS, public_overwrites(), (), False),
            (CHANNEL_QA, public_overwrites(), QA_CHANNEL_CANDIDATES, True),
            (CHANNEL_EXTRA_VOTES, subscriber_overwrites(), EXTRA_VOTES_CHANNEL_CANDIDATES, True),
            (CHANNEL_RULES, rules_overwrites(), RULES_CHANNEL_CANDIDATES, False),
        ]

        for name, overwrites, aliases, rename in channel_specs:
            _, was_created = await self._ensure_text_channel(
                guild, name, overwrites, *aliases, rename=rename
            )
            (created_channels if was_created else updated_channels).append(name)

        database.ensure_cycle(guild.id)
        database.log_event(
            guild.id,
            "infrastructure_setup",
            {
                "actor_id": ctx.author.id,
                "created_roles": created_roles,
                "created_channels": created_channels,
                "updated_channels": updated_channels,
            },
        )

        mod_channel = self._find_channel(guild, CHANNEL_MOD)
        summary = discord.Embed(
            title="Infrastructure Setup Complete",
            description=(
                f"Roles created: {', '.join(created_roles) if created_roles else 'none'}\n"
                f"Channels created: {', '.join(created_channels) if created_channels else 'none'}\n"
                f"Channels updated: {len(updated_channels)}"
            ),
            color=discord.Color.green(),
        )
        if mod_channel:
            await mod_channel.send(embed=summary)
        if ctx.channel != mod_channel:
            await ctx.send(embed=summary)

    @commands.command(name="clear_here")
    @commands.has_role("ADMIN")
    @commands.guild_only()
    async def clear_here(self, ctx: commands.Context):
        """
        ADMIN: Wipes ALL messages in THIS channel, including very old and bot messages.
        Method:
          1) Try a few bulk-purge passes for recent messages (<14 days).
          2) Then iterate history in chunks (before=...) and delete one-by-one.
        The command does NOT post progress in the channel (keeps it clean).
        A DM confirmation is sent to the admin when done.
        """
        if not isinstance(ctx.channel, discord.TextChannel) or not ctx.guild:
            await ctx.send("This command can only be used in a server text channel.")
            return

        me = ctx.guild.me
        perms = ctx.channel.permissions_for(me)
        if not perms.manage_messages:
            await ctx.send("I need the **Manage Messages** permission in this channel to do that.")
            return

        channel: discord.TextChannel = ctx.channel
        actor = ctx.author

        deleted_total = 0

        # -------- PASS 1: a couple of bulk purge sweeps for recent msgs --------
        for _ in range(3):
            try:
                batch = await channel.purge(limit=100, bulk=True, check=lambda m: True)
            except discord.NotFound:
                # Messages disappeared mid-flight — continue
                batch = []
            except discord.HTTPException:
                # Bulk failed (likely due to age); stop bulk pass
                break

            if not batch:
                break
            deleted_total += len(batch)
            print(
                f"[clear_here] Bulk purged {len(batch)} (total: {deleted_total}) in #{channel.name}")
            await asyncio.sleep(PAUSE_BETWEEN_CHUNKS)

        # -------- PASS 2: iterate history and delete individually -------------
        # We walk backwards using 'before' to guarantee forward progress.
        before: Optional[discord.Message] = None
        while True:
            msgs: List[discord.Message] = []
            try:
                async for msg in channel.history(limit=CHUNK_SIZE, before=before, oldest_first=False):
                    msgs.append(msg)
            except discord.HTTPException as e:
                # If fetching history fails transiently, wait and retry a bit
                print(
                    f"[clear_here] history fetch HTTPException: {e}; retrying…")
                await asyncio.sleep(1.0)
                continue

            if not msgs:
                break  # no more history

            # Delete this chunk
            for i, msg in enumerate(msgs, start=1):
                try:
                    await msg.delete()
                    deleted_total += 1
                except discord.NotFound:
                    # already gone — ignore
                    pass
                except discord.Forbidden:
                    # can't delete this one — skip
                    pass
                except discord.HTTPException as e:
                    # rate limit / transient — short backoff and continue
                    await asyncio.sleep(0.5)
                    continue

                if deleted_total % PROGRESS_EVERY == 0:
                    print(
                        f"[clear_here] Deleted ~{deleted_total} so far in #{channel.name}")

                # gentle pacing
                await asyncio.sleep(PAUSE_BETWEEN_DELETES)

            # Advance the cursor: continue from *before* the oldest msg in this chunk
            before = msgs[-1]

            # small pause between chunks
            await asyncio.sleep(PAUSE_BETWEEN_CHUNKS)

        # Final log + DM confirmation (channel stays clean)
        print(
            f"[clear_here] Finished. Total deleted in #{channel.name}: ~{deleted_total}")
        try:
            await actor.send(f"✅ Cleared **#{channel.name}**. Deleted approximately {deleted_total} messages.")
        except Exception:
            # If DMs are closed, we silently finish
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminToolsCog(bot))
