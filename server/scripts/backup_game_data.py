"""Export or restore core Supabase game tables (no API keys).

  python scripts/backup_game_data.py --out backup.json
  python scripts/backup_game_data.py --restore backup.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

import database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup or restore stock-bot game data")
    parser.add_argument("--out", help="Write JSON backup to this path")
    parser.add_argument("--restore", help="Restore users/subscriptions/cycles from JSON")
    parser.add_argument("--guild-id", type=int, default=None)
    args = parser.parse_args()
    if bool(args.out) == bool(args.restore):
        print("Pass exactly one of --out PATH or --restore PATH", flush=True)
        return 2
    if args.out:
        payload = database.export_game_backup(args.guild_id)
        path = Path(args.out)
        path.write_text(json.dumps(payload, default=str, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {path} ({path.stat().st_size} bytes)", flush=True)
        return 0
    payload = json.loads(Path(args.restore).read_text(encoding="utf-8"))
    counts = database.restore_game_backup(payload)
    print("restored", counts, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
