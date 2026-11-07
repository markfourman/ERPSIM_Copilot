"""Terminal use.

  python -m copilot.cli                                  # live, interactive questions
  python -m copilot.cli --replay my_game.xlsx --day 51   # replay a saved game
  python -m copilot.cli --replay my_game.xlsx --day 51 --dry-run   # engine only, no AI
"""
from __future__ import annotations

import argparse
import json
import os

from .config import load_settings
from .engine import TOOLS
from .memory import load_history, load_notes
from .model import GameState
from .odata import ExcelSource, ODataSource, cut_to_day


def make_state_provider(settings, replay=None, day=None):
    if replay:
        frames = ExcelSource(replay).load()
        if day:
            frames = cut_to_day(frames, day)
        return lambda: GameState(frames, settings, replay_day=day)
    src = ODataSource(settings.odata_url, settings.odata_user, settings.odata_password)
    return lambda: GameState(src.load(), settings)


def main():
    ap = argparse.ArgumentParser(description="ERPsim Co-pilot")
    ap.add_argument("question", nargs="*", help="ask one question and exit")
    ap.add_argument("--config", default=None)
    ap.add_argument("--replay", help="saved game workbook (one sheet per OData table)")
    ap.add_argument("--day", type=int, help="replay day (1-80)")
    ap.add_argument("--dry-run", action="store_true", help="print every engine tool, no AI call")
    a = ap.parse_args()
    s = load_settings(a.config)
    get_state = make_state_provider(s, a.replay, a.day)
    if a.dry_run:
        st = get_state()
        for name, (fn, _) in TOOLS.items():
            print(f"\n===== {name} =====")
            print(json.dumps(fn(st), indent=1, ensure_ascii=False)[:4000])
        return
    if not s.has_chat_key():
        raise SystemExit(f"Add {s.chat_key_name()} to .env (see README), or use --dry-run.")
    from .agent import Copilot

    bot = Copilot(s, get_state, load_notes(s.memory_dir), load_history(s.history_dir, s))
    if a.question:
        print(bot.ask(" ".join(a.question))["answer"])
        return
    print("ERPsim Co-pilot. Ask a question (empty line to quit).")
    while True:
        q = input("\n> ").strip()
        if not q:
            break
        print(bot.ask(q)["answer"])


if __name__ == "__main__":
    main()
