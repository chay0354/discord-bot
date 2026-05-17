# main.py – רק הקטע הרלוונטי לטעינת הקוגים
import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
from database import init_db

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")


async def load_cogs():
    for fn in os.listdir("./cogs"):
        if fn.endswith(".py") and not fn.startswith("__"):
            ext = f"cogs.{fn[:-3]}"
            try:
                await bot.load_extension(ext)
                print(f"✅ Loaded extension {ext}")
            except Exception as e:
                print(f"❌ Error loading extension {ext}: {e}")


async def main():
    init_db()
    await load_cogs()
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set")
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
