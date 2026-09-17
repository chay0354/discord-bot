from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

import discord

from discord_names import find_game_role, find_text_channel
from config import (
    CHANNEL_ADMIN_ACTIONS,
    CHANNEL_BLUE_LIVE,
    CHANNEL_BLUE_TICKER,
    CHANNEL_BLUE_VOTE,
    CHANNEL_EXTRA_VOTES,
    CHANNEL_FINAL_LEADERBOARD,
    CHANNEL_MANAGE_SUBSCRIPTION,
    CHANNEL_MID_LIVE,
    CHANNEL_MID_TICKER,
    CHANNEL_MID_VOTE,
    CHANNEL_MOD,
    CHANNEL_PICK_RESULTS,
    CHANNEL_QA,
    CHANNEL_RULES,
    CHANNEL_SMALL_LIVE,
    CHANNEL_SMALL_TICKER,
    CHANNEL_SMALL_VOTE,
    CHANNEL_SUBSCRIBE,
    CHANNEL_WINNERS,
    EXTRA_VOTES_CHANNEL_CANDIDATES,
    MANAGE_SUBSCRIPTION_CHANNEL_CANDIDATES,
    PICK_RESULTS_CHANNEL_CANDIDATES,
    QA_CHANNEL_CANDIDATES,
    ROLE_ADMIN,
    ROLE_NPC,
    ROLE_PLAYER,
    ROLE_WINNER,
    RULES_CHANNEL_CANDIDATES,
    SUBSCRIBE_CHANNEL_CANDIDATES,
)


def _find_channel(guild: discord.Guild, *names: str) -> discord.TextChannel | None:
    return find_text_channel(guild, *names)


async def _ensure_role(
    guild: discord.Guild,
    name: str,
    permissions: discord.Permissions,
) -> tuple[discord.Role, bool]:
    role = find_game_role(guild, name) or discord.utils.get(guild.roles, name=name)
    if role and getattr(role, "managed", False):
        print(f"[SKIP] managed role @{role.name}", flush=True)
        return role, False
    if role:
        edits: dict = {}
        if role.name != name:
            edits["name"] = name
        # Never rewrite an existing role's permission bitfield. A previous
        # run stripped bot-admin by treating it as ADMIN.
        if edits:
            print(f"[ROLE] editing @{role.name} -> {edits.keys()}", flush=True)
            try:
                await asyncio.wait_for(
                    role.edit(**edits, reason="Stock bot permission verification"),
                    timeout=25,
                )
            except asyncio.TimeoutError:
                print(f"[WARN] Timed out editing role @{role.name}", flush=True)
            except discord.Forbidden:
                print(f"[WARN] Cannot edit role @{role.name}; check bot role hierarchy.", flush=True)
            except discord.HTTPException as exc:
                print(f"[WARN] Role edit @{role.name} failed: {exc}", flush=True)
        else:
            print(f"[ROLE] @{role.name} already current", flush=True)
        return role, False
    role = await guild.create_role(
        name=name,
        permissions=permissions,
        reason="Stock bot permission verification",
    )
    return role, True


async def _ensure_channel(
    guild: discord.Guild,
    name: str,
    overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite],
    *aliases: str,
    rename: bool = True,
) -> None:
    channel = _find_channel(guild, name, *aliases)
    if not channel:
        channel = await guild.create_text_channel(
            name,
            overwrites=overwrites,
            reason="Stock bot permission verification",
        )
        print(f"[CREATE] #{name}", flush=True)
        return
    try:
        await asyncio.wait_for(
            channel.edit(overwrites=overwrites, reason="Stock bot permission verification"),
            timeout=25,
        )
        print(f"[UPDATE] #{channel.name}", flush=True)
    except asyncio.TimeoutError:
        print(f"[WARN] Timed out updating #{channel.name}", flush=True)
    except discord.HTTPException as exc:
        print(f"[WARN] Channel overwrite #{channel.name} failed: {exc}", flush=True)
    if rename and channel.name != name:
        try:
            await asyncio.wait_for(
                channel.edit(name=name, reason="Stock bot Part D channel rename"),
                timeout=25,
            )
            print(f"[RENAME] #{channel.name} -> #{name}", flush=True)
        except asyncio.TimeoutError:
            print(f"[WARN] Timed out renaming #{channel.name}", flush=True)
        except discord.HTTPException as exc:
            print(f"[WARN] Channel rename #{channel.name} -> #{name} failed: {exc}", flush=True)


