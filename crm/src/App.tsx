import { useCallback, useEffect, useState } from "react";
import { api, isApiConfigured, type GameHistoryEntry, type GameStatus } from "./api";

const CATS = ["small", "mid", "blue"] as const;
type Cat = (typeof CATS)[number];

const CAT_CLASS: Record<Cat, string> = {
  small: "cat-small",
  mid: "cat-mid",
  blue: "cat-blue",
};

const ACTIONS: { id: string; label: string; hint: string }[] = [
  {
    id: "start_pre_vote",
    label: "Start pre-vote",
    hint: "End the current week, then open ticker picks for the next week.",
  },
  {
    id: "start_vote",
    label: "Start vote",
    hint: "Close pre-vote and open the live vote stage with the current ballot.",
  },
];

type LbRow = {
  rank: number;
  ticker: string;
  votes: number;
  name: string;
  quote: string;
};

function formatTime(iso: string) {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function describeGameStage(cycle: Record<string, boolean | string> | undefined): {
  title: string;
  detail: string;
} {
  if (!cycle) {
    return { title: "Loading…", detail: "Fetching game state from the server." };
  }

  const status = String(cycle.status || "");
  const picksOpen = Boolean(cycle.ticker_selection_open);
  const votingOpen = Boolean(cycle.voting_open);
  const earlyOpen = Boolean(cycle.early_window_open);

  if (status === "closed") {
    return { title: "Week closed", detail: "This week’s competition has ended." };
  }
  if (picksOpen || status === "ticker_selection") {
    return {
      title: "Pre-voting",
      detail: "Ticker picks are open — members submit stocks for the ballot.",
    };
  }
  if (votingOpen && earlyOpen) {
    return {
      title: "Voting · early window",
      detail: "Vote stage is live and the early voting window is still open.",
    };
  }
  if (votingOpen || status === "voting") {
    return {
      title: "Voting",
      detail: "Live vote stage — ballot is set; members vote on listed tickers.",
    };
  }
  if (status === "voting") {
    return {
      title: "Voting (paused)",
      detail: "Vote stage is configured but voting is not open in Discord.",
    };
  }
  return {
    title: status.replace(/_/g, " ") || "Unknown",
    detail: "Use the controls below to advance the game.",
  };
}

export default function App() {
  const [status, setStatus] = useState<GameStatus | null>(null);
  const [tickers, setTickers] = useState<Record<string, string[]>>({});
  const [leaderboards, setLeaderboards] = useState<Record<string, LbRow[]>>({});
  const [gameHistory, setGameHistory] = useState<GameHistoryEntry[]>([]);
  const [subs, setSubs] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lbLoading, setLbLoading] = useState(false);
  const [pendingActionId, setPendingActionId] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(""), 4000);
  };

  const refreshLeaderboards = useCallback(async () => {
    if (!isApiConfigured()) return;
    setLbLoading(true);
    try {
      const lb = await api.leaderboards();
      setLeaderboards(lb.leaderboards);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Leaderboards failed";
      setError((prev) => prev || msg);
    } finally {
      setLbLoading(false);
    }
  }, []);

  const refresh = useCallback(async (opts?: { silent?: boolean }) => {
    if (!isApiConfigured()) {
      setError("Set VITE_API_URL to your Railway API URL (e.g. in Vercel env).");
      setInitialLoading(false);
      return;
    }
    const silent = opts?.silent ?? false;
    if (silent) {
      setRefreshing(true);
    } else {
      setInitialLoading(true);
    }
    setError("");
    try {
      const [s, t, hist, sub] = await Promise.all([
        api.status(),
        api.tickers(),
        api.gameHistory(15),
        api.subscriptions(40),
      ]);
      setStatus(s);
      setTickers(t.by_category);
      setGameHistory(hist);
      setSubs(sub);
      void refreshLeaderboards();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to load";
      if (
        msg.includes("Failed to fetch") ||
        msg.includes("ECONNREFUSED") ||
        msg.includes("NetworkError") ||
        msg.includes("timed out") ||
        msg.includes("TimeoutError")
      ) {
        setError("Server offline — run: cd server && python run.py");
      } else {
        setError(msg);
      }
    } finally {
      setInitialLoading(false);
      setRefreshing(false);
    }
  }, [refreshLeaderboards]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const id = setInterval(() => {
      void refresh({ silent: true });
      void refreshLeaderboards();
    }, 30_000);
    return () => clearInterval(id);
  }, [refresh, refreshLeaderboards]);

  const runAction = async (action: (typeof ACTIONS)[number]) => {
    if (!confirm(`${action.hint}\n\nRun "${action.label}" on the live Discord?`)) return;
    setPendingActionId(action.id);
    setError("");
    try {
      const res = await api.action(action.id);
      showToast(res.message);
      await refresh({ silent: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setPendingActionId(null);
    }
  };

  const cycle = status?.cycle as Record<string, boolean | string> | undefined;
  const phase = (cycle?.status as string) || "—";
  const gameStage = describeGameStage(cycle);
  const limit = status?.ticker_limit ?? 20;

  return (
    <div className="dashboard">
      {initialLoading && (
        <div className="loading-overlay">
          <div className="spinner" />
        </div>
      )}

      <header className="header">
        <div className="brand">
          <div className="brand-icon">MS</div>
          <div>
            <h1>Meme Stock</h1>
            <p>Weekly game control</p>
          </div>
        </div>
        <div className="header-meta">
          {status && (
            <>
              <span className="pill mono">{status.week_key}</span>
              <span className="pill pill-phase">{phase}</span>
              {cycle?.voting_open && <span className="pill">Voting open</span>}
              {cycle?.ticker_selection_open && <span className="pill">Picks open</span>}
              <span className={`pill ${status.bot_connected ? "pill-live" : "pill-dead"}`}>
                {status.bot_connected ? "Bot online" : "Bot offline"}
              </span>
            </>
          )}
          <button
            type="button"
            className="btn btn-ghost"
            disabled={initialLoading || refreshing}
            onClick={() => void refresh({ silent: true })}
          >
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </header>

      {error && <div className="alert">{error}</div>}

      <div className="main-grid">
        <div className="controls-row">
          <div className="stats-strip">
            {CATS.map((cat) => {
              const n = status?.ticker_counts[cat] ?? 0;
              const pct = Math.min(100, (n / limit) * 100);
              const title = status?.category_titles[cat] ?? cat;
              return (
                <div key={cat} className={`stat-card ${CAT_CLASS[cat]}`}>
                  <h4>{title}</h4>
                  <div className="count">
                    {n}
                    <span style={{ color: "var(--muted)", fontSize: "0.9rem", fontWeight: 500 }}>
                      /{limit}
                    </span>
                  </div>
                  <div className="bar">
                    <div className="bar-fill" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })}
          </div>

          <div className="actions-panel">
            <div className="actions-panel-head">
              <h4>Game controls</h4>
              <p className="current-stage-title" aria-live="polite" title={gameStage.detail}>
                {gameStage.title}
              </p>
            </div>
            <div className="actions-grid">
              {ACTIONS.map((a) => {
                const actionLoading = pendingActionId === a.id;
                return (
                  <button
                    key={a.id}
                    type="button"
                    className={`btn btn-sm btn-accent${actionLoading ? " btn-loading" : ""}`}
                    disabled={initialLoading || pendingActionId !== null}
                    aria-busy={actionLoading}
                    onClick={() => void runAction(a)}
                  >
                    {actionLoading ? (
                      <span className="btn-inline-loading">
                        <span className="btn-spinner" aria-hidden="true" />
                        Running…
                      </span>
                    ) : (
                      a.label
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        <div className="categories-row">
          {CATS.map((cat) => (
            <CategoryColumn
              key={cat}
              cat={cat}
              title={status?.category_titles[cat] ?? cat}
              tickers={tickers[cat] ?? []}
              leaderboard={leaderboards[cat] ?? []}
              limit={limit}
              votesLoading={lbLoading}
            />
          ))}
        </div>

        <div className="bottom-row">
          <div className="bottom-panel bottom-panel-history">
            <h4>Past games</h4>
            <GameHistoryList games={gameHistory} categoryTitles={status?.category_titles} />
          </div>

          <div className="bottom-panel">
            <h4>Subscriptions</h4>
            <div className="bottom-scroll">
              <table>
                <thead>
                  <tr>
                    <th>User</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {subs.length === 0 ? (
                    <tr>
                      <td colSpan={2} className="empty-hint">
                        No subscribers
                      </td>
                    </tr>
                  ) : (
                    subs.map((row, i) => (
                      <tr key={i}>
                        <td className="mono">{String(row.discord_id || "")}</td>
                        <td>{String(row.status || "")}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

function winnerDisplayName(game: GameHistoryEntry, userId: number): string {
  const row = game.winners?.find((w) => w.user_id === userId);
  return row?.username?.trim() || `Player ${userId}`;
}

function gameHistorySummary(game: GameHistoryEntry): string {
  const names =
    game.winners?.length > 0
      ? game.winners.map((w) => w.username).join(", ")
      : game.winner_ids.length > 0
        ? game.winner_ids.map((id) => winnerDisplayName(game, id)).join(", ")
        : "No winners";
  const tops = CATS.map((cat) => {
    const w = game.winning_stocks[cat]?.[0];
    return w ? `$${w.ticker}` : null;
  })
    .filter(Boolean)
    .join(" · ");
  return tops ? `${names} · ${tops}` : names;
}

function GameHistoryList({
  games,
  categoryTitles,
}: {
  games: GameHistoryEntry[];
  categoryTitles?: Record<string, string>;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);

  if (games.length === 0) {
    return <p className="game-history-empty">No completed games yet.</p>;
  }

  return (
    <div className="game-history-scroll">
      {games.map((game) => {
        const isOpen = expanded === game.week_key;
        const winnerCount = game.winners?.length || game.winner_ids.length;
        const winnerRows =
          game.winners?.length > 0
            ? game.winners
            : game.winner_ids.map((id) => ({
                user_id: id,
                username: winnerDisplayName(game, id),
              }));

        return (
          <article key={game.week_key} className={`game-card ${isOpen ? "game-card-open" : ""}`}>
            <button
              type="button"
              className="game-card-toggle"
              onClick={() => setExpanded(isOpen ? null : game.week_key)}
              aria-expanded={isOpen}
            >
              <span className="game-card-chevron" aria-hidden>
                {isOpen ? "▼" : "▶"}
              </span>
              <span className="game-card-week mono">{game.week_key}</span>
              {game.closed_at && (
                <span className="game-card-date">{formatTime(game.closed_at)}</span>
              )}
              <span className="game-card-meta">
                {winnerCount} winner{winnerCount === 1 ? "" : "s"}
              </span>
            </button>
            {!isOpen && <p className="game-card-summary">{gameHistorySummary(game)}</p>}
            {isOpen && (
              <div className="game-card-details">
                <div className="game-card-winners game-card-winners-compact">
                  <span className="game-winners-label">Winners</span>
                  {winnerCount === 0 ? (
                    <span className="muted">No one picked all three winning stocks</span>
                  ) : (
                    <ul className="game-winners-list">
                      {winnerRows.map((w) => (
                        <li key={w.user_id} className="game-winner-chip">
                          {w.username}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div className="game-category-tables">
                  {CATS.map((cat) => {
                    const title = categoryTitles?.[cat] ?? game.category_titles[cat] ?? cat;
                    const rows = game.vote_totals?.[cat] ?? [];
                    const winnerTickers = new Set(
                      (game.winning_stocks[cat] ?? []).map((s) => s.ticker)
                    );
                    return (
                      <section key={cat} className={`game-cat-block ${CAT_CLASS[cat]}`}>
                        <h5>{title}</h5>
                        {rows.length === 0 ? (
                          <p className="empty-hint">No votes recorded</p>
                        ) : (
                          <table className="game-votes-table">
                            <thead>
                              <tr>
                                <th>Stock</th>
                                <th className="col-votes">Votes</th>
                              </tr>
                            </thead>
                            <tbody>
                              {rows.map((row) => (
                                <tr
                                  key={row.ticker}
                                  className={winnerTickers.has(row.ticker) ? "row-winner" : undefined}
                                >
                                  <td className="mono">${row.ticker}</td>
                                  <td className="col-votes">{row.votes}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        )}
                      </section>
                    );
                  })}
                </div>
              </div>
            )}
          </article>
        );
      })}
    </div>
  );
}

function CategoryColumn({
  cat,
  title,
  tickers,
  leaderboard,
  limit,
  votesLoading,
}: {
  cat: Cat;
  title: string;
  tickers: string[];
  leaderboard: LbRow[];
  limit: number;
  votesLoading?: boolean;
}) {
  return (
    <article className={`category-panel ${CAT_CLASS[cat]}`}>
      <div className="category-header">
        <h3 className={CAT_CLASS[cat]}>{title}</h3>
        <span className="count-badge">
          {tickers.length}/{limit} tickers · {leaderboard.length} voted
        </span>
      </div>
      <div className="category-body">
        <div>
          <div className="section-label">Tickers of this week</div>
          <div className="ticker-scroll">
            {tickers.length === 0 ? (
              <span className="empty-hint">No tickers yet</span>
            ) : (
              tickers.map((t) => (
                <span key={t} className="ticker-chip">
                  ${t}
                </span>
              ))
            )}
          </div>
        </div>
        <div>
          <div className="section-label">Live votes</div>
          <div className="leader-scroll">
            {votesLoading && leaderboard.length === 0 ? (
              <p className="empty-hint">Loading prices…</p>
            ) : leaderboard.length === 0 ? (
              <p className="empty-hint">No votes yet</p>
            ) : (
              leaderboard.map((row) => (
                <div key={row.ticker} className="lb-row">
                  <span className={`lb-rank ${row.rank <= 3 ? "top" : ""}`}>{row.rank}</span>
                  <div>
                    <div className="lb-ticker">${row.ticker}</div>
                    <div className="lb-meta">
                      {row.name || "—"} · {row.quote}
                    </div>
                  </div>
                  <span className="lb-votes">{row.votes}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </article>
  );
}
