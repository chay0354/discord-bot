"""Diagnose channel visibility for a specific user via REST API only.

Usage:
    python server/scripts/diagnose_user_channels.py [username_or_id]

Uses Discord HTTP REST endpoints with the bot token; no gateway session
opened, so it does not disturb the live Railway bot.
"""
from __future__ import annotations

import io
import os
import sys
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
    CHANNEL_ADMIN_ACTIONS,
    CHANNEL_BLUE_LIVE,
    CHANNEL_BLUE_TICKER,
    CHANNEL_BLUE_VOTE,
    CHANNEL_FINAL_LEADERBOARD,
    CHANNEL_MID_LIVE,
    CHANNEL_MID_TICKER,
    CHANNEL_MID_VOTE,
    CHANNEL_MOD,
    CHANNEL_PICK_RESULTS,
    CHANNEL_PLAYER,
    CHANNEL_SMALL_LIVE,
    CHANNEL_SMALL_TICKER,
    CHANNEL_SMALL_VOTE,
    CHANNEL_WINNERS,
    PLAYER_CHANNEL_CANDIDATES,
    ROLE_ADMIN,
    ROLE_NPC,
    ROLE_PLAYER,
    ROLE_WINNER,
)

TARGET_QUERY = sys.argv[1] if len(sys.argv) > 1 else "chay"
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()

API = "https://discord.com/api/v10"
S = requests.Session()
S.headers.update({"Authorization": f"Bot {TOKEN}", "User-Agent": "stock-bot-diagnose (chay0354)"})

# Discord permission bit flags
P_VIEW_CHANNEL = 1 << 10
P_READ_MESSAGE_HISTORY = 1 << 16
P_ADMINISTRATOR = 1 << 3
P_SEND_MESSAGES = 1 << 11

SUBSCRIBE_CANDIDATE_NAMES = tuple({CHANNEL_PLAYER, *PLAYER_CHANNEL_CANDIDATES})

GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("Pre-vote (subscriber)", (CHANNEL_SMALL_TICKER, CHANNEL_MID_TICKER, CHANNEL_BLUE_TICKER, CHANNEL_PICK_RESULTS)),
    ("Live leaderboards (subscriber)", (CHANNEL_SMALL_LIVE, CHANNEL_MID_LIVE, CHANNEL_BLUE_LIVE)),
    ("Vote (public)", (CHANNEL_SMALL_VOTE, CHANNEL_MID_VOTE, CHANNEL_BLUE_VOTE)),
    ("Public / winners", (CHANNEL_FINAL_LEADERBOARD, CHANNEL_WINNERS)),
    ("Subscribe / PLAYER", SUBSCRIBE_CANDIDATE_NAMES),
    ("Admin", (CHANNEL_MOD, CHANNEL_ADMIN_ACTIONS)),
]


def get(path: str, **params: Any) -> Any:
    r = S.get(f"{API}{path}", params=params or None, timeout=20)
    if r.status_code == 429:
        retry = float(r.json().get("retry_after", 1))
        import time
        time.sleep(retry + 0.1)
        return get(path, **params)
    r.raise_for_status()
    return r.json()


def search_member(guild_id: str, query: str, limit: int = 50) -> list[dict]:
    return get(f"/guilds/{guild_id}/members/search", query=query, limit=limit)


def list_members(guild_id: str, limit: int = 1000) -> list[dict]:
    out: list[dict] = []
    after = 0
    while True:
        batch = get(f"/guilds/{guild_id}/members", limit=min(limit, 1000), after=after or None)
        if not batch:
            break
        out.extend(batch)
        after = int(batch[-1]["user"]["id"])
        if len(batch) < 1000:
            break
    return out


def list_channels(guild_id: str) -> list[dict]:
    return get(f"/guilds/{guild_id}/channels")


def list_roles(guild_id: str) -> list[dict]:
    return get(f"/guilds/{guild_id}/roles")


def get_member(guild_id: str, user_id: str) -> dict:
    return get(f"/guilds/{guild_id}/members/{user_id}")


def compute_permissions(
    guild_owner_id: str,
    member: dict,
    roles_by_id: dict[str, dict],
    channel: dict,
    parent: dict | None,
) -> tuple[bool, list[str]]:
    """Return (can_view_channel, reasons[])."""
    user_id = str(member["user"]["id"])
    reasons: list[str] = []
    if guild_owner_id == user_id:
        return True, ["server owner"]

    member_role_ids = [str(r) for r in member.get("roles", [])]
    everyone = roles_by_id.get(channel["guild_id"]) if "guild_id" in channel else None
    # @everyone has the same id as the guild
    guild_id = channel.get("guild_id") or (parent or {}).get("guild_id")
    everyone = roles_by_id.get(str(guild_id))

    # Base permissions from roles
    base = int(everyone["permissions"]) if everyone else 0
    for rid in member_role_ids:
        r = roles_by_id.get(rid)
        if r:
            base |= int(r["permissions"])
    if base & P_ADMINISTRATOR:
        reasons.append("role grants ADMINISTRATOR")
        return True, reasons

    def apply_overwrites(perms: int, overwrites: list[dict]) -> tuple[int, list[str]]:
        notes: list[str] = []
        ow_by_id = {str(o["id"]): o for o in overwrites}
        # @everyone overwrite
        if everyone and str(everyone["id"]) in ow_by_id:
            o = ow_by_id[str(everyone["id"])]
            allow = int(o["allow"]); deny = int(o["deny"])
            perms = (perms & ~deny) | allow
            if deny & P_VIEW_CHANNEL:
                notes.append("@everyone DENY view")
            if allow & P_VIEW_CHANNEL:
                notes.append("@everyone ALLOW view")
        # Role overwrites (allow/deny accumulated)
        allow_total = 0
        deny_total = 0
        for rid in member_role_ids:
            o = ow_by_id.get(rid)
            if not o:
                continue
            allow_total |= int(o["allow"])
            deny_total |= int(o["deny"])
            rname = roles_by_id.get(rid, {}).get("name", rid)
            if int(o["deny"]) & P_VIEW_CHANNEL:
                notes.append(f"@{rname} DENY view")
            if int(o["allow"]) & P_VIEW_CHANNEL:
                notes.append(f"@{rname} ALLOW view")
        perms = (perms & ~deny_total) | allow_total
        # Member-specific overwrite
        if user_id in ow_by_id:
            o = ow_by_id[user_id]
            allow = int(o["allow"]); deny = int(o["deny"])
            perms = (perms & ~deny) | allow
            if deny & P_VIEW_CHANNEL:
                notes.append("member DENY view")
            if allow & P_VIEW_CHANNEL:
                notes.append("member ALLOW view")
        return perms, notes

    perms = base
    if parent:
        perms, notes = apply_overwrites(perms, parent.get("permission_overwrites", []) or [])
        for n in notes:
            reasons.append(f"category #{parent.get('name')}: {n}")
    perms, notes = apply_overwrites(perms, channel.get("permission_overwrites", []) or [])
    for n in notes:
        reasons.append(f"channel #{channel.get('name')}: {n}")

    return bool(perms & P_VIEW_CHANNEL), reasons


