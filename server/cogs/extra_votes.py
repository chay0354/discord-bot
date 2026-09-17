"""Dedicated #extra-votes channel: one-time Stripe packs that raise the vote limit."""
from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

import database
import game_copy
from config import (
    CHANNEL_EXTRA_VOTES,
    CHANNEL_SMALL_TICKER,
    EXTRA_VOTE_PACK_CENTS,
    EXTRA_VOTE_PACK_SIZE,
    EXTRA_VOTES_CHANNEL_CANDIDATES,
    ROLE_ADMIN,
    ROLE_PLAYER,
    ROLE_WINNER,
)
from discord_names import (
    apply_deny_pin_to_game_channels,
    deny_pin_and_slowmode,
    ensure_styled_channel,
    ensure_styled_roles,
    find_game_role,
    find_text_channel,
    member_role_keys,
)
from services.stripe_client import StripeClientError, create_extra_votes_checkout_session


def _find_channel(guild: discord.Guild, names: tuple[str, ...]) -> discord.TextChannel | None:
    return find_text_channel(guild, *names)


class ExtraVotesView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Buy extra votes",
        style=discord.ButtonStyle.success,
        custom_id="extra_votes:buy",
    )
    async def buy_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Use this in the server.", ephemeral=True)
            return
        names = member_role_keys(interaction.user)
        if not ({"PLAYER", "WINNER", "ADMIN"} & names):
            await interaction.response.send_message(
                "Extra votes are for **PLAYER** and **WINNER** only. Subscribe first, then come back.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        week_key = await asyncio.to_thread(database.open_voting_week_key, interaction.guild.id)
        if not week_key:
            await interaction.followup.send(
                "Extra-vote purchases open with voting. Please come back when voting is open.",
                ephemeral=True,
            )
            return
        try:
            url = await asyncio.to_thread(
                create_extra_votes_checkout_session,
                interaction.user.id,
                str(interaction.user),
                week_key=week_key,
                guild_id=interaction.guild.id,
                pack_size=EXTRA_VOTE_PACK_SIZE,
                amount_cents=EXTRA_VOTE_PACK_CENTS,
            )
        except StripeClientError as exc:
            await interaction.followup.send(f"Payments are not available right now: {exc}", ephemeral=True)
            return
        view = discord.ui.View(timeout=600)
        view.add_item(discord.ui.Button(label="Pay on Stripe", style=discord.ButtonStyle.link, url=url))
        current = database.extra_vote_credits(interaction.guild.id, interaction.user.id, week_key)
        await interaction.followup.send(
            f"This pack adds **+{EXTRA_VOTE_PACK_SIZE}** vote(s) per category for week `{week_key}`.\n"
            f"You currently have **{current}** extra vote(s) this week.\n"
            "Tap **Pay on Stripe** to complete checkout.",
            view=view,
            ephemeral=True,
        )


def extra_votes_embed() -> discord.Embed:
    dollars = EXTRA_VOTE_PACK_CENTS / 100
    return discord.Embed(
        title="Buy extra votes",
        description=(
            f"PLAYER and WINNER start with **5 votes** per category and may stack them "
            f"on the same ticker.\n\n"
            f"**This pack:** +{EXTRA_VOTE_PACK_SIZE} extra vote(s) per category "
            f"for the current voting week.\n"
            f"**Price:** ${dollars:.2f} (one-time, not a subscription).\n\n"
            "Credits are tied to your Discord ID. After Stripe confirms payment, "
            "your vote limit updates automatically.\n\n"
            f"{game_copy.NOT_INVESTMENT_ADVICE}"
        ),
        color=discord.Color.green(),
    )


class ExtraVotesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self.bot.add_view(ExtraVotesView())
        for guild in self.bot.guilds:
            try:
                await self._ensure_channel(guild)
                await apply_deny_pin_to_game_channels(guild)
            except Exception as exc:
                print(f"[extra_votes] setup failed for {guild.id}: {exc!r}", flush=True)

    async def _ensure_channel(self, guild: discord.Guild) -> None:
        await ensure_styled_roles(guild)
        ch = await ensure_styled_channel(
            guild, CHANNEL_EXTRA_VOTES, *EXTRA_VOTES_CHANNEL_CANDIDATES
        )
        player = find_game_role(guild, "PLAYER")
        winner = find_game_role(guild, "WINNER")
        admin = find_game_role(guild, "ADMIN")
        me = guild.me
        no_pin = deny_pin_and_slowmode()
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, **no_pin),
        }
        if player:
            overwrites[player] = discord.PermissionOverwrite(view_channel=True, send_messages=False, **no_pin)
        if winner:
            overwrites[winner] = discord.PermissionOverwrite(view_channel=True, send_messages=False, **no_pin)
        if admin:
            overwrites[admin] = discord.PermissionOverwrite(view_channel=True, send_messages=True, **no_pin)
        if me:
            overwrites[me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_messages=True
            )
        ticker_ch = find_text_channel(guild, CHANNEL_SMALL_TICKER)
        parent = ticker_ch.category if ticker_ch else None
        if not ch:
            ch = await guild.create_text_channel(
                CHANNEL_EXTRA_VOTES,
                overwrites=overwrites,
                category=parent,
                reason="Extra-votes purchase channel",
            )
        else:
            edits: dict = {"overwrites": overwrites}
            if ch.name != CHANNEL_EXTRA_VOTES:
                edits["name"] = CHANNEL_EXTRA_VOTES
            if parent is not None and ch.category != parent:
                edits["category"] = parent
            try:
                await ch.edit(**edits, reason="Extra-votes permission sync")
            except (discord.Forbidden, discord.HTTPException):
                pass
        posted = None
        async for msg in ch.history(limit=15):
            if msg.author == guild.me and msg.embeds and (msg.embeds[0].title or "") == "Buy extra votes":
                posted = msg
                break
        if posted is None:
            posted = await ch.send(embed=extra_votes_embed(), view=ExtraVotesView())
        if not posted.pinned:
            try:
                await posted.pin(reason="Keep extra-votes checkout visible")
            except (discord.Forbidden, discord.HTTPException):
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ExtraVotesCog(bot))
