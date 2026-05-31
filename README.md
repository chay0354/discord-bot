# Meme Stock Discord Game

Monorepo with two apps:

| Folder | Purpose | Deploy |
|--------|---------|--------|
| [`server/`](server/) | Discord bot + admin REST API (Supabase, game logic) | [Railway](https://railway.app) |
| [`crm/`](crm/) | React admin dashboard | [Vercel](https://vercel.com) |

## Quick start (local)

### 1. Server (bot + API)

```bash
cd server
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy ..\.env .env        # or symlink from repo root
python run.py
```

API: `http://127.0.0.1:8000`  
Health: `GET /api/health`

### 2. CRM (dev)

```bash
cd crm
npm install
# optional: echo VITE_API_URL=http://127.0.0.1:8000 > .env.local
npm run dev
```

Copy `crm/.env.local.example` → `crm/.env.local`, then open `http://localhost:5173`.

Vite proxies `/api` to port 8000 when `VITE_API_URL` is empty.

## Railway (server)

1. New project → deploy from repo, set **root directory** to `server`.
2. Variables: `DISCORD_TOKEN`, `DISCORD_GUILD_ID`, `SUPABASE_*`, `FINNHUB_API_KEY`, `PORT` (Railway sets this).
3. Optional: `SERVE_CRM=true` and build CRM into `crm/dist` in CI, or host CRM on Vercel only.

Start command: `python run.py` (see `Procfile`).

## Vercel (CRM)

1. Import repo, set **root directory** to `crm`.
2. Environment: `VITE_API_URL=https://<your-railway-app>.up.railway.app`
3. Build: `npm run build`, output `dist`.

Add your Vercel URL to Railway `CRM_CORS_ORIGINS` (or use `CRM_CORS_ALLOW_ALL=true` only for testing).

## CRM features

- **Dashboard** — week, phase, ticker counts, bot status, winners
- **Tickers** — current picks per cap bucket (live cap reconcile)
- **Votes** — leaderboards with quotes
- **Actions** — start/end pre-vote, vote stage, early window, end competition
- **Audit** — Supabase `audit_logs`
- **Billing** — Stripe subscriptions table

Actions require the Discord bot process to be online on Railway.
