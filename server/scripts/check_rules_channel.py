"""Quick check: RULES channel overwrites for @everyone."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT.parent / ".env")

TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD = os.getenv("DISCORD_GUILD_ID", "")
from config import CHANNEL_RULES, ROLE_NPC, ROLE_PLAYER, ROLE_WINNER  # noqa: E402

S = requests.Session()
S.headers["Authorization"] = f"Bot {TOKEN}"
channels = S.get(f"https://discord.com/api/v10/guilds/{GUILD}/channels", timeout=20).json()
roles = S.get(f"https://discord.com/api/v10/guilds/{GUILD}/roles", timeout=20).json()
role_names = {r["id"]: r["name"] for r in roles}

P_VIEW = 1 << 10
target = next((c for c in channels if c.get("type") == 0 and c["name"] == CHANNEL_RULES), None)
if not target:
    print(f"Channel {CHANNEL_RULES!r} not found")
    sys.exit(1)
print(f"Channel: #{target['name']}")
for ow in target.get("permission_overwrites", []):
    rid = ow["id"]
    name = "@everyone" if rid == GUILD else role_names.get(rid, rid)
    allow = int(ow.get("allow", "0") or 0)
    deny = int(ow.get("deny", "0") or 0)
    can_view = bool(allow & P_VIEW) and not bool(deny & P_VIEW)
    print(f"  {name}: view_channel={can_view}")
