"""ERPsim Co-pilot web app.  Run:  streamlit run app.py"""
from __future__ import annotations

import html
import io
import time

import pandas as pd
import streamlit as st

from copilot import engine
from copilot.config import load_settings
from copilot.live import LiveFeed, ReplayServer, alert_sound
from copilot.memory import load_history, load_notes
from copilot.model import GameState
from copilot.odata import ExcelSource, ODataSource, cut_to_day

st.set_page_config(page_title="ERPsim Co-pilot", layout="wide")
S = load_settings()
ss = st.session_state
LIVE, PRACTICE, PICK = "Live OData feed", "Practice live (saved game)", "Saved game (pick a day)"
UPLOAD = "Upload a file…"

st.markdown("""<style>
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.55rem;margin:.3rem 0 1rem 0}
.kpi{padding:.5rem .75rem;border:1px solid #E4E7EC;border-left:4px solid #2E6B5B;border-radius:10px;background:#FFFFFF;min-width:0}
.kpi.warn{border-left-color:#B54708}
.kpi .l{font-size:.78rem;color:#475467;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kpi .v{font-size:clamp(1.05rem,1.8vw,1.55rem);font-weight:600;color:#1C2B33;line-height:1.3;white-space:nowrap}
.kpi .s{font-size:.72rem;color:#667085;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.livebar{font-size:.8rem;color:#475467;margin:0 0 .3rem 0;line-height:1.5}
.livebar .dot{display:inline-block;width:.6rem;height:.6rem;border-radius:50%;margin-right:.45rem}
.today{border:1px solid #E4E7EC;border-left:4px solid #2E6B5B;border-radius:10px;padding:.55rem .85rem;margin:.1rem 0 .9rem 0;background:#FFFFFF}
.today.urgent{border-left-color:#B42318}
.today .brief{font-weight:600;color:#1C2B33}
.today .src{font-weight:400;font-size:.7rem;color:#667085;margin-left:.5rem;border:1px solid #E4E7EC;border-radius:6px;padding:0 .35rem;white-space:nowrap}
.today ul{margin:.35rem 0 0 0;padding-left:1.15rem}
.today li{margin:.12rem 0;font-size:.88rem;color:#344054}
.today li.act{font-weight:600;color:#1C2B33}
.today li.act::marker{color:#B42318}.today li.do::marker{color:#2E6B5B}.today li.watch::marker{color:#B54708}
.today .note{font-size:.75rem;color:#667085;margin-top:.3rem}
.st-key-chime{display:none}
</style>""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def file_frames(data: bytes) -> dict:
    return ExcelSource(io.BytesIO(data)).load()


def choose_workbook(key: str):
    """A saved game from history/ or an upload. Returns (name, bytes), or (None, None) until one is chosen."""
    games = sorted(S.history_dir.glob("*.xlsx")) if S.history_dir.exists() else []
    choice = st.selectbox("Saved game", [g.name for g in games] + [UPLOAD], key=f"{key}_pick") if games else UPLOAD
    if choice == UPLOAD:
        up = st.file_uploader("Workbook with one sheet per OData table", type=["xlsx"], key=f"{key}_upload")
        return (up.name, up.getvalue()) if up else (None, None)
    return choice, (S.history_dir / choice).read_bytes()


def save_game(frames: dict, path):
    """Write every table to one workbook (one sheet per table): the format replay and history read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        for table, df in frames.items():
            if df is not None and not df.empty:
                df.to_excel(xl, sheet_name=table[:31], index=False)
    return path


def ago(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds < 90 else f"{seconds / 60:.0f} min"


def size(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{max(1, round(n / 1000))} KB"


def live_bar(feed: LiveFeed, every: float, err: str) -> str:
    now = time.time()
    parts = [f"<b>{'PRACTICE' if feed.replay else 'LIVE'}</b>", f"day {feed.day}"]
    if feed.day_seen_at:
        parts.append(f"new data {ago(now - feed.day_seen_at)} ago")
    if feed.replay:
        nxt = feed.source.seconds_to_next_day()
        parts.append(f"next day in {nxt:.0f} s" if nxt is not None else "end of the saved game")
    else:
        parts.append(f"checking every {every:.0f} s")
    s = feed.stats
    if s["kind"] == "new day":
        parts.append(f"last update {s['requests']} small requests, {size(s['bytes'])} (whole game: {size(s['full_bytes'])})")
    elif s["kind"]:
        parts.append(f"whole game downloaded: {s['requests']} requests, {size(s['bytes'])}")
    stale = bool(err) or (feed.last_ok is not None and now - feed.last_ok > max(20.0, 3 * every))
    text = " · ".join(parts)
    if err:
        text += (f"<br><span style='color:#B42318'>Can't reach the game server ({html.escape(err[:140])}). "
                 "Showing the last data and retrying.</span>")
    return f"<div class='livebar'><span class='dot' style='background:{'#B42318' if stale else '#2E6B5B'}'></span>{text}</div>"


# ---------------------------------------------------------------- data source
with st.sidebar:
    st.subheader("Game data")
    mode = st.radio("Source", [LIVE, PRACTICE, PICK], label_visibility="collapsed",
                    help="Practice live plays a saved game day by day as if it were live: a safe way to learn the alerts.")
    sound = ai_brief = False
    if mode == LIVE:
        url = st.text_input("OData URL", S.odata_url, placeholder="https://<server>/odata/<client>")
        user = st.text_input("ERPsim user", S.odata_user)
        pwd = st.text_input("Password", S.odata_password, type="password")
        every = st.slider("Check for a new game day every (seconds)", 3, 30, 5,
                          help="Each check is one tiny request (the latest day only). A new day then downloads only its own rows.")
        ready, feed_key = bool(url and user and pwd), (LIVE, url, user, pwd)
    elif mode == PRACTICE:
        name, data = choose_workbook("practice")
        start = st.number_input("Start on day", 1, 79, 20)
        pace = st.slider("Seconds per game day", 5, 90, 45, help="The championship ran 45-second days.")
        if st.button("Restart"):
            ss.restarts = ss.get("restarts", 0) + 1
        every = 2.0 if pace >= 10 else 1.0
        ready, feed_key = data is not None, (PRACTICE, name, start, pace, ss.get("restarts", 0))
    else:
        name, data = choose_workbook("pick")
        day = st.slider("Replay day", 1, 80, 40)
        ready = data is not None
    if mode != PICK:
        sound = st.toggle("Sound on urgent alerts", True, help="Click anywhere on the page once so the browser allows sound.")
        ai_brief = st.toggle("AI brief each new day", S.has_chat_key(), disabled=not S.has_chat_key(),
                             help="One AI call per game day, which fits the free tier." if S.has_chat_key()
                             else f"Add {S.chat_key_name()} to .env to turn this on.")
    if st.button("Reload everything", help="Download the whole game again."):
        if mode == PICK:
            st.cache_data.clear()
        elif "feed" in ss:
            ss.feed.reset()
    S.teams_active = st.number_input("Teams playing", 1, 20, int(S.teams_active))
    S.rules.capacity = st.number_input("Factory capacity (boxes/day)", 10000, 60000, int(S.rules.capacity), step=1000)

if not ready:
    st.title("ERPsim Co-pilot")
    st.write("Connect the live feed, or pick a saved game, in the sidebar.")
    st.stop()

if mode == PICK:
    new_day = first_view = False
    try:
        state = GameState(cut_to_day(file_frames(data), day), S, replay_day=day)
    except Exception as e:
        st.error(f"Could not read the game data: {e}")
        st.stop()
else:
    if ss.get("feed_key") != feed_key:
        source = (ODataSource(url, user, pwd) if mode == LIVE
                  else ReplayServer(file_frames(data), start_day=start, seconds_per_day=pace))
        ss.feed, ss.feed_key, ss.shown_day, ss.briefs = LiveFeed(source), feed_key, None, {}
    feed: LiveFeed = ss.feed
    if not feed.frames:
        try:
            with st.spinner("Downloading the whole game once. After this, only new days are fetched."):
                feed.check()
        except Exception as e:
            st.error(f"Could not read the game data: {e}")
            st.stop()
    first_view = ss.get("shown_day") is None
    new_day = feed.day != ss.get("shown_day")
    ss.shown_day = feed.day

    @st.fragment(run_every=every)
    def pulse():
        """The heartbeat: a tiny 'what day is it?' request; the whole page redraws only when a new day arrives."""
        err = ""
        try:
            feed.check(min_interval=max(0.5, every * 0.8))
        except Exception as e:
            err = str(e)
        if feed.frames and feed.day != ss.get("shown_day"):
            st.rerun()
        st.markdown(live_bar(feed, every, err), unsafe_allow_html=True)

    pulse()
    if not feed.frames:
        st.info("Connected. Waiting for the game's first day…")
        st.stop()
    state = GameState(feed.frames, S, replay_day=feed.day if feed.replay else None)


def current_state() -> GameState:
    """Fresh state for the chat (reads the local copy; no download)."""
    if mode == PICK:
        return GameState(cut_to_day(file_frames(data), day), S, replay_day=day)
    f = ss.feed
    return GameState(f.frames, S, replay_day=f.day if f.replay else None)


snap = engine.snapshot(state)
plan = engine.production_plan(state)
pa = engine.price_advice(state)
alerts = engine.daily_alerts(state, plan=plan, prices=pa["products"], snap=snap)

# ---------------------------------------------------------------- save a live game for later (history/)
if mode == LIVE:
    with st.sidebar:
        st.divider()
        save_as = st.text_input("Save as", f"game_{pd.Timestamp.now():%Y-%m-%d}", help="Saved into history/ so the co-pilot remembers this game")
        if st.button("Save this game to history"):
            path = save_game(feed.frames, S.history_dir / f"{save_as}.xlsx")
            st.success(f"Saved {path.name} (day {snap['day']}).")

# ---------------------------------------------------------------- header + today's to-do + gauge
st.markdown(f"### Day {snap['day']} of {S.rules.total_days} — round {snap['round']}, day {snap['day_in_round']}"
            f"  \nTeam code **{snap['team_code']}** · phase: {snap['phase']}")

briefs = ss.setdefault("briefs", {})
want_ai = (mode != PICK and ai_brief and new_day and not first_view and state.day not in briefs
           and 0 < state.day < S.rules.total_days)
rules_line = engine.brief_line(alerts, state.day)
today_box = st.empty()


def render_today(line: str, source: str, note: str = ""):
    urgent = any(a["level"] == "act" for a in alerts)
    items = "".join(f"<li class='{a['level']}'>{html.escape(a['text'])}</li>" for a in alerts)
    today_box.markdown(
        f"<div class='today{' urgent' if urgent else ''}'><div class='brief'>{html.escape(line)}<span class='src'>{source}</span></div>"
        + (f"<ul>{items}</ul>" if items else "") + (f"<div class='note'>{html.escape(note)}</div>" if note else "") + "</div>",
        unsafe_allow_html=True)


render_today(briefs.get(state.day) or rules_line, "AI brief" if briefs.get(state.day) else "rules",
             "Writing the AI brief…" if want_ai else "")

if mode != PICK and new_day and not first_view:
    urgent = [a for a in alerts if a["level"] == "act"]
    for a in urgent[:3]:
        st.toast(a["text"])
    if sound and urgent:
        with st.container(key="chime"):
            st.audio(alert_sound(), format="audio/wav", autoplay=True)

fmt_m = lambda x: "—" if x is None else f"€{x / 1e6:,.1f}M"
use = snap["factory_use_3d"]
cards = [
    ("Valuation", fmt_m(snap["valuation"]), "our score", False),
    ("Profit", fmt_m(snap["profit"]), "so far", False),
    ("Rating", snap["rating"] or "—", f"debt loading {fmt_m(snap['debt_loading'])}", (snap["rating"] or "AAA+") != "AAA+"),
    ("Market share", f"{snap['market_share_last_week']:.0%}", "last full week", False),
    ("Sold per day", f"{snap['sold_per_day_3d']:,}", "3-day average", False),
    ("Made per day", f"{snap['made_per_day_3d']:,}", "3-day average", False),
    ("Factory use", "—" if use is None else f"{use:.0%}", "last 3 days · winners 94–98%", use is not None and use < 0.9 and snap["phase"] == "running"),
    ("Storage cost", f"€{snap['storage_cost_per_day']:,.0f}", "per day", snap["storage_cost_per_day"] > 0),
]
st.markdown('<div class="kpis">' + "".join(
    f'<div class="kpi{" warn" if warn else ""}"><div class="l">{label}</div><div class="v">{value}</div><div class="s">{sub}</div></div>'
    for label, value, sub, warn in cards) + "</div>", unsafe_allow_html=True)

fg, (lo, tgt, hi) = snap["finished_goods"], snap["gauge_band"]
pct = min(1.0, fg / (hi * 1.5))
colour = {"IN BAND": "#2E6B5B", "TOO HIGH": "#B42318", "TOO LOW": "#B54708",
          "TOO MUCH FOR DAYS LEFT": "#B42318", "ON TRACK TO SELL OUT": "#2E6B5B"}.get(snap["finished_goods_gauge"], "#475467")
st.markdown(
    f"""<div style="margin:.4rem 0 1rem 0">
    <div style="font-weight:600">Finished goods: {fg:,.0f} boxes · <span style="color:{colour}">{snap['finished_goods_gauge']}</span>
    <span style="color:#667085;font-weight:400"> (band {lo/1000:.0f}k–{hi/1000:.0f}k)</span></div>
    <div style="position:relative;height:14px;background:linear-gradient(90deg,#FEF0C7 0 {lo/(hi*1.5)*100:.0f}%,#D1FADF {lo/(hi*1.5)*100:.0f}% {hi/(hi*1.5)*100:.0f}%,#FEE4E2 {hi/(hi*1.5)*100:.0f}% 100%);border-radius:7px">
    <div style="position:absolute;left:{pct*100:.1f}%;top:-3px;width:4px;height:20px;background:{colour};border-radius:2px"></div></div></div>""",
    unsafe_allow_html=True)

# ---------------------------------------------------------------- tabs
t_ask, t_make, t_price, t_stock, t_money, t_mkt = st.tabs(["Ask", "Make", "Prices", "Stock", "Money", "Marketing"])

with t_make:
    (st.error if plan.get("production_brake") else st.subheader)(plan["action"])
    if plan.get("warning"):
        st.warning(plan["warning"])
    st.caption(plan["forecast_rule"] + (" Today is a checkpoint day." if plan["checkpoint_day"] else ""))
    st.dataframe(pd.DataFrame(plan["per_product"]), hide_index=True, use_container_width=True)
    mats = state.materials(plan["next_product"]["product"] if plan["next_product"] else None)
    st.dataframe(mats, hide_index=True, use_container_width=True)

with t_price:
    st.caption(pa["rule"] + f"  Costs from: {snap['costs_from']}.")
    rows = [{"code": p["code"], "product": p["product"], "signal": p["signal"],
             "move €": p["move"], "type in VK32": p["new_prices"], "every day?": "yes" if p.get("cut_every_day") else "",
             "why": p["why"], "stock": p["stock"],
             "days of stock": p["days_of_stock"], "rivals €": p["rivals_avg_price"], "model best €": p["model_best_price"],
             "price sensitivity": p["price_model"]["sensitivity"], "model quality": p["price_model"]["quality"],
             "cost/box €": p.get("cost_per_box"), "flags": " ".join(p["flags"])} for p in pa["products"]]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

with t_stock:
    inv = engine.inventory_advice(state)
    for a in inv["actions"]:
        st.write("• " + a)
    st.dataframe(pd.DataFrame(inv["by_product"]), hide_index=True, use_container_width=True)
    st.caption(inv["note"])

with t_money:
    amt = st.number_input("Amount to compare (€)", 250_000, 5_000_000, 1_000_000, step=250_000)
    cl = engine.capacity_vs_loan(state, amt)
    st.subheader(cl["verdict"])
    st.json({k: v for k, v in cl.items() if k != "verdict"}, expanded=False)

with t_mkt:
    mk = engine.marketing_check(state)
    st.dataframe(pd.DataFrame(mk["regions"]), hide_index=True, use_container_width=True)
    if mk["spend_last_week"]:
        st.write("Marketing spend last week:", mk["spend_last_week"])
    st.caption(mk["how_to_test"])

with t_ask:
    if not S.has_chat_key():
        st.info(f"To chat, add your free key to .env as {S.chat_key_name()}= (see README). Every other tab works without it.")
    else:
        if "bot" not in ss:
            from copilot.agent import Copilot
            ss.bot = Copilot(S, current_state, load_notes(S.memory_dir), load_history(S.history_dir, S))
            ss.chat = []
        ss.bot.get_state = current_state           # always this page's source (live, practice or saved game)
        st.caption(f"AI: {S.provider} · {S.chat_model()}"
                   + (" · free tier: a few questions per minute, so ask during breaks or between game days" if S.provider == "gemini" else ""))
        quick = st.columns(4)
        presets = [("Make next?", "What should we produce next, and how much?"),
                   ("Too much stock?", "We have too much stock: what about prices and marketing?"),
                   ("Price changes?", "Which prices should we change right now?"),
                   ("Capacity or loan?", "Capacity or pay down the loan?")]
        clicked = next((full for (label, full), col in zip(presets, quick)
                        if col.button(label, use_container_width=True, help=full)), None)
        q = st.chat_input("Ask about the game") or clicked
        if q:
            with st.spinner("Reading the game..."):
                res = ss.bot.ask(q)
            ss.chat.append((q, res["answer"], res["tool_calls"]))
        for q_, a_, calls in reversed(ss.chat[-8:]):
            st.chat_message("user").write(q_)
            st.chat_message("assistant").write(a_ + (f"\n\n*used: {', '.join(calls)}*" if calls else ""))

# ---------------------------------------------------------------- the AI brief comes last, so the numbers show first
if want_ai:
    from copilot.agent import daily_brief

    line, why = daily_brief(S, engine.brief_facts(state, snap, alerts))
    briefs[state.day] = line                       # None = tried; don't retry this day
    render_today(line or rules_line, "AI brief" if line else "rules", "" if line else f"AI brief skipped: {why}.")
