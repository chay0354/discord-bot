# נספח ג' — תיעוד טכני מלא
## Meme Stock Discord Game — Bot + CRM + DB

**גרסת מסמך:** 1.0  
**Repository:** `https://github.com/chay0354/discord-bot`  
**מבנה:** Monorepo — `server/` (בוט Discord + API) + `crm/` (לוח בקרה React)

מסמך זה מספק את כל המידע הנדרש להתקנה, פריסה, תחזוקה, גיבוי ושחזור של המערכת — ללא תלות במפתח המקורי.

---

## תוכן עניינים

1. [סקירת המערכת](#1-סקירת-המערכת)
2. [דרישות מקדימות](#2-דרישות-מקדימות)
3. [מבנה הפרויקט](#3-מבנה-הפרויקט)
4. [התקנה והרצה מקומית](#4-התקנה-והרצה-מקומית)
5. [פריסה לייצור (Production)](#5-פריסה-לייצור-production)
6. [משתני סביבה (.env)](#6-משתני-סביבה-env)
7. [Discord — בוט, Roles, Channels, הרשאות](#7-discord--בוט-roles-channels-הרשאות)
8. [Supabase — מסד נתונים וסכמה](#8-supabase--מסד-נתונים-וסכמה)
9. [Stripe — מנויים, Webhooks, PLAYER role](#9-stripe--מנויים-webhooks-player-role)
10. [נתוני שוק — Finnhub / Yahoo](#10-נתוני-שוק--finnhub--yahoo)
11. [מחזור שבועי, טיימרים ו-Automation](#11-מחזור-שבועי-טיימרים-ו-automation)
12. [CRM — API ולוח הבקרה](#12-crm--api-ולוח-הבקרה)
13. [גיבוי, שחזור ו-Disaster Recovery](#13-גיבוי-שחזור-ו-disaster-recovery)
14. [אבטחה, Secrets ו-RLS](#14-אבטחה-secrets-ו-rls)
15. [Restart / Crash Recovery](#15-restart--crash-recovery)
16. [פקודות Admin ב-Discord](#16-פקודות-admin-ב-discord)
17. [סקריפטי בדיקה ו-QA](#17-סקריפטי-בדיקה-ו-qa)
18. [פתרון תקלות (Troubleshooting)](#18-פתרון-תקלות-troubleshooting)
19. [רשימת מסירה (Handover Checklist)](#19-רשימת-מסירה-handover-checklist)

---

## 1. סקירת המערכת

### מה המערכת עושה

| רכיב | תפקיד |
|------|--------|
| **Discord Bot** | ניהול משחק שבועי: בחירת טיקרים (pre-vote), הצבעות, לידרבורד, WINNER role, מנויים Stripe |
| **FastAPI (`/api/*`)** | REST API ל-CRM + health check + Stripe webhook |
| **Supabase (Postgres)** | מקור אמת לכל הנתונים: votes, picks, cycles, subscriptions, winners, audit |
| **CRM (React/Vite)** | לוח בקרה: סטטוס משחק, טיקרים, הצבעות, פעולות admin, billing, audit |

### תהליך שבועי (תמצית)

```
שישי 16:00 ET (סגירת שוק) ──► Pre-vote: מנויים בוחרים טיקרים (עד 20 לקטגוריה)
        │
        ▼
שני 09:00 ET ──► פתיחת הצבעות + חלון Early 24 שעות (WINNER eligibility)
        │
        ▼
שלישי 09:00 ET ──► סגירת חלון Early (הצבעות ממשיכות עד שישי)
        │
        ▼
שישי 16:00 ET ──► סגירת הצבעות, לידרבורד סופי, WINNER, איפוס, Pre-vote מחדש
```

> **הערה:** כל הזמנים מחושבים לפי **America/New_York (ET)** עם תמיכה ב-DST.  
> אין cron חיצוני — ה-scheduler רץ **בתוך תהליך הבוט** (`cogs/scheduler.py`).

### נקודת כניסה (Entry Point)

| סביבה | פקודה |
|--------|--------|
| מקומי | `cd server && python run.py` |
| Railway / Docker | `python server/run.py` |

`run.py` מפעיל **במקביל**: Discord bot + Uvicorn API על `PORT` (ברירת מחדל 8000).

---

## 2. דרישות מקדימות

### תוכנה

| כלי | גרסה מינימלית |
|-----|----------------|
| Python | 3.10+ (מומלץ 3.13) |
| Node.js | 18+ (ל-CRM בלבד) |
| Git | כל גרסה עדכנית |

### חשבונות חיצוניים (חובה לייצור)

| שירות | שימוש |
|--------|--------|
| **Discord Developer Portal** | בוט + Token + Intents |
| **Supabase** | Postgres + REST API |
| **Finnhub** | אימות טיקרים + market cap (מפתח חינמי) |
| **Stripe** | מנויים חודשיים + webhooks |
| **Railway** (או Docker host) | הרצת `server/` |
| **Vercel** (אופציונלי) | אירוח CRM |

---

## 3. מבנה הפרויקט

```
stock-bot/
├── server/                 # בוט + API + לוגיקת משחק
│   ├── run.py              # Entry point (bot + API)
│   ├── main.py             # Legacy wrapper → run.py
│   ├── config.py           # Channels, roles, limits, env
│   ├── database.py         # Supabase REST client
│   ├── game_control.py     # CRM actions → scheduler
│   ├── api/
│   │   ├── main.py         # FastAPI routes
│   │   └── auth.py         # X-Admin-Key
│   ├── cogs/               # Discord modules
│   │   ├── billing.py      # Stripe + PLAYER role
│   │   ├── scheduler.py    # מחזור שבועי NY
│   │   ├── submission_ui.py# Pre-vote ticker picker
│   │   ├── weekly_picks.py # הצבעות + לידרבורד
│   │   ├── admin_actions.py
│   │   └── admin_tools.py
│   ├── services/           # Finnhub, Yahoo, Stripe, Email
│   ├── scripts/            # QA + תחזוקה
│   └── docs/               # תיעוד (מסמך זה + Stripe + Restart)
├── crm/                    # React admin dashboard
├── Dockerfile              # פריסה מ-monorepo root
├── railway.toml            # הגדרות Railway
├── requirements.txt        # הפניה ל-server (Railpack)
└── .env.example            # תבנית משתני סביבה
```

---

## 4. התקנה והרצה מקומית

### 4.1 Server (Bot + API)

```bash
cd server
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
copy ..\.env .env          # Windows — או symlink
# cp ../.env .env           # macOS/Linux

python run.py
```

**אימות:**

| בדיקה | URL / פעולה |
|--------|-------------|
| API health | `GET http://127.0.0.1:8000/api/health` → `{"status":"ok"}` |
| Bot online | לוג: `[bot] Logged in as ...` |
| Discord | הבוט מופיע Online בשרת |

### 4.2 CRM (פיתוח)

```bash
cd crm
npm install
copy .env.example .env.local   # הגדר VITE_API_URL + VITE_ADMIN_API_KEY
npm run dev
```

פתח `http://localhost:5173`. ב-dev, Vite מפרוקס `/api` ל-port 8000 אם `VITE_API_URL` ריק.

### 4.3 Stripe Webhook מקומי (אופציונלי)

```bash
stripe login
stripe listen --forward-to http://127.0.0.1:8000/stripe/webhook
# העתק את whsec_... ל-STRIPE_WEBHOOK_SECRET
```

---

## 5. פריסה לייצור (Production)

### 5.1 Railway — Server (Bot + API)

**אפשרות א' — Monorepo root (מומלץ, Dockerfile):**

1. חבר את ה-repo ל-Railway.
2. Railway יזהה `Dockerfile` בשורש ויבנה אוטומטית.
3. Start command (מוגדר ב-`railway.toml`): `python server/run.py`
4. Health check: `/api/health`

**אפשרות ב' — Root Directory = `server`:**

1. Settings → **Root Directory** = `server`
2. Config file path: `/server/railway.toml`
3. Start: `python run.py`

**משתני סביבה חובה ב-Railway:**

```
DISCORD_TOKEN
DISCORD_GUILD_ID
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
FINNHUB_API_KEY
PORT                    # Railway מגדיר אוטומטית
```

**Stripe (אם מופעל):**

```
STRIPE_SECRET_KEY
STRIPE_WEBHOOK_SECRET
STRIPE_MONTHLY_PRICE_ID
STRIPE_SUCCESS_URL
STRIPE_CANCEL_URL
STRIPE_PORTAL_RETURN_URL
```

**CRM CORS:**

```
CRM_CORS_ORIGINS=https://your-crm.vercel.app
CRM_ADMIN_API_KEY=<secret>
```

### 5.2 Vercel — CRM

1. Import repo → Root Directory: `crm`
2. Build: `npm run build` → Output: `dist`
3. Environment:
   ```
   VITE_API_URL=https://<railway-app>.up.railway.app
   VITE_ADMIN_API_KEY=<same as CRM_ADMIN_API_KEY>
   ```

### 5.3 Stripe Webhook URL (Production)

```
POST https://<railway-app>.up.railway.app/stripe/webhook
```

אירועים נדרשים: `checkout.session.completed`, `customer.subscription.*`, `invoice.payment_succeeded`, `invoice.payment_failed`.

> פרטים מלאים: [`STRIPE_SUBSCRIPTIONS.md`](STRIPE_SUBSCRIPTIONS.md)

---

## 6. משתני סביבה (.env)

העתק `.env.example` → `.env` (repo root או `server/.env`).  
`run.py` טוען גם `server/.env` וגם `.env` בשורש.

### Discord

| משתנה | חובה | תיאור |
|--------|------|--------|
| `DISCORD_TOKEN` | ✅ | Bot token מ-Developer Portal |
| `DISCORD_GUILD_ID` | ✅ | Snowflake ID של השרת |

### Supabase

| משתנה | חובה | תיאור |
|--------|------|--------|
| `SUPABASE_URL` | ✅ | `https://<ref>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | Service role (server-side only!) |
| `SUPABASE_PROJECT_REF` | | מזהה פרויקט (ברירת מחדל ב-config) |

### Market Data

| משתנה | חובה | תיאור |
|--------|------|--------|
| `FINNHUB_API_KEY` | ✅ | מ-[finnhub.io](https://finnhub.io) |

### API / CRM

| משתנה | חובה | תיאור |
|--------|------|--------|
| `PORT` / `CRM_API_PORT` | | פורט API (Railway: `PORT`) |
| `CRM_ADMIN_API_KEY` | מומלץ | מפתח ל-`X-Admin-Key` header |
| `CRM_CORS_ORIGINS` | | רשימה מופרדת בפסיקים |
| `CRM_CORS_ALLOW_ALL` | | `true` ל-dev בלבד |
| `SERVE_CRM` | | `true` = הגש CRM static מ-Railway |

### Stripe

| משתנה | חובה | תיאור |
|--------|------|--------|
| `STRIPE_SECRET_KEY` | ✅* | Secret key (test/live) |
| `STRIPE_WEBHOOK_SECRET` | ✅* | `whsec_...` |
| `STRIPE_MONTHLY_PRICE_ID` | ✅* | Price ID למנוי חודשי |
| `STRIPE_SUCCESS_URL` | | Redirect אחרי checkout |
| `STRIPE_CANCEL_URL` | | Redirect בביטול |
| `STRIPE_PORTAL_RETURN_URL` | | חזרה מ-Billing Portal |
| `STRIPE_WEBHOOK_PORT` | | **מקומי בלבד** — listener נפרד |

\* חובה אם billing מופעל.

### Email (אופציונלי)

| משתנה | תיאור |
|--------|--------|
| `RESEND_API_KEY` + `EMAIL_FROM` | Resend (מומלץ) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS` | SMTP חלופי |

ללא email provider — הבוט **ממשיך לעבוד** ורק רושם ללוג את תוכן המייל.

### Roles / Channels (override)

| משתנה | ברירת מחדל |
|--------|------------|
| `ROLE_NPC` | `NPC` |
| `ROLE_PLAYER` | `PLAYER` |
| `ROLE_WINNER` | `WINNER` |
| `ROLE_ADMIN` | `ADMIN` |
| `SUBSCRIBE_CHANNEL` | `subscribe` |
| `MANAGE_SUBSCRIPTION_CHANNEL` | `manage-subscription` |
| `PICK_RESULTS_CHANNEL` | `pick-results` |

### CRM Frontend (`crm/.env.local`)

| משתנה | תיאור |
|--------|--------|
| `VITE_API_URL` | URL של Railway API |
| `VITE_ADMIN_API_KEY` | זהה ל-`CRM_ADMIN_API_KEY` |

---

## 7. Discord — בוט, Roles, Channels, הרשאות

### 7.1 יצירת הבוט (Developer Portal)

1. [Discord Developer Portal](https://discord.com/developers/applications) → New Application
2. **Bot** → Reset Token → `DISCORD_TOKEN`
3. **Privileged Gateway Intents** (חובה):
   - ✅ Server Members Intent
   - ✅ Message Content Intent
4. **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Permissions: `Manage Roles`, `Manage Channels`, `Send Messages`, `Embed Links`, `Read Message History`, `Manage Messages`, `Use External Emojis`

### 7.2 Roles

| Role | שימוש |
|------|--------|
| `NPC` | משתמש רגיל — 1 vote לקטגוריה |
| `PLAYER` | מנוי Stripe — 5 votes + גישה ל-pre-vote channels |
| `WINNER` | זוכה שבועי — 5 votes + pre-vote (שבוע אחד) |
| `ADMIN` | פקודות admin + CRM actions |

> **חשוב:** Role של הבוט חייב להיות **מעל** `PLAYER` ו-`WINNER` בהיררכיית Discord.

### 7.3 Channels (שמות ברירת מחדל)

| Channel | תפקיד |
|---------|--------|
| `small-cap-ticker` | Pre-vote — Small Cap |
| `mid-cap-ticker` | Pre-vote — Mid Cap |
| `large-cap-ticker` | Pre-vote — Large Cap |
| `pick-results` | לוח טיקרים שנבחרו (0/20) |
| `small-cap` | הצבעות Small Cap |
| `mid-cap` | הצבעות Mid Cap |
| `large-cap` | הצבעות Large Cap |
| `small-cap-live` | לידרבורד חי Small |
| `mid-cap-live` | לידרבורד חי Mid |
| `large-cap-live` | לידרבורד חי Large |
| `mod` | דוחות admin + שגיאות |
| `admin-actions` | פאנל פעולות |
| `subscribe` | Subscribe (Stripe checkout) |
| `manage-subscription` | Billing portal |
| `#🏆LEADERBOARD🏆` | לידרבורד סופי (emoji channel) |
| `#🏆1st-WINNERS🏆` | הכרזת זוכים |

**הקמה אוטומטית:** `!setup_infrastructure` (ADMIN)

**אימות הרשאות:** `python server/scripts/verify_game_permissions.py`

### 7.4 סיווג Market Cap

| קטגוריה | סף (USD) | Channel pre-vote |
|---------|-----------|------------------|
| Small Cap | < $2B | `#small-cap-ticker` |
| Mid Cap | $2B – $10B | `#mid-cap-ticker` |
| Large Cap | ≥ $10B | `#large-cap-ticker` |

מקור: Finnhub (ראשי) → Yahoo (fallback). רק **NASDAQ/NYSE**.

---

## 8. Supabase — מסד נתונים וסכמה

### 8.1 חיבור

הבוט משתמש ב-**Supabase REST API** (לא ORM) עם `SUPABASE_SERVICE_ROLE_KEY`.  
ב-startup: `init_db()` מוודא גישה ל-`game_cycles` ו-`completed_games`.

### 8.2 טבלאות

#### `users`

| עמודה | סוג | תיאור |
|--------|-----|--------|
| `discord_id` | BIGINT PK | מזהה Discord |
| `username` | TEXT | שם משתמש |
| `full_name` | TEXT | מ-Stripe checkout |
| `email` | TEXT | מ-Stripe |
| `phone` | TEXT | מ-Stripe |
| `marketing_consent` | BOOLEAN | opt-in |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

#### `subscriptions`

| עמודה | סוג | תיאור |
|--------|-----|--------|
| `discord_id` | BIGINT PK | |
| `status` | TEXT | `active`, `trialing`, `active_until_period_end`, `canceled`, `payment_failed`, … |
| `payment_status` | TEXT | |
| `stripe_customer_id` | TEXT | מיפוי Stripe ↔ Discord |
| `stripe_subscription_id` | TEXT | |
| `current_period_end` | TIMESTAMPTZ | |
| `canceled_at` | TIMESTAMPTZ | |
| `last_event_type` | TEXT | |
| `last_event_id` | TEXT | |
| `updated_at` | TIMESTAMPTZ | |

#### `stripe_events` (idempotency)

| עמודה | סוג | תיאור |
|--------|-----|--------|
| `id` | TEXT PK | Stripe event id |
| `type` | TEXT | סוג אירוע |
| `payload` | JSONB | |
| `processed` | BOOLEAN | |
| `discord_id` | BIGINT | |
| `status` | TEXT | |
| `error` | TEXT | |
| `received_at` | TIMESTAMPTZ | |
| `processed_at` | TIMESTAMPTZ | |

#### `ticker_picks`

| עמודה | סוג | תיאור |
|--------|-----|--------|
| `id` | BIGSERIAL PK | |
| `guild_id` | BIGINT | |
| `week_key` | TEXT | e.g. `2026-W22` |
| `category` | TEXT | `small` / `mid` / `blue` |
| `ticker` | TEXT | |
| `market_cap` | BIGINT | |
| `exchange` | TEXT | |
| `submitted_by` | BIGINT | Discord user id |
| `submitted_at` | TIMESTAMPTZ | |

**Unique constraints:** `(guild_id, week_key, category, ticker)`, `(guild_id, week_key, category, submitted_by)`

#### `votes`

| עמודה | סוג | תיאור |
|--------|-----|--------|
| `id` | BIGSERIAL PK | |
| `guild_id` | BIGINT | |
| `week_key` | TEXT | |
| `category` | TEXT | |
| `ticker` | TEXT | |
| `user_id` | BIGINT | |
| `role_at_vote` | TEXT | `NPC` / `PLAYER` / `WINNER` |
| `is_early` | BOOLEAN | בתוך חלון 24h |
| `created_at` | TIMESTAMPTZ | |

#### `game_cycles`

| עמודה | סוג | תיאור |
|--------|-----|--------|
| `guild_id` | BIGINT | |
| `week_key` | TEXT | |
| `status` | TEXT | `ticker_selection` / `voting` / `closed` |
| `ticker_selection_open` | BOOLEAN | |
| `voting_open` | BOOLEAN | |
| `early_window_open` | BOOLEAN | |
| `monday_open_at` | TIMESTAMPTZ | |
| `early_window_end_at` | TIMESTAMPTZ | |
| `friday_close_at` | TIMESTAMPTZ | |
| `started_at` | TIMESTAMPTZ | |

#### `winners`

| עמודה | סוג | תיאור |
|--------|-----|--------|
| `id` | BIGSERIAL PK | |
| `guild_id` | BIGINT | |
| `week_key` | TEXT | |
| `user_id` | BIGINT | |
| `awarded_at` | TIMESTAMPTZ | |
| `expires_at` | TIMESTAMPTZ | +7 ימים |
| `removed_at` | TIMESTAMPTZ | כשה-role הוסר |

#### `audit_logs`

| עמודה | סוג | תיאור |
|--------|-----|--------|
| `id` | BIGSERIAL PK | |
| `guild_id` | BIGINT | nullable |
| `event_type` | TEXT | e.g. `stripe_webhook`, `ticker_pick`, `start_pre_vote` |
| `details` | JSONB | |
| `created_at` | TIMESTAMPTZ | |

#### `message_state`

| עמודה | סוג | תיאור |
|--------|-----|--------|
| `guild_id` | BIGINT | |
| `key` | TEXT | מזהה הודעה (leaderboard, picker, …) |
| `channel_id` | BIGINT | |
| `message_id` | BIGINT | |
| `payload` | JSONB | |
| `updated_at` | TIMESTAMPTZ | |

#### `completed_games`

| עמודה | סוג | תיאור |
|--------|-----|--------|
| `id` | BIGSERIAL PK | |
| `guild_id` | BIGINT | |
| `week_key` | TEXT | UNIQUE per guild |
| `closed_at` | TIMESTAMPTZ | |
| `winner_ids` | JSONB | |
| `winning_stocks` | JSONB | |
| `vote_totals` | JSONB | |
| `winners` | JSONB | usernames |
| `created_at` | TIMESTAMPTZ | |

SQL ליצירה: `server/scripts/supabase_completed_games.sql`

### 8.3 week_key

פורמט ISO: `YYYY-Www` (e.g. `2026-W22`).  
Pre-vote בסופ"ש שייך ל-**שבוע הבא** (`ticker_selection_week_key_for()`).

---

## 9. Stripe — מנויים, Webhooks, PLAYER role

→ **מסמך מפורט:** [`STRIPE_SUBSCRIPTIONS.md`](STRIPE_SUBSCRIPTIONS.md)

**תמצית:**

1. משתמש לוחץ Subscribe → Checkout Session עם `metadata.discord_id`
2. Webhook → `POST /stripe/webhook` → idempotency ב-`stripe_events`
3. `subscriptions` מתעדכן → `PLAYER` role ניתן/מוסר
4. DM + email (אם מוגדר)

**בדיקות:**

```bash
python server/scripts/test_stripe_flow.py      # offline
python server/scripts/check_stripe_live.py     # live signature + DB
```

---

## 10. נתוני שוק — Finnhub / Yahoo

| מקור | שימוש |
|------|--------|
| **Finnhub** | ראשי — profile, market cap, exchange |
| **Yahoo** | Fallback + חיפוש autocomplete |

**כללי אימות טיקר (pre-vote):**

- התאמה **exact** לסימבול (לא auto-pick לפי prefix)
- רק NASDAQ/NYSE (`exchange_ok`)
- Market cap חייב להתאים לקטגוריית הערוץ
- Dual-class: `GOOG`, `BRK.B` נתמכים

**בדיקה:**

```bash
python server/scripts/test_ticker_resolution.py
python server/scripts/test_pre_vote_selection.py --regression-only
```

**מגבלות ידועות:** ADRs (TSM, NVO) ו-ETFs (SPY) עלולים להידחות — מגבלת Finnhub free tier.

---

## 11. מחזור שבועי, טיימרים ו-Automation

מימוש: `server/cogs/scheduler.py` — לולאת asyncio פנימית (לא cron חיצוני).

### לוח זמנים (America/New_York)

| אירוע | יום | שעה ET | פעולה |
|--------|-----|--------|--------|
| **Monday Open** | שני | 09:00 | סגירת pre-vote, פתיחת הצבעות, Early window 24h, כפתורי vote |
| **Early Close** | שלישי | 09:00 | סגירת חלון Early (הצבעות ממשיכות) |
| **Friday Close** | שישי | 16:00 | סגירת הצבעות, לידרבורד, WINNER, איפוס, pre-vote חדש |

### Early Window (24 שעות)

- נפתח ב-Monday Open
- הצבעות עם `is_early=true` נספרות לזכאות WINNER
- Discord מציג countdown: `<t:unix:F>` / `<t:unix:R>`
- נשמר ב-DB: `game_cycles.monday_open_at`, `votes.is_early`

### Restart-safe

אם הבוט עולה מחדש בתוך `[Mon 09:00, Tue 09:00) ET`:
- **לא** מריץ Monday Open שוב אם `voting_open=true` כבר ב-DB
- **כן** משחזר early window timer מ-`monday_open_at`

### פעולות CRM (ידני — גיבוי)

| Action | API | תיאור |
|--------|-----|--------|
| `start_pre_vote` | `POST /api/game/actions/start_pre_vote` | סיום שבוע + פתיחת pre-vote |
| `start_vote` | `POST /api/game/actions/start_vote` | Monday open |
| `close_early` | `POST /api/game/actions/close_early` | סגירת early window |
| `end_competition` | `POST /api/game/actions/end_competition` | Friday close |

> דורש bot online + `X-Admin-Key`.

---

## 12. CRM — API ולוח הבקרה

### Endpoints

| Method | Path | Auth | תיאור |
|--------|------|------|--------|
| GET | `/api/health` | — | Health check |
| GET | `/api/meta` | Admin | קטגוריות, limits |
| GET | `/api/game/status` | Admin | week, phase, counts, bot status |
| GET | `/api/game/tickers` | Admin | טיקרים נוכחיים |
| GET | `/api/game/votes` | Admin | ספירת הצבעות |
| GET | `/api/game/leaderboards` | Admin | לידרבורד + quotes |
| GET | `/api/game/history` | Admin | שבועות שהסתיימו |
| GET | `/api/game/audit` | Admin | audit logs |
| GET | `/api/subscriptions` | Admin | Stripe subscriptions |
| POST | `/api/game/actions/{action}` | Admin | פעולות scheduler |
| POST | `/stripe/webhook` | Stripe signature | Billing webhooks |

**Auth:** Header `X-Admin-Key: <CRM_ADMIN_API_KEY>`  
אם `CRM_ADMIN_API_KEY` לא מוגדר — ה-API **פתוח** (dev only!).

### CRM Panels

| Tab | תוכן |
|-----|--------|
| Dashboard | week, phase, ticker counts, bot connected |
| Tickers | picks per category + live cap reconcile |
| Votes | leaderboards |
| Actions | start/end phases |
| Audit | `audit_logs` |
| Billing | subscriptions table |

---

## 13. גיבוי, שחזור ו-Disaster Recovery

### 13.1 Supabase — גיבוי אוטומטי

1. Supabase Dashboard → **Project Settings → Database → Backups**
2. Pro plan: Point-in-Time Recovery (PITR)
3. Free tier: ייצוא ידני תקופתי

### 13.2 ייצוא ידני (pg_dump / Dashboard)

```bash
# דרך Supabase CLI (אם מותקן)
supabase db dump -f backup.sql
```

או: Dashboard → **Table Editor** → Export CSV per table.

### 13.3 טבלאות קריטיות לשחזור

| עדיפות | טבלאות |
|--------|---------|
| גבוהה | `votes`, `ticker_picks`, `game_cycles`, `subscriptions`, `winners` |
| בינונית | `users`, `stripe_events`, `audit_logs`, `completed_games` |
| נמוכה | `message_state` (נבנה מחדש) |

### 13.4 שחזור אחרי אובדן DB

1. שחזר backup ב-Supabase
2. ודא env vars (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`)
3. Restart bot: `python run.py`
4. הבוט משחזר state אוטומטית (ראה סעיף 15)
5. אמת: `!sched_status`, `!weekly_status`, לחץ על כפתור vote

### 13.5 שחזור Discord messages

הודעות Discord **לא** ב-DB. אחרי restart:
- Persistent views נרשמים מחדש (`OpenPickerView`, vote buttons, subscribe panels)
- Leaderboards מתעדכנים בהצבעה הבאה או דרך admin commands

---

## 14. אבטחה, Secrets ו-RLS

### 14.1 Secrets — כללים

| Secret | איפה | אסור |
|--------|------|------|
| `DISCORD_TOKEN` | Railway env only | Git, CRM frontend |
| `SUPABASE_SERVICE_ROLE_KEY` | Server only | Browser, CRM |
| `STRIPE_SECRET_KEY` | Server only | Git |
| `STRIPE_WEBHOOK_SECRET` | Server only | Git |
| `CRM_ADMIN_API_KEY` | Server + CRM (VITE_) | Public repos |

> `.env` ב-`.gitignore` — **לעולם לא** commit secrets.

### 14.2 API Security

- Production: **חובה** `CRM_ADMIN_API_KEY`
- CORS: הגדר `CRM_CORS_ORIGINS` — אל תשאיר `CRM_CORS_ALLOW_ALL=true` בייצור
- Stripe: אימות חתימה `Stripe-Signature` על כל webhook

### 14.3 Supabase RLS

- הבוט משתמש ב-**service role** (עוקף RLS) — server-side only
- `completed_games`: RLS enabled (ראה SQL script)
- **אין** anon key ב-frontend — CRM עובר דרך FastAPI

### 14.4 Stripe Anti-Hijack

- Discord user נקבע **רק** מ-`metadata.discord_id` / `client_reference_id`
- customer קיים **לא** מועבר למשתמש Discord אחר
- קונפליקט → log ל-`#mod` + `audit_logs`

### 14.5 Discord Permissions

- הבוט לא צריך Administrator
- צריך: Manage Roles (מעל PLAYER/WINNER), Manage Messages, Send Messages

---

## 15. Restart / Crash Recovery

→ **מסמך מפורט:** [`RESTART_AND_STATE.md`](RESTART_AND_STATE.md)

**בדיקה offline:**

```bash
python server/scripts/test_restart_recovery.py
```

**אימות אחרי restart:**

1. לוג: `[bot] Logged in as ...`
2. לוג: `[weekly_picks] recovery: re-registered voting buttons ...`
3. לחיצה על כפתור vote — לא "This interaction failed"
4. `!sched_status` — זמני automation נכונים

---

## 16. פקודות Admin ב-Discord

| פקודה | Role | תיאור |
|--------|------|--------|
| `!setup_infrastructure` | ADMIN | יצירת channels/roles |
| `!sched_status` | ADMIN | זמני automation הבאים |
| `!sched_monday_open_now` | ADMIN | Monday open ידני |
| `!sched_friday_close_now` | ADMIN | Friday close ידני |
| `!weekly_status` | ADMIN | סטטוס ערוצי vote |
| `!early_status` | ADMIN | סטטוס early window |
| `!early_arm_now` | ADMIN | arm early window ידני |
| `!stripe_events` | ADMIN | אירועי Stripe אחרונים |
| `!resync_subscription @user` | ADMIN | סנכרון PLAYER role |
| `!subscription_status` | כולם | סטטוס מנוי שלי |
| `!post_subscribe_panel` | ADMIN | פרסום פאנל subscribe |
| `!admin_actions_panel` | ADMIN | פאנל CRM actions |
| `!ui_picker` | PLAYER/WINNER | picker בערוץ ticker |

---

## 17. סקריפטי בדיקה ו-QA

| סקריפט | מטרה |
|--------|------|
| `test_stripe_flow.py` | Stripe offline acceptance |
| `check_stripe_live.py` | Stripe signature + idempotency live |
| `test_ticker_resolution.py` | Finnhub/Yahoo resolver |
| `test_pre_vote_selection.py` | Pre-vote per category simulation |
| `test_winner_eligibility.py` | WINNER rules unit test |
| `test_restart_recovery.py` | Restart / early window / buttons |
| `verify_game_permissions.py` | Discord channel permissions |
| `check_discord_live.py` | Discord connectivity |
| `check_flow.py` | End-to-end flow smoke |
| `diagnose_user_channels.py` | Debug user channel access |

הרצה (מתוך repo root):

```bash
python server/scripts/<script_name>.py
```

---

## 18. פתרון תקלות (Troubleshooting)

### "This interaction failed" על כפתור

| סיבה | פתרון |
|------|--------|
| Bot restart | המתן ל-`on_ready` + recovery logs |
| View לא registered | restart bot |
| Voting closed | בדוק `game_cycles.voting_open` |
| Missing permissions | `verify_game_permissions.py` |

### טיקר תקין נדחה

| סיבה | הודעה | פתרון |
|------|--------|--------|
| קטגוריה שגויה | wrong category | שלח לערוץ הנכון |
| לא US exchange | bad exchange | רק NASDAQ/NYSE |
| לא קיים | not found | בדוק איות |
| Market cap חסר | no market cap | retry / Finnhub |

### שילם Stripe — אין PLAYER

1. `!stripe_events` — האם האירוע processed?
2. Bot role מעל PLAYER?
3. `!resync_subscription @user`
4. בדוק `#mod` לשגיאות hierarchy

### CRM לא טוען

1. `GET /api/health` — API up?
2. `VITE_API_URL` נכון?
3. `VITE_ADMIN_API_KEY` = `CRM_ADMIN_API_KEY`?
4. CORS: `CRM_CORS_ORIGINS` כולל את domain של Vercel

### Scheduler לא רץ

1. Bot online 24/7?
2. `!sched_status` — next fire times
3. לוגים: `[scheduler] Next automation at (UTC): ...`
4. Railway restart policy: ON_FAILURE

### Railway build fails

- Monorepo: ודא `Dockerfile` בשורש קיים
- או: Root Directory = `server`

---

## 19. רשימת מסירה (Handover Checklist)

מסמך זה + הפריטים הבאים = מסירה מלאה לפי נספח ג':

- [ ] **Repository access** — buyer as owner/collaborator on GitHub
- [ ] **`.env.example`** — מעודכן (קיים בשורש)
- [ ] **תיעוד זה** — `server/docs/APPENDIX_C.md`
- [ ] **Stripe doc** — `server/docs/STRIPE_SUBSCRIPTIONS.md`
- [ ] **Restart doc** — `server/docs/RESTART_AND_STATE.md`
- [ ] **Discord** — bot application transferred / buyer has admin
- [ ] **Supabase** — project ownership / service role key
- [ ] **Stripe** — account access / webhook configured
- [ ] **Finnhub** — API key
- [ ] **Railway** — project access / env vars documented
- [ ] **Vercel** — CRM project access (if used)
- [ ] **QA scripts** — all pass (section 17)

---

## נספחים — קישורים

| מסמך | נושא |
|------|------|
| [`STRIPE_SUBSCRIPTIONS.md`](STRIPE_SUBSCRIPTIONS.md) | Billing, webhooks, PLAYER, tests |
| [`RESTART_AND_STATE.md`](RESTART_AND_STATE.md) | Persistence, recovery |
| [`../../README.md`](../../README.md) | Quick start |
| [`../../.env.example`](../../.env.example) | Environment template |

---

*סוף נספח ג' — תיעוד טכני מלא*
