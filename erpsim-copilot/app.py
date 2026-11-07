"""ERPsim Co-pilot web app.  Run:  streamlit run app.py"""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from copilot import engine
from copilot.config import load_settings
from copilot.memory import load_history, load_notes
from copilot.model import GameState
from copilot.odata import ExcelSource, ODataSource, cut_to_day

st.set_page_config(page_title="ERPsim Co-pilot", layout="wide")
S = load_settings()

# ---------------------------------------------------------------- data source
with st.sidebar:
    st.subheader("Game data")
    mode = st.radio("Source", ["Live OData feed", "Saved game (replay)"], label_visibility="collapsed")
    if mode == "Live OData feed":
        url = st.text_input("OData URL", S.odata_url, placeholder="https://<server>/odata/<client>")
        user = st.text_input("ERPsim user", S.odata_user)
        pwd = st.text_input("Password", S.odata_password, type="password")
        every = st.slider("Refresh every (seconds)", 15, 120, 30)
        replay_file, day = None, None
    else:
        replay_file = st.file_uploader("Workbook with one sheet per OData table", type=["xlsx"])
        day = st.slider("Replay day", 1, 80, 40)
        every = None
    if st.button("Refresh now"):
        st.cache_data.clear()
    S.teams_active = st.number_input("Teams playing", 1, 20, int(S.teams_active))
    S.rules.capacity = st.number_input("Factory capacity (boxes/day)", 10000, 60000, int(S.rules.capacity), step=1000)

if every:
    try:  # optional auto refresh
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=every * 1000, key="tick")
    except ImportError:
        pass


@st.cache_data(ttl=25, show_spinner=False)
def live_frames(u, us, pw):
    return ODataSource(u, us, pw).load()


@st.cache_data(show_spinner=False)
def file_frames(data: bytes):
    import io
    return ExcelSource(io.BytesIO(data)).load()


def save_game(frames: dict, path):
    """Write every table to one workbook (one sheet per table): the format replay and history read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        for table, df in frames.items():
            if df is not None and not df.empty:
                df.to_excel(xl, sheet_name=table[:31], index=False)
    return path


def get_state() -> GameState:
    if mode == "Live OData feed":
        return GameState(live_frames(url, user, pwd), S)
    frames = file_frames(replay_file.getvalue())
    return GameState(cut_to_day(frames, day), S, replay_day=day)


ready = (mode == "Live OData feed" and url and user and pwd) or (mode != "Live OData feed" and replay_file)
if not ready:
    st.title("ERPsim Co-pilot")
    st.write("Connect the live feed (sidebar) or load a saved game to start.")
    st.stop()
try:
    state = get_state()
except Exception as e:
    st.error(f"Could not read the game data: {e}")
    st.stop()

snap = engine.snapshot(state)

# ---------------------------------------------------------------- save a live game for later (history/)
if mode == "Live OData feed":
    with st.sidebar:
        st.divider()
        name = st.text_input("Save as", f"game_{pd.Timestamp.now():%Y-%m-%d}", help="Saved into history/ so the co-pilot remembers this game")
        if st.button("Save this game to history"):
            path = save_game(live_frames(url, user, pwd), S.history_dir / f"{name}.xlsx")
            st.success(f"Saved {path.name} (day {snap['day']}).")

# ---------------------------------------------------------------- header + gauge
st.markdown(f"### Day {snap['day']} of {S.rules.total_days} — round {snap['round']}, day {snap['day_in_round']}"
            f"  \nTeam code **{snap['team_code']}** · phase: {snap['phase']}")
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
st.markdown("""<style>
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.55rem;margin:.3rem 0 1rem 0}
.kpi{padding:.5rem .75rem;border:1px solid #E4E7EC;border-left:4px solid #2E6B5B;border-radius:10px;background:#FFFFFF;min-width:0}
.kpi.warn{border-left-color:#B54708}
.kpi .l{font-size:.78rem;color:#475467;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kpi .v{font-size:clamp(1.05rem,1.8vw,1.55rem);font-weight:600;color:#1C2B33;line-height:1.3;white-space:nowrap}
.kpi .s{font-size:.72rem;color:#667085;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
</style>""" + '<div class="kpis">' + "".join(
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
alerts = [k.replace("_", " ") for k, v in snap["alerts"].items() if v]
if alerts:
    st.warning("Check: " + "; ".join(alerts))

# ---------------------------------------------------------------- tabs
t_ask, t_make, t_price, t_stock, t_money, t_mkt = st.tabs(["Ask", "Make", "Prices", "Stock", "Money", "Marketing"])

with t_make:
    plan = engine.production_plan(state)
    (st.error if plan.get("production_brake") else st.subheader)(plan["action"])
    if plan.get("warning"):
        st.warning(plan["warning"])
    st.caption(plan["forecast_rule"] + (" Today is a checkpoint day." if plan["checkpoint_day"] else ""))
    st.dataframe(pd.DataFrame(plan["per_product"]), hide_index=True, use_container_width=True)
    mats = state.materials(plan["next_product"]["product"] if plan["next_product"] else None)
    st.dataframe(mats, hide_index=True, use_container_width=True)

with t_price:
    pa = engine.price_advice(state)
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
        if "bot" not in st.session_state:
            from copilot.agent import Copilot
            st.session_state.bot = Copilot(S, get_state, load_notes(S.memory_dir), load_history(S.history_dir, S))
            st.session_state.chat = []
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
                res = st.session_state.bot.ask(q)
            st.session_state.chat.append((q, res["answer"], res["tool_calls"]))
        for q_, a_, calls in reversed(st.session_state.chat[-8:]):
            st.chat_message("user").write(q_)
            st.chat_message("assistant").write(a_ + (f"\n\n*used: {', '.join(calls)}*" if calls else ""))
