# Restart, Crash Recovery & State Persistence

This document explains how the bot survives a restart or server crash without
losing votes, roles, weekly state, or interactive buttons. It addresses report
item **#11 (Restart / server crash / state persistence)**.

## TL;DR

All durable state lives in **Supabase (Postgres)**, not in the bot's memory. On
startup the bot reconnects, reloads that state, and re-registers its interactive
components. A restart at any point in the week is safe and idempotent.

## What is persisted (source of truth: Supabase)

| State | Table | Survives restart |
|-------|-------|------------------|
| Votes (who, ticker, category, role-at-vote, early-flag) | `votes` | ✅ |
| Weekend ticker picks | `ticker_picks` | ✅ |
| Weekly phase (ticker-selection / voting / closed, early-window times) | `game_cycles` | ✅ |
| Subscriptions & PLAYER status | `subscriptions` | ✅ |
| WINNER grants + validity window | `winners` | ✅ |
| Completed games / winners history | `completed_games` | ✅ |
| Stripe webhook idempotency | `stripe_events` | ✅ |
| Audit log (opens/closes, picks, winners, billing, errors) | `audit_logs` | ✅ |

Nothing required to resume the game is stored only in memory. In-memory caches
(vote counts, early-window timer, leaderboard message ids) are **derived** and
rebuilt on startup.

## What happens on startup (automatic recovery)

1. **Reconnect & login** — `run.py` starts the bot and the admin API together.
2. **DB check** — `init_db()` verifies Supabase connectivity before login.
3. **Persistent views re-registered** (so buttons keep working — no
   "This interaction failed"):
   - `OpenPickerView` (CHOOSE YOUR TICKER) — `submission_ui.setup`
   - `PlayerSubscribeOnlyView` / `PlayerManageSubscriptionView` — `billing`
   - `AdminActionsView` — `admin_actions`
   - **WEEKLY PICKS voting buttons** — rebuilt per category from the stored
     ballot in `weekly_picks.on_ready` and re-registered with matching
     `custom_id`s (`vote:{category}:{ticker}`).
4. **Live vote counts rehydrated** from `votes` (`hydrate_vote_state`) so the
   leaderboards keep summing correctly.
5. **24h early-vote window re-armed** from `game_cycles.monday_open_at` (only if
   the window has not already elapsed). The authoritative early-vote flag is also
   stored per-vote (`votes.is_early`), so winner eligibility is correct even if
   the timer were lost.
6. **Expired WINNER roles removed** (`scheduler.on_ready`) in case the expiry
   moment passed while the bot was down.
7. **Scheduler bootstrap** — if the bot starts inside the Monday 09:00–Tue 09:00
   ET window, it checks the DB first: if voting is **already open** for the week
   it only re-arms the in-memory timer; it does **not** re-run Monday-open
   (which would wipe the active voting channels). It runs Monday-open only if it
   genuinely has not happened yet.

## Crash safety guarantees

- **Votes are never double-counted.** Re-hydration reads the DB; the in-memory
  cache is replaced, not appended.
- **Stripe webhooks are idempotent.** A retry after a crash is a no-op
  (`stripe_events` unique id). A transient failure is logged to `audit_logs`
  and `#mod`, and Stripe retries safely.
- **Logging never breaks a flow.** `database.log_event` swallows and prints
  errors instead of raising.
- **A restart mid-vote keeps the buttons alive.** Voting and picker buttons are
  registered as persistent views on every boot.

## Operating instructions (server crash / restart)

### Normal restart
```bash
# from the server/ directory
python run.py
```
The bot recovers all state automatically (see steps above). No manual action is
required.

### Hosting / process manager
On Railway (or any host) the process restart policy should be **on-failure /
always**. The single entry point is `python run.py`, which runs both the Discord
bot and the admin API. Required environment variables are listed in
`.env.example`.

### Verifying recovery after a restart
1. Check the logs for:
   - `[bot] Logged in as ...`
   - `[weekly_picks] recovery: re-registered voting buttons for N category(ies)`
   - `[weekly_picks] recovery: re-armed early window ...` (only during the 24h window)
2. In Discord, click a vote button in a WEEKLY PICKS channel — it should record
   a vote, not show "This interaction failed".
3. Run `!sched_status` (ADMIN) to confirm the next automation times.
4. Run `!weekly_status` (ADMIN, in `#mod`) to confirm channel layout.

### If state looks wrong after a restart
- **Voting buttons dead:** ensure the bot has `Manage Messages` and that the
  `game_cycles` row for the current week has `voting_open = true`. Re-run
  `!sched_monday_open_now` (ADMIN) only if voting was supposed to be open but
  the channels are empty — note this reposts the voting banners.
- **Leaderboards not updating:** they rehydrate on the next vote; or re-open via
  the admin panel.
- **Early window timer wrong:** it is derived from `game_cycles.monday_open_at`;
  confirm that timestamp is correct for the week.

## Offline test

```bash
python server/scripts/test_restart_recovery.py
```
Verifies (without Discord) that the early-vote window restores correctly, an
elapsed window is treated as inactive, and that recovered voting buttons share
the exact `custom_id`s of the original message so clicks route correctly.
