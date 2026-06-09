"""Shared Discord channel permission overwrite builders."""
from __future__ import annotations

import discord


def role_gated_view_overwrites(
    guild: discord.Guild,
    npc_role: discord.Role,
    player_role: discord.Role,
    winner_role: discord.Role,
    admin_role: discord.Role,
    bot_member: discord.Member,
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    """Hide from @everyone; visible to NPC/PLAYER/WINNER/ADMIN (read-only for game roles)."""
    everyone = guild.default_role
    view_only = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=False,
        read_message_history=True,
    )
    admin_full = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
    )
    bot_full = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        manage_messages=True,
        read_message_history=True,
        embed_links=True,
    )
    return {
        everyone: discord.PermissionOverwrite(view_channel=False),
        npc_role: view_only,
        player_role: view_only,
        winner_role: view_only,
        admin_role: admin_full,
        bot_member: bot_full,
    }


def entry_view_overwrites(
    guild: discord.Guild,
    npc_role: discord.Role,
    player_role: discord.Role,
    winner_role: discord.Role,
    admin_role: discord.Role,
    bot_member: discord.Member,
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    """Onboarding channels (subscribe): @everyone can view, send disabled."""
    everyone = guild.default_role
    view_only = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=False,
        read_message_history=True,
    )
    admin_full = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
    )
    bot_full = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        manage_messages=True,
        read_message_history=True,
        embed_links=True,
    )
    return {
        everyone: view_only,
        npc_role: view_only,
        player_role: view_only,
        winner_role: view_only,
        admin_role: admin_full,
        bot_member: bot_full,
    }
