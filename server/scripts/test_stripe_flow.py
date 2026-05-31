"""Offline acceptance test for the Stripe subscription lifecycle.

Proves the contract requirements for section 4 WITHOUT touching the live
Supabase project or Discord:

  * A paid checkout grants the PLAYER role automatically.
  * A duplicate webhook delivery is a no-op (no double role / record / DM / email).
  * Renewal, payment failure, and cancellation drive the right status + role + notices.
  * Subscriptions are mapped to a Discord user by metadata / customer mapping only
    (never guessed by email), and a customer can't be hijacked by another account.
  * A blocked DM never prevents the PLAYER role from being granted.

Run:  python server/scripts/test_stripe_flow.py
Exits non-zero if any assertion fails.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import discord

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cogs.billing as billing  # noqa: E402
from config import ROLE_PLAYER  # noqa: E402

PLAYER = ROLE_PLAYER
GUILD_ID = 999
TS = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp())

failures: list[str] = []
notify_dms: list[tuple[int, str]] = []
notify_emails: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# --------------------------------------------------------------------------
# Fake Supabase layer (in-memory)
# --------------------------------------------------------------------------
class FakeDB:
    def __init__(self) -> None:
        self.users: dict[int, dict] = {}
        self.subs: dict[int, dict] = {}
        self.events: dict[str, dict] = {}
        self.audit: list[dict] = []

    # idempotency
    def get_stripe_event(self, event_id):
        return self.events.get(event_id)

    def claim_stripe_event(self, event_id, event_type, payload=None):
        if not event_id:
            return True
        if event_id in self.events:
            return False
        self.events[event_id] = {
            "id": event_id, "type": event_type, "processed": False,
            "discord_id": None, "status": None, "error": None,
            "received_at": datetime.now(timezone.utc).isoformat(),
        }
        return True

    def mark_stripe_event_processed(self, event_id, *, discord_id=None, status=None, error=None):
        row = self.events.setdefault(event_id, {"id": event_id})
        row["processed"] = error is None
        if discord_id is not None:
            row["discord_id"] = discord_id
        if status is not None:
            row["status"] = status
        if error is not None:
            row["error"] = error

    def recent_stripe_events(self, limit=25):
        return list(self.events.values())[:limit]

    # users / subs
    def upsert_user(self, discord_id, username=None, **fields):
        row = self.users.setdefault(discord_id, {"discord_id": discord_id})
        if username:
            row["username"] = username
        for k, v in fields.items():
            if v is not None:
                row[k] = v

    def upsert_subscription(self, discord_id, *, status, **fields):
        row = self.subs.setdefault(discord_id, {"discord_id": discord_id})
        row["status"] = status
        for k, v in fields.items():
            if v is not None:
                row[k] = v

    def get_subscription(self, discord_id):
        return self.subs.get(discord_id)

    def get_subscription_by_customer(self, customer_id):
        for row in self.subs.values():
            if row.get("stripe_customer_id") == customer_id:
                return row
        return None

    def is_paid_member(self, discord_id):
        row = self.subs.get(discord_id)
        return bool(row and row.get("status") in billing.ACTIVE_STATUSES)

    def log_event(self, guild_id, event_type, details):
        self.audit.append({"event_type": event_type, "details": details})


# --------------------------------------------------------------------------
# Fake Discord layer
# --------------------------------------------------------------------------
class FakeRole:
    def __init__(self, name, position):
        self.name = name
        self.position = position

    def __ge__(self, other):
        return self.position >= other.position

    def __lt__(self, other):
        return self.position < other.position

    def __eq__(self, other):
        return isinstance(other, FakeRole) and other.name == self.name

    def __hash__(self):
        return hash(self.name)


def _http_exc(status=403):
    resp = SimpleNamespace(status=status, reason="blocked")
    return discord.HTTPException(resp, "blocked")


class FakeMember:
    def __init__(self, member_id, guild, *, dm_blocked=False):
        self.id = member_id
        self.guild = guild
        self.roles: list[FakeRole] = []
        self.dm_blocked = dm_blocked
        self.dms: list[str] = []

    async def add_roles(self, role, reason=None):
        if role not in self.roles:
            self.roles.append(role)

    async def remove_roles(self, role, reason=None):
        self.roles = [r for r in self.roles if r != role]

    async def send(self, content):
        if self.dm_blocked:
            raise _http_exc()
        self.dms.append(content)


class FakeChannel:
    def __init__(self, name):
        self.name = name
        self.sent: list = []

    async def send(self, *args, **kwargs):
        self.sent.append(kwargs.get("embed") or (args[0] if args else None))


class FakeGuild:
    def __init__(self):
        self.id = GUILD_ID
        self.player_role = FakeRole(PLAYER, position=5)
        self.roles = [self.player_role]
        self.me = SimpleNamespace(top_role=FakeRole("bot", position=10))
        self.mod = FakeChannel(billing.CHANNEL_MOD)
        self.text_channels = [self.mod]
        self._members: dict[int, FakeMember] = {}

    def add_member(self, member_id, **kw):
        m = FakeMember(member_id, self, **kw)
        self._members[member_id] = m
        return m

    def get_member(self, member_id):
        return self._members.get(member_id)

    async def fetch_member(self, member_id):
        m = self._members.get(member_id)
        if not m:
            raise discord.NotFound(SimpleNamespace(status=404, reason="nf"), "missing")
        return m


class FakeBot:
    def __init__(self, guild):
        self.guilds = [guild]


# --------------------------------------------------------------------------
# Event payload builders
# --------------------------------------------------------------------------
def evt(event_id, type_, obj):
    return json.dumps({"id": event_id, "type": type_, "data": {"object": obj}}).encode()


def checkout_completed(discord_id, customer, sub_id, email="user@example.com"):
    return {
        "object": "checkout.session", "payment_status": "paid", "status": "complete",
        "customer": customer, "subscription": sub_id,
        "client_reference_id": str(discord_id),
        "metadata": {"discord_id": str(discord_id), "discord_username": f"user{discord_id}"},
        "customer_details": {"email": email, "name": "Test User", "phone": None},
    }


def invoice(event_paid, discord_id, customer, sub_id):
    return {
        "object": "invoice", "customer": customer, "subscription": sub_id,
        "customer_email": "user@example.com",
        "metadata": {"discord_id": str(discord_id)},
    }


def subscription_obj(discord_id, customer, sub_id, status="active", cancel_at_period_end=False, canceled=False):
    obj = {
        "object": "subscription", "id": sub_id, "customer": customer, "status": status,
        "cancel_at_period_end": cancel_at_period_end,
        "items": {"data": [{"current_period_end": TS}]},
        "metadata": {"discord_id": str(discord_id)} if discord_id else {},
    }
    if canceled:
        obj["canceled_at"] = TS
    return obj


# --------------------------------------------------------------------------
# Test driver
# --------------------------------------------------------------------------
async def main() -> int:
    db = FakeDB()
    guild = FakeGuild()
    bot = FakeBot(guild)

    # Patch the billing module's external dependencies.
    billing.database = db
    billing.StripeSettings = lambda: SimpleNamespace(webhook_secret=None, secret_key="x", price_id="y")
    billing.verify_webhook_signature = lambda *a, **k: True

    def fake_retrieve(sub_id):
        # Default retrieval returns an active subscription for that id.
        return subscription_obj(None, _sub_customer.get(sub_id), sub_id, status="active")

    billing.retrieve_subscription = fake_retrieve
    _sub_customer: dict[str, str] = {}

    def fake_send_email(to, subject, html, text):
        notify_emails.append((to, subject))
        return True

    billing.send_email = fake_send_email

    cog = billing.BillingCog.__new__(billing.BillingCog)
    cog.bot = bot

    # ---- Scenario 1: paid checkout grants PLAYER + welcome --------------
    print("\nScenario 1: checkout.session.completed grants PLAYER role")
    A = 111
    guild.add_member(A)
    _sub_customer["sub_A"] = "cus_A"
    res = await cog.process_stripe_webhook_payload(
        evt("evt_1", "checkout.session.completed", checkout_completed(A, "cus_A", "sub_A"))
    )
    member_a = guild.get_member(A)
    check("subscription stored active", db.subs.get(A, {}).get("status") == "active")
    check("PLAYER role granted", guild.player_role in member_a.roles)
    check("welcome DM sent", any("active" in d.lower() for d in member_a.dms), str(member_a.dms))
    check("welcome email sent", any("active" in s.lower() for _, s in notify_emails), str(notify_emails))
    check("event processed flag", db.events["evt_1"]["processed"] is True)
    check("result not duplicate", res.get("duplicate") is not True)

    # ---- Scenario 2: duplicate delivery is a no-op ----------------------
    print("\nScenario 2: duplicate webhook is ignored (idempotency)")
    dms_before = len(member_a.dms)
    emails_before = len(notify_emails)
    res_dup = await cog.process_stripe_webhook_payload(
        evt("evt_1", "checkout.session.completed", checkout_completed(A, "cus_A", "sub_A"))
    )
    check("duplicate flagged", res_dup.get("duplicate") is True)
    check("no extra DM on duplicate", len(member_a.dms) == dms_before)
    check("no extra email on duplicate", len(notify_emails) == emails_before)
    check("still exactly one PLAYER role", member_a.roles.count(guild.player_role) == 1)

    # ---- Scenario 3: renewal -------------------------------------------
    print("\nScenario 3: invoice.payment_succeeded renews (renewal notice)")
    emails_before = len(notify_emails)
    await cog.process_stripe_webhook_payload(
        evt("evt_2", "invoice.payment_succeeded", invoice(True, A, "cus_A", "sub_A"))
    )
    check("still active after renewal", db.subs[A]["status"] == "active")
    check("PLAYER role retained", guild.player_role in member_a.roles)
    check("renewal email sent", any("renewed" in s.lower() for _, s in notify_emails[emails_before:]), str(notify_emails))

    # ---- Scenario 4: payment failure removes role -----------------------
    print("\nScenario 4: invoice.payment_failed removes PLAYER + alerts user")
    await cog.process_stripe_webhook_payload(
        evt("evt_3", "invoice.payment_failed", invoice(False, A, "cus_A", "sub_A"))
    )
    check("status payment_failed", db.subs[A]["status"] == "payment_failed")
    check("PLAYER role removed", guild.player_role not in member_a.roles)
    check("payment-failed DM sent", any("failed" in d.lower() for d in member_a.dms), str(member_a.dms))
    check("payment-failed email sent", any("failed" in s.lower() for _, s in notify_emails), str(notify_emails))

    # ---- Scenario 5: cancellation --------------------------------------
    print("\nScenario 5: customer.subscription.deleted cancels")
    # re-activate first so we can observe the transition to canceled
    await cog.process_stripe_webhook_payload(
        evt("evt_4", "customer.subscription.updated", subscription_obj(A, "cus_A", "sub_A", status="active"))
    )
    check("re-activated", guild.player_role in member_a.roles)
    await cog.process_stripe_webhook_payload(
        evt("evt_5", "customer.subscription.deleted", subscription_obj(A, "cus_A", "sub_A", status="canceled", canceled=True))
    )
    check("status canceled", db.subs[A]["status"] == "canceled")
    check("PLAYER role removed on cancel", guild.player_role not in member_a.roles)
    check("canceled DM sent", any("ended" in d.lower() for d in member_a.dms), str(member_a.dms))

    # ---- Scenario 6: mapping by customer (no metadata) ------------------
    print("\nScenario 6: webhook without metadata resolves via customer mapping")
    res6 = await cog.process_stripe_webhook_payload(
        evt("evt_6", "customer.subscription.updated", subscription_obj(None, "cus_A", "sub_A", status="active"))
    )
    check("resolved to original user A", res6.get("discord_id") == A, str(res6))
    check("A re-activated via mapping", db.subs[A]["status"] == "active")

    # ---- Scenario 7: mapping conflict cannot hijack a customer ----------
    print("\nScenario 7: another Discord account cannot hijack an existing customer")
    B = 222
    guild.add_member(B)
    res7 = await cog.process_stripe_webhook_payload(
        evt("evt_7", "customer.subscription.updated", subscription_obj(B, "cus_A", "sub_A", status="active"))
    )
    check("customer kept with user A", res7.get("discord_id") == A, str(res7))
    check("user B not subscribed", B not in db.subs or db.subs.get(B, {}).get("status") not in billing.ACTIVE_STATUSES)
    check("user B has no PLAYER role", guild.player_role not in guild.get_member(B).roles)

    # ---- Scenario 8: blocked DM still grants the role -------------------
    print("\nScenario 8: a blocked DM never blocks the PLAYER role")
    C = 333
    guild.add_member(C, dm_blocked=True)
    _sub_customer["sub_C"] = "cus_C"
    await cog.process_stripe_webhook_payload(
        evt("evt_8", "checkout.session.completed", checkout_completed(C, "cus_C", "sub_C", email="c@example.com"))
    )
    member_c = guild.get_member(C)
    check("PLAYER granted despite blocked DM", guild.player_role in member_c.roles)
    check("subscription active for C", db.subs[C]["status"] == "active")

    # ---- Scenario 9: unmapped event is safely ignored -------------------
    print("\nScenario 9: event with no resolvable user is ignored, not crashed")
    res9 = await cog.process_stripe_webhook_payload(
        evt("evt_9", "customer.subscription.updated", subscription_obj(None, "cus_UNKNOWN", "sub_X", status="active"))
    )
    check("unmapped returns no discord_id", res9.get("discord_id") is None, str(res9))

    print("\n" + ("=" * 52))
    if failures:
        print(f"RESULT: FAILED ({len(failures)} check(s)): {', '.join(failures)}")
        return 1
    print("RESULT: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
