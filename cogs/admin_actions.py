from __future__ import annotations

import discord
from discord.ext import commands

from config import CHANNEL_ADMIN_ACTIONS, ROLE_ADMIN
from game_control import run_action

# Keep in sync with crm/src/App.tsx ACTIONS
GAME_ACTIONS: tuple[dict[str, str], ...] = (
    {
        "id": "start_pre_vote",
        "label": "Start pre-vote",
        "hint": "End the current week, then open ticker picks for the next week.",
    },
    {
        "id": "start_vote",
        "label": "Start vote",
        "hint": "Close pre-vote and open the live vote stage with the current ballot.",
    },
)


def _is_admin(member: discord.Member) -> bool:
    return any(role.name.upper() == ROLE_ADMIN.upper() for role in member.roles)


def _find_admin_actions_channel(guild: discord.Guild) -> discord.TextChannel | None:
    for channel in guild.text_channels:
        if channel.name.lower() == CHANNEL_ADMIN_ACTIONS.lower():
            return channel
    return None


def admin_actions_embed() -> discord.Embed:
    lines = [
        "Use the buttons below (same as the CRM **Game controls** panel).",
        "",
    ]
    for action in GAME_ACTIONS:
        lines.append(f"**{action['label']}** — {action['hint']}")
    return discord.Embed(title="Game controls", description="\n".join(lines), color=discord.Color.blurple())


class AdminActionsView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot
        for action in GAME_ACTIONS:
            button = discord.ui.Button(
                label=action["label"],
                style=discord.ButtonStyle.primary,
                custom_id=f"admin_actions:{action['id']}",
            )
            button.callback = self._make_callback(action["id"])
            self.add_item(button)

    def _action_by_id(self, action_id: str) -> dict[str, str] | None:
        for action in GAME_ACTIONS:
            if action["id"] == action_id:
                return action
        return None

    def _make_callback(self, action_id: str):
        async def callback(interaction: discord.Interaction) -> None:
            await self._run(interaction, action_id)

        return callback

    async def _set_buttons_loading(self, interaction: discord.Interaction, active_action_id: str) -> None:
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            item.disabled = True
            meta = self._action_by_id(active_action_id)
            if meta and item.custom_id == f"admin_actions:{active_action_id}":
                item.label = "Running…"
        if interaction.message:
            await interaction.message.edit(view=self)

    async def _restore_buttons(self, interaction: discord.Interaction) -> None:
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            item.disabled = False
            if item.custom_id and item.custom_id.startswith("admin_actions:"):
                action_id = item.custom_id.split(":", 1)[1]
                meta = self._action_by_id(action_id)
                if meta:
                    item.label = meta["label"]
        if interaction.message:
            await interaction.message.edit(view=self)

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
        await self._set_buttons_loading(interaction, action)
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
        finally:
            await self._restore_buttons(interaction)


async def refresh_admin_actions_panel(guild: discord.Guild, bot: commands.Bot) -> discord.TextChannel | None:
    """Replace bot messages in #admin-actions with the current two-button panel."""
    channel = _find_admin_actions_channel(guild)
    if not channel:
        return None
    me = guild.me
    if not me:
        return channel
    async for message in channel.history(limit=50):
        if message.author == me and (message.components or message.embeds):
            await message.delete()
    await channel.send(embed=admin_actions_embed(), view=AdminActionsView(bot))
    return channel


class AdminActionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._panel_posted = False

    async def cog_load(self) -> None:
        self.bot.add_view(AdminActionsView(self.bot))

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._panel_posted:
            return
        self._panel_posted = True
        for guild in self.bot.guilds:
            try:
                ch = await refresh_admin_actions_panel(guild, self.bot)
                if ch:
                    print(f"[admin_actions] Panel refreshed in #{ch.name}", flush=True)
            except Exception as exc:
                print(f"[admin_actions] Panel refresh failed for {guild.id}: {exc!r}", flush=True)

    @commands.command(name="admin_actions_panel")
    @commands.has_role(ROLE_ADMIN)
    @commands.guild_only()
    async def admin_actions_panel(self, ctx: commands.Context) -> None:
        if ctx.channel.name.lower() != CHANNEL_ADMIN_ACTIONS:
            await ctx.send(f"Please run this in **#{CHANNEL_ADMIN_ACTIONS}**.")
            return
        await refresh_admin_actions_panel(ctx.guild, self.bot)
        await ctx.send("Admin actions panel refreshed.", delete_after=8)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminActionsCog(bot))
