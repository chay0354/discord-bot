# Deployment and verification — September 2026 flow fixes

## Required configuration before deployment

- Railway: set `CRM_ADMIN_API_KEY` to a private, strong key. Admin endpoints now
  return 503 if it is absent, and 401 for an incorrect key. `/api/health` remains public.
- Railway: set `STRIPE_WEBHOOK_SECRET` to the signing secret for the actual Stripe
  webhook endpoint. Unsigned requests are no longer accepted, even when this variable
  is missing. Keep `STRIPE_SECRET_KEY` and `STRIPE_MONTHLY_PRICE_ID` configured.
- Stripe webhook endpoint: include `checkout.session.async_payment_succeeded` alongside
  the existing checkout, subscription and invoice events. Completed but unpaid sessions
  do not grant access/credits; delayed settlement is handled by this additional event.
- CRM: deploy the updated frontend as well as the server. Enter the admin key in its
  sign-in form. Remove `VITE_ADMIN_API_KEY` from frontend build settings. If a real key
  was previously bundled into the public site, rotate it in Railway.
- If CRM runs on Vercel, set `CRM_CORS_ORIGINS` to its exact HTTPS origin(s), comma
  separated. Arbitrary Vercel sites are no longer automatically allowed.
- Run **one bot/API replica**, matching the existing architecture. Vote and webhook
  locks serialize within that process; multi-replica operation needs database transactions.

No new database migration is introduced here. Extra-vote grant identifiers are stored
in the existing `message_state` payload with the balance. The existing
`supabase_part_e_votes.sql` migration is still a prerequisite for paid vote stacking.

## Changes

- Serializes concurrent clicks by the same member across ballot views.
- Uses the persisted early window exclusively; stale memory cannot override a closed window.
- Rechecks allowance in the destination category when a ticker has been reclassified.
- Rejects missing historical vote roles for WINNER eligibility.
- Uses New York time for week boundaries and winter/summer Friday selection cutoff.
- Makes purchased credits and winner bonuses retry-safe using durable grant ids.
- Anchors next-week bonuses to the awarded ISO week, including year boundaries.
- Requires confirmed checkout settlement and webhook signatures; handles rotating signatures.
- Keeps unrelated one-time checkouts out of subscription handling.
- Avoids starting an extra-vote purchase when no voting week is open.
- Finds the styled Q&A channel, falls back to an accessible existing channel, and exposes
  all ten configured questions. Refreshes its message and callbacks after restart.
- Adds a game guide in RULES linking to the existing reaction gate; preserves that gate.
- Does not publish final results if closing votes in the database fails. Admin actions
  report partial channel/close failures instead of unconditional success.
- Moves the CRM key from the public build to a per-page in-memory sign-in.

## Local verification

Run the offline `server/scripts/test_*_logic.py` suites, `test_part_d_names.py`,
`test_restart_recovery.py`, `test_winner_eligibility.py`, `test_stripe_flow.py`, and
`test_flow_regressions.py`. The last suite is behavioral regression coverage using
in-memory fakes and mocked external calls. Build the CRM with `npm run build`.

## Live checks after deployment

1. Confirm Railway loads all cogs and Discord reports the bot online. Confirm the
   Q&A and RULES guide appear; check logs if the bot lacks channel permissions.
2. With a dedicated test member, verify the no-role gate, NPC visibility, and an
   early vote in each category. An ADMIN+NPC account is not a representative NPC.
3. In a test game, check NPC duplicate/parallel clicks, PLAYER stacking to its limit,
   a paid extra pack, and subsequent limit enforcement. Use Stripe test mode for
   payment success/failure, retries, and delayed settlement; do not charge live cards.
4. Close the early window and verify later votes remain in totals but cannot win.
5. End the test competition, compare all three tables with stored votes, and verify
   awarded roles, expiry dates, notifications and the next pre-vote stage.
6. Restart during a test voting cycle; verify the same ballot, saved votes and early
   window are restored. Check Auto/Manual settings separately from manual test starts.

Local tests cannot establish production secrets, permissions, applied migrations,
Stripe deliveries, or role/DM outcomes. Those require the live checks above.
