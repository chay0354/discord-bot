from __future__ import annotations

import os

from fastapi import Header, HTTPException, status


def _expected_admin_key() -> str | None:
    key = os.getenv("CRM_ADMIN_API_KEY", "").strip()
    return key or None


def require_admin_key(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> None:
    expected = _expected_admin_key()
    if not expected:
        return
    if not x_admin_key or x_admin_key != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin key")
