"""Live mode: heartbeat, new-day-only downloads, alerts and the daily brief.  Run:  pytest -q"""
import json

import pandas as pd
import pytest
import requests

from copilot import engine
from copilot.config import Settings
from copilot.live import CURRENT, DAILY, LiveFeed, ReplayServer, alert_sound
from copilot.model import GameState
from copilot.odata import GameDay, ODataSource, cut_to_day, day_filter, query_string
from test_engine import MIX, NUT, _with, frames

TABLES = DAILY[:4] + CURRENT[:4] + ["Market"]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def game(days=30):
    """A small game with the columns the real feed has (round, step, elapsed day, week)."""
    def when(d):
        return {"SIM_ROUND": (d - 1) // 20 + 1, "SIM_STEP": (d - 1) % 20 + 1, "SIM_PERIOD": (d - 1) // 5 + 1, "SIM_ELAPSED_STEPS": d}
    cv, sales, inv, prod, po, pu, ir, mkt = [], [], [], [], [], [], [], []
    for d in range(1, days + 1):
        cv.append({**when(d), "COMPANY_VALUATION": 1e8 + d, "PROFIT": 1e5 * d, "CREDIT_RATING": "AAA+", "DEBT_LOADING": 0,
                   "BANK_CASH_ACCOUNT": 1e6, "BANK_LOAN": 0, "COMPANY_RISK_RATE_PCT": 5.0})
        for name, q in ((MIX, 6000 + d), (NUT, 1000)):
            sales.append({**when(d), "MATERIAL_DESCRIPTION": name, "AREA": "North", "DISTRIBUTION_CHANNEL": 12,
                          "QUANTITY": q, "NET_VALUE": q * 8.0, "NET_PRICE": 8.0})
            inv.append({**when(d), "MATERIAL_NUMBER": "EE-F16" if name == MIX else "EE-F11", "MATERIAL_DESCRIPTION": name,
                        "INVENTORY_OPENING_BALANCE": 50000 - 100 * d})
        prod.append({**when(d), "MATERIAL_DESCRIPTION": MIX, "YIELD": 24000 if d > 2 else 0})
        if d % 4 == 0:
            po.append({**when(d), "MATERIAL_DESCRIPTION": MIX, "TARGET_QUANTITY": 48000, "CONFIRMED_QUANTITY": 48000,
                       "BEGIN_ROUND": 1, "BEGIN_STEP": d, "END_ROUND": 1, "END_STEP": d + 2})
            pu.append({**when(d), "MATERIAL_DESCRIPTION": "Oats", "QUANTITY": 100000, "STATUS": "Delivered",
                       "GOODS_RECEIPT_ROUND": 1, "GOODS_RECEIPT_STEP": d + 1})
            ir.append({**when(d), "MATERIAL_NUMBER": "EE-F16", "QUANTITY": 48000})
    for w in range(1, days // 5 + 1):
        mkt.append({"SIM_ROUND": (w - 1) // 4 + 1, "SIM_PERIOD": w, "MATERIAL_DESCRIPTION": MIX, "DISTRIBUTION_CHANNEL": 12,
                    "AREA": "North", "QUANTITY": 90000, "NET_VALUE": 90000 * 7.5})
    return {"Company_Valuation": pd.DataFrame(cv), "Sales": pd.DataFrame(sales), "Inventory": pd.DataFrame(inv),
            "Production": pd.DataFrame(prod), "Production_Orders": pd.DataFrame(po), "Purchase_Orders": pd.DataFrame(pu),
            "Independent_Requirements": pd.DataFrame(ir), "Market": pd.DataFrame(mkt),
            "Current_Pricing_Conditions": pd.DataFrame([{"MATERIAL_DESCRIPTION": MIX, "DISTRIBUTION_CHANNEL": 12, "PRICE": 8.0}]),
            "Marketing_Expenses": pd.DataFrame(), "Carbon_Emissions": pd.DataFrame(), "Current_Suppliers_Prices": pd.DataFrame()}


def same_rows(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    if a is None or b is None or a.empty or b.empty:
        return (a is None or a.empty) and (b is None or b.empty)
    cols = sorted(a.columns)
    norm = lambda df: df[cols].sort_values(cols).reset_index(drop=True)
    return set(a.columns) == set(b.columns) and norm(a).equals(norm(b))


class SpyServer(ReplayServer):
    """Records which tables were downloaded whole."""
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.whole = []

    def fetch(self, entity):
        self.whole.append(entity)
        return super().fetch(entity)


# ---------------------------------------------------------------- the feed
def test_new_day_downloads_only_new_rows_and_matches_a_full_copy():
    clock, full = Clock(), game(30)
    server = SpyServer(full, start_day=10, seconds_per_day=10, clock=clock)
    feed = LiveFeed(server, clock=clock)
    assert feed.check() and feed.day == 10 and feed.stats["kind"] == "full download"
    clock.t += 35                                     # 3 game days later
    assert feed.check() and feed.day == 13 and feed.stats["kind"] == "new day"
    expected = cut_to_day(full, 13)
    for t in TABLES:
        assert same_rows(feed.frames[t], expected[t]), t
    for t in DAILY:                                   # daily tables came day by day, never whole
        assert t not in server.whole, t
    assert feed.stats["bytes"] < feed.stats["full_bytes"]


def test_market_is_read_only_when_a_new_week_is_due():
    clock, full = Clock(), game(30)
    server = SpyServer(full, start_day=11, seconds_per_day=10, clock=clock)
    feed = LiveFeed(server, clock=clock)
    feed.check()
    clock.t += 10                                     # day 12: week 2 is already in, nothing new due
    feed.check()
    assert "Market" not in server.whole
    clock.t += 30                                     # day 15: week 3's numbers are due
    feed.check()
    assert server.whole.count("Market") == 1
    assert int(feed.frames["Market"].SIM_PERIOD.max()) == 3


def test_heartbeat_is_throttled():
    clock = Clock()
    server = ReplayServer(game(20), start_day=5, seconds_per_day=10, clock=clock)
    feed = LiveFeed(server, clock=clock)
    feed.check()
    asked = server.requests
    clock.t += 2
    assert feed.check(min_interval=5) is False and server.requests == asked     # too soon: no request at all
    clock.t += 4
    assert feed.check(min_interval=5) is False and server.requests == asked + 1  # asked, same day


def test_a_new_game_on_the_same_client_reloads_everything():
    clock = Clock()
    server = ReplayServer(game(30), start_day=20, seconds_per_day=10, clock=clock)
    feed = LiveFeed(server, clock=clock)
    feed.check()
    server.start_day, server.t0 = 1, clock.t          # the moderator starts a new game
    assert feed.check() and feed.day == 1 and feed.stats["kind"] == "full download"
    assert int(feed.frames["Sales"].SIM_ELAPSED_STEPS.max()) == 1


def test_many_missed_days_reload_everything():
    clock = Clock()
    feed = LiveFeed(ReplayServer(game(40), start_day=5, seconds_per_day=10, clock=clock), clock=clock)
    feed.check()
    clock.t += 200                                    # laptop asleep for 20 game days
    assert feed.check() and feed.day == 25 and feed.stats["kind"] == "full download"


def test_a_refused_filter_falls_back_to_the_whole_table():
    class NoFilter(ReplayServer):
        def fetch_day(self, entity, day):
            if entity == "Production":
                resp = requests.Response()
                resp.status_code = 400
                raise requests.HTTPError("400 Bad Request", response=resp)
            return super().fetch_day(entity, day)

    clock, full = Clock(), game(20)
    feed = LiveFeed(NoFilter(full, start_day=5, seconds_per_day=10, clock=clock), clock=clock)
    feed.check()
    clock.t += 20
    assert feed.check() and "Production" in feed.full_tables
    assert same_rows(feed.frames["Production"], cut_to_day(full, 7)["Production"])


def test_connection_problems_keep_the_last_data():
    clock = Clock()
    server = ReplayServer(game(20), start_day=5, seconds_per_day=10, clock=clock)
    feed = LiveFeed(server, clock=clock)
    feed.check()
    before = feed.frames

    def down():
        raise requests.ConnectionError("server unreachable")
    server.latest = down
    clock.t += 30
    with pytest.raises(requests.ConnectionError):
        feed.check()
    assert feed.frames is before and feed.day == 5 and "unreachable" in feed.error


def test_practice_state_matches_a_replay_of_the_same_day():
    clock, full = Clock(), game(30)
    feed = LiveFeed(ReplayServer(full, start_day=12, seconds_per_day=10, clock=clock), clock=clock)
    feed.check()
    clock.t += 40
    feed.check()
    live = engine.snapshot(GameState(feed.frames, Settings(), replay_day=feed.day))
    replay = engine.snapshot(GameState(cut_to_day(full, 16), Settings(), replay_day=16))
    assert live == replay


# ---------------------------------------------------------------- the OData questions
class FakeResponse:
    def __init__(self, payload, status=200):
        self.status_code, self._payload = status, payload
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)


def test_query_string_writes_spaces_as_percent_20():
    q = query_string({"$filter": "SIM_ROUND eq '04' and SIM_STEP eq '20'", "$orderby": "SIM_ROUND desc,SIM_STEP desc"})
    assert q == "$filter=SIM_ROUND%20eq%20'04'%20and%20SIM_STEP%20eq%20'20'&$orderby=SIM_ROUND%20desc,SIM_STEP%20desc"


def test_day_filter_quotes_text_exactly_as_sent():
    assert day_filter(GameDay(80, "04", "20")) == "SIM_ROUND eq '04' and SIM_STEP eq '20'"
    assert day_filter(GameDay(80, 4, 20)) == "SIM_ROUND eq 4 and SIM_STEP eq 20"


def test_odata_heartbeat_and_one_day_of_sales(monkeypatch):
    src = ODataSource("https://example.test/odata/418", "u", "p")
    seen = []
    row = {"SIM_ROUND": "04", "SIM_STEP": "20", "SIM_ELAPSED_STEPS": 80, "COMPANY_VALUATION": "217668288.00"}
    sale = {"__metadata": {}, "SIM_ROUND": "04", "SIM_STEP": "20", "SIM_ELAPSED_STEPS": 80, "QUANTITY": "4485", "NET_VALUE": "26865.15"}

    def get(url, timeout=None):
        seen.append(url)
        return FakeResponse({"d": {"results": [row] if "Company_Valuation" in url else [sale]}})
    monkeypatch.setattr(src.session, "get", get)
    day = src.latest()
    assert day == GameDay(80, "04", "20")
    assert "$top=1&$orderby=SIM_ROUND%20desc,SIM_STEP%20desc" in seen[-1] and " " not in seen[-1]
    df = src.fetch_day("Sales", day)
    assert "$filter=SIM_ROUND%20eq%20'04'%20and%20SIM_STEP%20eq%20'20'" in seen[-1]
    assert df.QUANTITY.tolist() == [4485.0] and "__metadata" not in df and src.requests == 2


def test_missing_table_is_remembered(monkeypatch):
    src = ODataSource("https://example.test/odata/418", "u", "p")
    monkeypatch.setattr(src.session, "get", lambda url, timeout=None: FakeResponse({}, status=404))
    assert src.fetch("Carbon_Emissions").empty and "Carbon_Emissions" in src.missing


# ---------------------------------------------------------------- alerts and brief
def test_undercut_and_forecast_day_make_the_list():
    fr = frames(days=16, mix_stock=lambda d: 30000 + 2000 * d)      # stock rising; day 16 is a forecast day
    m = fr["Market"].copy()
    m.loc[m.MATERIAL_DESCRIPTION == MIX, "NET_VALUE"] = m.loc[m.MATERIAL_DESCRIPTION == MIX, "QUANTITY"] * 6.2
    st = GameState(_with(fr, Market=m), Settings())
    alerts = engine.daily_alerts(st)
    keys = {a["key"]: a for a in alerts}
    assert keys["undercut:EE-F16"]["level"] == "act" and "VK32" in keys["undercut:EE-F16"]["text"]
    assert keys["forecast"]["level"] == "do"
    assert [a["level"] for a in alerts] == sorted((a["level"] for a in alerts), key=engine.LEVELS.get)
    assert engine.brief_line(alerts, st.day).startswith("Day 16: cut EE-F16 to €")


def test_stock_out_within_two_days_with_nothing_queued():
    st = GameState(frames(mix_stock=lambda d: 9000, mix_sales=7000), Settings())   # ~1.3 days left
    alert = next(a for a in engine.daily_alerts(st) if a["key"] == "stockout:EE-F16")
    assert alert["level"] == "act" and "CO41" in alert["text"]


def test_game_over_has_one_quiet_line():
    st = GameState(frames(days=80), Settings())
    assert [a["key"] for a in engine.daily_alerts(st)] == ["game_over"]


def test_daily_brief_is_one_call(monkeypatch):
    from google import genai

    from copilot.agent import daily_brief
    calls = []

    class Models:
        def generate_content(self, model, contents, config):
            calls.append(contents)
            return type("R", (), {"text": "Day 16: cut EE-F16 to €7.00 in VK32;\n MD61 forecast day."})()

    monkeypatch.setattr(genai, "Client", lambda: type("C", (), {"models": Models()})())
    line, why = daily_brief(Settings(), {"day": 16, "to_do": ["[act] UNDERCUT ..."]})
    assert line == "Day 16: cut EE-F16 to €7.00 in VK32; MD61 forecast day." and not why and len(calls) == 1

    class Busy:
        def generate_content(self, **kw):
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
    monkeypatch.setattr(genai, "Client", lambda: type("C", (), {"models": Busy()})())
    assert daily_brief(Settings(), {"day": 16})[0] is None


def test_alert_sound_is_a_wav():
    assert alert_sound()[:4] == b"RIFF"
