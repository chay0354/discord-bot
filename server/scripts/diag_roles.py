"""One-shot diagnostic: log in, print role hierarchy + NPC assignability.

Run from repo root:  python server/scripts/diag_roles.py
"""
from __future__ import annotations

import os
import sys

import discord
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, ".env"))
load_dotenv(os.path.join(os.path.dirname(ROOT), ".env"))

from config import ROLE_NPC, ROLE_PLAYER, ROLE_WINNER  # noqa: E402

TOKEN = os.getenv("DISCORD_TOKEN")


def main() -> None:
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        print(f"\n=== Logged in as {client.user} ===", flush=True)
        print(f"members intent (code): {client.intents.members}", flush=True)
        for guild in client.guilds:
            print(f"\nGUILD: {guild.name} ({guild.id})", flush=True)
            print(f"  member_count (server says): {guild.member_count}", flush=True)
            print(f"  cached members BEFORE chunk: {len(guild.members)}", flush=True)
            import asyncio as _a
            try:
                await _a.wait_for(guild.chunk(), timeout=20)
                print(f"  cached members AFTER chunk: {len(guild.members)}", flush=True)
            except _a.TimeoutError:
                print("  (guild.chunk() timed out; using already-cached members)", flush=True)
            me = guild.me
            print(f"  Bot top role: '{me.top_role.name}' position={me.top_role.position}", flush=True)
            print(f"  Bot manage_roles permission: {me.guild_permissions.manage_roles}", flush=True)

            print("  --- Role list (high -> low) ---", flush=True)
            for r in sorted(guild.roles, key=lambda x: x.position, reverse=True):
                managed = " [MANAGED/locked]" if r.managed else ""
                print(f"    pos={r.position:<3} '{r.name}'{managed}", flush=True)

            for rname in (ROLE_NPC, ROLE_PLAYER, ROLE_WINNER):
                role = discord.utils.get(guild.roles, name=rname)
                if not role:
                    print(f"  !! Role '{rname}' NOT FOUND in this guild", flush=True)
                    continue
                can_assign = (role < me.top_role) and me.guild_permissions.manage_roles and not role.managed
                print(
                    f"  Role '{rname}': pos={role.position} managed={role.managed} "
                    f"-> bot can assign: {can_assign}",
                    flush=True,
                )

            print("  --- Members without PLAYER/WINNER/NPC ---", flush=True)
            target = {ROLE_NPC.upper(), ROLE_PLAYER.upper(), ROLE_WINNER.upper()}
            none_count = 0
            for m in guild.members:
                if m.bot:
                    continue
                names = {x.name.upper() for x in m.roles}
                if not (target & names):
                    none_count += 1
                    print(f"    NO-ROLE: {m} ({m.id}) roles={[x.name for x in m.roles if x.name!='@everyone']}", flush=True)
            print(f"  Total non-bot members missing a game role: {none_count}", flush=True)

        await client.close()

    client.run(TOKEN)


if __name__ == "__main__":
    main()
