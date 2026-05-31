"""Live check: signature verification + Supabase idempotency round-trip.

Inserts a clearly-fake event id, proves the unique constraint blocks a second
claim, then cleans up. Safe to run against production; touches only a throwaway
stripe_events row.
"""
import sys
import time
import hmac
from hashlib import sha256
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

import database
from config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL, StripeSettings
from services.stripe_client import verify_webhook_signature

ok = True

# 1) Signature verification with the real secret format
secret = StripeSettings().webhook_secret or "whsec_dummy"
payload = b'{"hello":"world"}'
ts = str(int(time.time()))
sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, sha256).hexdigest()
header = f"t={ts},v1={sig}"
good = verify_webhook_signature(payload, header, secret)
bad = verify_webhook_signature(payload, f"t={ts},v1=deadbeef", secret)
print(f"[sig] valid-signature accepted={good}  forged-signature rejected={not bad}")
ok = ok and good and not bad

# 2) Live idempotency round-trip
event_id = f"evt_test_{int(time.time())}"
first = database.claim_stripe_event(event_id, "test.event", {"t": 1})
second = database.claim_stripe_event(event_id, "test.event", {"t": 1})
print(f"[idempotency] first-claim={first}  duplicate-claim={second}")
ok = ok and first and not second

database.mark_stripe_event_processed(event_id, discord_id=1, status="active")
row = database.get_stripe_event(event_id)
print(f"[idempotency] processed flag persisted={bool(row and row.get('processed'))}")
ok = ok and bool(row and row.get("processed"))

# cleanup
requests.delete(
    f"{SUPABASE_URL.rstrip('/')}/rest/v1/stripe_events?id=eq.{event_id}",
    headers={"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"},
    timeout=15,
)
print(f"[cleanup] removed {event_id}")
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
