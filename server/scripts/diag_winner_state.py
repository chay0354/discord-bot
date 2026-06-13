"""Diagnose WINNER grants vs Discord roles."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

import database
from config import ROLE_WINNER

API = "https://discord.com/api/v10"
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GID = int(os.getenv("DISCORD_GUILD_ID", "1359180229616205864"))

s = requests.Session()
s.headers["Authorization"] = f"Bot {TOKEN}"

roles = s.get(f"{API}/guilds/{GID}/roles").json()
winner_role = next((r for r in roles if r["name"].upper() == ROLE_WINNER.upper()), None)
winner_role_id = str(winner_role["id"]) if winner_role else None

members = s.get(f"{API}/guilds/{GID}/members", params={"limit": 1000}).json()
discord_winners: list[tuple[int, str]] = []
for m in members:
    if winner_role_id and winner_role_id in {str(r) for r in m.get("roles", [])}:
        u = m.get("user") or {}
        discord_winners.append((int(u["id"]), u.get("global_name") or u.get("username") or str(u["id"])))

now = database.utc_now_iso()
active = database.active_winner_grants(GID, now)
expired_pending = database.expired_winner_grants(GID, now)

print(f"Now (UTC): {now}")
print(f"\nDiscord members with {ROLE_WINNER} role ({len(discord_winners)}):")
for uid, name in discord_winners:
    print(f"  {name} ({uid})")

print(f"\nActive DB grants ({len(active)}):")
for row in active:
    print(
        f"  user={row['user_id']} week={row['week_key']} "
        f"expires={row.get('expires_at')} id={row.get('id')}"
    )

print(f"\nExpired DB grants not yet marked removed ({len(expired_pending)}):")
for row in expired_pending:
    print(
        f"  user={row['user_id']} week={row['week_key']} "
        f"expires={row.get('expires_at')} id={row.get('id')}"
    )

active_ids = {int(r["user_id"]) for r in active}
discord_ids = {uid for uid, _ in discord_winners}
print("\nMismatch:")
print(f"  role on Discord but no active grant: {sorted(discord_ids - active_ids)}")
print(f"  active grant but no Discord role: {sorted(active_ids - discord_ids)}")
