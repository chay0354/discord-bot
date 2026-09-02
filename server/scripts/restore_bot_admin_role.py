"""Restore the bot-admin role if Part D accidentally renamed it to ADMIN."""
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


class Restorer(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        super().__init__(intents=intents)

    async def on_ready(self) -> None:
        try:
            for guild in self.guilds:
                me = guild.me
                print(f"Guild: {guild.name}", flush=True)
                print(f"  bot top role: {me.top_role.name if me else '?'}", flush=True)
                print(f"  manage_channels={me.guild_permissions.manage_channels if me else None}", flush=True)
                print(f"  manage_roles={me.guild_permissions.manage_roles if me else None}", flush=True)
                print(f"  administrator={me.guild_permissions.administrator if me else None}", flush=True)
                for r in sorted(guild.roles, key=lambda x: x.position, reverse=True):
                    print(
                        f"  pos={r.position:<3} '{r.name}' managed={r.managed} "
                        f"admin={r.permissions.administrator} "
                        f"mch={r.permissions.manage_channels} "
                        f"mroles={r.permissions.manage_roles}",
                        flush=True,
                    )
                bot_admin = discord.utils.get(guild.roles, name="bot-admin")
                if bot_admin:
                    print(f"  bot-admin already exists id={bot_admin.id}", flush=True)
                    continue
                restored = None
                try:
                    async for entry in guild.audit_logs(limit=25, action=discord.AuditLogAction.role_update):
                        before = getattr(entry.changes, "before", None)
                        after = getattr(entry.changes, "after", None)
                        before_name = getattr(before, "name", None)
                        after_name = getattr(after, "name", None)
                        if before_name == "bot-admin" and after_name == "ADMIN" and entry.target:
                            restored = guild.get_role(getattr(entry.target, "id", 0))
                            old_perms = getattr(before, "permissions", None)
                            print(f"  Found renamed bot-admin id={getattr(entry.target, 'id', None)}", flush=True)
                            if restored is None:
                                print("  Role object missing from cache.", flush=True)
                                break
                            try:
                                await restored.edit(
                                    name="bot-admin",
                                    reason="Restore bot-admin name after Part D mistake",
                                )
                                print("  Restored name to bot-admin", flush=True)
                            except discord.HTTPException as exc:
                                print(f"  Name restore failed: {exc}", flush=True)
                                break
                            if old_perms is not None:
                                try:
                                    await restored.edit(
                                        permissions=old_perms,
                                        reason="Restore bot-admin original permissions",
                                    )
                                    print("  Restored original permissions", flush=True)
                                except discord.HTTPException as exc:
                                    print(
                                        f"  Permission restore blocked ({exc}). "
                                        "Please restore bot-admin permissions manually in Discord.",
                                        flush=True,
                                    )
                            break
                except discord.HTTPException as exc:
                    print(f"  Restore failed: {exc}", flush=True)
                if restored is None:
                    print("  Could not identify the renamed bot-admin role from audit log.", flush=True)
        finally:
            await self.close()


async def main() -> int:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN is missing.", flush=True)
        return 1
    await Restorer().start(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
