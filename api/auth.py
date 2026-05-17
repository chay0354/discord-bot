from __future__ import annotations

import os

from fastapi import Header, HTTPException, status


def _expected_admin_key() -> str:
    key = os.getenv("CRM_ADMIN_API_KEY", "").strip()
    if key:
        return key
    # Local dev default (override in production via CRM_ADMIN_API_KEY)
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_SERVICE_NAME"):
        return ""
    return "dev-local-admin-key"


def require_admin_key(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> None:
    expected = _expected_admin_key()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CRM_ADMIN_API_KEY is not configured on the server",
        )
    if not x_admin_key or x_admin_key != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin key")
