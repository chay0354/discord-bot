-- Run in Supabase SQL editor (discord project) if not applied via migration.
CREATE TABLE IF NOT EXISTS public.completed_games (
  id BIGSERIAL PRIMARY KEY,
  guild_id BIGINT NOT NULL,
  week_key TEXT NOT NULL,
  closed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  winner_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  winning_stocks JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT completed_games_guild_week_unique UNIQUE (guild_id, week_key)
);

CREATE INDEX IF NOT EXISTS completed_games_guild_closed_idx
  ON public.completed_games (guild_id, closed_at DESC);

ALTER TABLE public.completed_games ENABLE ROW LEVEL SECURITY;
