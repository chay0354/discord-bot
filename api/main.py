from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api.auth import require_admin_key
from game_control import (
    get_audit_logs,
    get_game_history,
    get_game_status,
    get_leaderboards,
    get_subscriptions,
    get_tickers,
    get_votes,
    run_action,
)

CRM_DIST = Path(__file__).resolve().parents[2] / "crm" / "dist"

app = FastAPI(title="Meme Stock Game API", version="1.0.0")

_origins = os.getenv(
    "CRM_CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)
allow_origins = [o.strip() for o in _origins.split(",") if o.strip()]
allow_all = os.getenv("CRM_CORS_ALLOW_ALL", "").lower() in {"1", "true", "yes"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if allow_all else allow_origins,
    allow_origin_regex=None if allow_all else r"https://.*\.vercel\.app",
    allow_credentials=not allow_all,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ActionBody(BaseModel):
    actor_id: int | None = None


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/meta")
def meta(_: None = Depends(require_admin_key)) -> dict[str, Any]:
    from config import CATEGORY_TITLES, CATEGORIES, TICKER_LIMIT_PER_CATEGORY

    return {
        "categories": CATEGORIES,
        "category_titles": CATEGORY_TITLES,
        "ticker_limit": TICKER_LIMIT_PER_CATEGORY,
    }


@app.get("/api/game/status")
def game_status(_: None = Depends(require_admin_key)) -> dict[str, Any]:
    try:
        return get_game_status()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/game/tickers")
def game_tickers(_: None = Depends(require_admin_key)) -> dict[str, Any]:
    try:
        return get_tickers()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/game/votes")
def game_votes(_: None = Depends(require_admin_key)) -> dict[str, Any]:
    try:
        return get_votes()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/game/leaderboards")
def game_leaderboards(_: None = Depends(require_admin_key)) -> dict[str, Any]:
    try:
        return get_leaderboards()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/game/history")
def game_history(limit: int = 20, _: None = Depends(require_admin_key)) -> list[dict[str, Any]]:
    try:
        return get_game_history(limit=min(limit, 50))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/game/audit")
def game_audit(limit: int = 50, _: None = Depends(require_admin_key)) -> list[dict[str, Any]]:
    try:
        return get_audit_logs(limit=min(limit, 200))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/subscriptions")
def subscriptions(limit: int = 100, _: None = Depends(require_admin_key)) -> list[dict[str, Any]]:
    try:
        return get_subscriptions(limit=min(limit, 500))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/game/actions/{action}")
async def game_action(
    action: str,
    body: ActionBody | None = None,
    _: None = Depends(require_admin_key),
) -> dict[str, Any]:
    try:
        return await run_action(action, actor_id=(body.actor_id if body else None))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _mount_crm_static() -> None:
    if not CRM_DIST.is_dir():
        return
    assets = CRM_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="crm-assets")

    @app.get("/")
    def crm_index() -> FileResponse:
        return FileResponse(CRM_DIST / "index.html")

    @app.get("/{full_path:path}")
    def crm_spa(full_path: str) -> FileResponse:
        if full_path.startswith("api"):
            raise HTTPException(status_code=404)
        candidate = CRM_DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(CRM_DIST / "index.html")


if os.getenv("SERVE_CRM", "false").lower() in {"1", "true", "yes"}:
    _mount_crm_static()
