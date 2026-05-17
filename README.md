# Meme Stock Discord Bot

Discord bot and admin REST API (Supabase, Finnhub). Deploy on [Railway](https://railway.app).

## Local

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python run.py
```

API: `http://127.0.0.1:8000` — health: `GET /api/health`

CRM dashboard: [discord-crm](https://github.com/chay0354/discord-crm)

## Railway

Set env from `.env.example`. Start: `python run.py` (see `Procfile`).
