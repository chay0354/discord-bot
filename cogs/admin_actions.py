from __future__ import annotations

import discord
from discord.ext import commands

import database
from cogs.scheduler import SchedulerCog
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


def _is_admin(member: discord.Member) -> bool:
    return any(role.name.upper() == ROLE_ADMIN.upper() for role in member.roles)


class AdminActionsView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def _scheduler(self) -> SchedulerCog:
        cog = self.bot.get_cog("SchedulerCog")
        if isinstance(cog, SchedulerCog):
            return cog
        scheduler = SchedulerCog(self.bot)
        await self.bot.add_cog(scheduler)
        return scheduler

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This only works inside the server.", ephemeral=True)
            return False
        if not _is_admin(interaction.user):
            await interaction.response.send_message("Only ADMIN can use this panel.", ephemeral=True)
            return False
        await interaction.response.defer(ephemeral=True)
        return True

    @discord.ui.button(
        label="Start Vote Stage",
        style=discord.ButtonStyle.success,
        custom_id="admin_actions:start_voting",
    )
    async def start_voting(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        scheduler = await self._scheduler()
        updated, counts = await scheduler._monday_open_one_guild(interaction.guild)
        await interaction.followup.send(
            f"Vote Stage started. Users now vote from the selected stocks. Updated {updated} channel(s). Counts: {counts}",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Close Early Window",
        style=discord.ButtonStyle.secondary,
        custom_id="admin_actions:close_early",
    )
    async def close_early(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        scheduler = await self._scheduler()
        await scheduler._tuesday_early_close_one_guild(interaction.guild)
        await interaction.followup.send("Early winner window closed manually.", ephemeral=True)

    @discord.ui.button(
        label="End Vote Stage",
        style=discord.ButtonStyle.danger,
        custom_id="admin_actions:end_competition",
    )
    async def end_competition(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        scheduler = await self._scheduler()
        await scheduler._friday_close_one_guild(interaction.guild)
        await interaction.followup.send("Vote Stage ended. Results/winners were processed.", ephemeral=True)

    @discord.ui.button(
        label="Start Pre-Voting Stage",
        style=discord.ButtonStyle.primary,
        custom_id="admin_actions:start_ticker_selection",
        row=1,
    )
    async def start_ticker_selection(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        scheduler = await self._scheduler()
        await scheduler._restart_pre_voting_one_guild(interaction.guild, actor_id=interaction.user.id)
        await interaction.followup.send(
            "Pre-Voting Stage restarted. Any current game state was stopped and users can choose the 20 stocks again.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="End Pre-Voting + Start Vote Stage",
        style=discord.ButtonStyle.success,
        custom_id="admin_actions:end_selection_start_voting",
        row=1,
    )
    async def end_selection_start_voting(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        scheduler = await self._scheduler()
        updated, counts = await scheduler._monday_open_one_guild(interaction.guild)
        database.log_event(
            interaction.guild.id,
            "manual_end_selection_start_voting",
            {"actor_id": interaction.user.id, "updated": updated, "counts": counts},
        )
        await interaction.followup.send(
            f"Pre-Voting ended and Vote Stage started. Updated {updated} channel(s). Counts: {counts}",
            ephemeral=True,
        )


def admin_actions_embed() -> discord.Embed:
    return discord.Embed(
        title="Admin Actions",
        description=(
            "**Pre-Voting Stage**\n"
            "Subscribed users choose the 20 stocks that will be in the next game for each category.\n"
            f"Channels: `#{CHANNEL_SMALL_TICKER}`, `#{CHANNEL_MID_TICKER}`, `#{CHANNEL_BLUE_TICKER}`\n\n"
            "**Vote Stage**\n"
            "The game itself. Users vote only from the 20 selected stocks in each category.\n"
            f"Channels: `#{CHANNEL_SMALL_VOTE}`, `#{CHANNEL_MID_VOTE}`, `#{CHANNEL_BLUE_VOTE}`\n\n"
            "**Buttons**\n"
            "- **Start Pre-Voting Stage**: opens the subscriber stock-selection channels.\n"
            "- **End Pre-Voting + Start Vote Stage**: closes selection and starts the game voting.\n"
            "- **Start Vote Stage**: starts/reposts voting from the selected stocks.\n"
            "- **Close Early Window**: stops new early-vote eligibility.\n"
            "- **End Vote Stage**: closes voting, posts final results, processes winners, then opens Pre-Voting again."
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
