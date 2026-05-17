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
    init_db()
    bot = _build_bot()
    app_state.bot = bot
    await _load_cogs(bot)
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set")

    print(f"[api] Listening on 0.0.0.0:{API_PORT}", flush=True)
    api_task = asyncio.create_task(_serve_api(), name="crm_api")
    try:
        await bot.start(TOKEN)
    finally:
        app_state.bot_ready = False
        api_task.cancel()
        try:
            await api_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
