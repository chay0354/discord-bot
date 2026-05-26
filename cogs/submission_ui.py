# cogs/submission_ui.py

import asyncio
from typing import List, Tuple, Dict

import discord
from discord.ext import commands

import database
from config import (
    CATEGORY_TITLES,
    CHANNEL_BLUE_TICKER,
    CHANNEL_BLUE_VOTE,
    CHANNEL_MID_TICKER,
    CHANNEL_MID_VOTE,
    CHANNEL_MOD,
    CHANNEL_PICK_RESULTS,
    CHANNEL_SMALL_TICKER,
    CHANNEL_SMALL_VOTE,
    ROLE_ADMIN,
    ROLE_PLAYER,
    ROLE_WINNER,
    TICKER_LIMIT_PER_CATEGORY,
)
from services.finnhub_client import validate_symbol_for_category as finnhub_validate_symbol
from services.yahoo_client import (
    category_for_channel,
    search_symbols_by_query,
    validate_symbol_for_category,
)
from services.example_stocks import (
    EXAMPLE_STOCKS,
    EXAMPLES_PAGE_SIZE,
    example_stock_lines_slice,
)

# ===== optional shared_state import (safe) =====
try:
    # may expose: has_user_pick/set_user_pick OR user_has_pick/record_user_pick
    import shared_state
except Exception:  # pragma: no cover
    shared_state = None  # type: ignore


def _search_first_options() -> List[discord.SelectOption]:
    return [
        discord.SelectOption(
            label="Search first",
            value="__search_first__",
            description="Click the Search button and type a ticker symbol.",
        )
    ]


def _option_description(row: dict, *, fallback: str = "NASDAQ/NYSE") -> str:
    name = (row.get("shortName") or row.get("exchange") or fallback) if isinstance(row, dict) else fallback
    return str(name)[:90]


def search_matches(ch: discord.TextChannel, query: str) -> List[discord.SelectOption]:
    """Build up to 25 select options: prefix match on examples + Yahoo search, cap-sorted."""
    q = query.upper().strip().lstrip("$")
    category = category_for_channel(ch.name)
    example_symbols = EXAMPLE_STOCKS.get(category, [])[:80]
    rows = search_symbols_by_query(q, category=category, limit=25) if q else []

    ranked: list[tuple[int, str, dict | None]] = []
    seen: set[str] = set()

    def add(symbol: str, row: dict | None, *, rank: int) -> None:
        sym = symbol.upper().strip()
        if not sym or sym in seen:
            return
        seen.add(sym)
        ranked.append((rank, sym, row))

    for symbol in example_symbols:
        if q:
            if symbol == q:
                add(symbol, None, rank=0)
            elif symbol.startswith(q):
                add(symbol, None, rank=1)
        else:
            add(symbol, None, rank=2)

    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        if not symbol:
            continue
        if symbol == q:
            add(symbol, row, rank=0)
        elif q and symbol.startswith(q):
            add(symbol, row, rank=1)
        else:
            add(symbol, row, rank=3)

    ranked.sort(key=lambda item: (item[0], item[1]))
    options: List[discord.SelectOption] = []
    for _, symbol, row in ranked[:25]:
        desc = _option_description(row or {}, fallback="Example / search match")
        options.append(
            discord.SelectOption(
                label=f"${symbol}",
                value=symbol,
                description=desc,
            )
        )
    return options


def example_select_options(category: str, start: int, end: int) -> List[discord.SelectOption]:
    options: List[discord.SelectOption] = []
    for idx, symbol in enumerate(EXAMPLE_STOCKS.get(category, [])[start:end], start=start + 1):
        options.append(
            discord.SelectOption(
                label=f"{idx}. ${symbol}",
                value=symbol,
                description="Example stock from this category.",
            )
        )
    return options


# ---------------- State (demo in-memory) ----------------
# Who already submitted: (channel_id, user_id)
_picks_done: set[Tuple[int, int]] = set()
# Who has an open picker right now: (channel_id, user_id) -> [message_ids]
_picker_open: Dict[Tuple[int, int], List[int]] = {}
# guild_id -> pick-results message id (avoids scanning channel history each submit)
_pick_results_msg_id: Dict[int, int] = {}


def reset_picker_runtime_state() -> None:
    _picks_done.clear()
    _picker_open.clear()


# ----------------- PICK-RESULTS helpers -----------------

def _category_index_for_channel(ch: discord.TextChannel) -> int:
    n = ch.name.lower()
    if "small-cap" in n:
        return 0
    if "mid-cap" in n:
        return 1
    return 2  # blue


def _category_title_for_idx(idx: int) -> str:
    return [CATEGORY_TITLES["small"], CATEGORY_TITLES["mid"], CATEGORY_TITLES["blue"]][idx]


def _category_key_for_idx(idx: int) -> str:
    return ["small", "mid", "blue"][idx]


