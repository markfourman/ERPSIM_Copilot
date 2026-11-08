"""Terminal use.

  python -m copilot.cli                                  # live, interactive questions
  python -m copilot.cli --replay my_game.xlsx --day 51   # replay a saved game
  python -m copilot.cli --replay my_game.xlsx --day 51 --dry-run   # engine only, no AI
  python -m copilot.cli --watch                          # live: print the to-do list as each game day arrives
  python -m copilot.cli --replay my_game.xlsx --watch --day 20 --seconds-per-day 5   # practise on a saved game
"""
from __future__ import annotations

import argparse
import json
import time

from . import engine
from .config import load_settings
from .engine import TOOLS
from .live import LiveFeed, ReplayServer
from .memory import load_history, load_notes
from .model import GameState
from .odata import ExcelSource, ODataSource, cut_to_day


def make_state_provider(settings, replay=None, day=None):
    if replay:
        frames = ExcelSource(replay).load()
        if day:
            frames = cut_to_day(frames, day)
        return lambda: GameState(frames, settings, replay_day=day)
    feed = LiveFeed(ODataSource(settings.odata_url, settings.odata_user, settings.odata_password))

    def get_state():
        feed.check(min_interval=5)          # tiny request; downloads only what's new
        return GameState(feed.frames, settings)
    return get_state


def watch(s, a):
    """Follow the game day by day and print what needs doing (live, or a saved game played in real time)."""
    if a.replay:
        source = ReplayServer(ExcelSource(a.replay).load(), start_day=a.day or 1, seconds_per_day=a.seconds_per_day)
        every = min(a.every, max(0.05, a.seconds_per_day / 3))
    else:
        source = ODataSource(s.odata_url, s.odata_user, s.odata_password)
        every = a.every
    feed = LiveFeed(source)
    brief = None
    if a.brief and s.has_chat_key():
        from .agent import daily_brief as brief
    print("Watching the game. Ctrl + C to stop.")
    try:
        while True:
            try:
                new = feed.check()
            except Exception as e:
                print(f"{time.strftime('%H:%M:%S')}  can't reach the game server: {e}. Retrying.")
                new = False
            if new and feed.frames:
                st = GameState(feed.frames, s, replay_day=feed.day if feed.replay else None)
                snap = engine.snapshot(st)
                alerts = engine.daily_alerts(st, snap=snap)
                stats = feed.stats
                print(f"\n{time.strftime('%H:%M:%S')}  Day {st.day} (round {st.round}, day {st.day_in_round}) · "
                      f"{stats['kind']}: {stats['requests']} requests, {stats['bytes'] / 1000:,.0f} KB")
                line = engine.brief_line(alerts, st.day)
                if brief:
                    ai, why = brief(s, engine.brief_facts(st, snap, alerts))
                    line = ai or f"{line}  ({why})"
                print("  " + line)
                for al in alerts:
                    print(f"  [{al['level']:>5}] {al['text']}")
                if st.day >= s.rules.total_days or (a.until and st.day >= a.until):
                    break
            time.sleep(every)
    except KeyboardInterrupt:
        pass


def main():
    ap = argparse.ArgumentParser(description="ERPsim Co-pilot")
    ap.add_argument("question", nargs="*", help="ask one question and exit")
    ap.add_argument("--config", default=None)
    ap.add_argument("--replay", help="saved game workbook (one sheet per OData table)")
    ap.add_argument("--day", type=int, help="replay day (1-80); with --watch, the day to start on")
    ap.add_argument("--dry-run", action="store_true", help="print every engine tool, no AI call")
    ap.add_argument("--watch", action="store_true", help="print the to-do list as each new game day arrives")
    ap.add_argument("--every", type=float, default=5, help="--watch: seconds between checks for a new day (default 5)")
    ap.add_argument("--seconds-per-day", type=float, default=45, help="--watch --replay: seconds per game day (default 45)")
    ap.add_argument("--until", type=int, help="--watch: stop after this day")
    ap.add_argument("--brief", action="store_true", help="--watch: add a one-line AI brief each day (needs the key)")
    a = ap.parse_args()
    s = load_settings(a.config)
    if a.watch:
        watch(s, a)
        return
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
