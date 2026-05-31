# shared_state.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Set


CATEGORIES = ("small", "mid", "blue")


@dataclass
class GuildState:
    # רשימת הטיקרים (לפי סדר בחירה) לכל קטגוריה
    picks: Dict[str, List[str]] = field(
        default_factory=lambda: {c: [] for c in CATEGORIES})
    # משתמשים שכבר בחרו באותו ערוץ (User IDs)
    user_picked: Dict[str, Set[int]] = field(
        default_factory=lambda: {c: set() for c in CATEGORIES})
    # הודעת ה-embed שנעדכן ב-#pick-results לכל קטגוריה
    pick_results_message_id: Dict[str, int] = field(default_factory=dict)
    # ערוצים שסגורים (הגיעו ל-20)
    closed: Set[str] = field(default_factory=set)


class Memory:
    def __init__(self):
        self._guilds: Dict[int, GuildState] = {}

    def for_guild(self, guild_id: int) -> GuildState:
        if guild_id not in self._guilds:
            self._guilds[guild_id] = GuildState()
        return self._guilds[guild_id]


memory = Memory()
