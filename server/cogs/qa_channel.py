"""Public Q&A channel: expandable FAQ. Only ADMIN may edit the content."""
from __future__ import annotations

import discord
from discord.ext import commands

import database
import game_copy
from config import CHANNEL_QA, QA_CHANNEL_CANDIDATES, ROLE_ADMIN
from discord_names import (
    deny_pin_and_slowmode,
    ensure_styled_roles,
    find_game_role,
    find_text_channel,
)

_QA_STATE_KEY = "qa_items"

_DEFAULT_ITEMS: list[dict[str, str]] = [
    {
        "q": "How do I vote?",
        "a": "Open the WEEKLY PICKS category channels (small / mid / large cap) and press a ticker button. You will get a private confirmation of what you picked.",
    },
    {
        "q": "Who can win the WINNER role?",
        "a": "Only a pure NPC who votes in the first 24 hours and picks the top ticker in every category. PLAYER / WINNER / ADMIN are never eligible.",
    },
    {
        "q": "How many votes do I get?",
        "a": "NPC: 1 vote per category. PLAYER / WINNER: 5 votes per category, and you may put more than one vote on the same ticker. Extra packs bought in #extra-votes add to that limit.",
    },
    {
        "q": "When does voting open and close?",
        "a": "Voting opens Monday 9:00 AM ET. The 24h WINNER window ends 24 hours later. All voting closes Friday 4:00 PM ET.",
    },
]


def _find_channel(guild: discord.Guild, names: tuple[str, ...]) -> discord.TextChannel | None:
    """Prefer the styled top-of-server Q&A channel even if perms still need a sync."""
    preferred = find_text_channel(guild, CHANNEL_QA)
    if preferred is not None:
        return preferred
    return find_text_channel(guild, *names)


def _load_items(guild_id: int) -> list[dict[str, str]]:
    row = database.get_message_state(guild_id, _QA_STATE_KEY)
    payload = (row or {}).get("payload") or {}
    items = payload.get("items")
    if isinstance(items, list) and items:
        out = []
        for item in items[:10]:
            if isinstance(item, dict) and item.get("q") and item.get("a"):
                out.append({"q": str(item["q"]), "a": str(item["a"])})
        if out:
            return out
    return list(_DEFAULT_ITEMS)


def _save_items(guild_id: int, items: list[dict[str, str]]) -> None:
    database.save_message_state(
        guild_id,
        _QA_STATE_KEY,
        channel_id=None,
        message_id=None,
        payload={"items": items, "editor": "admin_only"},
    )


class QAView(discord.ui.View):
    """One persistent button per question. Clicking toggles the answer (ephemeral)."""

    def __init__(self, items: list[dict[str, str]] | None = None) -> None:
        super().__init__(timeout=None)
        source = items or _DEFAULT_ITEMS
        for idx, item in enumerate(source[:10]):
            label = (item.get("q") or f"Q{idx + 1}")[:80]
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"qa:toggle:{idx}",
            )

            async def _cb(interaction: discord.Interaction, i: int = idx) -> None:
                guild = interaction.guild
                if not guild:
                    await interaction.response.send_message("Use this in the server.", ephemeral=True)
                    return
                rows = _load_items(guild.id)
                if i >= len(rows):
                    await interaction.response.send_message("That question is no longer available.", ephemeral=True)
                    return
                q = rows[i]["q"]
                a = rows[i]["a"]
                await interaction.response.send_message(f"**{q}**\n{a}", ephemeral=True)

            btn.callback = _cb  # type: ignore[method-assign]
            self.add_item(btn)


def qa_embed(items: list[dict[str, str]]) -> discord.Embed:
    lines = [f"**{i + 1}.** {item['q']}" for i, item in enumerate(items)]
    return discord.Embed(
        title="Q&A",
        description=(
            "Press a button below to **open** that answer (only you see it). "
            "Press again anytime to read it again.\n\n"
            + "\n".join(lines)
            + "\n\n*Only admins can add or change these questions.*\n"
            + game_copy.NOT_INVESTMENT_ADVICE
        ),
        color=discord.Color.blurple(),
    )


class QAChannelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self._ensure_channel(guild)
            except Exception as exc:
                print(f"[qa] setup failed for {guild.id}: {exc!r}", flush=True)

    async def _ensure_channel(self, guild: discord.Guild) -> discord.TextChannel:
        await ensure_styled_roles(guild)
        ch = _find_channel(guild, QA_CHANNEL_CANDIDATES)
        everyone = guild.default_role
        me = guild.me
        no_pin = deny_pin_and_slowmode()
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
            everyone: discord.PermissionOverwrite(view_channel=True, send_messages=False, **no_pin),
        }
        for key in ("NPC", "PLAYER", "WINNER", "ADMIN"):
            role = find_game_role(guild, key)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=key == "ADMIN",
                    read_message_history=True,
                    **no_pin,
                )
        if me:
            overwrites[me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_messages=True
            )
        parent = next(
            (c for c in guild.categories if c.name.upper() == "STARTING"),
            None,
        )
        if not ch:
            ch = await guild.create_text_channel(
                CHANNEL_QA,
                overwrites=overwrites,
                category=parent,
                reason="Q&A channel the bot can manage",
            )
        else:
            edits: dict = {"overwrites": overwrites}
            if ch.name != CHANNEL_QA:
                edits["name"] = CHANNEL_QA
            try:
                await ch.edit(**edits, reason="Q&A permission sync")
            except (discord.Forbidden, discord.HTTPException):
                pass
        items = _load_items(guild.id)
        posted = None
        async for msg in ch.history(limit=15):
            if msg.author == guild.me and msg.embeds and (msg.embeds[0].title or "") == "Q&A":
                await msg.edit(embed=qa_embed(items), view=QAView(items))
                posted = msg
                break
        if posted is None:
            posted = await ch.send(embed=qa_embed(items), view=QAView(items))
        if not posted.pinned:
            try:
                await posted.pin(reason="Keep Q&A at the top of ℚ＆𝗔")
            except (discord.Forbidden, discord.HTTPException):
                pass
        return ch

    @commands.command(name="qa_set")
    @commands.has_role(ROLE_ADMIN)
    @commands.guild_only()
    async def qa_set(self, ctx: commands.Context, index: int, *, text: str) -> None:
        """ADMIN: set Q&A item. Format: !qa_set 1 Question || Answer"""
        if "||" not in text:
            await ctx.reply("Use `!qa_set 1 Question || Answer`", mention_author=False)
            return
        q, a = [part.strip() for part in text.split("||", 1)]
        if not q or not a:
            await ctx.reply("Both question and answer are required.", mention_author=False)
            return
        items = _load_items(ctx.guild.id)
        pos = max(1, min(index, len(items) + 1)) - 1
        entry = {"q": q[:80], "a": a[:1000]}
        if pos < len(items):
            items[pos] = entry
        else:
            items.append(entry)
        _save_items(ctx.guild.id, items[:10])
        ch = await self._ensure_channel(ctx.guild)
        async for msg in ch.history(limit=15):
            if msg.author == ctx.guild.me and msg.embeds and (msg.embeds[0].title or "") == "Q&A":
                await msg.edit(embed=qa_embed(items), view=QAView(items))
                break
        await ctx.reply(f"Updated Q&A item {pos + 1}.", mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QAChannelCog(bot))
