from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

import requests


class SiteBridgeError(RuntimeError):
    pass


def _site_url() -> str:
    return os.getenv("SITE_URL", "https://memestock.shop").rstrip("/")


def is_configured() -> bool:
    return len(os.getenv("BRIDGE_SECRET", "").strip()) >= 64


def post(path: str, payload: dict[str, Any], *, attempts: int = 4) -> dict[str, Any]:
    """Signed call to the website bridge (see Wallet::bridgeAuth on the site)."""
    secret = os.getenv("BRIDGE_SECRET", "").strip()
    if len(secret) < 64:
        raise SiteBridgeError("BRIDGE_SECRET is not set on the bot")
    body = json.dumps(payload, separators=(",", ":"))
    last: Exception | None = None
    for attempt in range(attempts):
        # Each attempt needs a fresh nonce: the site rejects replays.
        ts = str(int(time.time()))
        nonce = secrets.token_hex(16)
        canonical = f"{ts}\n{nonce}\nPOST\n{path}\n{hashlib.sha256(body.encode()).hexdigest()}"
        signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        try:
            response = requests.post(
                _site_url() + path,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-MS-Timestamp": ts,
                    "X-MS-Nonce": nonce,
                    "X-MS-Signature": signature,
                },
                timeout=60,
            )
        except requests.RequestException as exc:
            last = exc
            time.sleep(2 + attempt * 2)
            continue
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            raise SiteBridgeError(str(data.get("error") or f"Website returned HTTP {response.status_code}"))
        return data
    raise SiteBridgeError(f"Website unreachable: {last}")
