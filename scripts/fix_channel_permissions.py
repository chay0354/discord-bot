"""Targeted fix for permission drift on winner / final-leaderboard channels and
a safe check (or creation) for the pick-results channel.

Uses Discord REST only (no gateway) so it can run while Railway is online.

Safety: lists candidate channels with similar names before creating anything,
and refuses to create a duplicate when a similar channel already exists. Pass
--apply to actually write changes; otherwise the script is dry-run.

Usage:
    python server/scripts/fix_channel_permissions.py            # dry run
    python server/scripts/fix_channel_permissions.py --apply    # apply
"""
from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

from config import (
    CHANNEL_FINAL_LEADERBOARD,
    CHANNEL_PICK_RESULTS,
    CHANNEL_WINNERS,
    ROLE_ADMIN,
    ROLE_NPC,
    ROLE_PLAYER,
    ROLE_WINNER,
)

APPLY = "--apply" in sys.argv[1:]

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()

API = "https://discord.com/api/v10"
S = requests.Session()
S.headers.update({"Authorization": f"Bot {TOKEN}", "User-Agent": "stock-bot-fix-perms"})

# Discord permission bit flags
P_VIEW_CHANNEL = 1 << 10
P_READ_MESSAGE_HISTORY = 1 << 16
P_SEND_MESSAGES = 1 << 11
P_EMBED_LINKS = 1 << 14
P_ATTACH_FILES = 1 << 15
P_USE_EXTERNAL_EMOJIS = 1 << 18
P_MANAGE_MESSAGES = 1 << 13


def _req(method: str, path: str, **kwargs: Any) -> Any:
    while True:
        r = S.request(method, f"{API}{path}", timeout=20, **kwargs)
        if r.status_code == 429:
            wait = float(r.json().get("retry_after", 1.0))
            print(f"  rate-limited; sleeping {wait:.1f}s")
            time.sleep(wait + 0.1)
            continue
        if r.status_code >= 400:
            raise requests.HTTPError(f"{r.status_code} {r.text[:300]}", response=r)
        if r.status_code == 204 or not r.content:
            return None
        return r.json()


def list_channels(guild_id: str) -> list[dict]:
    return _req("GET", f"/guilds/{guild_id}/channels")


def list_roles(guild_id: str) -> list[dict]:
    return _req("GET", f"/guilds/{guild_id}/roles")


def patch_channel(channel_id: str, payload: dict) -> dict:
    return _req("PATCH", f"/channels/{channel_id}", json=payload)


def create_text_channel(guild_id: str, payload: dict) -> dict:
    return _req("POST", f"/guilds/{guild_id}/channels", json=payload)


def find_channel_by_exact_name(channels: list[dict], name: str) -> dict | None:
    target = name.casefold()
    for c in channels:
        if c.get("type") == 0 and (c.get("name") or "").casefold() == target:
            return c
    return None


def find_similar_channels(channels: list[dict], substrings: list[str]) -> list[dict]:
    """Return text channels whose names contain any of the substrings."""
    out: list[dict] = []
    needles = [s.casefold() for s in substrings if s]
    for c in channels:
        if c.get("type") != 0:
            continue
        n = (c.get("name") or "").casefold()
        if any(s in n for s in needles):
            out.append(c)
    return out


def role_id(roles: list[dict], name: str) -> str | None:
    for r in roles:
        if r["name"] == name:
            return str(r["id"])
    return None


def public_view_overwrites(roles: list[dict], guild_id: str) -> list[dict]:
    """@everyone view; NPC/PLAYER/WINNER view; ADMIN view+send."""
    everyone_id = guild_id  # @everyone role id equals guild id
    npc = role_id(roles, ROLE_NPC)
    player = role_id(roles, ROLE_PLAYER)
    winner = role_id(roles, ROLE_WINNER)
    admin = role_id(roles, ROLE_ADMIN)

    view_only = P_VIEW_CHANNEL | P_READ_MESSAGE_HISTORY
    admin_full = (
        P_VIEW_CHANNEL
        | P_READ_MESSAGE_HISTORY
        | P_SEND_MESSAGES
        | P_EMBED_LINKS
        | P_ATTACH_FILES
        | P_USE_EXTERNAL_EMOJIS
    )

    out = [
        {"id": everyone_id, "type": 0, "allow": str(view_only), "deny": str(P_SEND_MESSAGES)},
    ]
    for rid in (npc, player, winner):
        if rid:
            out.append({"id": rid, "type": 0, "allow": str(view_only), "deny": str(P_SEND_MESSAGES)})
    if admin:
        out.append({"id": admin, "type": 0, "allow": str(admin_full), "deny": "0"})
    return out


