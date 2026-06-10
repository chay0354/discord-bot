"""Inspect SUBSCRIBE category and channel permission overwrites."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GID = os.getenv("DISCORD_GUILD_ID", "1359180229616205864").strip()
API = "https://discord.com/api/v10"
P_VIEW = 1 << 10
P_ADMIN = 1 << 3

s = requests.Session()
s.headers["Authorization"] = f"Bot {TOKEN}"

roles = s.get(f"{API}/guilds/{GID}/roles").json()
channels = s.get(f"{API}/guilds/{GID}/channels").json()
role_by_id = {r["id"]: r["name"] for r in roles}

print("=== Game roles ===")
for r in roles:
    if r["name"] in ("ADMIN", "PLAYER", "NPC", "WINNER"):
        perms = int(r.get("permissions", 0))
        print(
            f"  {r['name']}: id={r['id']} "
            f"administrator={bool(perms & P_ADMIN)} permissions={perms}"
        )

cat = next(
    (c for c in channels if c.get("type") == 4 and "SUBSCRIBE" in (c.get("name") or "").upper()),
    None,
)
print("\n=== SUBSCRIBE category ===")
if not cat:
    print("  NOT FOUND")
else:
    print(f"  name={cat['name']!r} id={cat['id']}")
    for ow in cat.get("permission_overwrites", []):
        rid = ow["id"]
        name = "@everyone" if rid == GID else role_by_id.get(rid, rid)
        allow = int(ow.get("allow", 0))
        deny = int(ow.get("deny", 0))
        print(f"  {name}: allow_view={bool(allow & P_VIEW)} deny_view={bool(deny & P_VIEW)}")

print("\n=== Channels under SUBSCRIBE ===")
if cat:
    kids = [c for c in channels if c.get("type") == 0 and c.get("parent_id") == cat["id"]]
    for ch in sorted(kids, key=lambda c: c.get("name", "")):
        print(f"\n  #{ch['name']} (id={ch['id']})")
        for ow in ch.get("permission_overwrites", []):
            rid = ow["id"]
            name = "@everyone" if rid == GID else role_by_id.get(rid, rid)
            allow = int(ow.get("allow", 0))
            deny = int(ow.get("deny", 0))
            print(f"    {name}: allow_view={bool(allow & P_VIEW)} deny_view={bool(deny & P_VIEW)}")
