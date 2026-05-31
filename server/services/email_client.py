"""Transactional email sender for subscription lifecycle events.

Pluggable provider, selected from environment:
  1. RESEND_API_KEY  -> Resend HTTPS API (recommended, no SMTP port needed)
  2. SMTP_HOST/...    -> standard SMTP via smtplib
  3. (none)           -> graceful no-op that logs the intended email

This keeps the bot fully functional in test mode even when no email
provider is configured: the email content is logged so QA can verify the
lifecycle without a live mailbox.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

import requests

RESEND_API = "https://api.resend.com/emails"


def _from_address() -> str:
    return os.getenv("EMAIL_FROM", "Meme Stock Game <onboarding@resend.dev>")


def is_configured() -> bool:
    return bool(os.getenv("RESEND_API_KEY") or os.getenv("SMTP_HOST"))


def _send_resend(to: str, subject: str, html: str, text: str) -> bool:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    resp = requests.post(
        RESEND_API,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": _from_address(), "to": [to], "subject": subject, "html": html, "text": text},
        timeout=15,
    )
    if resp.status_code >= 400:
        print(f"[email] Resend send failed {resp.status_code}: {resp.text[:200]}", flush=True)
        return False
    return True


def _send_smtp(to: str, subject: str, html: str, text: str) -> bool:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes"}

    msg = EmailMessage()
    msg["From"] = _from_address()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            if use_tls:
                server.starttls(context=ssl.create_default_context())
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001 - report and fall through
        print(f"[email] SMTP send failed: {exc!r}", flush=True)
        return False


def send_email(to: str | None, subject: str, html: str, text: str) -> bool:
    """Send an email. Returns True if a provider accepted it.

    Never raises: a failure to email must not break webhook processing.
    """
    if not to:
        print(f"[email] No recipient for '{subject}' — skipped", flush=True)
        return False
    try:
        if os.getenv("RESEND_API_KEY"):
            return _send_resend(to, subject, html, text)
        if os.getenv("SMTP_HOST"):
            return _send_smtp(to, subject, html, text)
    except Exception as exc:  # noqa: BLE001
        print(f"[email] send error: {exc!r}", flush=True)
        return False
    # No provider configured: log so QA can still verify the lifecycle.
    print(f"[email] (provider not configured) would send to {to}: {subject}", flush=True)
    return False


# --- Lifecycle templates -------------------------------------------------

def _wrap(title: str, body_html: str) -> str:
    return (
        f"<div style=\"font-family:Arial,sans-serif;max-width:540px;margin:auto\">"
        f"<h2 style=\"color:#5865F2\">{title}</h2>{body_html}"
        f"<hr><p style=\"color:#888;font-size:12px\">Meme Stock Game · This is a transactional message about your subscription.</p>"
        f"</div>"
    )


def subscription_email(kind: str, *, username: str | None, period_end: str | None) -> dict[str, str] | None:
    """Return {subject, html, text} for a lifecycle kind, or None if no email."""
    name = username or "there"
    pe = period_end or "the end of your billing period"
    templates: dict[str, dict[str, str]] = {
        "welcome": {
            "subject": "Your PLAYER subscription is active",
            "title": "Welcome to PLAYER 🎉",
            "body": (
                f"<p>Hi {name},</p><p>Your subscription is now <b>active</b>. "
                "You now have PLAYER access: up to 5 votes per category, live leaderboards, "
                "and ticker pick channels.</p>"
            ),
        },
        "renewal": {
            "subject": "Your PLAYER subscription renewed",
            "title": "Subscription renewed ✅",
            "body": (
                f"<p>Hi {name},</p><p>Your PLAYER subscription has renewed successfully. "
                f"Your access continues until <b>{pe}</b>.</p>"
            ),
        },
        "cancel_scheduled": {
            "subject": "Your PLAYER subscription will not renew",
            "title": "Cancellation scheduled",
            "body": (
                f"<p>Hi {name},</p><p>Your subscription is set to cancel at the end of the current period "
                f"(<b>{pe}</b>). You keep PLAYER access until then.</p>"
            ),
        },
        "canceled": {
            "subject": "Your PLAYER subscription has ended",
            "title": "Subscription ended",
            "body": (
                f"<p>Hi {name},</p><p>Your PLAYER subscription has ended and PLAYER access has been removed. "
                "You can re-subscribe anytime from the server.</p>"
            ),
        },
        "payment_failed": {
            "subject": "Payment failed for your PLAYER subscription",
            "title": "Payment failed ⚠️",
            "body": (
                f"<p>Hi {name},</p><p>We could not process your latest payment. "
                "Please update your payment method in the billing portal to keep PLAYER access.</p>"
            ),
        },
    }
    tpl = templates.get(kind)
    if not tpl:
        return None
    html = _wrap(tpl["title"], tpl["body"])
    # crude text fallback
    import re

    text = re.sub("<[^>]+>", "", tpl["body"]).strip()
    return {"subject": tpl["subject"], "html": html, "text": text}
