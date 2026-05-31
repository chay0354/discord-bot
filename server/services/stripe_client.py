from __future__ import annotations

import hmac
import time
from hashlib import sha256
from typing import Any

import requests

from config import StripeSettings


STRIPE_API = "https://api.stripe.com/v1"


class StripeClientError(RuntimeError):
    pass


def _settings() -> StripeSettings:
    settings = StripeSettings()
    if not settings.secret_key:
        raise StripeClientError("STRIPE_SECRET_KEY is not set")
    if not settings.price_id:
        raise StripeClientError("STRIPE_MONTHLY_PRICE_ID is not set")
    return settings


def create_checkout_session(discord_id: int, username: str) -> str:
    settings = _settings()
    response = requests.post(
        f"{STRIPE_API}/checkout/sessions",
        auth=(settings.secret_key, ""),
        data={
            "mode": "subscription",
            "line_items[0][price]": settings.price_id,
            "line_items[0][quantity]": "1",
            "client_reference_id": str(discord_id),
            "success_url": settings.success_url,
            "cancel_url": settings.cancel_url,
            "allow_promotion_codes": "false",
            "subscription_data[metadata][discord_id]": str(discord_id),
            "metadata[discord_id]": str(discord_id),
            "metadata[discord_username]": username,
            "phone_number_collection[enabled]": "true",
        },
        timeout=15,
    )
    if response.status_code >= 400:
        raise StripeClientError(f"Stripe checkout failed: {response.status_code} {response.text}")
    data = response.json()
    return data["url"]


def create_billing_portal_session(customer_id: str) -> str:
    settings = _settings()
    response = requests.post(
        f"{STRIPE_API}/billing_portal/sessions",
        auth=(settings.secret_key, ""),
        data={
            "customer": customer_id,
            "return_url": settings.portal_return_url,
        },
        timeout=15,
    )
    if response.status_code >= 400:
        raise StripeClientError(f"Stripe portal session failed: {response.status_code} {response.text}")
    return response.json()["url"]


def verify_webhook_signature(payload: bytes, signature_header: str, webhook_secret: str) -> bool:
    parts = dict(item.split("=", 1) for item in signature_header.split(",") if "=" in item)
    timestamp = parts.get("t")
    signature = parts.get("v1")
    if not timestamp or not signature:
        return False
    if abs(time.time() - int(timestamp)) > 300:
        return False
    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    expected = hmac.new(webhook_secret.encode("utf-8"), signed_payload, sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def retrieve_subscription(subscription_id: str) -> dict[str, Any]:
    settings = _settings()
    response = requests.get(
        f"{STRIPE_API}/subscriptions/{subscription_id}",
        auth=(settings.secret_key, ""),
        timeout=15,
    )
    if response.status_code >= 400:
        raise StripeClientError(f"Stripe subscription lookup failed: {response.status_code} {response.text}")
    return response.json()