def _can_choose_weekly_ticker(member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    names = {r.name.upper() for r in member.roles}
    admin_aliases = {ROLE_ADMIN.upper(), "BOT ADMIN", "BOT-ADMIN", "BOT_ADMIN"}
    return bool(
        ROLE_PLAYER.upper() in names
        or ROLE_WINNER.upper() in names
        or names.intersection(admin_aliases)
        or any("ADMIN" in name for name in names)
    )


async def _get_pick_results_channel(guild: discord.Guild) -> discord.TextChannel | None:
    for ch in guild.text_channels:
        if ch.name.lower() in {CHANNEL_PICK_RESULTS.lower(), "pick-results"}:
            return ch
    return None


async def _find_pick_results_message(pr_ch: discord.TextChannel) -> tuple[discord.Message, discord.Embed] | None:
    async for msg in pr_ch.history(limit=50):
        if msg.author == pr_ch.guild.me and msg.embeds:
            emb = msg.embeds[0]
            title = (emb.title or "").lower()
            if "pick results" in title:
                return msg, emb
    return None


def _pick_results_embed_scaffold() -> discord.Embed:
    emb = discord.Embed(
        title="PICK RESULTS",
        description=f"Small / Mid / Blue weekly lists. Each category closes at {TICKER_LIMIT_PER_CATEGORY} tickers.",
        color=discord.Color.gold(),
    )
    emb.add_field(name=f"{CATEGORY_TITLES['small']} (0/{TICKER_LIMIT_PER_CATEGORY})", value="—", inline=False)
    emb.add_field(name=f"{CATEGORY_TITLES['mid']} (0/{TICKER_LIMIT_PER_CATEGORY})", value="—", inline=False)
    emb.add_field(name=f"{CATEGORY_TITLES['blue']} (0/{TICKER_LIMIT_PER_CATEGORY})", value="—", inline=False)
    return emb


async def _ensure_pick_results_message(guild: discord.Guild) -> tuple[discord.Message, discord.Embed] | None:
    pr_ch = await _get_pick_results_channel(guild)
    if pr_ch is None:
        return None
    cached_id = _pick_results_msg_id.get(guild.id)
    if cached_id:
        try:
            msg = await pr_ch.fetch_message(cached_id)
            if msg.embeds:
                return msg, msg.embeds[0]
        except Exception:
            _pick_results_msg_id.pop(guild.id, None)
    found = await _find_pick_results_message(pr_ch)
    if found:
        _pick_results_msg_id[guild.id] = found[0].id
        return found
    msg = await pr_ch.send(embed=_pick_results_embed_scaffold())
    _pick_results_msg_id[guild.id] = msg.id
    return msg, msg.embeds[0]


def _parse_field_lines(val: str | None) -> list[str]:
    if not val or val.strip() == "—":
        return []
    lines = []
    for ln in val.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        # expected format: "1) $TICK"
        if ") $" in ln:
            try:
                after = ln.split(") $", 1)[1]
                lines.append(after.strip().upper())
                continue
            except Exception:
                pass
        # fallback: take last word
        parts = ln.split()
        lines.append(parts[-1].lstrip("$").upper())
    return lines


def _render_field_lines(tickers: list[str]) -> str:
    if not tickers:
        return "—"
    out = []
    for i, t in enumerate(tickers, start=1):
        out.append(f"{i}) ${t}")
    return "\n".join(out)


async def _try_add_ticker_to_pick_results(
    guild: discord.Guild,
    category_idx: int,
    ticker: str,
    submitted_by: int | None = None,
    market_cap: int | None = None,
    exchange: str | None = None,
) -> tuple[bool, str, int | None]:
    """
    Returns (ok, reason, category_count). reason ∈ {'not_found', 'duplicate', 'full', 'ok', ...}
    """
    week_key = database.ticker_selection_week_key_for()
    category_key = _category_key_for_idx(category_idx)
    found = await _ensure_pick_results_message(guild)
    if not found:
        return False, "not_found", None
    msg, emb = found

    if submitted_by is not None:
        ok, reason = database.add_ticker_pick(
            guild.id,
            week_key,
            category_key,
            ticker,
            submitted_by,
            market_cap=market_cap,
            exchange=exchange,
        )
        if not ok:
            return False, reason, None
    # ensure 3 fields
    while len(emb.fields) < 3:
        emb.add_field(name="—", value="—", inline=False)

    field = emb.fields[category_idx]
    current = _parse_field_lines(field.value)
    t = ticker.upper()
    if submitted_by is None:
        if t in current:
            return False, "duplicate", len(current)
        if len(current) >= TICKER_LIMIT_PER_CATEGORY:
            return False, "full", len(current)
        current.append(t)
    elif t not in current:
        current.append(t)

    # update the field name with count
    base_name = _category_title_for_idx(category_idx)
    new_name = f"{base_name} ({len(current)}/{TICKER_LIMIT_PER_CATEGORY})"
    new_value = _render_field_lines(current)

    # rebuild embed (discord.py doesn't support editing a single field in place)
    new_emb = discord.Embed(
        title=emb.title or "PICK RESULTS",
        description=emb.description,
        color=emb.color
    )
    for i in range(3):
        if i == category_idx:
            new_emb.add_field(name=new_name, value=new_value, inline=False)
        else:
            if i < len(emb.fields):
                new_emb.add_field(
                    name=emb.fields[i].name, value=emb.fields[i].value, inline=False)
            else:
                new_emb.add_field(
                    name=_category_title_for_idx(i) + f" (0/{TICKER_LIMIT_PER_CATEGORY})",
                    value="—",
                    inline=False
                )

    await msg.edit(embed=new_emb)
    return True, "ok", len(current)


# --------- closed-channel helpers (read-only; no side effects) ---------

async def _is_channel_closed(ch: discord.TextChannel) -> bool:
    """A channel is closed once its category reaches the weekly ticker limit."""
    try:
        category_key = category_for_channel(ch.name)
        return database.count_tickers(ch.guild.id, database.ticker_selection_week_key_for(), category_key) >= TICKER_LIMIT_PER_CATEGORY
    except Exception:
        pass
    pr_ch = await _get_pick_results_channel(ch.guild)
    if pr_ch is None:
        return False
    found = await _find_pick_results_message(pr_ch)
    if not found:
        return False
    _, emb = found
    idx = _category_index_for_channel(ch)
    if idx >= len(emb.fields):
        return False
    count = len(_parse_field_lines(emb.fields[idx].value))
    return count >= TICKER_LIMIT_PER_CATEGORY


def _closed_banner_embed(guild: discord.Guild, count: int = TICKER_LIMIT_PER_CATEGORY) -> discord.Embed:
    pick_results_mention = f"#{CHANNEL_PICK_RESULTS}"
    pr_ch = None
    for ch in guild.text_channels:
        if ch.name.lower() in {CHANNEL_PICK_RESULTS.lower(), "pick-results"}:
            pr_ch = ch
            break
    if pr_ch:
        pick_results_mention = pr_ch.mention

    emb = discord.Embed(
        title=f"Submissions Closed ({count}/{TICKER_LIMIT_PER_CATEGORY})",
        description=(
            f"This ticker channel reached **{TICKER_LIMIT_PER_CATEGORY} unique submissions** and is **closed for this week**.\n\n"
            "• Try the other ticker channels.\n"
            "• Next opening: **Friday 4:00 PM ET** (after market close).\n"
            f"• See the current picks in {pick_results_mention}."
        ),
        color=discord.Color.red()
    )
    return emb


# ---------------- small helpers: #mod logging ----------------

def _find_text_channel(guild: discord.Guild, name: str) -> discord.TextChannel | None:
    for ch in guild.text_channels:
        if ch.name.lower() == name.lower():
            return ch
    return None


async def _post_mod_log_submission_closed(
    guild: discord.Guild,
    cat_idx: int,
    ch: discord.TextChannel,
    count: int,
    triggered_by: discord.Member | None,
):
    mod = _find_text_channel(guild, "mod")
    if not mod:
        return
    cat = _category_title_for_idx(cat_idx)
    who = f"{triggered_by.mention}" if triggered_by else "Unknown"
    emb = discord.Embed(
        title=f"Submissions Closed — Reached {TICKER_LIMIT_PER_CATEGORY}/{TICKER_LIMIT_PER_CATEGORY}",
        description=(
            f"Category: **{cat}**\n"
            f"Channel: {ch.mention}\n"
            f"Count: **{count}/{TICKER_LIMIT_PER_CATEGORY}**\n"
            f"Triggered by: {who}"
        ),
        color=discord.Color.red()
    )
    try:
        await mod.send(embed=emb)
    except Exception:
        pass


# ----------------- WEEKLY PICKS push helpers -----------------

def _weekly_channel_name_for_idx(idx: int) -> str:
    return [CHANNEL_SMALL_VOTE, CHANNEL_MID_VOTE, CHANNEL_BLUE_VOTE][idx]


def _category_title(idx: int) -> str:
    return [CATEGORY_TITLES["small"], CATEGORY_TITLES["mid"], CATEGORY_TITLES["blue"]][idx]


class WeeklyPickButtonsView(discord.ui.View):
    """Buttons only (no voting yet)."""

    def __init__(self, category_idx: int, tickers: List[str]):
        super().__init__(timeout=None)  # static message; no timeout
        # add up to 20 buttons (Discord max: 25; 5 rows * 5 per row)
        for t in tickers[:20]:
            # custom_id to avoid duplicates; callback just ACKs
            btn = discord.ui.Button(
                label=t,
                style=discord.ButtonStyle.primary,
                custom_id=f"wpick:{category_idx}:{t}"
            )

            async def _cb(interaction: discord.Interaction, ticker=t):
                try:
                    await interaction.response.defer(ephemeral=True)
                except discord.InteractionResponded:
                    pass
                await interaction.followup.send(
                    f"Voting will open soon. (Pressed ${ticker})",
                    ephemeral=True
                )
            btn.callback = _cb  # type: ignore
            self.add_item(btn)


# ----------------- UI Components -----------------

class TickerSelect(discord.ui.Select):
    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.InteractionResponded:
            pass


class ExampleTickerSelect(discord.ui.Select):
    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.InteractionResponded:
            pass


class TickerEntryModal(discord.ui.Modal, title="Try Ticker"):
    """Free-text ticker entry. We auto-complete to the best valid match for this cap category."""

    query: discord.ui.TextInput = discord.ui.TextInput(
        label="Type a ticker (with or without $)",
        placeholder="e.g. NVDA, $AAPL, TSLA",
        min_length=1,
        max_length=10,
        required=True,
    )

    def __init__(self, parent_view: "StockPickerView"):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.parent_view.frozen:
            await interaction.response.send_message(
                "You already submitted a ticker for this channel.",
                ephemeral=True,
            )
            return
        raw = str(self.query.value or "").strip()
        if not raw:
            await interaction.response.send_message(
                "Please type a ticker symbol.", ephemeral=True
            )
            return
        await self.parent_view.submit_ticker(interaction, raw)


class StockPickerView(discord.ui.View):
    """Single button → modal where the user types a ticker (auto-completed and validated)."""

    def __init__(self, channel: discord.TextChannel, user_id: int):
        super().__init__(timeout=300)
        self.channel = channel
        self.user_id = user_id
        self.message_id: int | None = None
        self.frozen: bool = False

        self.try_btn = discord.ui.Button(
            label="Try Ticker",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        self.try_btn.callback = self.on_try_ticker
        self.add_item(self.try_btn)

    async def on_try_ticker(self, interaction: discord.Interaction) -> None:
        if self.frozen:
            await interaction.response.send_message(
                "You already submitted a ticker for this channel.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(TickerEntryModal(self))

    async def submit_ticker(
        self,
        interaction: discord.Interaction,
        ticker: str,
        *,
        typed_hint: str | None = None,
    ) -> None:
        """Validate and save a chosen symbol (from dropdown or quick pick)."""
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except discord.InteractionResponded:
            pass

        if self.frozen:
            await interaction.followup.send(
                "You already submitted a ticker for this channel.",
                ephemeral=True,
            )
            return

        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.followup.send("This can only be used in a server.", ephemeral=True)
            return
        if not _can_choose_weekly_ticker(interaction.user):
            await interaction.followup.send(
                "Only PLAYER subscribers, active WINNERS, and admins can choose weekly tickers.",
                ephemeral=True,
            )
            return

        key = (self.channel.id, interaction.user.id)
        already_picked = key in _picks_done
        try:
            already_picked = already_picked or database.user_has_ticker_pick(
                self.channel.guild.id,
                database.ticker_selection_week_key_for(),
                category_for_channel(self.channel.name),
                interaction.user.id,
            )
        except Exception:
            pass
        try:
            if shared_state:
                if hasattr(shared_state, "has_user_pick"):
                    already_picked = already_picked or shared_state.has_user_pick(
                        self.channel.id, interaction.user.id
                    )
                elif hasattr(shared_state, "user_has_pick"):
                    already_picked = already_picked or shared_state.user_has_pick(
                        self.channel.id, interaction.user.id
                    )
        except Exception:
            pass

        if already_picked:
            await interaction.followup.send(
                "You already submitted a ticker for this channel.",
                ephemeral=True,
            )
            return

        typed = (typed_hint or ticker).upper().strip().lstrip("$")
        status_msg = await interaction.followup.send(
            f"Submitting **${ticker.upper()}**…",
            ephemeral=True,
            wait=True,
        )
        asyncio.create_task(
            self._finish_ticker_submit(
                interaction,
                status_msg=status_msg,
                query=ticker,
                typed=typed,
                key=key,
            )
        )

    def _complete_ticker(self, query: str) -> tuple[str, dict] | None:
        q = query.upper().strip().lstrip("$")
        if not q:
            return None
        category = category_for_channel(self.channel.name)
        examples = EXAMPLE_STOCKS.get(category, [])[:40]

        def example_row(symbol: str) -> tuple[str, dict]:
            return symbol, {
                "symbol": symbol,
                "shortName": "Example stock",
                "exchange": "NASDAQ/NYSE",
                "marketCap": None,
                "category": category,
                "source": "examples",
            }

        exact_examples = [symbol for symbol in examples if symbol == q]
        if exact_examples:
            return example_row(exact_examples[0])
        prefix_examples = [symbol for symbol in examples if symbol.startswith(q)]
        if prefix_examples:
            return example_row(prefix_examples[0])

        finnhub_row = finnhub_validate_symbol(q, category)
        if finnhub_row:
            return q, finnhub_row

        candidate_symbols: list[str] = []

        rows = search_symbols_by_query(q, category=category, limit=10)
        for row in rows:
            symbol = row.get("symbol", "").upper()
            if symbol == q and row.get("category") == category:
                return symbol, row
        for row in rows:
            symbol = row.get("symbol", "").upper()
            if symbol and row.get("category") == category and symbol not in candidate_symbols:
                candidate_symbols.append(symbol)

        if q not in candidate_symbols:
            candidate_symbols.append(q)
        for symbol in candidate_symbols:
            market_row = validate_symbol_for_category(symbol, category)
            if market_row:
                return symbol, market_row
        return None

    def _freeze_controls(self) -> None:
        self.frozen = True
        self.try_btn.disabled = True

    async def _finish_ticker_submit(
        self,
        interaction: discord.Interaction,
        *,
        status_msg: discord.Message,
        query: str,
        typed: str,
        key: Tuple[int, int],
    ) -> None:
        try:
            completed = await asyncio.to_thread(self._complete_ticker, query)
            if not completed:
                await status_msg.edit(
                    content=(
                        "Could not complete that input to a valid stock for this category. "
                        "Press **Try Ticker** again and enter another symbol."
                    )
                )
                return

            ticker, market_row = completed
            cat_idx = _category_index_for_channel(self.channel)
            ok, reason, count_now = await _try_add_ticker_to_pick_results(
                guild=interaction.guild,
                category_idx=cat_idx,
                ticker=ticker,
                submitted_by=interaction.user.id,
                market_cap=market_row.get("marketCap"),
                exchange=market_row.get("exchange"),
            )

            if not ok:
                if reason == "not_found":
                    await status_msg.edit(
                        content=(
                            f"Could not locate the **{CHANNEL_PICK_RESULTS}** embed. "
                            "Ask an admin to run `!prep_pick_results_demo`."
                        )
                    )
                    return
                if reason in {"duplicate", "user_already_picked"}:
                    await status_msg.edit(
                        content=(
                            "That ticker is already listed, or you already submitted for this category."
                        )
                    )
                    return
                if reason == "full":
                    self._freeze_controls()
                    try:
                        if self.message_id:
                            await interaction.followup.edit_message(
                                self.message_id, view=self
                            )
                    except Exception:
                        pass
                    await status_msg.edit(
                        content=(
                            f"This category already has {TICKER_LIMIT_PER_CATEGORY} unique tickers. "
                            "Please try a different channel."
                        )
                    )
                    return
                await status_msg.edit(content="Submission failed. Please try again.")
                return

            try:
                if shared_state:
                    if hasattr(shared_state, "set_user_pick"):
                        shared_state.set_user_pick(
                            self.channel.id, interaction.user.id, ticker
                        )
                    elif hasattr(shared_state, "record_user_pick"):
                        shared_state.record_user_pick(
                            self.channel.id, interaction.user.id, ticker
                        )
            except Exception:
                pass

            _picks_done.add(key)
            self._freeze_controls()

            try:
                if self.message_id:
                    await interaction.followup.edit_message(self.message_id, view=self)
            except Exception:
                pass

            _picker_open.pop((self.channel.id, interaction.user.id), None)

            if count_now is not None and count_now >= TICKER_LIMIT_PER_CATEGORY:
                try:
                    await self.channel.send(
                        embed=_closed_banner_embed(interaction.guild, count=count_now)
                    )
                except Exception:
                    pass
                try:
                    await _post_mod_log_submission_closed(
                        guild=interaction.guild,
                        cat_idx=cat_idx,
                        ch=self.channel,
                        count=count_now,
                        triggered_by=interaction.user
                        if isinstance(interaction.user, discord.Member)
                        else None,
                    )
                except Exception:
                    pass

            completed_note = (
                f"Completed `{typed}` to `${ticker}`. " if typed != ticker else ""
            )
            await status_msg.edit(
                content=f"{completed_note}Ticker **${ticker}** has been submitted."
            )
        except Exception:
            try:
                await status_msg.edit(
                    content="Something went wrong while saving. Please try again."
                )
            except Exception:
                pass


# ===== TESTING VARIANT (does NOT enforce one-submission-per-user) =====

class TestingStockPickerView(StockPickerView):
    """Same Try-Ticker modal UI as production; testing commands may relax pick limits elsewhere."""


def _embeds_from_example_lines(title: str, lines: list[str]) -> list[discord.Embed]:
    """Split many lines into multiple embeds under Discord description limits."""
    footer = "Examples only. Final ticker must still pass category validation."
    if not lines:
        emb = discord.Embed(
            title=f"{title} — examples",
            description="No examples available.",
            color=discord.Color.teal(),
        )
        emb.set_footer(text=footer)
        return [emb]
    embeds: list[discord.Embed] = []
    i = 0
    part = 0
    while i < len(lines) and len(embeds) < 10:
        part += 1
        chunk: list[str] = []
        size = 0
        while i < len(lines) and size + len(lines[i]) + 1 <= 3800:
            chunk.append(lines[i])
            size += len(lines[i]) + 1
            i += 1
        if not chunk:
            chunk = [lines[i]]
            i += 1
        emb = discord.Embed(
            title=f"{title} — examples (part {part})",
            description="\n".join(chunk),
            color=discord.Color.teal(),
        )
        emb.set_footer(text=footer)
        embeds.append(emb)
    return embeds


class ExamplesPagerView(discord.ui.View):
    """Ephemeral follow-up: first 20 stocks + optional **Show 20 more** for the same category."""

    def __init__(
        self,
        owner_id: int,
        category: str,
        display_title: str,
        initial_lines: list[str],
    ) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.category = category
        self.display_title = display_title
        self._accumulated: list[str] = list(initial_lines)
        self._total_symbols = len(EXAMPLE_STOCKS.get(category, []))
        self._next_offset = min(EXAMPLES_PAGE_SIZE, self._total_symbols)
        self.show_more.disabled = self._next_offset >= self._total_symbols

    @discord.ui.button(label="Show 20 more", style=discord.ButtonStyle.primary, row=0)
    async def show_more(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the member who opened this list can load more examples.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        try:
            new_lines = await asyncio.to_thread(
                example_stock_lines_slice,
                self.category,
                self._next_offset,
                EXAMPLES_PAGE_SIZE,
            )
        except Exception as exc:
            print("[ExamplesPagerView.show_more] Exception:", repr(exc))
            await interaction.followup.send("Could not load more examples. Try again.", ephemeral=True)
            return
        if not new_lines:
            self.show_more.disabled = True
            await interaction.edit_original_response(
                content=None,
                embeds=_embeds_from_example_lines(self.display_title, self._accumulated)[:10],
                view=self,
            )
            return
        self._accumulated.extend(new_lines)
        self._next_offset += len(new_lines)
        if self._next_offset >= self._total_symbols:
            self.show_more.disabled = True
        embeds = _embeds_from_example_lines(self.display_title, self._accumulated)
        await interaction.edit_original_response(content=None, embeds=embeds[:10], view=self)


class OpenPickerView(discord.ui.View):
    def __init__(self, channel: discord.TextChannel, user_id: int):
        super().__init__(timeout=None)
        self.channel = channel
        self.user_id = user_id

    @discord.ui.button(label="Open Picker", style=discord.ButtonStyle.primary, custom_id="pre_voting:open_picker")
    async def open_picker(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.InteractionResponded:
            pass

        key = (self.channel.id, interaction.user.id)
        try:
            if not isinstance(interaction.user, discord.Member) or not _can_choose_weekly_ticker(interaction.user):
                await interaction.followup.send(
                    "Only PLAYER subscribers, active WINNERS, and admins can choose weekly tickers.",
                    ephemeral=True,
                )
                return
            if await _is_channel_closed(self.channel):
                await interaction.followup.send(
                    f"This category already has {TICKER_LIMIT_PER_CATEGORY} unique tickers and is closed for this week.",
                    ephemeral=True
                )
                return

            already_picked = key in _picks_done
            try:
                already_picked = already_picked or database.user_has_ticker_pick(
                    self.channel.guild.id,
                    database.ticker_selection_week_key_for(),
                    category_for_channel(self.channel.name),
                    interaction.user.id,
                )
            except Exception:
                pass
            try:
                if shared_state:
                    if hasattr(shared_state, "has_user_pick"):
                        already_picked = already_picked or shared_state.has_user_pick(
                            self.channel.id, interaction.user.id)
                    elif hasattr(shared_state, "user_has_pick"):
                        already_picked = already_picked or shared_state.user_has_pick(
                            self.channel.id, interaction.user.id)
            except Exception:
                pass

            if already_picked:
                await interaction.followup.send(
                    "You already submitted a ticker for this channel.",
                    ephemeral=True
                )
                return

            open_list = _picker_open.get(key, [])
            if open_list:
                await interaction.followup.send(
                    "You already have an open picker for this channel.",
                    ephemeral=True
                )
                return

            _picker_open[key] = [-1]

            view = StockPickerView(channel=self.channel,
                                   user_id=interaction.user.id)

            sent = await interaction.followup.send(
                content=(
                    "Click **Try Ticker**, type part or all of a ticker (with or without `$`), "
                    "and the system will auto-complete it to the best valid match for this cap category."
                ),
                view=view,
                ephemeral=True
            )
            view.message_id = sent.id
            _picker_open[key] = [sent.id]

            async def _on_timeout():
                _picker_open.pop(key, None)
            view.on_timeout = _on_timeout  # type: ignore

        except Exception as e:
            if _picker_open.get(key) == [-1]:
                _picker_open.pop(key, None)
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True)
                await interaction.followup.send(
                    "Something went wrong. Try again.",
                    ephemeral=True
                )
            except Exception:
                pass
            print("[OpenPickerView] Exception:", repr(e))

    @discord.ui.button(label="Show 20 Examples", style=discord.ButtonStyle.secondary, custom_id="pre_voting:show_examples")
    async def show_examples(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_message("Loading example stocks...", ephemeral=True)
        except discord.InteractionResponded:
            pass

        try:
            category = category_for_channel(self.channel.name)
            title = {
                "small": CATEGORY_TITLES["small"],
                "mid": CATEGORY_TITLES["mid"],
                "blue": CATEGORY_TITLES["blue"],
            }.get(category, "Stocks")
            first_lines = await asyncio.to_thread(
                example_stock_lines_slice, category, 0, EXAMPLES_PAGE_SIZE
            )
            view = ExamplesPagerView(
                interaction.user.id,
                category,
                title,
                first_lines,
            )
            embeds = _embeds_from_example_lines(title, view._accumulated)
            await interaction.edit_original_response(
                content="Showing **20** example stocks. Use **Show 20 more** for the next batch.",
                embeds=embeds[:10],
                view=view,
            )
        except Exception as exc:
            try:
                await interaction.edit_original_response(content="Could not load examples right now. Please try again.")
            except Exception:
                pass
            print("[OpenPickerView.show_examples] Exception:", repr(exc))


class OpenPickerViewMulti(discord.ui.View):
    def __init__(self, channel: discord.TextChannel, user_id: int):
        super().__init__(timeout=120)
        self.channel = channel
        self.user_id = user_id

    @discord.ui.button(label="Open Picker", style=discord.ButtonStyle.primary)
    async def open_picker(self, interaction: discord.Interaction, button: discord.ui.Button):
        key = (self.channel.id, interaction.user.id)
        try:
            if await _is_channel_closed(self.channel):
                try:
                    await interaction.response.defer(ephemeral=True)
                except discord.InteractionResponded:
                    pass
                await interaction.followup.send(
                    f"This category already has {TICKER_LIMIT_PER_CATEGORY} unique tickers and is closed for this week.",
                    ephemeral=True
                )
                return

            open_list = _picker_open.get(key, [])
            if open_list:
                try:
                    await interaction.response.defer(ephemeral=True)
                except discord.InteractionResponded:
                    pass
                await interaction.followup.send(
                    "You already have an open picker for this channel.",
                    ephemeral=True
                )
                return

            _picker_open[key] = [-1]

            view = TestingStockPickerView(
                channel=self.channel, user_id=interaction.user.id)

            try:
                await interaction.response.defer(ephemeral=True)
            except discord.InteractionResponded:
                pass

            sent = await interaction.followup.send(
                content="Use the dropdown to pick a ticker. Your actions are private.",
                view=view,
                ephemeral=True
            )
            view.message_id = sent.id
            _picker_open[key] = [sent.id]

            async def _on_timeout():
                _picker_open.pop(key, None)
            view.on_timeout = _on_timeout  # type: ignore

        except Exception as e:
            if _picker_open.get(key) == [-1]:
                _picker_open.pop(key, None)
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True)
                await interaction.followup.send(
                    "Something went wrong. Try again.",
                    ephemeral=True
                )
            except Exception:
                pass
            print("[OpenPickerViewMulti] Exception:", repr(e))


# ----------------- Pick Results DEMO (prep + fill + push) -----------------

def _demo_pick_results_embed() -> discord.Embed:
    return _pick_results_embed_scaffold()


async def _fill_category_to_20(emb: discord.Embed, idx: int, pool: List[str]) -> None:
    existing = _parse_field_lines(
        emb.fields[idx].value if idx < len(emb.fields) else None)
    want = TICKER_LIMIT_PER_CATEGORY - len(existing)
    if want <= 0:
        want = 0
    candidates = [t for t in pool if t not in existing]
    existing.extend(candidates[:want])

    base_name = [CATEGORY_TITLES["small"], CATEGORY_TITLES["mid"], CATEGORY_TITLES["blue"]][idx]
    new_name = f"{base_name} ({len(existing)}/{TICKER_LIMIT_PER_CATEGORY})"
    new_value = _render_field_lines(existing)
    emb.set_field_at(idx, name=new_name, value=new_value, inline=False)


def _extract_lists_from_pick_results(emb: discord.Embed) -> List[List[str]]:
    """Return [small_list, mid_list, blue_list] from the pick-results embed."""
    out: List[List[str]] = [[], [], []]
    for i in range(3):
        if i < len(emb.fields):
            out[i] = _parse_field_lines(emb.fields[i].value)
        else:
            out[i] = []
    return out


async def _clear_pick_results_message(msg: discord.Message, emb: discord.Embed) -> None:
    new = discord.Embed(
        title=emb.title, description=emb.description, color=emb.color)
    new.add_field(name=f"{CATEGORY_TITLES['small']} (0/{TICKER_LIMIT_PER_CATEGORY})", value="—", inline=False)
    new.add_field(name=f"{CATEGORY_TITLES['mid']} (0/{TICKER_LIMIT_PER_CATEGORY})", value="—", inline=False)
    new.add_field(name=f"{CATEGORY_TITLES['blue']} (0/{TICKER_LIMIT_PER_CATEGORY})", value="—", inline=False)
    await msg.edit(embed=new)


class SubmissionUICog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="ui_picker")
    async def ui_picker(self, ctx: commands.Context):
        if isinstance(ctx.channel, discord.TextChannel) and await _is_channel_closed(ctx.channel):
            await ctx.send(embed=_closed_banner_embed(ctx.guild))
            return

        embed = discord.Embed(
            title="CHOOSE YOUR TICKER",
            description=(
                "Click **Open Picker**, then **Try Ticker** and type part or all of a ticker "
                "(with or without `$`). The system auto-completes it to the best valid match.\n\n"
                "Need ideas? Click **Show 20 Examples** for twenty sample stocks (names and prices). "
                "Then use **Show 20 more** on that private message to load the next twenty, as many times as you like."
            ),
            color=discord.Color.blurple()
        )
        await ctx.send(embed=embed, view=OpenPickerView(channel=ctx.channel, user_id=ctx.author.id))

    @commands.command(name="ui_picker_multi")
    @commands.has_role("ADMIN")
    async def ui_picker_multi(self, ctx: commands.Context):
        await ctx.send("The multi-submit testing picker is disabled in production mode.")

    @commands.command(name="prep_pick_results_demo")
    @commands.has_role("ADMIN")
    async def prep_pick_results_demo(self, ctx: commands.Context):
        if ctx.channel.name.lower() not in {CHANNEL_PICK_RESULTS.lower(), "pick-results"}:
            await ctx.send(f"Please run this in the **#{CHANNEL_PICK_RESULTS}** channel.")
            return
        emb = _demo_pick_results_embed()
        await ctx.send(embed=emb)
        await ctx.send("Pick-results demo embed created.")

    @commands.command(name="fill_pick_results_limit")
    @commands.has_role("ADMIN")
    async def fill_pick_results_20(self, ctx: commands.Context):
        """ADMIN: fill all three lists to the configured limit and broadcast closed banners."""
        if ctx.channel.name.lower() not in {CHANNEL_PICK_RESULTS.lower(), "pick-results"}:
            await ctx.send(f"Please run this in the **#{CHANNEL_PICK_RESULTS}** channel.")
            return

        found = await _find_pick_results_message(ctx.channel)
        if not found:
            msg = await ctx.send(embed=_demo_pick_results_embed())
            emb = msg.embeds[0]
        else:
            msg, emb = found

        week_key = database.ticker_selection_week_key_for()
        stored = database.list_tickers(ctx.guild.id, week_key) if ctx.guild else {"small": [], "mid": [], "blue": []}
        new = _pick_results_embed_scaffold()
        for idx, category in enumerate(("small", "mid", "blue")):
            tickers = stored[category]
            new.set_field_at(
                idx,
                name=f"{_category_title_for_idx(idx)} ({len(tickers)}/{TICKER_LIMIT_PER_CATEGORY})",
                value=_render_field_lines(tickers),
                inline=False,
            )

        await msg.edit(embed=new)
        await ctx.send("Refreshed pick-results from the Supabase ticker selections. Broadcasting closed banners where needed…")

        guild = ctx.guild
        if guild:
            name_map = {
                CHANNEL_SMALL_TICKER: 0,
                CHANNEL_MID_TICKER: 1,
                CHANNEL_BLUE_TICKER: 2,
            }
            for ch in guild.text_channels:
                idx = name_map.get(ch.name.lower())
                if idx is None:
                    continue
                fld = new.fields[idx] if idx < len(new.fields) else None
                cnt = len(_parse_field_lines(fld.value if fld else None))
                if cnt >= TICKER_LIMIT_PER_CATEGORY:
                    try:
                        await ch.send(embed=_closed_banner_embed(guild, count=cnt))
                    except Exception:
                        pass

    @commands.command(name="force_push_buttons")
    @commands.has_role("ADMIN")
    async def force_push_buttons(self, ctx: commands.Context):
        """
        ADMIN: Read #pick-results, post ticker buttons to WEEKLY PICKS channels,
        then clear #pick-results. (No voting logic yet.)
        """
        # Must run in any channel; we'll read from #pick-results inside.
        pr_ch = await _get_pick_results_channel(ctx.guild)
        if pr_ch is None:
            await ctx.send(f"Could not find **#{CHANNEL_PICK_RESULTS}**. Please create it and run `!prep_pick_results_demo`.")
            return

        found = await _find_pick_results_message(pr_ch)
        if not found:
            await ctx.send(f"No pick-results embed found in **#{CHANNEL_PICK_RESULTS}**. Run `!prep_pick_results_demo`.")
            return

        pr_msg, pr_emb = found
        while len(pr_emb.fields) < 3:
            pr_emb.add_field(name="—", value="—", inline=False)

        lists = _extract_lists_from_pick_results(pr_emb)  # [small, mid, blue]

        # Post buttons into weekly picks channels
        created = 0
        for idx, tickers in enumerate(lists):
            # Find channel
            ch_name = _weekly_channel_name_for_idx(idx)
            dest = discord.utils.get(ctx.guild.text_channels, name=ch_name)
            if not dest:
                await ctx.send(f"Weekly picks channel **#{ch_name}** not found. Please create it.")
                continue

            # Build an embed and a view of buttons
            title = f"WEEKLY PICKS — {_category_title(idx)}"
            desc = "Click your favorite tickers. (Voting will be enabled soon.)"
            embed = discord.Embed(
                title=title, description=desc, color=discord.Color.blue())
            if tickers:
                embed.add_field(name="Tickers", value=" • ".join(
                    f"${t}" for t in tickers), inline=False)
            else:
                embed.add_field(name="Tickers", value="—", inline=False)

            view = WeeklyPickButtonsView(category_idx=idx, tickers=tickers)
            await dest.send(embed=embed, view=view)
            created += 1

        # Clear pick-results contents (reset to 0/20)
        await _clear_pick_results_message(pr_msg, pr_emb)

        await ctx.send(f"Pushed buttons to {created} WEEKLY PICKS channels and cleared **#{CHANNEL_PICK_RESULTS}**.")


async def setup(bot: commands.Bot):
    await bot.add_cog(SubmissionUICog(bot))
