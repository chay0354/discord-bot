"""Remove WINNER role from a user (manual admin reset). Usage: python reset_user_winner.py [username_or_id]"""
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
from config import ROLE_NPC, ROLE_WINNER

API = "https://discord.com/api/v10"
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "1359180229616205864"))
QUERY = sys.argv[1] if len(sys.argv) > 1 else "chay tests"


def main() -> int:
    if not TOKEN:
        print("DISCORD_TOKEN is missing.", flush=True)
        return 1

    s = requests.Session()
    s.headers.update({"Authorization": f"Bot {TOKEN}", "User-Agent": "stock-bot-reset-winner"})

    members = s.get(f"{API}/guilds/{GUILD_ID}/members", params={"limit": 1000}).json()
    user_id: int | None = None
    if QUERY.isdigit():
        user_id = int(QUERY)
    else:
        q = QUERY.lower()
        for m in members:
            name = (m.get("user") or {}).get("username") or ""
            gname = (m.get("user") or {}).get("global_name") or ""
            if q in name.lower() or q in gname.lower():
                user_id = int((m["user"])["id"])
                print(f"Matched: {gname or name} ({user_id})", flush=True)
                break
    if user_id is None:
        print(f"User not found: {QUERY}", flush=True)
        return 1

    database.revoke_all_active_winner_grants(GUILD_ID)

    roles = s.get(f"{API}/guilds/{GUILD_ID}/roles").json()
    by_name = {r["name"].upper(): r for r in roles}
    winner_role = by_name.get(ROLE_WINNER.upper())
    npc_role = by_name.get(ROLE_NPC.upper())

    member = s.get(f"{API}/guilds/{GUILD_ID}/members/{user_id}")
    if member.status_code == 404:
        print("Member not in guild.", flush=True)
        return 1
    member_roles = {str(r) for r in member.json().get("roles", [])}

    if winner_role and str(winner_role["id"]) in member_roles:
        resp = s.delete(
            f"{API}/guilds/{GUILD_ID}/members/{user_id}/roles/{winner_role['id']}",
        )
        if resp.status_code not in (204, 200):
            print(f"Failed to remove WINNER: {resp.status_code} {resp.text}", flush=True)
            return 1
        print("Removed WINNER role from Discord.", flush=True)
    else:
        print("No WINNER role on Discord (already clear).", flush=True)

    member = s.get(f"{API}/guilds/{GUILD_ID}/members/{user_id}").json()
    member_roles = {str(r) for r in member.get("roles", [])}
    if npc_role and str(npc_role["id"]) not in member_roles:
        resp = s.put(f"{API}/guilds/{GUILD_ID}/members/{user_id}/roles/{npc_role['id']}")
        if resp.status_code in (204, 200):
            print("Restored NPC role.", flush=True)

    database.log_event(
        GUILD_ID,
        "reset_winner_grants",
        {"user_id": user_id, "manual": True, "query": QUERY},
    )
    print("Done — chay tests is back to NPC only.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
