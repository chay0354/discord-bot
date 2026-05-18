from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any

from aiohttp import web
import discord
from discord.ext import commands

import database
from config import (
    CHANNEL_MOD,
    ROLE_PLAYER,
    STRIPE_WEBHOOK_HOST,
    STRIPE_WEBHOOK_PORT,
    StripeSettings,
)
from services.stripe_client import (
    StripeClientError,
    create_billing_portal_session,
    create_checkout_session,
    retrieve_subscription,
    verify_webhook_signature,
)


def _find_text_channel(guild: discord.Guild, name: str) -> discord.TextChannel | None:
    for ch in guild.text_channels:
        if ch.name.lower() == name.lower():
            return ch
    return None


def _period_end_from_subscription(subscription: dict[str, Any]) -> str | None:
    raw = subscription.get("current_period_end")
    if not raw:
        return None
    return datetime.fromtimestamp(int(raw), tz=timezone.utc).isoformat()


def _discord_id_from_payload(obj: dict[str, Any]) -> int | None:
    for source in (obj.get("metadata") or {}, obj):
        raw = source.get("discord_id") or source.get("client_reference_id")
        if raw:
            try:
                return int(raw)
            except ValueError:
                return None
    return None


def _marketing_consent_from_session(obj: dict[str, Any]) -> bool:
    consent = obj.get("consent") or {}
    promotions = consent.get("promotions")
    return promotions == "opt_in"


class BillingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._runner: web.AppRunner | None = None

    async def cog_load(self) -> None:
        settings = StripeSettings()
        api_port = int(os.getenv("PORT", os.getenv("CRM_API_PORT", "8000")))
        webhook_port = int(os.getenv("STRIPE_WEBHOOK_PORT", str(STRIPE_WEBHOOK_PORT)))
        # PaaS (Railway, etc.) exposes one PORT; never bind a second listener on it.
        use_api_webhook = bool(os.getenv("PORT")) or webhook_port == api_port
        if use_api_webhook:
            print(
                f"[billing] Stripe webhook on CRM API at POST /stripe/webhook (port {api_port})",
                flush=True,
            )
            return
        if not settings.webhook_secret:
            print("[billing] Stripe webhook server disabled (no STRIPE_WEBHOOK_SECRET)", flush=True)
            return
        self._runner = web.AppRunner(self._app())
        await self._runner.setup()
        site = web.TCPSite(self._runner, STRIPE_WEBHOOK_HOST, STRIPE_WEBHOOK_PORT)
        await site.start()
        print(f"[billing] Stripe webhook listening on {STRIPE_WEBHOOK_HOST}:{STRIPE_WEBHOOK_PORT}", flush=True)

    async def cog_unload(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    def _app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/stripe/webhook", self._handle_webhook)
        return app

    async def _mod_log(self, guild: discord.Guild | None, title: str, body: str, color: discord.Color) -> None:
        if not guild:
            return
        ch = _find_text_channel(guild, CHANNEL_MOD)
        if not ch:
            return
        try:
            await ch.send(embed=discord.Embed(title=title, description=body, color=color))
        except Exception:
            pass

    async def _set_player_role(self, discord_id: int, active: bool) -> None:
        for guild in self.bot.guilds:
            member = guild.get_member(discord_id)
            if not member:
                continue
            role = discord.utils.get(guild.roles, name=ROLE_PLAYER)
            if not role:
                await self._mod_log(guild, "PLAYER role missing", f"Cannot update <@{discord_id}>.", discord.Color.red())
                continue
            try:
                if active and role not in member.roles:
                    await member.add_roles(role, reason="Stripe subscription active")
                    await member.send("Your subscription is active. PLAYER role has been added.")
                elif not active and role in member.roles:
                    await member.remove_roles(role, reason="Stripe subscription inactive")
                    await member.send("Your subscription is no longer active. PLAYER role has been removed.")
            except Exception:
                await self._mod_log(guild, "PLAYER role update warning", f"Could not DM or update <@{discord_id}>.", discord.Color.orange())

    async def _sync_subscription(self, obj: dict[str, Any], event_type: str) -> int | None:
        discord_id = _discord_id_from_payload(obj)
        subscription_id = obj.get("subscription") or obj.get("id")
        subscription = obj if obj.get("object") == "subscription" else None
        if subscription_id and not subscription:
            try:
                subscription = await asyncio.to_thread(retrieve_subscription, str(subscription_id))
            except Exception:
                subscription = None
        if not discord_id and subscription:
            discord_id = _discord_id_from_payload(subscription)
        if not discord_id:
            return None

        customer_details = obj.get("customer_details") or {}
        database.upsert_user(
            discord_id,
            username=(obj.get("metadata") or {}).get("discord_username"),
            full_name=customer_details.get("name"),
            email=customer_details.get("email") or obj.get("customer_email"),
            phone=(customer_details.get("phone") if isinstance(customer_details, dict) else None),
            marketing_consent=_marketing_consent_from_session(obj),
        )

        status = (subscription or obj).get("status") or "unknown"
        payment_status = obj.get("payment_status") or (subscription or {}).get("collection_method")
        cancel_at_period_end = bool((subscription or {}).get("cancel_at_period_end"))
        current_period_end = _period_end_from_subscription(subscription or {})
        canceled_at_raw = (subscription or {}).get("canceled_at")
        canceled_at = datetime.fromtimestamp(int(canceled_at_raw), tz=timezone.utc).isoformat() if canceled_at_raw else None

        if event_type == "invoice.payment_failed":
            status = "payment_failed"
            payment_status = "failed"
        elif cancel_at_period_end and status == "active":
            status = "active_until_period_end"

        database.upsert_subscription(
            discord_id,
            status=status,
            payment_status=payment_status,
            stripe_customer_id=(subscription or obj).get("customer"),
            stripe_subscription_id=(subscription or {}).get("id") or obj.get("subscription"),
            current_period_end=current_period_end,
            canceled_at=canceled_at,
        )
        database.log_event(None, "stripe_webhook", {"type": event_type, "discord_id": discord_id, "status": status})

        active = status in {"active", "trialing", "active_until_period_end"}
        if event_type == "invoice.payment_failed":
            active = False
        await self._set_player_role(discord_id, active)
        for guild in self.bot.guilds:
            if guild.get_member(discord_id):
                await self._mod_log(
                    guild,
                    "Stripe Subscription Updated",
                    f"User: <@{discord_id}>\nEvent: `{event_type}`\nStatus: `{status}`\nPLAYER active: `{active}`",
                    discord.Color.green() if active else discord.Color.orange(),
                )
        return discord_id

    async def process_stripe_webhook_payload(
        self,
        payload: bytes,
        stripe_signature: str | None = None,
    ) -> dict[str, bool]:
        settings = StripeSettings()
        if settings.webhook_secret:
            signature = stripe_signature or ""
            if not verify_webhook_signature(payload, signature, settings.webhook_secret):
                raise ValueError("invalid signature")
        event = json.loads(payload.decode("utf-8"))
        event_type = event.get("type", "")
        obj = (event.get("data") or {}).get("object") or {}
        if event_type in {
            "checkout.session.completed",
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
            "invoice.payment_succeeded",
            "invoice.payment_failed",
        }:
            await self._sync_subscription(obj, event_type)
        return {"received": True}

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        try:
            result = await self.process_stripe_webhook_payload(
                await request.read(),
                request.headers.get("Stripe-Signature"),
            )
        except ValueError as exc:
            return web.Response(status=400, text=str(exc))
        return web.json_response(result)

    @commands.command(name="subscribe")
    @commands.guild_only()
    async def subscribe(self, ctx: commands.Context) -> None:
        try:
            url = await asyncio.to_thread(create_checkout_session, ctx.author.id, str(ctx.author))
        except StripeClientError as exc:
            await ctx.send(f"Stripe is not configured yet: {exc}")
            return
        database.upsert_user(ctx.author.id, username=str(ctx.author))
        try:
            await ctx.author.send(f"Subscribe here: {url}")
            await ctx.reply("I sent you a private subscription link.", mention_author=False)
        except Exception:
            await ctx.reply(f"I could not DM you. Subscribe here: {url}", mention_author=False)

    @commands.command(name="manage_subscription")
    @commands.guild_only()
    async def manage_subscription(self, ctx: commands.Context) -> None:
        subscription = database.get_subscription(ctx.author.id)
        customer_id = subscription.get("stripe_customer_id") if subscription else None
        if not customer_id:
            await ctx.reply("I could not find an active Stripe customer record for you.", mention_author=False)
            return
        try:
            url = await asyncio.to_thread(create_billing_portal_session, customer_id)
        except StripeClientError as exc:
            await ctx.reply(f"Stripe billing portal is not configured yet: {exc}", mention_author=False)
            return
        try:
            await ctx.author.send(f"Manage your subscription here: {url}")
            await ctx.reply("I sent you a private subscription management link.", mention_author=False)
        except Exception:
            await ctx.reply(f"I could not DM you. Manage your subscription here: {url}", mention_author=False)

    @commands.command(name="subscription_status")
    @commands.guild_only()
    async def subscription_status(self, ctx: commands.Context) -> None:
        subscription = database.get_subscription(ctx.author.id)
        if not subscription:
            await ctx.reply("No subscription record found.", mention_author=False)
            return
        status = subscription.get("status") or "unknown"
        period_end = subscription.get("current_period_end") or "unknown"
        await ctx.reply(
            f"Subscription status: `{status}`\nCurrent paid period ends: `{period_end}`",
            mention_author=False,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(BillingCog(bot))
