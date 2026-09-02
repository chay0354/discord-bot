-- Part E: allow PLAYER/WINNER to insert multiple vote rows for the same ticker.
-- Drops unique constraints on public.votes (primary key is kept).
-- Safe to re-run.

DO $$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT c.conname
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public'
      AND t.relname = 'votes'
      AND c.contype = 'u'
  LOOP
    EXECUTE format('ALTER TABLE public.votes DROP CONSTRAINT IF EXISTS %I', r.conname);
  END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS votes_guild_week_cat_user_idx
  ON public.votes (guild_id, week_key, category, user_id);
