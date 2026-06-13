"""Close duplicate pre-vote cycles and reset the active week's ticker picks."""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

import database

GID = 1359180229616205864


def main() -> int:
    active = database.ticker_selection_week_key_for_guild(GID)
    print(f"Active pre-vote week: {active}", flush=True)

    closed = database.close_open_ticker_selection_cycles(GID, except_week_key=active)
    if closed:
        print(f"Closed duplicate open cycles: {', '.join(closed)}", flush=True)
    else:
        print("No duplicate open cycles.", flush=True)

    database.reset_week_game_data(GID, active)
    database.set_cycle_phase(
        GID,
        active,
        status="ticker_selection",
        ticker_selection_open=True,
        voting_open=False,
        early_window_open=False,
    )
    picks = database.list_ticker_pick_rows(GID, active)
    print(f"Ticker picks remaining for {active}: {len(picks)}", flush=True)
    print("Done — users can submit fresh pre-vote picks after bot restart or Start pre-vote.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
