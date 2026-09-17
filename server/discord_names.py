"""Normalize styled Discord channel/role names so lookups survive emojis
and mathematical letters (𝖫𝖨𝖵𝖤 / 𝖳𝖨𝖢𝖪𝖤𝖱)."""
from __future__ import annotations

import unicodedata
from typing import Iterable

import discord

_ROLE_KEYS = ("ADMIN", "PLAYER", "WINNER", "NPC")


def normalize_discord_name(name: str) -> str:
    folded = unicodedata.normalize("NFKC", name or "").lower()
    return "".join(ch for ch in folded if ch.isalnum())


def names_match(left: str, right: str) -> bool:
    a, b = normalize_discord_name(left), normalize_discord_name(right)
    return bool(a) and a == b


def find_text_channel(
    guild: discord.Guild,
    *names: str,
) -> discord.TextChannel | None:
    wanted = {normalize_discord_name(n) for n in names if n}
    wanted.discard("")
    if not wanted:
        return None
    for ch in guild.text_channels:
        if normalize_discord_name(ch.name) in wanted:
            return ch
    return None


def logical_role_key(name: str) -> str | None:
    n = normalize_discord_name(name)
    if not n:
        return None
    for key in _ROLE_KEYS:
        if n == normalize_discord_name(key):
            return key
    return None


def member_role_keys(member: discord.Member) -> set[str]:
    keys: set[str] = set()
    for role in getattr(member, "roles", []) or []:
        key = logical_role_key(getattr(role, "name", ""))
        if key:
            keys.add(key)
    return keys


async def ensure_styled_roles(guild: discord.Guild) -> dict[str, discord.Role | None]:
    """Rename NPC / PLAYER / WINNER to the emoji display names if needed."""
    from config import ROLE_NPC, ROLE_PLAYER, ROLE_WINNER

    wanted = {"NPC": ROLE_NPC, "PLAYER": ROLE_PLAYER, "WINNER": ROLE_WINNER}
    found: dict[str, discord.Role | None] = {}
    for key, display in wanted.items():
        role = find_game_role(guild, key)
        if role is not None and role.name != display:
            try:
                await role.edit(name=display, reason="Apply game role emoji")
            except (discord.Forbidden, discord.HTTPException):
                pass
        found[key] = role
    return found


async def ensure_styled_channel(
    guild: discord.Guild,
    canonical: str,
    *aliases: str,
) -> discord.TextChannel | None:
    """Find a game channel by alias and rename it to the styled canonical name."""
    channel = find_text_channel(guild, canonical, *aliases)
    if channel is None:
        return None
    if channel.name != canonical:
        try:
            await channel.edit(name=canonical, reason="Apply styled channel name")
        except (discord.Forbidden, discord.HTTPException):
            pass
    return channel


def find_game_role(guild: discord.Guild | None, logical_name: str) -> discord.Role | None:
    if guild is None:
        return None
    want = logical_role_key(logical_name) or str(logical_name).upper()
    for role in guild.roles:
        if getattr(role, "managed", False):
            continue
        if logical_role_key(role.name) == want:
            return role
    return discord.utils.get(guild.roles, name=logical_name)


def deny_pin_and_slowmode() -> dict[str, bool]:
    return {"pin_messages": False, "bypass_slowmode": False}


async def apply_deny_pin_to_game_channels(guild: discord.Guild) -> int:
    """Force PIN MESSAGES / BYPASS SLOWMODE off on every known game channel."""
    from config import ALL_REQUIRED_CHANNELS, CHANNEL_RULES

    updated = 0
    names = (*ALL_REQUIRED_CHANNELS, CHANNEL_RULES)
    for name in names:
        channel = find_text_channel(guild, name)
        if channel is None:
            continue
        mapping = dict(channel.overwrites)
        dirty = False
        for target, overwrite in mapping.items():
            if overwrite.pin_messages is not False or overwrite.bypass_slowmode is not False:
                overwrite.update(pin_messages=False, bypass_slowmode=False)
                mapping[target] = overwrite
                dirty = True
        if dirty:
            try:
                await channel.edit(overwrites=mapping, reason="Deny pin / bypass slowmode")
                updated += 1
            except (discord.Forbidden, discord.HTTPException):
                pass
    return updated
