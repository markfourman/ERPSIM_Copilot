"""Live mode: keep a copy of the game on your laptop and top it up as each game day arrives.

OData can't push (it's "fetch", like fetch email), so the co-pilot asks small and often:

1. Heartbeat: every few seconds, one row: "what is the latest game day?"
2. New day: only that day's rows for the daily tables (sales, stock, production, valuation...), plus a fresh copy
   of the small "current" tables whose rows change (prices, production and purchase orders, forecasts).
3. The weekly market table only when a new week's numbers are due.

The whole game is downloaded once, when you connect (again only if days were missed or a filter is refused).

ReplayServer plays a saved game in real time and answers the same questions, for practice and for tests.
"""
from __future__ import annotations

import io
import math
import struct
import threading
import time
import wave
from functools import lru_cache

import pandas as pd
import requests

from .odata import OPTIONAL, REQUIRED, GameDay, cut_to_day

DAILY = ["Company_Valuation", "Sales", "Inventory", "Production", "Marketing_Expenses", "Carbon_Emissions"]
CURRENT = ["Production_Orders", "Purchase_Orders", "Current_Pricing_Conditions", "Independent_Requirements",
           "Current_Suppliers_Prices"]
WEEKLY = "Market"
DAY = "SIM_ELAPSED_STEPS"
MAX_CATCH_UP = 6          # missed more days than this (laptop asleep?): download everything again
REFUSED = (400, 405, 501)  # a server that refuses a filter answers with one of these


class LiveFeed:
    """The game as of the latest day, kept up to date with small requests. Safe to share between threads."""

    def __init__(self, source, clock=time.time):
        self.source = source
        self.clock = clock
        self.frames: dict = {}
        self.day = 0
        self.day_seen_at: float | None = None    # when the current day's data arrived here
        self.last_check: float | None = None     # when we last asked the server
        self.last_ok: float | None = None        # ... and last got an answer
        self.error = ""
        self.full_tables: set[str] = set()       # tables to re-read whole (no day column, or filter refused)
        self.stats = {"kind": "", "requests": 0, "bytes": 0, "full_requests": 0, "full_bytes": 0}
        self._lock = threading.Lock()

    @property
    def replay(self) -> bool:
        return getattr(self.source, "replay", False)

    def reset(self):
        """Forget everything; the next check downloads the whole game again."""
        with self._lock:
            self.frames, self.day, self.last_check = {}, 0, None

    # ---------------------------------------------------------------- heartbeat
    def check(self, min_interval: float = 0.0) -> bool:
        """Ask the server for the latest day (at most once per `min_interval` seconds).

        Returns True when new data arrived: the first download, a new game day, or a new game.
        Raises on connection or login problems; the last good data stays in place.
        """
        with self._lock:
            now = self.clock()
            if self.last_check is not None and now - self.last_check < min_interval:
                return False
            self.last_check = now
            try:
                changed = self._update(self.source.latest(), now)
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"[:300]
                raise
            self.error, self.last_ok = "", now
            return changed

    def _update(self, latest: GameDay | None, now: float) -> bool:
        if latest is None:                              # connected, but the game has no day yet
            return False
        if not self.frames or latest.day < self.day:    # first time, or a new game on this client
            self._full_load(now)
            return True
        if latest.day == self.day:
            return False
        if latest.day - self.day > MAX_CATCH_UP:
            self._full_load(now)
        else:
            self._top_up(latest, now)
        return True

    # ---------------------------------------------------------------- downloads
    def _counters(self) -> tuple[int, int]:
        return getattr(self.source, "requests", 0), getattr(self.source, "bytes", 0)

    def _record(self, kind: str, start: tuple[int, int]):
        req, byt = self._counters()
        self.stats.update(kind=kind, requests=req - start[0], bytes=byt - start[1])
        if kind == "full download":
            self.stats.update(full_requests=req - start[0], full_bytes=byt - start[1])

    def _full_load(self, now: float):
        start = self._counters()
        frames = self.source.load()
        cv = frames.get("Company_Valuation")
        self.frames = frames
        self.day = int(cv[DAY].max()) if cv is not None and not cv.empty else 0
        self.day_seen_at = now
        self.full_tables = {t for t in DAILY if t in frames and frames[t] is not None
                            and not frames[t].empty and DAY not in frames[t]}
        self._record("full download", start)

    def _top_up(self, latest: GameDay, now: float):
        start = self._counters()
        missing = getattr(self.source, "missing", set())
        frames = dict(self.frames)        # build the new copy aside, swap at the end: never half-updated
        # the new days, plus the last day we already have (picks up any rows that arrived late)
        days, cv = self.source.recent(latest.day - self.day + 1)
        days = [d for d in days if d.day >= self.day] or [latest]
        numbers = [d.day for d in days]
        frames["Company_Valuation"] = _merge(frames.get("Company_Valuation"), cv, numbers)
        for t in DAILY[1:]:
            if t in missing:
                continue
            if t in self.full_tables:
                frames[t] = self.source.fetch(t)
                continue
            try:
                parts = [self.source.fetch_day(t, d) for d in days]
            except requests.HTTPError as e:
                if e.response is None or e.response.status_code not in REFUSED:
                    raise
                self.full_tables.add(t)             # this table can't be filtered: read it whole from now on
                frames[t] = self.source.fetch(t)
                continue
            parts = [p for p in parts if p is not None and not p.empty]
            frames[t] = _merge(frames.get(t), pd.concat(parts, ignore_index=True) if parts else None, numbers)
        for t in CURRENT:
            if t not in missing:
                frames[t] = self.source.fetch(t)
        market = frames.get(WEEKLY)
        have = int(market["SIM_PERIOD"].max()) if market is not None and not market.empty and "SIM_PERIOD" in market else 0
        if WEEKLY not in missing and have < latest.day // 5:   # a new week's market numbers are due
            frames[WEEKLY] = self.source.fetch(WEEKLY)
        self.frames = frames
        self.day = max(self.day, latest.day)
        self.day_seen_at = now
        self._record("new day", start)