class PermissionEnsurer(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents)

    async def on_ready(self) -> None:
        assert self.user is not None
        print(f"Logged in as {self.user}", flush=True)
        try:
            await self._apply_all()
        except Exception as exc:
            print(f"[ERROR] permission apply failed: {exc!r}", flush=True)
            raise
        finally:
            await self.close()

    async def _apply_all(self) -> None:
        for guild in self.guilds:
            me = guild.me
            if not me:
                continue
            print(f"Ensuring permissions in {guild.name}", flush=True)

            admin_perms = discord.Permissions(
                manage_roles=True,
                manage_messages=True,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )
            subscriber_perms = discord.Permissions(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
                use_external_emojis=True,
            )
            subscriber_perms.pin_messages = False
            subscriber_perms.bypass_slowmode = False
            admin_perms.pin_messages = False
            admin_perms.bypass_slowmode = False
            skip_roles = "--channels-only" in sys.argv
            if skip_roles:
                print("[ROLE] skipped (--channels-only)", flush=True)
                npc_role = find_game_role(guild, "NPC")
                player_role = find_game_role(guild, "PLAYER")
                winner_role = find_game_role(guild, "WINNER")
                admin_role = find_game_role(guild, "ADMIN") or discord.utils.get(guild.roles, name=ROLE_ADMIN)
                created_winner = False
                if not all((npc_role, player_role, winner_role, admin_role)):
                    print("[ERROR] Missing a game role; cannot apply channel overwrites.", flush=True)
                    continue
            else:
                print("[ROLE] ensuring NPC / PLAYER / WINNER / ADMIN", flush=True)
                npc_role, _ = await _ensure_role(guild, ROLE_NPC, discord.Permissions.none())
                player_role, _ = await _ensure_role(guild, ROLE_PLAYER, subscriber_perms)
                winner_role, created_winner = await _ensure_role(guild, ROLE_WINNER, subscriber_perms)
                admin_role, _ = await _ensure_role(guild, ROLE_ADMIN, admin_perms)
            if created_winner:
                print(f"[CREATE] @{ROLE_WINNER}", flush=True)

            everyone = guild.default_role
            no_pin = {"pin_messages": False, "bypass_slowmode": False}

            def public_overwrites() -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
                # @everyone hidden: a brand-new member sees only RULES until they get NPC.
                return {
                    everyone: discord.PermissionOverwrite(view_channel=False, send_messages=False, **no_pin),
                    npc_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                    player_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                    winner_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                    admin_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, **no_pin),
                    me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True, read_message_history=True, embed_links=True),
                }

            def subscribe_funnel_overwrites() -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
                """Subscribe / become-PLAYER channel: NPCs only (hide from PLAYER and ADMIN)."""
                return {
                    everyone: discord.PermissionOverwrite(view_channel=False, send_messages=False, **no_pin),
                    npc_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                    player_role: discord.PermissionOverwrite(view_channel=False, **no_pin),
                    winner_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                    admin_role: discord.PermissionOverwrite(view_channel=False, **no_pin),
                    me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True, read_message_history=True, embed_links=True),
                }

            def subscriber_overwrites() -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
                return {
                    everyone: discord.PermissionOverwrite(view_channel=False, **no_pin),
                    npc_role: discord.PermissionOverwrite(view_channel=False, **no_pin),
                    player_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                    winner_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                    admin_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, **no_pin),
                    me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True, read_message_history=True, embed_links=True),
                }

            def mod_overwrites() -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
                return {
                    everyone: discord.PermissionOverwrite(view_channel=False, **no_pin),
                    npc_role: discord.PermissionOverwrite(view_channel=False, **no_pin),
                    player_role: discord.PermissionOverwrite(view_channel=False, **no_pin),
                    winner_role: discord.PermissionOverwrite(view_channel=False, **no_pin),
                    admin_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, **no_pin),
                    me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True, read_message_history=True, embed_links=True),
                }

            def rules_overwrites() -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
                # Brand-new members can see only this channel until they get NPC.
                return {
                    everyone: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                    npc_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                    player_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                    winner_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True, **no_pin),
                    admin_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, **no_pin),
                    me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True, read_message_history=True, embed_links=True),
                }

            channel_specs: list[tuple[str, dict, tuple[str, ...], bool]] = [
                (CHANNEL_SMALL_TICKER, subscriber_overwrites(), (), True),
                (CHANNEL_MID_TICKER, subscriber_overwrites(), (), True),
                (CHANNEL_BLUE_TICKER, subscriber_overwrites(), (), True),
                (CHANNEL_PICK_RESULTS, subscriber_overwrites(), PICK_RESULTS_CHANNEL_CANDIDATES, True),
                (CHANNEL_SMALL_LIVE, subscriber_overwrites(), (), True),
                (CHANNEL_MID_LIVE, subscriber_overwrites(), (), True),
                (CHANNEL_BLUE_LIVE, subscriber_overwrites(), (), True),
                (CHANNEL_SMALL_VOTE, public_overwrites(), (), True),
                (CHANNEL_MID_VOTE, public_overwrites(), (), True),
                (CHANNEL_BLUE_VOTE, public_overwrites(), (), True),
                (CHANNEL_MOD, mod_overwrites(), (), False),
                (CHANNEL_ADMIN_ACTIONS, mod_overwrites(), (), False),
                (CHANNEL_FINAL_LEADERBOARD, public_overwrites(), (), False),
                (CHANNEL_WINNERS, public_overwrites(), (), False),
                (CHANNEL_MANAGE_SUBSCRIPTION, public_overwrites(), MANAGE_SUBSCRIPTION_CHANNEL_CANDIDATES, True),
                (CHANNEL_QA, public_overwrites(), QA_CHANNEL_CANDIDATES, True),
                (CHANNEL_EXTRA_VOTES, subscriber_overwrites(), EXTRA_VOTES_CHANNEL_CANDIDATES, True),
                (CHANNEL_RULES, rules_overwrites(), RULES_CHANNEL_CANDIDATES, False),
            ]
            for name, overwrites, aliases, rename in channel_specs:
                await _ensure_channel(guild, name, overwrites, *aliases, rename=rename)

            await _ensure_channel(
                guild,
                CHANNEL_SUBSCRIBE,
                subscribe_funnel_overwrites(),
                *SUBSCRIBE_CHANNEL_CANDIDATES,
                rename=True,
            )


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN is missing.", flush=True)
        return 1
    client = PermissionEnsurer()
    await client.start(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