def subscriber_overwrites(roles: list[dict], guild_id: str) -> list[dict]:
    """@everyone hidden; PLAYER/WINNER view; ADMIN view+send."""
    everyone_id = guild_id
    npc = role_id(roles, ROLE_NPC)
    player = role_id(roles, ROLE_PLAYER)
    winner = role_id(roles, ROLE_WINNER)
    admin = role_id(roles, ROLE_ADMIN)

    view_only = P_VIEW_CHANNEL | P_READ_MESSAGE_HISTORY
    admin_full = (
        P_VIEW_CHANNEL
        | P_READ_MESSAGE_HISTORY
        | P_SEND_MESSAGES
        | P_EMBED_LINKS
        | P_ATTACH_FILES
        | P_USE_EXTERNAL_EMOJIS
        | P_MANAGE_MESSAGES
    )
    out = [
        {"id": everyone_id, "type": 0, "allow": "0", "deny": str(P_VIEW_CHANNEL)},
    ]
    if npc:
        out.append({"id": npc, "type": 0, "allow": "0", "deny": str(P_VIEW_CHANNEL)})
    if player:
        out.append({"id": player, "type": 0, "allow": str(view_only), "deny": str(P_SEND_MESSAGES)})
    if winner:
        out.append({"id": winner, "type": 0, "allow": str(view_only), "deny": str(P_SEND_MESSAGES)})
    if admin:
        out.append({"id": admin, "type": 0, "allow": str(admin_full), "deny": "0"})
    return out


def main() -> int:
    if not TOKEN or not GUILD_ID:
        print("[ERROR] DISCORD_TOKEN and DISCORD_GUILD_ID must be set.")
        return 1

    mode = "APPLY" if APPLY else "DRY-RUN"
    print(f"Mode: {mode}")
    print(f"Guild: {GUILD_ID}")

    channels = list_channels(GUILD_ID)
    roles = list_roles(GUILD_ID)

    print("\n--- Fix 1: Final leaderboard channel ---")
    target = find_channel_by_exact_name(channels, CHANNEL_FINAL_LEADERBOARD)
    if not target:
        print(f"[WARN] No channel named exactly {CHANNEL_FINAL_LEADERBOARD!r}; skipping.")
    else:
        print(f"Channel: #{target['name']} (id={target['id']})")
        new_ow = public_view_overwrites(roles, GUILD_ID)
        if APPLY:
            patch_channel(target["id"], {"permission_overwrites": new_ow})
            print("  -> updated overwrites: @everyone view, NPC/PLAYER/WINNER view, ADMIN view+send")
        else:
            print("  would update overwrites to: @everyone view, NPC/PLAYER/WINNER view, ADMIN view+send")

    print("\n--- Fix 2: Winners channel ---")
    target = find_channel_by_exact_name(channels, CHANNEL_WINNERS)
    if not target:
        print(f"[WARN] No channel named exactly {CHANNEL_WINNERS!r}; skipping.")
    else:
        print(f"Channel: #{target['name']} (id={target['id']})")
        new_ow = public_view_overwrites(roles, GUILD_ID)
        if APPLY:
            patch_channel(target["id"], {"permission_overwrites": new_ow})
            print("  -> updated overwrites: @everyone view, NPC/PLAYER/WINNER view, ADMIN view+send")
        else:
            print("  would update overwrites to: @everyone view, NPC/PLAYER/WINNER view, ADMIN view+send")

    print("\n--- Fix 3: pick-results channel ---")
    target = find_channel_by_exact_name(channels, CHANNEL_PICK_RESULTS)
    if target:
        print(f"Channel: #{target['name']} (id={target['id']})")
        new_ow = subscriber_overwrites(roles, GUILD_ID)
        if APPLY:
            patch_channel(target["id"], {"permission_overwrites": new_ow})
            print("  -> updated overwrites: @everyone hidden, PLAYER/WINNER view, ADMIN view+send")
        else:
            print("  would update overwrites to: @everyone hidden, PLAYER/WINNER view, ADMIN view+send")
    elif False:
        similar = find_similar_channels(channels, ["pic-results", "pic_result", "pick-result", "pickresult", "picks", "pickresults"])
        if similar:
            print(f"[STOP] '{CHANNEL_PICK_RESULTS}' is missing, but I found channel(s) that look similar:")
            for c in similar:
                print(f"  - #{c['name']} (id={c['id']})")
            print("Not creating a new channel to avoid a duplicate.")
            print("Pick one:")
            print(f"  A) Rename one of the above back to '{CHANNEL_PICK_RESULTS}', OR")
            print(f"  B) Set env var PICK_RESULTS_CHANNEL on Railway to the actual name (e.g. '{similar[0]['name']}').")
        else:
            print(f"No channel named '{CHANNEL_PICK_RESULTS}' or anything similar was found.")
            payload = {
                "name": CHANNEL_PICK_RESULTS,
                "type": 0,
                "permission_overwrites": subscriber_overwrites(roles, GUILD_ID),
                "topic": "PICK RESULTS — 20 stocks per category (small / mid / large).",
            }
            if APPLY:
                created = create_text_channel(GUILD_ID, payload)
                print(f"  -> created #{created['name']} (id={created['id']})")
            else:
                print(f"  would create #{CHANNEL_PICK_RESULTS} with subscriber visibility")

    print("\nDone.")
    if not APPLY:
        print("This was a dry run. Re-run with --apply to write changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