def main() -> int:
    if not TOKEN:
        print("[ERROR] DISCORD_TOKEN missing in .env")
        return 1
    if not GUILD_ID:
        print("[ERROR] DISCORD_GUILD_ID missing in .env")
        return 1

    print(f"Searching guild {GUILD_ID} for user '{TARGET_QUERY}'...")
    matches: list[dict] = []
    if TARGET_QUERY.isdigit():
        try:
            matches = [get_member(GUILD_ID, TARGET_QUERY)]
        except requests.HTTPError as exc:
            print(f"[ERROR] HTTP {exc.response.status_code}: {exc.response.text[:200]}")
            return 2
    else:
        matches = search_member(GUILD_ID, TARGET_QUERY, limit=25)
    if not matches:
        # Fallback: full member list and substring match
        print("Search returned nothing; falling back to full member list...")
        try:
            all_members = list_members(GUILD_ID)
        except requests.HTTPError as exc:
            print(f"[ERROR] HTTP {exc.response.status_code}: {exc.response.text[:200]}")
            return 3
        q = TARGET_QUERY.lower()
        matches = [
            m for m in all_members
            if q in (m["user"].get("username") or "").lower()
            or q in (m.get("nick") or "").lower()
            or q in (m["user"].get("global_name") or "").lower()
        ]
        if not matches:
            print(f"[ERROR] No member matched '{TARGET_QUERY}' in guild {GUILD_ID}.")
            print(f"Inspected {len(all_members)} members.")
            return 4

    if len(matches) > 1:
        print(f"Multiple matches for '{TARGET_QUERY}':")
        for m in matches:
            u = m["user"]
            print(f"  - id={u['id']}  username={u.get('username')}  global={u.get('global_name')}  nick={m.get('nick')}")
        print("Pass an exact user id as the argument to disambiguate.")
        return 5
    member = matches[0]

    guild_meta = get(f"/guilds/{GUILD_ID}")
    owner_id = str(guild_meta.get("owner_id") or "")
    roles = list_roles(GUILD_ID)
    roles_by_id = {str(r["id"]): r for r in roles}
    role_name_by_id = {str(r["id"]): r["name"] for r in roles}
    channels = list_channels(GUILD_ID)
    channels_by_id = {str(c["id"]): c for c in channels}
    channels_by_name: dict[str, dict] = {}
    for c in channels:
        if c.get("type") == 0:  # text channel
            channels_by_name[(c.get("name") or "").lower()] = c

    u = member["user"]
    print("=" * 72)
    print(f"User: {u.get('username')}  (global_name={u.get('global_name')}, id={u['id']})")
    if member.get("nick"):
        print(f"Nickname: {member['nick']}")
    role_ids = [str(r) for r in member.get("roles", [])]
    role_names = [role_name_by_id.get(rid, rid) for rid in role_ids]
    print(f"Roles: {', '.join(role_names) if role_names else '(none beyond @everyone)'}")
    flags = {
        ROLE_NPC: ROLE_NPC in role_names,
        ROLE_PLAYER: ROLE_PLAYER in role_names,
        ROLE_WINNER: ROLE_WINNER in role_names,
        ROLE_ADMIN: ROLE_ADMIN in role_names,
    }
    print("Game roles:")
    for name, present in flags.items():
        print(f"  - @{name}: {'yes' if present else 'no'}")
    print("=" * 72)

    for group_name, names in GROUPS:
        print(f"\n{group_name}:")
        for cname in names:
            ch = channels_by_name.get(cname.lower())
            if not ch:
                print(f"  [MISSING] #{cname}  — not found in guild")
                continue
            parent_id = ch.get("parent_id")
            parent = channels_by_id.get(str(parent_id)) if parent_id else None
            can_view, reasons = compute_permissions(owner_id, member, roles_by_id, ch, parent)
            state = "VISIBLE" if can_view else "HIDDEN"
            why = "; ".join(reasons) if reasons else "no view overrides; base permission only"
            print(f"  [{state}] #{ch['name']}  ({why})")

    print("\n" + "=" * 72)
    print("VISIBLE = the user sees this channel in Discord; HIDDEN = blocked.")
    print("Category overrides are included; member-specific overrides too.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
