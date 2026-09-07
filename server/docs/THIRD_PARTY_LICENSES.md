# Third-party libraries (version + license)

Generated for Appendix C handover. Runtime versions are pinned by the environment
that installs `server/requirements.txt` and `crm/package.json`. No secrets.

## Python — `server/requirements.txt`

| Package | Typical license | Use |
|---------|-----------------|-----|
| discord.py | MIT | Discord bot |
| aiohttp | Apache-2.0 | HTTP client (Stripe / market data) |
| python-dotenv | BSD-3-Clause | Load `.env` |
| requests | Apache-2.0 | HTTP |
| fastapi | MIT | CRM / health / Stripe webhook API |
| uvicorn | BSD-3-Clause | ASGI server |
| pydantic | MIT | API models |
| tzdata | Apache-2.0 / public-domain IANA | America/New_York DST |

## CRM — `crm/package.json`

| Package | Typical license | Use |
|---------|-----------------|-----|
| react / react-dom | MIT | Admin dashboard |
| vite | MIT | Dev server / build |
| typescript | Apache-2.0 | Types |
| @vitejs/plugin-react | MIT | Vite React plugin |
| @types/react / @types/react-dom | MIT | Types |

## Third-party services (not libraries)

| Service | Cost note |
|---------|-----------|
| Discord | Bot application; no per-message fee at this scale |
| Railway | Hosting for `server/` — paid plan as configured by the account owner |
| Supabase | Postgres — free or paid tier of the project owner |
| Stripe | Processing fees on subscriptions / extra-vote packs |
| Finnhub | Market data API key (free tier available) |
| Vercel | Optional CRM host |
| Resend / SMTP | Optional transactional email |

Card numbers are **never** stored in this project. Stripe holds payment methods.
