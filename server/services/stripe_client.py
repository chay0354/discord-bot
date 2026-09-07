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


def _settings(*, require_price: bool = False) -> StripeSettings:
    settings = StripeSettings()
    if not settings.secret_key:
        raise StripeClientError("STRIPE_SECRET_KEY is not set")
    if require_price and not settings.price_id:
        raise StripeClientError("STRIPE_MONTHLY_PRICE_ID is not set")
    return settings


def create_checkout_session(discord_id: int, username: str) -> str:
    settings = _settings(require_price=True)
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


def create_extra_votes_checkout_session(
    discord_id: int,
    username: str,
    *,
    week_key: str,
    guild_id: int,
    pack_size: int,
    amount_cents: int,
    price_id: str | None = None,
) -> str:
    """One-time checkout that grants extra votes (not a PLAYER subscription)."""
    settings = _settings()
    data: dict[str, str] = {
        "mode": "payment",
        "client_reference_id": str(discord_id),
        "success_url": settings.success_url,
        "cancel_url": settings.cancel_url,
        "metadata[kind]": "extra_votes",
        "metadata[discord_id]": str(discord_id),
        "metadata[discord_username]": username,
        "metadata[week_key]": week_key,
        "metadata[guild_id]": str(guild_id),
        "metadata[pack_size]": str(pack_size),
        "allow_promotion_codes": "false",
    }
    if price_id or settings.extra_votes_price_id:
        data["line_items[0][price]"] = str(price_id or settings.extra_votes_price_id)
        data["line_items[0][quantity]"] = "1"
    else:
        data["line_items[0][quantity]"] = "1"
        data["line_items[0][price_data][currency]"] = "usd"
        data["line_items[0][price_data][unit_amount]"] = str(max(50, int(amount_cents)))
        data["line_items[0][price_data][product_data][name]"] = (
            f"+{pack_size} extra vote(s) per category — week {week_key}"
        )
    response = requests.post(
        f"{STRIPE_API}/checkout/sessions",
        auth=(settings.secret_key, ""),
        data=data,
        timeout=15,
    )
    if response.status_code >= 400:
        raise StripeClientError(f"Stripe extra-votes checkout failed: {response.status_code} {response.text}")
    return response.json()["url"]


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
    parts = [item.strip().split("=", 1) for item in signature_header.split(",") if "=" in item]
    timestamp = next((v for k, v in parts if k == "t"), None)
    signatures = [v for k, v in parts if k == "v1"]
    if not timestamp or not signatures:
        return False
    try:
        age = abs(time.time() - int(timestamp))
    except (ValueError, OverflowError):
        return False
    if age > 300:
        return False
    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    expected = hmac.new(webhook_secret.encode("utf-8"), signed_payload, sha256).hexdigest()
    return any(hmac.compare_digest(expected, signature) for signature in signatures)


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
