"""
Railway / local entry: Discord bot + FastAPI admin API in one process.
"""
from __future__ import annotations

import asyncio
import os
import sys

import discord
import uvicorn
from discord.ext import commands
from dotenv import load_dotenv

# Ensure server/ is on path when started from repo root.
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

load_dotenv(os.path.join(ROOT, ".env"))
load_dotenv(os.path.join(os.path.dirname(ROOT), ".env"))  # repo root .env

import app_state
from database import init_db

TOKEN = os.getenv("DISCORD_TOKEN")
API_PORT = int(os.getenv("PORT", os.getenv("CRM_API_PORT", "8000")))


def _build_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready() -> None:
        app_state.bot_ready = True
        print(f"[bot] Logged in as {bot.user} ({bot.user.id})", flush=True)

    return bot


async def _load_cogs(bot: commands.Bot) -> None:
    cogs_dir = os.path.join(ROOT, "cogs")
    for fn in os.listdir(cogs_dir):
        if fn.endswith(".py") and not fn.startswith("__"):
            ext = f"cogs.{fn[:-3]}"
            try:
                await bot.load_extension(ext)
                print(f"[bot] Loaded {ext}", flush=True)
            except Exception as exc:
                print(f"[bot] Failed {ext}: {exc!r}", flush=True)


async def _serve_api() -> None:
    config = uvicorn.Config(
        "api.main:app",
        host="0.0.0.0",
        port=API_PORT,
        log_level=os.getenv("UVICORN_LOG_LEVEL", "info"),
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    bot = _build_bot()
    app_state.bot = bot

    # Start the API/health server FIRST so the Railway healthcheck (/api/health)
    # passes immediately, even if the database or Discord login are temporarily
    # slow/unavailable. This prevents a crash-loop from killing the deployment.
    print(f"[api] Listening on 0.0.0.0:{API_PORT}", flush=True)
    api_task = asyncio.create_task(_serve_api(), name="crm_api")
    await asyncio.sleep(0)  # yield so uvicorn can bind the socket

    # DB init must never be fatal to the process; log and continue if it fails
    # (e.g. Supabase paused). Run off the event loop so it can't block the API.
    try:
        await asyncio.to_thread(init_db)
        print("[db] init_db ok", flush=True)
    except Exception as exc:
        print(f"[db] init_db failed (continuing): {exc!r}", flush=True)

    await _load_cogs(bot)

    if not TOKEN:
        print("[bot] DISCORD_TOKEN not set; serving API only", flush=True)
        await api_task
        return

    try:
        await bot.start(TOKEN)
    except Exception as exc:
        print(f"[bot] Discord client stopped: {exc!r}", flush=True)
    finally:
        app_state.bot_ready = False

    # Keep the API alive so the deployment stays healthy and logs stay visible
    # even if the Discord client exits.
    await api_task


if __name__ == "__main__":
    asyncio.run(main())
