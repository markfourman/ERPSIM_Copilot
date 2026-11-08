"""Team memory: your own notes (memory/private/*.md) + short summaries of past games (history/*.xlsx).

Nothing here ships with the public repo: every team brings its own strategy and past data.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .config import Settings
from .model import GameState
from .odata import ExcelSource

MAX_CHARS = 40000


def load_notes(folder: Path) -> str:
    folder = Path(folder)
    if not folder.exists():
        return "(no team memory found: add .md files to memory/private/)"
    parts = []
    for f in sorted(folder.glob("*.md")) + sorted(folder.glob("*.txt")):
        parts.append(f"## {f.stem}\n{f.read_text(encoding='utf-8').strip()}")
    text = "\n\n".join(parts) or "(memory folder is empty)"
    return text[:MAX_CHARS]


def summarize_game(path: Path, settings: Settings) -> str:
    frames = ExcelSource(path).load()
    v = frames.get("Company_Valuation", pd.DataFrame())
    if v.empty:
        return f"{path.name}: no Company_Valuation data."
    st = GameState(frames, settings)
    v = v.sort_values("SIM_ELAPSED_STEPS")
    last = v.iloc[-1]
    prof = v.set_index("SIM_ELAPSED_STEPS").PROFIT
    by_round = {}
    for rnd in range(1, 5):
        end = prof[prof.index <= rnd * 20]
        start = prof[prof.index <= (rnd - 1) * 20]
        if not end.empty:
            by_round[rnd] = round(float(end.iloc[-1] - (start.iloc[-1] if not start.empty else 0)))
    s = frames.get("Sales", pd.DataFrame())
    lines = [f"### {path.stem}",
             f"Final day {int(last.SIM_ELAPSED_STEPS)}: valuation €{last.COMPANY_VALUATION:,.0f}, profit €{last.PROFIT:,.0f}, rating {last.CREDIT_RATING}.",
             "Profit by round: " + ", ".join(f"R{k} €{val:,.0f}" for k, val in by_round.items())]
    if not s.empty:
        k = s.groupby("MATERIAL_DESCRIPTION").agg(q=("QUANTITY", "sum"), v=("NET_VALUE", "sum"))
        k = k[k.q > 0].sort_values("v", ascending=False)
        lines.append("Sales: " + "; ".join(f"{n} {r.q:,.0f} boxes at €{r.v / r.q:.2f}" for n, r in k.iterrows()))
    p = frames.get("Production", pd.DataFrame())
    if not p.empty:
        d = p.groupby("SIM_ELAPSED_STEPS").YIELD.sum().reindex(range(1, int(last.SIM_ELAPSED_STEPS) + 1), fill_value=0)
        lines.append(f"Production: {d.sum():,.0f} boxes; idle days {list(d[d == 0].index)}; best day {d.max():,.0f}.")
    po = frames.get("Production_Orders", pd.DataFrame())
    if not po.empty:
        lines.append(f"Orders: {len(po)}, under 48,000: {int((po.TARGET_QUANTITY < 48000).sum())}, setup hours {po.get('SETUP_TIME', pd.Series(dtype=float)).sum():,.0f}.")
    lines.append(f"Finished goods left at the end: {st.finished_goods():,.0f} boxes.")
    return "\n".join(lines)


def load_history(folder: Path, settings: Settings) -> str:
    folder = Path(folder)
    if not folder.exists():
        return "(no past games: drop exported game workbooks into history/)"
    cache_file = folder / "_summaries.json"
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    out = []
    for f in sorted(folder.glob("*.xlsx")):
        key = f"{f.name}:{f.stat().st_mtime}"
        if key not in cache:
            try:
                cache[key] = summarize_game(f, settings)
            except Exception as e:  # keep going if one file is odd
                cache[key] = f"{f.name}: could not read ({e})"
        out.append(cache[key])
    cache_file.write_text(json.dumps(cache, indent=1))
    return ("\n\n".join(out) or "(history folder is empty)")[:MAX_CHARS]