def _merge(old: pd.DataFrame | None, rows: pd.DataFrame | None, days: list[int]) -> pd.DataFrame:
    """Replace the rows of `days` in a daily table with freshly downloaded ones."""
    if old is None or old.empty or DAY not in old:
        return rows.reset_index(drop=True) if rows is not None and not rows.empty else (old if old is not None else pd.DataFrame())
    keep = old[~old[DAY].isin(days)]
    if rows is None or rows.empty:
        return keep.reset_index(drop=True)
    if keep.empty:
        return rows.sort_values(DAY, kind="stable").reset_index(drop=True)
    return pd.concat([keep, rows], ignore_index=True).sort_values(DAY, kind="stable").reset_index(drop=True)


class ReplayServer:
    """A saved game played back in real time, answering the same small questions as the live feed."""
    replay = True

    def __init__(self, frames: dict, start_day: int = 1, seconds_per_day: float = 45.0, clock=time.time):
        self.all = frames
        cv = frames.get("Company_Valuation")
        self.last_day = int(cv[DAY].max()) if cv is not None and not cv.empty else 0
        self.start_day = max(1, min(int(start_day), self.last_day or 1))
        self.seconds_per_day = max(0.01, float(seconds_per_day))
        self.clock = clock
        self.t0 = clock()
        self.requests = 0
        self.bytes = 0
        self.missing = {t for t, df in frames.items() if df is None or len(df.columns) == 0}

    def today(self) -> int:
        if not self.last_day:
            return 0
        return min(self.last_day, self.start_day + int((self.clock() - self.t0) / self.seconds_per_day))

    def seconds_to_next_day(self) -> float | None:
        if self.today() >= self.last_day:
            return None
        return self.seconds_per_day - ((self.clock() - self.t0) % self.seconds_per_day)

    def _send(self, df: pd.DataFrame) -> pd.DataFrame:
        """Count it like a network answer (JSON size), so the app's stats mean the same in practice."""
        self.requests += 1
        self.bytes += len(df.to_json(orient="records")) if df is not None and not df.empty else 30
        return df

    def _game_day(self, day: int) -> GameDay:
        cv = self.all["Company_Valuation"]
        row = cv[cv[DAY] == day]
        if not row.empty and "SIM_ROUND" in row and "SIM_STEP" in row:
            return GameDay(day, int(row["SIM_ROUND"].iloc[0]), int(row["SIM_STEP"].iloc[0]))
        return GameDay(day, (day - 1) // 20 + 1, (day - 1) % 20 + 1)

    def latest(self) -> GameDay | None:
        day = self.today()
        self._send(pd.DataFrame([{"day": day}]))
        return self._game_day(day) if day else None

    def recent(self, n: int) -> tuple[list[GameDay], pd.DataFrame]:
        today = self.today()
        days = [self._game_day(d) for d in range(max(1, today - n + 1), today + 1)]
        cv = self.all["Company_Valuation"]
        return days, self._send(cv[cv[DAY].isin([d.day for d in days])].copy())

    def fetch_day(self, entity: str, day: GameDay) -> pd.DataFrame:
        df = self.all.get(entity)
        if df is None or df.empty or DAY not in df:
            return self._send(pd.DataFrame())
        return self._send(df[df[DAY] == day.day].copy())

    def fetch(self, entity: str) -> pd.DataFrame:
        df = cut_to_day({entity: self.all.get(entity)}, self.today())[entity]
        return self._send(df.copy() if df is not None else pd.DataFrame())

    def load(self) -> dict:
        """Every OData table as of today (a monitor workbook's own sheets are left out)."""
        tables = {t: self.all.get(t) for t in REQUIRED + OPTIONAL}
        return {name: self._send(df.copy()) if df is not None else pd.DataFrame()
                for name, df in cut_to_day(tables, self.today()).items()}


@lru_cache(maxsize=1)
def alert_sound() -> bytes:
    """A short two-tone chime (WAV) for urgent alerts."""
    rate, out = 22050, bytearray()
    for freq in (880, 1320):
        n = int(rate * 0.16)
        for i in range(n):
            fade = min(1.0, i / 300, (n - i) / 600)
            out += struct.pack("<h", int(11000 * fade * math.sin(2 * math.pi * freq * i / rate)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(out))
    return buf.getvalue()
