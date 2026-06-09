-- Section 12 persistence: winner audit fields (run once in Supabase SQL editor).
ALTER TABLE winners ADD COLUMN IF NOT EXISTS reason TEXT;
ALTER TABLE winners ADD COLUMN IF NOT EXISTS winning_tickers JSONB;
