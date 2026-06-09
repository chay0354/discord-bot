"""Reaction-role onboarding gate: press the emoji in the welcome channel -> NPC role.

Posts (or reuses) a single gate message in the welcome channel with a reaction.
When a member reacts with the configured emoji, they are granted the NPC role.
The gate message is identified by a hidden marker in its embed footer, so it
survives restarts without needing a settings table.
"""
from __future__ import annotations

import discord
from discord.ext import commands

from config import (
    CHANNEL_MOD,
    CHANNEL_WELCOME,
    NPC_GATE_EMOJI,
    ROLE_NPC,
    ROLE_PLAYER,
    ROLE_WINNER,
    WELCOME_CHANNEL_CANDIDATES,
)

GATE_MARKER = "npc-gate-v1"
GATE_TITLE = "🎮 Get Access"
GATE_BODY = (
    "Press the **{emoji}** reaction below to join the game.\n\n"
    "You'll receive the **NPC** role, which unlocks the rules and the weekly "
    "voting channels. Free members get **1 vote** per category."
)


class OnboardingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._gate_message_ids: dict[int, int] = {}  # guild_id -> message_id
        self._synced = False

    def _find_channel(self, guild: discord.Guild, name: str) -> discord.TextChannel | None:
        for channel in guild.text_channels:
            if channel.name.lower() == name.lower():
                return channel
        return None

    def _welcome_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        for name in (CHANNEL_WELCOME, *WELCOME_CHANNEL_CANDIDATES):
            ch = self._find_channel(guild, name)
            if ch:
                return ch
        return None

    def _gate_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=GATE_TITLE,
            description=GATE_BODY.format(emoji=NPC_GATE_EMOJI),
            color=discord.Color.green(),
        )
        embed.set_footer(text=GATE_MARKER)
        return embed

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._synced:
            return
        self._synced = True
        for guild in self.bot.guilds:
            try:
                await self._ensure_gate_message(guild)
            except Exception as exc:  # noqa: BLE001
                print(f"[onboarding] gate setup failed for {guild.id}: {exc!r}", flush=True)

    async def _ensure_gate_message(self, guild: discord.Guild) -> None:
        channel = self._welcome_channel(guild)
        if not channel:
            print(f"[onboarding] no welcome channel in guild {guild.id}", flush=True)
            return

        # Look for an existing gate message authored by the bot (marker in footer).
        existing: discord.Message | None = None
        try:
            async for msg in channel.history(limit=50):
                if msg.author.id != self.bot.user.id:
                    continue
                if any(e.footer and e.footer.text == GATE_MARKER for e in msg.embeds):
                    existing = msg
                    break
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[onboarding] cannot read welcome history in {guild.id}: {exc!r}", flush=True)

        if existing is None:
            try:
                existing = await channel.send(embed=self._gate_embed())
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[onboarding] cannot post gate message in {guild.id}: {exc!r}", flush=True)
                return

        self._gate_message_ids[guild.id] = existing.id

        # Make sure the bot's own reaction is present so members can just click it.
        already = any(
            str(r.emoji) == NPC_GATE_EMOJI and r.me for r in existing.reactions
        )
        if not already:
            try:
                await existing.add_reaction(NPC_GATE_EMOJI)
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[onboarding] cannot add gate reaction in {guild.id}: {exc!r}", flush=True)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or payload.member is None:
            return
        if payload.member.bot:
            return
        if str(payload.emoji) != NPC_GATE_EMOJI:
            return
        gate_id = self._gate_message_ids.get(payload.guild_id)
        if gate_id is None or payload.message_id != gate_id:
            return
        await self._grant_npc(payload.member)

    async def _grant_npc(self, member: discord.Member) -> None:
        guild = member.guild
        existing = {r.name.upper() for r in member.roles}
        if {ROLE_PLAYER.upper(), ROLE_WINNER.upper(), ROLE_NPC.upper()} & existing:
            return  # already has a game role
        role = discord.utils.get(guild.roles, name=ROLE_NPC)
        if not role:
            await self._mod_log(
                guild, "NPC role missing",
                f"Role `{ROLE_NPC}` not found — cannot grant it to <@{member.id}> from the welcome gate.",
                discord.Color.orange(),
            )
            return
        me = guild.me
        if me and role >= me.top_role:
            await self._mod_log(
                guild, "NPC role hierarchy error",
                f"`{ROLE_NPC}` is above my highest role, so I cannot grant it to <@{member.id}>. "
                f"Move my bot role above `{ROLE_NPC}` in Server Settings → Roles.",
                discord.Color.orange(),
            )
            return
        try:
            await member.add_roles(role, reason="Welcome gate reaction")
            print(f"[onboarding] granted NPC to {member.id} in {guild.id}", flush=True)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[onboarding] could not grant NPC to {member.id}: {exc!r}", flush=True)

    async def _mod_log(self, guild: discord.Guild, title: str, body: str, color: discord.Color) -> None:
        ch = self._find_channel(guild, CHANNEL_MOD)
        if not ch:
            return
        try:
            await ch.send(embed=discord.Embed(title=title, description=body, color=color))
        except Exception:
            pass

    @commands.command(name="post_npc_gate")
    @commands.has_role("ADMIN")
    @commands.guild_only()
    async def post_npc_gate(self, ctx: commands.Context) -> None:
        """ADMIN: (re)post the 'react to get NPC' gate in the welcome channel."""
        await self._ensure_gate_message(ctx.guild)
        ch = self._welcome_channel(ctx.guild)
        if ch:
            await ctx.send(f"NPC gate is set in {ch.mention}.")
        else:
            await ctx.send("No welcome channel found. Set WELCOME_CHANNEL env or create one.")


async def setup(bot: commands.Bot):
    await bot.add_cog(OnboardingCog(bot))
