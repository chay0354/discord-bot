"""Inspect welcome channel messages + reactions to mirror any existing reaction-role."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT.parent / ".env")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD = os.getenv("DISCORD_GUILD_ID")
S = requests.Session()
S.headers["Authorization"] = f"Bot {TOKEN}"

channels = S.get(f"https://discord.com/api/v10/guilds/{GUILD}/channels", timeout=20).json()
wch = None
for c in channels:
    if c.get("type") != 0:
        continue
    name = c["name"]
    if "WHERE" in name.upper() or "𝙒𝙃𝙀𝙍𝙀" in name or "AM-I" in name.upper() or "AM\u2049" in name:
        wch = c
        break

if not wch:
    print("welcome channel not found. text channels:")
    for c in channels:
        if c.get("type") == 0:
            print("  ", repr(c["name"]), c["id"])
    sys.exit(0)

print("Welcome channel:", repr(wch["name"]), wch["id"])
msgs = S.get(f"https://discord.com/api/v10/channels/{wch['id']}/messages?limit=20", timeout=20).json()
if not isinstance(msgs, list):
    print("error fetching messages:", msgs)
    sys.exit(0)
for m in msgs:
    author = m.get("author", {}).get("username")
    bot = m.get("author", {}).get("bot")
    content = (m.get("content") or "")[:300]
    reacts = [(r["emoji"].get("name"), r["emoji"].get("id"), r["count"]) for r in m.get("reactions", [])]
    embeds = [(e.get("title"), (e.get("description") or "")[:150]) for e in m.get("embeds", [])]
    print("---")
    print(f"by {author} (bot={bot}) | reactions: {reacts}")
    if content:
        print("content:", content)
    if embeds:
        print("embeds:", embeds)
