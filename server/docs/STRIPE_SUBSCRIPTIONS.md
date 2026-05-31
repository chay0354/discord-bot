# Stripe Subscriptions — How it works and how to test

This document covers the PLAYER subscription flow: payment → `PLAYER` role,
webhooks, Discord ↔ Stripe mapping, lifecycle DMs/emails, logging, and the
idempotency guarantees required by section 4 of the change report.

## Components

| Piece | File |
|-------|------|
| Checkout / billing portal calls | `server/services/stripe_client.py` |
| Webhook handling, role grant, DMs, emails | `server/cogs/billing.py` |
| Lifecycle emails (Resend / SMTP / no-op) | `server/services/email_client.py` |
| Persistence + idempotency helpers | `server/database.py` |
| Webhook endpoint (Railway, shares `PORT`) | `server/api/main.py` → `POST /stripe/webhook` |

## Data model (Supabase)

- `subscriptions` — one row per Discord user: `status`, `payment_status`,
  `stripe_customer_id`, `stripe_subscription_id`, `current_period_end`,
  `canceled_at`, `created_at`, `last_event_type`, `last_event_id`.
- `stripe_events` — one row per Stripe **event id** (`id` is the primary key).
  This is what makes duplicate webhook deliveries safe: an event that is
  already `processed = true` is skipped entirely.
- `users` — `full_name`, `email`, `phone`, `marketing_consent`.
- `audit_logs` — `stripe_webhook`, `stripe_webhook_error`,
  `stripe_webhook_unresolved` entries for traceability.

## Flow

1. User clicks **Subscribe** → `create_checkout_session` puts the Discord id in
   both `client_reference_id` and `metadata.discord_id`.
2. Stripe sends webhooks to `POST /stripe/webhook`. Signature is verified with
   `STRIPE_WEBHOOK_SECRET`.
3. The event id is recorded in `stripe_events`. If it was already processed, the
   delivery is a no-op.
4. The owning Discord user is resolved **only** from metadata / `client_reference_id`,
   or from the stored `stripe_customer_id → discord_id` mapping. It is **never**
   guessed from email, and an existing customer cannot be reassigned to a
   different Discord account (a conflict is logged to `#mod` and ignored).
5. `subscriptions` is upserted, the `PLAYER` role is added/removed, and a
   lifecycle DM + email is sent based on the state transition.
6. The event is marked `processed`. On error it is left unprocessed and a `500`
   is returned so Stripe retries safely.

### Status → role

| Status | PLAYER role |
|--------|-------------|
| `active`, `trialing`, `active_until_period_end` | granted |
| `payment_failed`, `canceled`, anything else | removed |

### Lifecycle notifications

| Transition | DM + email |
|-----------|------------|
| becomes active (first time) | **welcome** |
| `invoice.payment_succeeded` while already active | **renewal** |
| `cancel_at_period_end` set | **cancel scheduled** |
| `customer.subscription.deleted` | **canceled** |
| `invoice.payment_failed` | **payment failed** |

## Environment

Required: `STRIPE_SECRET_KEY`, `STRIPE_MONTHLY_PRICE_ID`, `STRIPE_WEBHOOK_SECRET`.
Optional email: `RESEND_API_KEY` + `EMAIL_FROM`, or the `SMTP_*` set. With no
email provider configured the bot logs the intended email and keeps working.

## Testing

### 1. Offline acceptance test (no Stripe/Discord/Supabase needed)

```bash
python server/scripts/test_stripe_flow.py
```

Proves: PLAYER granted on paid checkout; duplicate webhook is a no-op (no double
role / record / DM / email); renewal, payment failure, cancellation; customer
mapping; anti-hijack; a blocked DM never blocks the role; unmapped events are
ignored, not crashed.

### 2. Live idempotency + signature check (safe against production)

```bash
python server/scripts/check_stripe_live.py
```

Verifies the real `STRIPE_WEBHOOK_SECRET` accepts valid and rejects forged
signatures, and that the Supabase `stripe_events` unique constraint blocks a
second claim. It cleans up its throwaway row.

### 3. End-to-end with Stripe CLI (test mode)

```bash
stripe login
stripe listen --forward-to https://<your-railway-app>/stripe/webhook
# in another shell — drive the lifecycle:
stripe trigger checkout.session.completed
stripe trigger invoice.payment_succeeded
stripe trigger invoice.payment_failed
stripe trigger customer.subscription.deleted
```

Then in Discord (as ADMIN):

- `!stripe_events` — recent events and their processing result.
- `!subscription_status` — your stored status.
- `!resync_subscription @user` — re-apply the PLAYER role from stored status.

To prove duplicate-safety, re-send any event with the same id (Stripe's
"Resend" button in the Dashboard, or replay from `stripe listen`): the second
delivery returns `{"duplicate": true}` and changes nothing.

## Troubleshooting

- **Paid but no PLAYER role**: check `!stripe_events`. If the event shows a role
  hierarchy / permission error, move the bot's role above `PLAYER` in
  Server Settings → Roles and run `!resync_subscription @user`. The bot also
  re-grants the role automatically when a paid user joins the server.
- **No email**: expected if no `RESEND_API_KEY` / `SMTP_*` is set — the intended
  email is logged instead.
