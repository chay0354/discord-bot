"""Strip WINNER from everyone without an active DB grant (REST, no member intent)."""
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
GID = int(os.getenv("DISCORD_GUILD_ID", "1359180229616205864"))


def main() -> int:
    if not TOKEN:
        print("DISCORD_TOKEN missing.", flush=True)
        return 1

    s = requests.Session()
    s.headers.update({"Authorization": f"Bot {TOKEN}", "User-Agent": "stock-bot-strip-winner"})

    roles = s.get(f"{API}/guilds/{GID}/roles").json()
    by_name = {r["name"].upper(): r for r in roles}
    winner_role = by_name.get(ROLE_WINNER.upper())
    npc_role = by_name.get(ROLE_NPC.upper())
    if not winner_role:
        print("WINNER role not found.", flush=True)
        return 1

    active_ids = database.active_winner_user_ids(GID)
    members = s.get(f"{API}/guilds/{GID}/members", params={"limit": 1000}).json()
    winner_role_id = str(winner_role["id"])
    removed = 0

    for m in members:
        uid = int((m.get("user") or {})["id"])
        member_roles = {str(r) for r in m.get("roles", [])}
        if winner_role_id not in member_roles or uid in active_ids:
            continue
        name = (m.get("user") or {}).get("global_name") or (m.get("user") or {}).get("username")
        resp = s.delete(f"{API}/guilds/{GID}/members/{uid}/roles/{winner_role_id}")
        if resp.status_code not in (204, 200):
            print(f"Failed {name} ({uid}): {resp.status_code} {resp.text}", flush=True)
            continue
        removed += 1
        print(f"Removed WINNER from {name} ({uid})", flush=True)
        database.log_event(
            GID,
            "winner_role_removed",
            {"discord_id": uid, "reason": "manual_cleanup"},
        )
        if npc_role:
            member = s.get(f"{API}/guilds/{GID}/members/{uid}").json()
            has_npc = str(npc_role["id"]) in {str(r) for r in member.get("roles", [])}
            if not has_npc:
                put = s.put(f"{API}/guilds/{GID}/members/{uid}/roles/{npc_role['id']}")
                if put.status_code in (204, 200):
                    print(f"  Restored NPC for {name}", flush=True)

    print(f"Done — removed {removed} WINNER role(s).", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
