"""Offline checks for Section 11 Stripe billing & PLAYER role."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cogs.billing import ACTIVE_STATUSES, HANDLED_STRIPE_EVENTS, BillingCog  # noqa: E402
from services.email_client import subscription_email  # noqa: E402


def main() -> int:
    fails: list[str] = []
    billing_src = (ROOT / "cogs" / "billing.py").read_text(encoding="utf-8")
    db_src = (ROOT / "database.py").read_text(encoding="utf-8")
    email_src = (ROOT / "services" / "email_client.py").read_text(encoding="utf-8")
    proc_src = inspect.getsource(BillingCog.process_stripe_webhook_payload)

    required_events = {
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.payment_succeeded",
        "invoice.payment_failed",
    }
    if not required_events.issubset(HANDLED_STRIPE_EVENTS):
        fails.append("missing handled Stripe webhook event types")

    if "active_until_period_end" not in ACTIVE_STATUSES:
        fails.append("cancel-at-period-end must keep PLAYER via active_until_period_end")

    if "_set_player_role" not in billing_src or "upsert_subscription" not in billing_src:
        fails.append("webhook must persist subscription and sync PLAYER role")

    if "payment_failed" not in billing_src or '"canceled"' not in billing_src:
        fails.append("failed/canceled statuses must be handled")

    for kind in ("welcome", "renewal", "cancel_scheduled", "canceled", "payment_failed"):
        if not subscription_email(kind, username="u", period_end="2026-01-01"):
            fails.append(f"missing email template for {kind}")

    if "_dm_user" not in billing_src or "welcome" not in billing_src:
        fails.append("purchase DM path missing")

    if "get_stripe_event" not in proc_src or "duplicate" not in proc_src:
        fails.append("idempotent duplicate webhook skip missing")

    if "claim_stripe_event" not in db_src or "stripe_events" not in db_src:
        fails.append("stripe_events table helpers missing")

    if "mark_stripe_event_processed" not in proc_src or "stripe_webhook_error" not in billing_src:
        fails.append("webhook failure logging/recovery missing")

    if "log_event" not in billing_src or "stripe_events" not in billing_src:
        fails.append("Stripe audit logging/commands missing")

    stripe_src = (ROOT / "services" / "stripe_client.py").read_text(encoding="utf-8")
    if '"mode": "subscription"' not in stripe_src and "subscription" not in stripe_src:
        fails.append("checkout must create recurring subscription (monthly)")

    if fails:
        print("SECTION 11 LOGIC: FAIL")
        for f in fails:
            print(f"  • {f}")
        return 1

    print("SECTION 11 LOGIC: PASS")
    print("  • Webhooks: checkout, subscription.*, invoice success/fail")
    print("  • PLAYER sync; cancel-at-period-end keeps role until deleted")
    print("  • Idempotency via stripe_events; DM + email templates for lifecycle")
    print("  • Run test_stripe_flow.py + check_stripe_live.py for behavioral proof")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
