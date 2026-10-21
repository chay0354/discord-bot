-- Spend website-purchased votes (site.wallets) on Discord ballots.
-- One transaction debits the wallet, writes the site ledger/operation and inserts
-- the public.votes rows, so a vote can never be counted without being paid (or
-- paid without being counted). The operation id makes retries idempotent.

create table if not exists public.site_vote_spends (
    id text primary key,
    guild_id bigint not null,
    week_key text not null,
    category text not null check (category in ('small', 'mid', 'blue')),
    ticker text not null,
    user_id bigint not null,
    site_user_id bigint not null,
    amount integer not null check (amount between 1 and 100),
    vote_ids bigint[] not null,
    created_at timestamptz not null default now()
);
alter table public.site_vote_spends enable row level security;
create index if not exists site_vote_spends_week_idx
    on public.site_vote_spends (guild_id, week_key, user_id);

create or replace function public.site_vote_balance(p_discord_id bigint)
returns json
language sql
stable
security definer
set search_path = ''
as $$
    select json_build_object(
        'linked', u.id is not null,
        'account_ready', coalesce(u.disabled = 0 and u.deleted_at is null
            and u.delete_after is null and u.billing_review = 0, false),
        'available', coalesce(w.balance, 0)
    )
    from (select 1) one
    left join site.users u on u.discord_id = p_discord_id::text
    left join site.wallets w on w.user_id = u.id
$$;

create or replace function public.spend_site_votes(
    p_op_id text,
    p_guild_id bigint,
    p_week_key text,
    p_category text,
    p_ticker text,
    p_discord_id bigint,
    p_role text,
    p_amount integer default 1
)
returns json
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now bigint := extract(epoch from now())::bigint;
    v_prev public.site_vote_spends;
    v_user site.users;
    v_lock text;
    v_got text;
    v_balance bigint;
    v_ids bigint[];
    v_ticker text := upper(btrim(p_ticker));
begin
    if p_op_id !~ '^[A-Za-z0-9_-]{8,100}$' or p_amount is null or p_amount < 1 or p_amount > 100
       or p_category not in ('small', 'mid', 'blue') or v_ticker !~ '^[A-Z0-9.\-]{1,16}$' then
        raise exception 'invalid site vote spend';
    end if;

    select * into v_prev from public.site_vote_spends where id = p_op_id;
    if found then
        return json_build_object('ok', true, 'duplicate', true,
            'available', (select balance from site.wallets where user_id = v_prev.site_user_id));
    end if;

    -- FOR SHARE blocks a concurrent close of this cycle until we commit.
    perform 1 from public.game_cycles
     where guild_id = p_guild_id and week_key = p_week_key and voting_open
       for share;
    if not found then
        return json_build_object('ok', false, 'reason', 'closed');
    end if;

    select * into v_user from site.users where discord_id = p_discord_id::text;
    if not found then
        return json_build_object('ok', false, 'reason', 'not_linked');
    end if;
    if v_user.disabled <> 0 or v_user.deleted_at is not null
       or v_user.delete_after is not null or v_user.billing_review <> 0 then
        return json_build_object('ok', false, 'reason', 'account_blocked');
    end if;

    -- Same lock row the website's DB::lock('wallet:<id>') uses.
    v_lock := encode(extensions.digest('wallet:' || v_user.id, 'sha256'), 'hex');
    insert into site.locks (name, expires_at) values (v_lock, v_now + 30)
    on conflict (name) do update set expires_at = excluded.expires_at
        where site.locks.expires_at < v_now
    returning name into v_got;
    if v_got is null then
        return json_build_object('ok', false, 'reason', 'busy');
    end if;

    update site.wallets set balance = balance - p_amount
     where user_id = v_user.id and balance >= p_amount
    returning balance into v_balance;
    if not found then
        delete from site.locks where name = v_lock;
        return json_build_object('ok', false, 'reason', 'insufficient',
            'available', coalesce((select balance from site.wallets where user_id = v_user.id), 0));
    end if;

    insert into site.vote_ledger (user_id, delta, reason, unique_key, created_at)
    values (v_user.id, -p_amount, left('Used in Discord on $' || v_ticker, 100), 'discord:' || p_op_id, v_now);

    insert into site.vote_operations
        (id, user_id, amount, category, ticker, week_id, state, payload_hash, created_at, updated_at)
    values (p_op_id, v_user.id, p_amount,
            case p_category when 'blue' then 'large' else p_category end,
            v_ticker, p_week_key, 'committed',
            encode(extensions.digest(concat_ws('|', p_discord_id, p_amount, p_category, v_ticker, p_week_key), 'sha256'), 'hex'),
            v_now, v_now);

    insert into public.users (discord_id) values (p_discord_id) on conflict (discord_id) do nothing;

    with ins as (
        insert into public.votes (guild_id, week_key, category, ticker, user_id, role_at_vote, is_early)
        select p_guild_id, p_week_key, p_category, v_ticker, p_discord_id, coalesce(nullif(p_role, ''), 'PLAYER'), false
          from generate_series(1, p_amount)
        returning id
    )
    select array_agg(id) into v_ids from ins;

    insert into public.site_vote_spends
        (id, guild_id, week_key, category, ticker, user_id, site_user_id, amount, vote_ids)
    values (p_op_id, p_guild_id, p_week_key, p_category, v_ticker, p_discord_id, v_user.id, p_amount, v_ids);

    insert into site.settings (name, value) values ('bridge_heartbeat', v_now::text)
    on conflict (name) do update set value = excluded.value;

    delete from site.locks where name = v_lock;
    return json_build_object('ok', true, 'duplicate', false, 'available', v_balance);
end
$$;

revoke all on function public.site_vote_balance(bigint) from public, anon, authenticated;
revoke all on function public.spend_site_votes(text, bigint, text, text, text, bigint, text, integer) from public, anon, authenticated;
grant execute on function public.site_vote_balance(bigint) to service_role;
grant execute on function public.spend_site_votes(text, bigint, text, text, text, bigint, text, integer) to service_role;
