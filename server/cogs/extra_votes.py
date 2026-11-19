"""Dedicated #extra-votes channel: points every member to the website, where extra votes are bought."""
from __future__ import annotations

import os

import discord
from discord.ext import commands

import game_copy
from config import (
    CHANNEL_EXTRA_VOTES,
    CHANNEL_PLAYER,
    EXTRA_VOTES_CHANNEL_CANDIDATES,
    PLAYER_CHANNEL_CANDIDATES,
)
from discord_names import (
    apply_deny_pin_to_game_channels,
    deny_pin_and_slowmode,
    ensure_styled_channel,
    ensure_styled_roles,
    find_game_role,
    find_text_channel,
)

EMBED_TITLE = "Buy extra votes"
VOTES_URL = os.getenv("SITE_URL", "https://memestock.shop").rstrip("/") + "/votes"


def extra_votes_view() -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Buy extra votes", style=discord.ButtonStyle.link, url=VOTES_URL))
    return view


def extra_votes_embed() -> discord.Embed:
    return discord.Embed(
        title=EMBED_TITLE,
        description=(
            "You can purchase extra votes for the competitions using the button below.\n"
            "You'll need to create an account on our website to purchase additional votes.\n"
            "Additional votes can be purchased and used at any time, whether you're a PLAYER or NPC.\n"
            "All purchases are final. No refunds.\n\n"
            f"{game_copy.NOT_INVESTMENT_ADVICE}"
        ),
        color=discord.Color.green(),
    )


class ExtraVotesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
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
        me = guild.me
        no_pin = deny_pin_and_slowmode()
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, **no_pin),
        }
        for key in ("NPC", "PLAYER", "WINNER"):
            role = find_game_role(guild, key)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, **no_pin)
        admin = find_game_role(guild, "ADMIN")
        if admin:
            overwrites[admin] = discord.PermissionOverwrite(view_channel=True, send_messages=True, **no_pin)
        if me:
            overwrites[me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_messages=True
            )
        player_ch = find_text_channel(guild, CHANNEL_PLAYER, *PLAYER_CHANNEL_CANDIDATES)
        parent = player_ch.category if player_ch else None
        if not ch:
            ch = await guild.create_text_channel(
                CHANNEL_EXTRA_VOTES,
                overwrites=overwrites,
                category=parent,
                reason="Extra-votes channel",
            )
        else:
            edits: dict = {"overwrites": overwrites}
            if ch.name != CHANNEL_EXTRA_VOTES:
                edits["name"] = CHANNEL_EXTRA_VOTES
            try:
                await ch.edit(**edits, reason="Extra-votes permission sync")
            except (discord.Forbidden, discord.HTTPException):
                pass
        if player_ch and parent is not None:
            siblings = sorted(parent.text_channels, key=lambda c: c.position)
            ids = [c.id for c in siblings]
            in_place = (
                ch.category == parent
                and player_ch.id in ids
                and ch.id in ids
                and ids.index(ch.id) == ids.index(player_ch.id) + 1
            )
            if not in_place:
                try:
                    await ch.move(
                        after=player_ch,
                        category=parent,
                        sync_permissions=False,
                        reason="Extra votes sits under PLAYER",
                    )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"[extra_votes] could not move the channel under PLAYER: {exc!r}", flush=True)

        posted = None
        async for msg in ch.history(limit=15):
            if msg.author == guild.me and msg.embeds and (msg.embeds[0].title or "") == EMBED_TITLE:
                posted = msg
                break
        embed = extra_votes_embed()
        if posted is None:
            posted = await ch.send(embed=embed, view=extra_votes_view())
        elif posted.embeds[0].description != embed.description or not _links_to_site(posted):
            await posted.edit(embed=embed, view=extra_votes_view())
        if not posted.pinned:
            try:
                await posted.pin(reason="Keep extra-votes checkout visible")
            except (discord.Forbidden, discord.HTTPException):
                pass


def _links_to_site(msg: discord.Message) -> bool:
    for row in msg.components:
        for item in getattr(row, "children", []):
            if getattr(item, "url", None) == VOTES_URL:
                return True
    return False


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ExtraVotesCog(bot))
