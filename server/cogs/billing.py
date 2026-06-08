from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any

from aiohttp import web
import discord
from discord.ext import commands, tasks

import database
from config import (
    CHANNEL_MOD,
    MANAGE_SUBSCRIPTION_CHANNEL_CANDIDATES,
    ROLE_NPC,
    ROLE_PLAYER,
    ROLE_WINNER,
    SUBSCRIBE_CHANNEL_CANDIDATES,
    STRIPE_WEBHOOK_HOST,
    STRIPE_WEBHOOK_PORT,
    StripeSettings,
)
from services.email_client import send_email, subscription_email
from services.stripe_client import (
    StripeClientError,
    create_billing_portal_session,
    create_checkout_session,
    retrieve_subscription,
    verify_webhook_signature,
)

# Stripe events the bot acts on.
HANDLED_STRIPE_EVENTS = {
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_succeeded",
    "invoice.payment_failed",
}

ACTIVE_STATUSES = {"active", "trialing", "active_until_period_end"}


def _find_text_channel(guild: discord.Guild, name: str) -> discord.TextChannel | None:
    for ch in guild.text_channels:
        if ch.name.lower() == name.lower():
            return ch
    return None


def find_subscribe_channel(guild: discord.Guild) -> discord.TextChannel | None:
    for name in SUBSCRIBE_CHANNEL_CANDIDATES:
        ch = _find_text_channel(guild, name)
        if ch:
            return ch
    return None


def find_manage_subscription_channel(guild: discord.Guild) -> discord.TextChannel | None:
    for name in MANAGE_SUBSCRIPTION_CHANNEL_CANDIDATES:
        ch = _find_text_channel(guild, name)
        if ch:
            return ch
    return None


def find_player_channel(guild: discord.Guild) -> discord.TextChannel | None:
    """Legacy alias — subscribe / new PLAYER channel."""
    return find_subscribe_channel(guild)


def _stripe_link_view(url: str, *, label: str) -> discord.ui.View:
    """Discord link button opens the URL in the browser (Stripe checkout / portal)."""
    view = discord.ui.View(timeout=600)
    view.add_item(discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=url))
    return view


def player_subscribe_embed() -> discord.Embed:
    return discord.Embed(
        title="Subscribe to PLAYER",
        description=(
            "Become a **PLAYER** subscriber:\n"
            "• **5 votes** per category each week (vs 1 as NPC)\n"
            "• Access to **live leaderboard** channels\n"
            "• Ticker pick channels during pre-vote\n\n"
            "Click **Subscribe**, then **Pay on Stripe** to complete checkout."
        ),
        color=discord.Color.green(),
    )


def player_manage_embed() -> discord.Embed:
    return discord.Embed(
        title="Manage your subscription",
        description=(
            "Already subscribed? Use **Manage subscription** to open the Stripe billing portal.\n\n"
            "There you can update your payment method, view invoices, or cancel your plan."
        ),
        color=discord.Color.blurple(),
    )


async def _ephemeral_billing_error(interaction: discord.Interaction, message: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        pass


class PlayerSubscribeOnlyView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Subscribe",
        style=discord.ButtonStyle.success,
        custom_id="billing:subscribe",
    )
    async def subscribe_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        try:
            if not interaction.guild or not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message("Use this in the server.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            url = await asyncio.to_thread(
                create_checkout_session,
                interaction.user.id,
                str(interaction.user),
            )
            database.upsert_user(interaction.user.id, username=str(interaction.user))
            await interaction.followup.send(
                "Tap **Pay on Stripe** below to open the secure checkout page.",
                view=_stripe_link_view(url, label="Pay on Stripe"),
                ephemeral=True,
            )
        except StripeClientError as exc:
            await _ephemeral_billing_error(interaction, f"Payments are not available right now: {exc}")
        except Exception as exc:
            await _ephemeral_billing_error(interaction, f"Subscribe failed: {exc}")


