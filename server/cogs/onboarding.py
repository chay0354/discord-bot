"""Reaction-role onboarding: react on the existing RULES message (bla + 🔥 🚀) -> NPC.

Does not post a duplicate gate message. Uses the existing rules-channel message
that already has the gate emojis (e.g. Carl-bot's "bla" message).
"""
from __future__ import annotations

import discord
from discord.ext import commands

import database
from discord_names import find_game_role, find_text_channel, member_role_keys
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
RULES_GUIDE_MARKER = "stock-game-rules-v1"
_RULES_GATE_STATE_KEY = "rules_gate"


def rules_guide_embed(gate_url: str) -> discord.Embed:
    embed = discord.Embed(
        title="Welcome to MEME STOCK — how to play",
        description=(
            "A weekly community stock-picking game. This is not real trading or investment advice.\n\n"
            f"**1. Join for free:** [open the entry message]({gate_url}) and react with 🔥 or 🚀 "
            "to receive **NPC** and unlock the game channels.\n"
            "**2. Pick stocks:** PLAYER / WINNER members submit real stocks in CHOOSE YOUR TICKER "
            "during pre-vote (up to 20 unique stocks per category).\n"
            "**3. Vote:** use the ticker buttons in WEEKLY PICKS. NPC gets **1 vote per category**; "
            "PLAYER / WINNER gets **5**, plus purchased votes and bonuses. Paid tiers may stack votes.\n"
            "**4. Win:** only NPC votes cast in the first **24 hours** qualify. Pick a top-voted "
            "stock in **all three categories**. Tied leaders count. Staff, paying players and "
            "active WINNER members cannot win. You must still be in the server when awards are given.\n\n"
            "**Automatic schedule (New York time):** voting opens Monday **09:00**; "
            "the early window ends Tuesday **09:00**; voting closes Friday **16:00**. "
            "Manual games use the opening time announced on their ballot.\n\n"
            "Subscribe in **PLAYER**, manage billing in **manage-subscription**, and find help in **Q&A**. "
            "Extra-vote packs are one-time purchases for the indicated week, not subscriptions."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=RULES_GUIDE_MARKER)
    return embed


class OnboardingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._gate_message_ids: dict[int, int] = {}  # guild_id -> message_id
        self._synced = False

    def _find_channel(self, guild: discord.Guild, name: str) -> discord.TextChannel | None:
        return find_text_channel(guild, name)

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
        guide: discord.Message | None = None
        cached = database.get_message_state(guild.id, _RULES_GATE_STATE_KEY) or {}
        cached_id = cached.get("message_id")
        if cached_id:
            try:
                gate = await channel.fetch_message(int(cached_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                gate = None
        try:
            async for msg in channel.history(limit=50):
                # Remove legacy duplicate embeds posted by this bot.
                if msg.author.id == self.bot.user.id:
                    if any(e.footer and e.footer.text == RULES_GUIDE_MARKER for e in msg.embeds):
                        guide = msg
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
        try:
            database.save_message_state(
                guild.id,
                _RULES_GATE_STATE_KEY,
                channel_id=channel.id,
                message_id=gate.id,
                payload={"kind": "rules_gate"},
            )
        except Exception as exc:
            print(f"[onboarding] cache rules gate failed: {exc!r}", flush=True)
        embed = rules_guide_embed(gate.jump_url)
        try:
            if guide:
                await guide.edit(embed=embed)
            else:
                await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[onboarding] cannot publish game instructions in {guild.id}: {exc!r}", flush=True)
        print(
            f"[onboarding] bound gate msg {gate.id} in #{channel.name} "
            f"(author={gate.author})",
            flush=True,
        )
        await self._backfill_gate_reactors(guild, gate)

    async def _backfill_gate_reactors(self, guild: discord.Guild, gate: discord.Message) -> None:
        """Grant NPC to members who already reacted but never received the role."""
        granted = 0
        for reaction in gate.reactions:
            if str(reaction.emoji) not in NPC_GATE_EMOJIS:
                continue
            try:
                async for user in reaction.users(limit=500):
                    if user.bot:
                        continue
                    member = guild.get_member(user.id)
                    if member is None:
                        continue
                    if not ({"PLAYER", "WINNER", "NPC"} & member_role_keys(member)):
                        granted += 1
                    await self._grant_npc(member)
            except (discord.Forbidden, discord.HTTPException):
                continue
        if granted:
            print(f"[onboarding] backfilled NPC for {granted} reactor(s) in {guild.id}", flush=True)

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
        existing = member_role_keys(member)
        if {"PLAYER", "WINNER", "NPC"} & existing:
            return
        role = find_game_role(guild, "NPC")
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
            database.log_event(
                guild.id,
                "npc_role_granted",
                {"discord_id": member.id, "reason": "rules_gate_reaction"},
            )
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
