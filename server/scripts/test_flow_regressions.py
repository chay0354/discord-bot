"""Offline behavioral regressions; no Discord, Stripe or Supabase connections."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database
from api.auth import require_admin_key
from cogs import billing, weekly_picks as votes
from cogs.qa_channel import QAView, _find_channel
from config import QA_CHANNEL_CANDIDATES
from discord_names import names_match
from services.stripe_client import verify_webhook_signature


class PureTests(unittest.TestCase):
    def test_friday_selection_cutoff_in_winter(self):
        before = datetime(2026, 1, 9, 20, 30, tzinfo=timezone.utc)
        after = before + timedelta(hours=1)
        self.assertEqual(database.ticker_selection_week_key_for(before), "2026-W02")
        self.assertEqual(database.ticker_selection_week_key_for(after), "2026-W03")

    def test_sunday_new_york_does_not_roll_week_at_utc_midnight(self):
        self.assertEqual(database.week_key_for(datetime(2026, 9, 7, 1, tzinfo=timezone.utc)), "2026-W36")

    def test_missing_vote_role_never_qualifies_as_npc(self):
        rows = [dict(user_id=1, category=c, ticker="A", is_early=True) for c in database.CATEGORIES]
        result, _ = database.compute_eligible_winner_ids(
            winning_tickers={c: {"A"} for c in database.CATEGORIES}, vote_rows=rows,
            active_winner_user_ids=set(),
        )
        self.assertEqual(result, [])

    def test_styled_qa_is_a_candidate(self):
        self.assertTrue(any(names_match("ℚ＆𝗔", name) for name in QA_CHANNEL_CANDIDATES))

    def test_qa_falls_back_when_first_channel_is_inaccessible(self):
        blocked = SimpleNamespace(name="ℚ＆𝗔", permissions_for=lambda _: SimpleNamespace(view_channel=False))
        usable = SimpleNamespace(name="q-and-a", permissions_for=lambda _: SimpleNamespace(
            view_channel=True, send_messages=True, read_message_history=True))
        guild = SimpleNamespace(me=object(), text_channels=[blocked, usable])
        self.assertIs(_find_channel(guild, ("ℚ＆𝗔", "q-and-a")), usable)

    def test_signature_rejects_invalid_timestamp(self):
        self.assertFalse(verify_webhook_signature(b"{}", "t=invalid,v1=abcd", "secret"))

    def test_signature_accepts_any_valid_rotation_signature(self):
        timestamp = str(int(time.time()))
        sig = hmac.new(b"secret", timestamp.encode() + b".{}", hashlib.sha256).hexdigest()
        self.assertTrue(verify_webhook_signature(b"{}", f"t={timestamp},v1={sig},v1=old", "secret"))

    def test_admin_api_fails_closed(self):
        with patch.dict(os.environ, {"CRM_ADMIN_API_KEY": ""}):
            with self.assertRaises(Exception) as raised:
                require_admin_key(None)
            self.assertEqual(raised.exception.status_code, 503)

    def test_admin_api_checks_key(self):
        with patch.dict(os.environ, {"CRM_ADMIN_API_KEY": "test-only-key"}):
            require_admin_key("test-only-key")
            with self.assertRaises(Exception) as raised:
                require_admin_key("wrong")
            self.assertEqual(raised.exception.status_code, 401)

    def test_credit_retry_does_not_duplicate_balance(self):
        states = {}
        def get(g, k):
            return copy.deepcopy(states.get((g, k)))
        def save(g, k, **kw):
            states[g, k] = copy.deepcopy(kw)
        with patch.object(database, "get_message_state", side_effect=get), patch.object(database, "save_message_state", side_effect=save):
            self.assertEqual(database.add_extra_vote_credits(1, 2, "2026-W37", 1, source="stripe_purchase", grant_id="stripe:cs1"), 1)
            self.assertEqual(database.add_extra_vote_credits(1, 2, "2026-W37", 1, source="stripe_purchase", grant_id="stripe:cs1"), 1)
            self.assertEqual(database.add_extra_vote_credits(1, 2, "2026-W37", 1, source="stripe_purchase", grant_id="stripe:cs2"), 2)

    def test_winner_bonus_retry_and_year_boundary(self):
        states = {}
        def get(g, k):
            return copy.deepcopy(states.get((g, k)))
        def save(g, k, **kw):
            states[g, k] = copy.deepcopy(kw)
        with patch.object(database, "get_message_state", side_effect=get), patch.object(database, "save_message_state", side_effect=save):
            stats = database.record_winner_incentive(1, 2, "2026-W53")
            again = database.record_winner_incentive(1, 2, "2026-W53")
            self.assertEqual(stats["bonus_week"], "2027-W01")
            self.assertEqual(again["total_wins"], 1)
            self.assertEqual(database.extra_vote_credits(1, 2, "2027-W01"), stats["bonus_votes"])


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_ten_qa_questions_have_buttons(self):
        view = QAView([dict(q=f"Question {i}", a="Answer") for i in range(10)])
        self.assertEqual(len(view.children), 10)
        self.assertEqual(view.children[-1].custom_id, "qa:toggle:9")

    async def test_parallel_clicks_are_serialized_across_views(self):
        count = 0
        accepted = []
        async def handler(interaction, ticker, **kwargs):
            nonlocal count
            if count >= 1:
                return
            await asyncio.sleep(0.01)
            count += 1
            accepted.append(ticker)
        a, b = votes.WeeklyVotingView(0, ["A"]), votes.WeeklyVotingView(0, ["B"])
        a._handle_vote_locked = handler
        b._handle_vote_locked = handler
        interaction = SimpleNamespace(guild=SimpleNamespace(id=1), user=SimpleNamespace(id=2))
        await asyncio.gather(a._handle_vote(interaction, "A", already_deferred=True), b._handle_vote(interaction, "B", already_deferred=True))
        self.assertEqual(len(accepted), 1)

    async def test_db_closed_early_window_overrides_stale_memory(self):
        view = votes.WeeklyVotingView(0, ["A"])
        ctx = dict(voting_open=True, actual_category="small", prior_vote_category=None,
                   vote_count=0, early_window_open=False, early_window_start_at=None, early_window_end_at=None)
        member = SimpleNamespace(id=2, roles=[SimpleNamespace(name="NPC")])
        with patch.object(database, "vote_button_context", return_value=ctx), patch.object(database, "record_vote", return_value=(True, "ok")) as record, patch.object(database, "log_event"), patch.object(votes, "_schedule_leaderboard_update"), patch.object(votes, "is_early_window_active", return_value=True), patch.object(votes, "_record_early_vote_if_applicable"):
            ok, _, _, _ = await view._persist_vote(guild=SimpleNamespace(id=1), cat=0, category_key="small", week_key="2026-W37", ticker="A", member=member, limit=1, role_at_vote="NPC")
            self.assertTrue(ok)
            self.assertFalse(record.call_args.args[-1])
            await asyncio.sleep(0.01)

    async def test_reclassified_ticker_uses_destination_category_limit(self):
        view = votes.WeeklyVotingView(0, ["A"])
        ctx = dict(voting_open=True, actual_category="mid", prior_vote_category=None, vote_count=0)
        member = SimpleNamespace(id=2, roles=[SimpleNamespace(name="NPC")])
        with patch.object(database, "vote_button_context", return_value=ctx), patch.object(database, "user_vote_count", return_value=1), patch.object(database, "record_vote") as record, patch.object(database, "log_event"):
            ok, _, _, _ = await view._persist_vote(guild=SimpleNamespace(id=1), cat=0, category_key="small", week_key="2026-W37", ticker="A", member=member, limit=1, role_at_vote="NPC")
            self.assertFalse(ok)
            record.assert_not_called()
            await asyncio.sleep(0.01)

    async def test_unpaid_completed_checkout_does_not_grant_extra_votes(self):
        cog = billing.BillingCog.__new__(billing.BillingCog)
        cog.bot = SimpleNamespace(guilds=[])
        with patch.object(database, "add_extra_vote_credits") as credit:
            result = await cog._grant_purchased_votes(dict(id="cs1", status="complete", payment_status="unpaid", metadata={"discord_id": "2"}))
            self.assertEqual(result, (2, "unpaid"))
            credit.assert_not_called()

    async def test_webhook_requires_configured_signing_secret(self):
        cog = billing.BillingCog.__new__(billing.BillingCog)
        with patch.object(billing, "StripeSettings", return_value=SimpleNamespace(webhook_secret=None)):
            with self.assertRaises(ValueError):
                await cog.process_stripe_webhook_payload(b"{}")

    async def test_async_settlement_routes_to_extra_votes_only(self):
        cog = billing.BillingCog.__new__(billing.BillingCog)
        cog._grant_purchased_votes = AsyncMock(return_value=(2, "extra_votes"))
        cog._sync_subscription = AsyncMock()
        event = {"id": "evt1", "type": "checkout.session.async_payment_succeeded", "data": {"object": {"id": "cs1", "metadata": {"kind": "extra_votes"}}}}
        with patch.object(billing, "StripeSettings", return_value=SimpleNamespace(webhook_secret="test")), patch.object(billing, "verify_webhook_signature", return_value=True), patch.object(database, "get_stripe_event", return_value=None), patch.object(database, "claim_stripe_event", return_value=True), patch.object(database, "mark_stripe_event_processed"):
            await cog.process_stripe_webhook_payload(json.dumps(event).encode())
            cog._grant_purchased_votes.assert_awaited_once()
            cog._sync_subscription.assert_not_awaited()

    async def test_concurrent_webhooks_do_not_overlap(self):
        cog = billing.BillingCog.__new__(billing.BillingCog)
        in_flight = 0
        peak = 0
        async def process(*args):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return {}
        cog._process_stripe_event = process
        await asyncio.gather(cog.process_stripe_webhook_payload(b"{}"), cog.process_stripe_webhook_payload(b"{}"))
        self.assertEqual(peak, 1)

    async def test_failed_event_persistence_prevents_side_effects(self):
        cog = billing.BillingCog.__new__(billing.BillingCog)
        cog._sync_subscription = AsyncMock()
        event = {"id": "evt1", "type": "checkout.session.completed", "data": {"object": {}}}
        with patch.object(billing, "StripeSettings", return_value=SimpleNamespace(webhook_secret="test")), patch.object(billing, "verify_webhook_signature", return_value=True), patch.object(database, "get_stripe_event", return_value=None), patch.object(database, "claim_stripe_event", side_effect=RuntimeError("database unavailable")):
            with self.assertRaises(RuntimeError):
                await cog.process_stripe_webhook_payload(json.dumps(event).encode())
            cog._sync_subscription.assert_not_awaited()

    async def test_delayed_paid_checkout_does_not_reactivate_canceled_subscription(self):
        cog = billing.BillingCog.__new__(billing.BillingCog)
        cog.bot = SimpleNamespace(guilds=[])
        cog._resolve_discord_id = Mock(return_value=(2, "subscription_metadata"))
        cog._set_player_role = AsyncMock()
        cog._notify = AsyncMock()
        cog._mod_log = AsyncMock()
        obj = dict(id="cs1", subscription="sub1", payment_status="paid")
        with patch.object(billing, "retrieve_subscription", return_value=dict(id="sub1", status="canceled")), patch.object(database, "get_subscription", return_value=None), patch.object(database, "upsert_user"), patch.object(database, "upsert_subscription") as save, patch.object(database, "log_event"):
            self.assertEqual(await cog._sync_subscription(obj, "checkout.session.completed"), (2, "canceled"))
            self.assertEqual(save.call_args.kwargs["status"], "canceled")
            cog._set_player_role.assert_awaited_once_with(2, False)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
