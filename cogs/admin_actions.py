from __future__ import annotations

import discord
from discord.ext import commands

from config import (
    CHANNEL_ADMIN_ACTIONS,
    CHANNEL_BLUE_TICKER,
    CHANNEL_BLUE_VOTE,
    CHANNEL_MID_TICKER,
    CHANNEL_MID_VOTE,
    CHANNEL_SMALL_TICKER,
    CHANNEL_SMALL_VOTE,
    ROLE_ADMIN,
)
from game_control import run_action


def _is_admin(member: discord.Member) -> bool:
    return any(role.name.upper() == ROLE_ADMIN.upper() for role in member.roles)


class AdminActionsView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This only works inside the server.", ephemeral=True)
            return False
        if not _is_admin(interaction.user):
            await interaction.response.send_message("Only ADMIN can use this panel.", ephemeral=True)
            return False
        await interaction.response.defer(ephemeral=True)
        return True

    async def _run(self, interaction: discord.Interaction, action: str) -> None:
        if not await self._guard(interaction):
            return
        try:
            result = await run_action(
                action,
                actor_id=interaction.user.id,
                guild=interaction.guild,
            )
            msg = str(result.get("message") or "Done.")
            counts = result.get("counts")
            if counts is not None:
                msg += f" Updated {result.get('updated', 0)} channel(s). Counts: {counts}"
            await interaction.followup.send(msg, ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"Action failed: {exc}", ephemeral=True)

    @discord.ui.button(
        label="Start pre-vote",
        style=discord.ButtonStyle.primary,
        custom_id="admin_actions:start_pre_vote",
    )
    async def start_pre_vote(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._run(interaction, "start_pre_vote")

    @discord.ui.button(
        label="Start vote",
        style=discord.ButtonStyle.success,
        custom_id="admin_actions:start_vote",
    )
    async def start_vote(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._run(interaction, "start_vote")


def admin_actions_embed() -> discord.Embed:
    return discord.Embed(
        title="Admin Actions",
        description=(
            "**Pre-vote** — subscribers pick the 20 stocks per category for the next game.\n"
            f"Channels: `#{CHANNEL_SMALL_TICKER}`, `#{CHANNEL_MID_TICKER}`, `#{CHANNEL_BLUE_TICKER}`\n\n"
            "**Vote** — members vote on the ballot stocks only.\n"
            f"Channels: `#{CHANNEL_SMALL_VOTE}`, `#{CHANNEL_MID_VOTE}`, `#{CHANNEL_BLUE_VOTE}`\n\n"
            "**Start pre-vote** — ends the current week (results saved), then opens ticker picks for the next week.\n"
            "**Start vote** — closes pre-vote and opens the live vote stage."
        ),
        color=discord.Color.blurple(),
    )


class AdminActionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(AdminActionsView(self.bot))

    @commands.command(name="admin_actions_panel")
    @commands.has_role(ROLE_ADMIN)
    @commands.guild_only()
    async def admin_actions_panel(self, ctx: commands.Context) -> None:
        if ctx.channel.name.lower() != CHANNEL_ADMIN_ACTIONS:
            await ctx.send(f"Please run this in **#{CHANNEL_ADMIN_ACTIONS}**.")
            return
        await ctx.send(embed=admin_actions_embed(), view=AdminActionsView(self.bot))


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminActionsCog(bot))
