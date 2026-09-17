"""Reset a user to NPC as if they never subscribed.

Removes PLAYER (and WINNER if present), restores NPC, deletes the DB
subscription row. Does not cancel Stripe — cancel there separately if needed.

Usage: python scripts/reset_user_subscription.py [username_or_discord_id]
"""
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
from config import ROLE_NPC, ROLE_PLAYER, ROLE_WINNER

API = "https://discord.com/api/v10"
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "1359180229616205864"))
QUERY = sys.argv[1] if len(sys.argv) > 1 else "chay tests"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bot {TOKEN}", "User-Agent": "stock-bot-reset-subscription"})
    return s


def _find_user_id(s: requests.Session, query: str) -> int | None:
    if query.isdigit():
        return int(query)
    q = query.lower()
    members = s.get(f"{API}/guilds/{GUILD_ID}/members", params={"limit": 1000}).json()
    for m in members:
        user = m.get("user") or {}
        name = user.get("username") or ""
        gname = user.get("global_name") or ""
        nick = m.get("nick") or ""
        if q in name.lower() or q in gname.lower() or q in nick.lower():
            uid = int(user["id"])
            print(f"Matched: {nick or gname or name} ({uid})", flush=True)
            return uid
    return None


def main() -> int:
    if not TOKEN:
        print("DISCORD_TOKEN is missing.", flush=True)
        return 1

    s = _session()
    user_id = _find_user_id(s, QUERY)
    if user_id is None:
        print(f"User not found: {QUERY}", flush=True)
        return 1

    sub = database.get_subscription(user_id)
    if sub:
        print(f"Subscription before: status={sub.get('status')!r} stripe={sub.get('stripe_subscription_id')}", flush=True)
    else:
        print("No subscription row in database.", flush=True)

    roles = s.get(f"{API}/guilds/{GUILD_ID}/roles").json()
    by_name = {r["name"].upper(): r for r in roles}
    player_role = by_name.get(ROLE_PLAYER.upper())
    winner_role = by_name.get(ROLE_WINNER.upper())
    npc_role = by_name.get(ROLE_NPC.upper())

    member_resp = s.get(f"{API}/guilds/{GUILD_ID}/members/{user_id}")
    if member_resp.status_code == 404:
        print("Member not in guild.", flush=True)
        return 1
    member_roles = {str(r) for r in member_resp.json().get("roles", [])}

    for label, role in (("PLAYER", player_role), ("WINNER", winner_role)):
        if role and str(role["id"]) in member_roles:
            resp = s.delete(f"{API}/guilds/{GUILD_ID}/members/{user_id}/roles/{role['id']}")
            if resp.status_code not in (204, 200):
                print(f"Failed to remove {label}: {resp.status_code} {resp.text}", flush=True)
                return 1
            print(f"Removed {label} role from Discord.", flush=True)

    member_resp = s.get(f"{API}/guilds/{GUILD_ID}/members/{user_id}")
    member_roles = {str(r) for r in member_resp.json().get("roles", [])}
    if npc_role and str(npc_role["id"]) not in member_roles:
        resp = s.put(f"{API}/guilds/{GUILD_ID}/members/{user_id}/roles/{npc_role['id']}")
        if resp.status_code in (204, 200):
            print("Restored NPC role.", flush=True)
        else:
            print(f"Failed to add NPC: {resp.status_code} {resp.text}", flush=True)
            return 1
    else:
        print("NPC role already present.", flush=True)

    if sub:
        database._request("DELETE", "subscriptions", query=f"?discord_id=eq.{user_id}")
        print("Deleted subscription row from database.", flush=True)

    database.log_event(
        GUILD_ID,
        "subscription_reset_manual",
        {"discord_id": user_id, "query": QUERY, "had_subscription": bool(sub)},
    )
    print(f"Done — <@{user_id}> reset to NPC-only (no DB subscription).", flush=True)
    if sub and sub.get("stripe_subscription_id"):
        print(
            "Note: Stripe subscription may still be active. Cancel in Stripe dashboard "
            "or the next webhook could recreate PLAYER.",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