class PlayerManageSubscriptionView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Manage subscription",
        style=discord.ButtonStyle.secondary,
        custom_id="billing:manage",
    )
    async def manage_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        try:
            if not interaction.guild:
                await interaction.response.send_message("Use this in the server.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            subscription = database.get_subscription(interaction.user.id)
            customer_id = subscription.get("stripe_customer_id") if subscription else None
            if not customer_id:
                sub_ch = find_subscribe_channel(interaction.guild)
                hint = sub_ch.mention if sub_ch else "the **Subscribe** channel"
                await interaction.followup.send(
                    f"No billing account found yet. Subscribe first in {hint}.",
                    ephemeral=True,
                )
                return
            url = await asyncio.to_thread(create_billing_portal_session, customer_id)
            await interaction.followup.send(
                "Tap **Manage billing** to open the Stripe customer portal.",
                view=_stripe_link_view(url, label="Manage billing"),
                ephemeral=True,
            )
        except StripeClientError as exc:
            await _ephemeral_billing_error(interaction, f"Billing portal unavailable: {exc}")
        except Exception as exc:
            await _ephemeral_billing_error(interaction, f"Manage failed: {exc}")


async def _refresh_channel_panel(
    guild: discord.Guild,
    bot: commands.Bot,
    channel: discord.TextChannel,
    *,
    embed: discord.Embed,
    view: discord.ui.View,
    label: str,
) -> discord.Message | None:
    me = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
    if not me:
        return None
    removed = 0
    async for message in channel.history(limit=100):
        if message.author.id != me.id:
            continue
        try:
            await message.delete()
            removed += 1
        except (discord.Forbidden, discord.HTTPException):
            pass
    panel = await channel.send(embed=embed, view=view)
    print(
        f"[billing] Posted {label} panel in #{channel.name} "
        f"(deleted {removed} old bot message(s), message_id={panel.id})",
        flush=True,
    )
    return panel


async def refresh_subscribe_panel(guild: discord.Guild, bot: commands.Bot) -> discord.TextChannel | None:
    settings = StripeSettings()
    if not settings.secret_key or not settings.price_id:
        print("[billing] Skipping subscribe panel (Stripe env missing)", flush=True)
        return None
    channel = find_subscribe_channel(guild)
    if not channel:
        print(
            f"[billing] No subscribe channel in guild {guild.id} "
            f"(candidates: {', '.join(SUBSCRIBE_CHANNEL_CANDIDATES)})",
            flush=True,
        )
        return None
    await _refresh_channel_panel(
        guild, bot, channel,
        embed=player_subscribe_embed(),
        view=PlayerSubscribeOnlyView(bot),
        label="subscribe",
    )
    return channel


async def refresh_manage_subscription_panel(
    guild: discord.Guild, bot: commands.Bot
) -> discord.TextChannel | None:
    settings = StripeSettings()
    if not settings.secret_key:
        print("[billing] Skipping manage panel (STRIPE_SECRET_KEY missing)", flush=True)
        return None
    channel = find_manage_subscription_channel(guild)
    if not channel:
        print(
            f"[billing] No manage-subscription channel in guild {guild.id} "
            f"(candidates: {', '.join(MANAGE_SUBSCRIPTION_CHANNEL_CANDIDATES)})",
            flush=True,
        )
        return None
    await _refresh_channel_panel(
        guild, bot, channel,
        embed=player_manage_embed(),
        view=PlayerManageSubscriptionView(bot),
        label="manage-subscription",
    )
    return channel


async def refresh_billing_panels(guild: discord.Guild, bot: commands.Bot) -> tuple[
    discord.TextChannel | None, discord.TextChannel | None
]:
    sub = await refresh_subscribe_panel(guild, bot)
    manage = await refresh_manage_subscription_panel(guild, bot)
    return sub, manage


async def refresh_player_subscribe_panel(guild: discord.Guild, bot: commands.Bot) -> discord.TextChannel | None:
    """Legacy entry — refreshes both billing panels; returns subscribe channel."""
    sub, _ = await refresh_billing_panels(guild, bot)
    return sub


def _period_end_from_subscription(subscription: dict[str, Any]) -> str | None:
    raw = subscription.get("current_period_end")
    if not raw:
        # Newer Stripe API nests the period end on each subscription item.
        items = ((subscription.get("items") or {}).get("data") or [])
        for item in items:
            raw = item.get("current_period_end")
            if raw:
                break
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return None


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
        self._panels_posted = False

    async def cog_load(self) -> None:
        self.bot.add_view(PlayerSubscribeOnlyView(self.bot))
        self.bot.add_view(PlayerManageSubscriptionView(self.bot))
        if not self._npc_reconcile_loop.is_running():
            self._npc_reconcile_loop.start()
        settings = StripeSettings()
        api_port = int(os.getenv("PORT", os.getenv("CRM_API_PORT", "8000")))
        webhook_port = int(os.getenv("STRIPE_WEBHOOK_PORT", str(STRIPE_WEBHOOK_PORT)))
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
        self._npc_reconcile_loop.cancel()
        if self._runner:
            await self._runner.cleanup()

    def _app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/stripe/webhook", self._handle_webhook)
        return app

    async def _refresh_subscribe_panels(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(2)
        for guild in self.bot.guilds:
            try:
                await refresh_billing_panels(guild, self.bot)
            except Exception as exc:
                print(f"[billing] Billing panel refresh failed for {guild.id}: {exc!r}", flush=True)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._panels_posted:
            return
        self._panels_posted = True
        self.bot.loop.create_task(self._refresh_subscribe_panels())

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Default every new member to NPC, or PLAYER if they already paid.

        Covers the case where someone pays before joining the server, or where
        the member cache missed during the original webhook. Bots are skipped.
        """
        if member.bot:
            return
        try:
            if database.is_paid_member(member.id):
                await self._set_player_role(member.id, True)
            else:
                await self._ensure_npc_role(member)
        except Exception as exc:  # noqa: BLE001
            print(f"[billing] on_member_join reconcile failed for {member.id}: {exc!r}", flush=True)

    async def _ensure_npc_role(self, member: discord.Member) -> bool:
        """Give a member the NPC role unless they already hold PLAYER/WINNER/NPC."""
        existing = {r.name.upper() for r in member.roles}
        if {ROLE_PLAYER.upper(), ROLE_WINNER.upper(), ROLE_NPC.upper()} & existing:
            return False
        role = discord.utils.get(member.guild.roles, name=ROLE_NPC)
        if not role:
            await self._mod_log(
                member.guild, "NPC role missing",
                f"Role `{ROLE_NPC}` not found — cannot auto-assign it to <@{member.id}>.",
                discord.Color.orange(),
            )
            return False
        me = member.guild.me
        if me and role >= me.top_role:
            await self._mod_log(
                member.guild, "NPC role hierarchy error",
                f"`{ROLE_NPC}` is above my highest role, so I cannot assign it to "
                f"<@{member.id}>. Move my bot role above `{ROLE_NPC}` in Server Settings → Roles.",
                discord.Color.orange(),
            )
            return False
        try:
            await member.add_roles(role, reason="New member default role")
            return True
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[billing] could not add NPC role to {member.id}: {exc!r}", flush=True)
            return False

    @tasks.loop(minutes=5)
    async def _npc_reconcile_loop(self) -> None:
        """Safety net: ensure every member has a role even if the join event was
        missed (e.g. members intent off, downtime, cache miss). Runs on startup
        and every 5 minutes."""
        for guild in list(self.bot.guilds):
            try:
                await self._reconcile_all_npc(guild)
            except Exception as exc:  # noqa: BLE001
                print(f"[billing] NPC reconcile failed for guild {guild.id}: {exc!r}", flush=True)

    @_npc_reconcile_loop.before_loop
    async def _before_npc_reconcile(self) -> None:
        await self.bot.wait_until_ready()

    async def _reconcile_all_npc(self, guild: discord.Guild) -> int:
        """Give NPC to every non-bot member that has no PLAYER/WINNER/NPC role.
        Returns the number of members updated."""
        try:
            # chunk() can hang on some gateways; don't let it stall the loop.
            await asyncio.wait_for(guild.chunk(), timeout=30)
        except Exception:
            pass  # fall back to whatever members are already cached
        assigned = 0
        for member in guild.members:
            if member.bot:
                continue
            existing = {r.name.upper() for r in member.roles}
            if {ROLE_PLAYER.upper(), ROLE_WINNER.upper(), ROLE_NPC.upper()} & existing:
                continue
            # Don't override paid members; promote them to PLAYER instead.
            try:
                if database.is_paid_member(member.id):
                    if await self._set_player_role(member.id, True):
                        assigned += 1
                    continue
            except Exception:
                pass
            if await self._ensure_npc_role(member):
                assigned += 1
        if assigned:
            print(f"[billing] NPC reconcile assigned {assigned} role(s) in guild {guild.id}", flush=True)
        return assigned

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

    async def _resolve_member(self, guild: discord.Guild, discord_id: int) -> discord.Member | None:
        member = guild.get_member(discord_id)
        if member:
            return member
        # Cache miss (common right after payment): fetch from the API.
        try:
            return await guild.fetch_member(discord_id)
        except (discord.NotFound, discord.HTTPException, discord.Forbidden):
            return None

    async def _dm_user(self, discord_id: int, content: str) -> bool:
        """Best-effort DM. Returns True if delivered."""
        for guild in self.bot.guilds:
            member = await self._resolve_member(guild, discord_id)
            if not member:
                continue
            try:
                await member.send(content)
                return True
            except (discord.Forbidden, discord.HTTPException):
                return False
        return False

    async def _set_player_role(self, discord_id: int, active: bool) -> bool:
        """Add/remove PLAYER role across guilds. Returns True if a change applied.

        Role assignment is independent from DMs/emails so a blocked DM can never
        prevent the PLAYER role from being granted (root cause of the demo bug).
        """
        changed = False
        for guild in self.bot.guilds:
            member = await self._resolve_member(guild, discord_id)
            if not member:
                continue
            role = discord.utils.get(guild.roles, name=ROLE_PLAYER)
            if not role:
                await self._mod_log(
                    guild, "PLAYER role missing",
                    f"Role `{ROLE_PLAYER}` not found — cannot update <@{discord_id}>.",
                    discord.Color.red(),
                )
                continue
            me = guild.me
            if me and role >= me.top_role:
                await self._mod_log(
                    guild, "PLAYER role hierarchy error",
                    f"`{ROLE_PLAYER}` is above my highest role, so I cannot assign it to "
                    f"<@{discord_id}>. Move my bot role above `{ROLE_PLAYER}` in Server Settings → Roles.",
                    discord.Color.red(),
                )
                continue
            try:
                if active and role not in member.roles:
                    await member.add_roles(role, reason="Stripe subscription active")
                    changed = True
                elif not active and role in member.roles:
                    await member.remove_roles(role, reason="Stripe subscription inactive")
                    changed = True
            except discord.Forbidden:
                await self._mod_log(
                    guild, "PLAYER role permission error",
                    f"Missing Manage Roles permission to update <@{discord_id}>.",
                    discord.Color.red(),
                )
            except discord.HTTPException as exc:
                await self._mod_log(
                    guild, "PLAYER role update failed",
                    f"Could not update <@{discord_id}>: {exc}.",
                    discord.Color.orange(),
                )
        return changed

    async def _notify(
        self,
        discord_id: int,
        kind: str,
        *,
        username: str | None,
        email: str | None,
        period_end: str | None,
    ) -> None:
        """Send the lifecycle DM + email for a state transition (best-effort)."""
        dm_messages = {
            "welcome": "🎉 Your subscription is active — **PLAYER** role added. You now get 5 votes per category and access to live leaderboards.",
            "renewal": f"✅ Your PLAYER subscription renewed. Access continues until `{period_end or 'next period'}`.",
            "cancel_scheduled": f"Your subscription will not renew. You keep PLAYER access until `{period_end or 'period end'}`.",
            "canceled": "Your subscription has ended and the PLAYER role was removed. You can re-subscribe anytime.",
            "payment_failed": "⚠️ Your latest payment failed. Update your payment method in the billing portal to keep PLAYER access.",
        }
        dm = dm_messages.get(kind)
        if dm:
            await self._dm_user(discord_id, dm)
        tpl = subscription_email(kind, username=username, period_end=period_end)
        if tpl and email:
            await asyncio.to_thread(send_email, email, tpl["subject"], tpl["html"], tpl["text"])

    def _resolve_discord_id(self, obj: dict[str, Any], subscription: dict[str, Any] | None) -> tuple[int | None, str]:
        """Resolve the owning Discord user, never guessing by email.

        Priority: explicit metadata/client_reference_id on the event/subscription,
        then a stored mapping from stripe_customer_id. Returns (id, source).
        """
        discord_id = _discord_id_from_payload(obj)
        if discord_id:
            return discord_id, "event_metadata"
        if subscription:
            discord_id = _discord_id_from_payload(subscription)
            if discord_id:
                return discord_id, "subscription_metadata"
        customer_id = (subscription or obj).get("customer")
        if customer_id:
            existing = database.get_subscription_by_customer(str(customer_id))
            if existing and existing.get("discord_id"):
                return int(existing["discord_id"]), "customer_mapping"
        return None, "unresolved"

    async def _sync_subscription(
        self, obj: dict[str, Any], event_type: str, event_id: str | None = None
    ) -> tuple[int | None, str | None]:
        subscription_id = obj.get("subscription") or obj.get("id")
        subscription = obj if obj.get("object") == "subscription" else None
        if subscription_id and not subscription:
            try:
                subscription = await asyncio.to_thread(retrieve_subscription, str(subscription_id))
            except Exception:
                subscription = None

        discord_id, source = self._resolve_discord_id(obj, subscription)
        if not discord_id:
            database.log_event(None, "stripe_webhook_unresolved", {"type": event_type, "event_id": event_id})
            print(f"[billing] Could not map {event_type} ({event_id}) to a Discord user", flush=True)
            return None, None

        customer_id = str((subscription or obj).get("customer") or "")

        # Mapping-safety guard: a Stripe customer must never flip to a different
        # Discord account. If the event metadata disagrees with the stored owner,
        # trust the stored owner and alert mods instead of silently reassigning.
        if customer_id and source == "event_metadata":
            existing = database.get_subscription_by_customer(customer_id)
            if existing and existing.get("discord_id") and int(existing["discord_id"]) != discord_id:
                owner = int(existing["discord_id"])
                await self._mod_log(
                    self.bot.guilds[0] if self.bot.guilds else None,
                    "Stripe mapping conflict",
                    f"Customer `{customer_id}` is already linked to <@{owner}> but event "
                    f"`{event_type}` carried <@{discord_id}>. Keeping <@{owner}>.",
                    discord.Color.red(),
                )
                discord_id = owner

        prior = database.get_subscription(discord_id)
        was_active = bool(prior and prior.get("status") in ACTIVE_STATUSES)

        customer_details = obj.get("customer_details") or {}
        email = customer_details.get("email") or obj.get("customer_email")
        username = (obj.get("metadata") or {}).get("discord_username")
        database.upsert_user(
            discord_id,
            username=username,
            full_name=customer_details.get("name"),
            email=email,
            phone=(customer_details.get("phone") if isinstance(customer_details, dict) else None),
            marketing_consent=_marketing_consent_from_session(obj),
        )

        status = (subscription or obj).get("status") or "unknown"
        payment_status = obj.get("payment_status") or (subscription or {}).get("collection_method")
        cancel_at_period_end = bool((subscription or {}).get("cancel_at_period_end"))
        current_period_end = _period_end_from_subscription(subscription or {})
        canceled_at_raw = (subscription or {}).get("canceled_at")
        canceled_at = (
            datetime.fromtimestamp(int(canceled_at_raw), tz=timezone.utc).isoformat()
            if canceled_at_raw else None
        )

        # A completed checkout session is a paid subscription even if the
        # subscription object could not be retrieved (root cause of demo bug:
        # session.status == "complete" was treated as inactive).
        if event_type == "checkout.session.completed":
            if (obj.get("payment_status") == "paid") or status in {"complete", "unknown"}:
                status = "active"
        if event_type == "customer.subscription.deleted":
            status = "canceled"
        elif event_type == "invoice.payment_failed":
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
            last_event_type=event_type,
            last_event_id=event_id,
        )
        database.log_event(
            None,
            "stripe_webhook",
            {
                "type": event_type,
                "event_id": event_id,
                "discord_id": discord_id,
                "status": status,
                "mapping_source": source,
            },
        )

        active = status in ACTIVE_STATUSES
        await self._set_player_role(discord_id, active)

        # Notifications are driven by the state transition + event type so that
        # distinct Stripe events each map to at most one DM/email, and a duplicate
        # delivery of the same event is skipped earlier by idempotency.
        kind: str | None = None
        if event_type == "invoice.payment_failed":
            kind = "payment_failed"
        elif status == "canceled":
            kind = "canceled"
        elif status == "active_until_period_end":
            kind = "cancel_scheduled"
        elif active and not was_active:
            kind = "welcome"
        elif active and was_active and event_type == "invoice.payment_succeeded":
            kind = "renewal"
        if kind:
            await self._notify(
                discord_id, kind,
                username=username or (prior or {}).get("username"),
                email=email,
                period_end=current_period_end,
            )

        color = discord.Color.green() if active else discord.Color.orange()
        await self._mod_log(
            self.bot.guilds[0] if self.bot.guilds else None,
            "Stripe Subscription Updated",
            f"User: <@{discord_id}>\nEvent: `{event_type}`\nStatus: `{status}`\n"
            f"PLAYER active: `{active}`\nNotified: `{kind or 'none'}`\nMapping: `{source}`",
            color,
        )
        return discord_id, status

    async def process_stripe_webhook_payload(
        self,
        payload: bytes,
        stripe_signature: str | None = None,
    ) -> dict[str, Any]:
        settings = StripeSettings()
        if settings.webhook_secret:
            signature = stripe_signature or ""
            if not verify_webhook_signature(payload, signature, settings.webhook_secret):
                raise ValueError("invalid signature")
        event = json.loads(payload.decode("utf-8"))
        event_id = str(event.get("id") or "")
        event_type = event.get("type", "")
        obj = (event.get("data") or {}).get("object") or {}

        if event_type not in HANDLED_STRIPE_EVENTS:
            return {"received": True, "ignored": True, "type": event_type}

        # Idempotency: a duplicate delivery of an already-processed event is a
        # no-op (no double role, record, email, or status change).
        if event_id:
            existing = database.get_stripe_event(event_id)
            if existing and existing.get("processed"):
                print(f"[billing] Duplicate Stripe event {event_id} ({event_type}) — skipped", flush=True)
                return {"received": True, "duplicate": True, "type": event_type}
            try:
                database.claim_stripe_event(event_id, event_type, event)
            except Exception as exc:  # noqa: BLE001 - claim failure shouldn't drop the event
                print(f"[billing] Could not record event {event_id}: {exc!r}", flush=True)

        try:
            discord_id, status = await self._sync_subscription(obj, event_type, event_id)
        except Exception as exc:  # noqa: BLE001
            database.mark_stripe_event_processed(event_id, error=repr(exc))
            database.log_event(None, "stripe_webhook_error", {"type": event_type, "event_id": event_id, "error": repr(exc)})
            print(f"[billing] Error processing {event_type} ({event_id}): {exc!r}", flush=True)
            try:
                await self._mod_log(
                    self.bot.guilds[0] if self.bot.guilds else None,
                    "Stripe Webhook Error",
                    f"Event `{event_type}` (`{event_id}`) failed and will be retried by Stripe.\n"
                    f"Error: `{exc!r}`\nSubscription status was **not** lost.",
                    discord.Color.red(),
                )
            except Exception:
                pass
            # Re-raise so Stripe retries; idempotency makes the retry safe.
            raise
        database.mark_stripe_event_processed(event_id, discord_id=discord_id, status=status)
        return {"received": True, "type": event_type, "discord_id": discord_id, "status": status}

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        try:
            result = await self.process_stripe_webhook_payload(
                await request.read(),
                request.headers.get("Stripe-Signature"),
            )
        except ValueError as exc:
            return web.Response(status=400, text=str(exc))
        except Exception as exc:  # noqa: BLE001
            return web.Response(status=500, text=str(exc))
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
        pay_view = _stripe_link_view(url, label="Pay on Stripe")
        try:
            await ctx.author.send(
                "Open the Stripe payment page:",
                view=pay_view,
            )
            await ctx.reply("I sent you a DM with **Pay on Stripe**.", mention_author=False)
        except Exception:
            await ctx.reply(
                "I could not DM you. Use the button below:",
                view=pay_view,
                mention_author=False,
            )

    @commands.command(name="post_subscribe_panel")
    @commands.has_role("ADMIN")
    @commands.guild_only()
    async def post_subscribe_panel(self, ctx: commands.Context) -> None:
        """ADMIN: post Subscribe and Manage panels in their separate channels."""
        sub, manage = await refresh_billing_panels(ctx.guild, self.bot)
        parts: list[str] = []
        if sub:
            parts.append(f"Subscribe → {sub.mention}")
        if manage:
            parts.append(f"Manage → {manage.mention}")
        if parts:
            await ctx.send("Posted: " + " · ".join(parts), delete_after=20)
        else:
            await ctx.send(
                "Could not post panels — check Stripe env vars and that "
                "#subscribe (or #𝐏𝐋𝐀𝐘𝐄𝐑) and #manage-subscription channels exist.",
                delete_after=20,
            )

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

    @commands.command(name="resync_subscription")
    @commands.has_role("ADMIN")
    @commands.guild_only()
    async def resync_subscription(self, ctx: commands.Context, member: discord.Member) -> None:
        """ADMIN: re-apply PLAYER role from the stored subscription status."""
        sub = database.get_subscription(member.id)
        if not sub:
            await ctx.reply(f"No subscription record for {member.mention}.", mention_author=False)
            return
        active = sub.get("status") in ACTIVE_STATUSES
        await self._set_player_role(member.id, active)
        await ctx.reply(
            f"{member.mention} status `{sub.get('status')}` → PLAYER active `{active}`.",
            mention_author=False,
        )

    @commands.command(name="stripe_events")
    @commands.has_role("ADMIN")
    @commands.guild_only()
    async def stripe_events(self, ctx: commands.Context, limit: int = 10) -> None:
        """ADMIN: show recent Stripe webhook events and their processing result."""
        try:
            rows = await asyncio.to_thread(database.recent_stripe_events, min(max(limit, 1), 25))
        except Exception as exc:  # noqa: BLE001
            await ctx.reply(f"Could not read events: {exc}", mention_author=False)
            return
        if not rows:
            await ctx.reply("No Stripe events recorded yet.", mention_author=False)
            return
        lines = []
        for r in rows:
            flag = "ok" if r.get("processed") else ("ERR" if r.get("error") else "pending")
            lines.append(
                f"`{r.get('received_at','')[:19]}` {r.get('type')} → {flag}"
                + (f" <@{r['discord_id']}>" if r.get("discord_id") else "")
                + (f" status=`{r.get('status')}`" if r.get("status") else "")
            )
        embed = discord.Embed(
            title="Recent Stripe events",
            description="\n".join(lines)[:4000],
            color=discord.Color.blurple(),
        )
        await ctx.reply(embed=embed, mention_author=False)

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
