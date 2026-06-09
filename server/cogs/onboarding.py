"""Reaction-role onboarding: react on the existing RULES message (bla + 🔥 🚀) -> NPC.

Does not post a duplicate gate message. Uses the existing rules-channel message
that already has the gate emojis (e.g. Carl-bot's "bla" message).
"""
from __future__ import annotations

import discord
from discord.ext import commands

from config import (
    CHANNEL_MOD,
    CHANNEL_RULES,
    NPC_GATE_EMOJIS,
    ROLE_NPC,
    ROLE_PLAYER,
    ROLE_WINNER,
    RULES_CHANNEL_CANDIDATES,
)

GATE_MARKER = "npc-gate-v1"  # legacy: delete if our bot posted one before


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

    def _rules_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        for name in (CHANNEL_RULES, *RULES_CHANNEL_CANDIDATES):
            ch = self._find_channel(guild, name)
            if ch:
                return ch
        return None

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._synced:
            return
        self._synced = True
        for guild in self.bot.guilds:
            try:
                await self._bind_gate_message(guild)
            except Exception as exc:  # noqa: BLE001
                print(f"[onboarding] gate bind failed for {guild.id}: {exc!r}", flush=True)

    async def _bind_gate_message(self, guild: discord.Guild) -> None:
        """Find the existing rules gate (bla + emojis); remove any duplicate we posted."""
        channel = self._rules_channel(guild)
        if not channel:
            print(f"[onboarding] no rules channel in guild {guild.id}", flush=True)
            return

        gate: discord.Message | None = None
        try:
            async for msg in channel.history(limit=50):
                # Remove legacy duplicate embeds posted by this bot.
                if msg.author.id == self.bot.user.id:
                    if any(e.footer and e.footer.text == GATE_MARKER for e in msg.embeds):
                        try:
                            await msg.delete()
                            print(f"[onboarding] removed duplicate gate embed in {guild.id}", flush=True)
                        except (discord.Forbidden, discord.HTTPException):
                            pass
                    continue
                if gate is not None:
                    continue
                reaction_emojis = {str(r.emoji) for r in msg.reactions}
                if any(em in reaction_emojis for em in NPC_GATE_EMOJIS):
                    gate = msg
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[onboarding] cannot read rules history in {guild.id}: {exc!r}", flush=True)
            return

        if gate is None:
            print(f"[onboarding] no gate message with {NPC_GATE_EMOJIS} in {guild.id}", flush=True)
            return

        self._gate_message_ids[guild.id] = gate.id
        print(
            f"[onboarding] bound gate msg {gate.id} in #{channel.name} "
            f"(author={gate.author})",
            flush=True,
        )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or payload.member is None:
            return
        if payload.member.bot:
            return
        if str(payload.emoji) not in NPC_GATE_EMOJIS:
            return
        gate_id = self._gate_message_ids.get(payload.guild_id)
        if gate_id is None or payload.message_id != gate_id:
            return
        await self._grant_npc(payload.member)

    async def _grant_npc(self, member: discord.Member) -> None:
        guild = member.guild
        existing = {r.name.upper() for r in member.roles}
        if {ROLE_PLAYER.upper(), ROLE_WINNER.upper(), ROLE_NPC.upper()} & existing:
            return
        role = discord.utils.get(guild.roles, name=ROLE_NPC)
        if not role:
            await self._mod_log(
                guild, "NPC role missing",
                f"Role `{ROLE_NPC}` not found — cannot grant it to <@{member.id}> from the rules gate.",
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
            await member.add_roles(role, reason="Rules channel gate reaction")
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
        """ADMIN: re-bind the rules gate message (does not post a new one)."""
        await self._bind_gate_message(ctx.guild)
        gate_id = self._gate_message_ids.get(ctx.guild.id)
        ch = self._rules_channel(ctx.guild)
        if gate_id and ch:
            await ctx.send(f"NPC gate bound to message `{gate_id}` in {ch.mention}.")
        else:
            await ctx.send("No gate message with 🔥/🚀 found in the rules channel.")


async def setup(bot: commands.Bot):
    await bot.add_cog(OnboardingCog(bot))
