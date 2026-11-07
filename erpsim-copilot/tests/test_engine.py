"""Run:  pytest -q"""
import pandas as pd

from copilot import engine
from copilot.config import Settings
from copilot.model import GameState

MIX, NUT = "1kg Mixed Fruit Muesli", "1kg Nut Muesli"


def frames(days=20, mix_stock=None, nut_stock=None, mix_sales=7000, nut_sales=1000):
    mix_stock = mix_stock or (lambda d: 40000)
    nut_stock = nut_stock or (lambda d: 3000)
    inv, sales, prod, val, mkt = [], [], [], [], []
    for d in range(1, days + 1):
        inv += [{"SIM_ELAPSED_STEPS": d, "MATERIAL_NUMBER": "EE-F16", "MATERIAL_DESCRIPTION": MIX, "INVENTORY_OPENING_BALANCE": mix_stock(d)},
                {"SIM_ELAPSED_STEPS": d, "MATERIAL_NUMBER": "EE-F11", "MATERIAL_DESCRIPTION": NUT, "INVENTORY_OPENING_BALANCE": nut_stock(d)},
                {"SIM_ELAPSED_STEPS": d, "MATERIAL_NUMBER": "EE-R06", "MATERIAL_DESCRIPTION": "Oats", "INVENTORY_OPENING_BALANCE": 200000}]
        for name, q, p in ((MIX, mix_sales, 8.0), (NUT, nut_sales, 7.0)):
            sales.append({"SIM_ELAPSED_STEPS": d, "SIM_PERIOD": (d - 1) // 5 + 1, "SIM_ROUND": 1, "MATERIAL_DESCRIPTION": name,
                          "DISTRIBUTION_CHANNEL": 12, "AREA": "North", "QUANTITY": q, "NET_VALUE": q * p, "NET_PRICE": p})
        prod.append({"SIM_ELAPSED_STEPS": d, "MATERIAL_DESCRIPTION": MIX, "YIELD": 24000 if d > 2 else 0})
        val.append({"SIM_ELAPSED_STEPS": d, "COMPANY_VALUATION": 1e8, "PROFIT": 1e5 * d, "CREDIT_RATING": "A",
                    "DEBT_LOADING": -5e6 + 5e4 * d, "BANK_CASH_ACCOUNT": 2e6, "BANK_LOAN": -8e6, "COMPANY_RISK_RATE_PCT": 5.0})
    for w in range(1, days // 5 + 1):
        mkt += [{"SIM_PERIOD": w, "MATERIAL_DESCRIPTION": MIX, "DISTRIBUTION_CHANNEL": 12, "AREA": "North", "QUANTITY": 80000, "NET_VALUE": 80000 * 7.5},
                {"SIM_PERIOD": w, "MATERIAL_DESCRIPTION": NUT, "DISTRIBUTION_CHANNEL": 12, "AREA": "North", "QUANTITY": 20000, "NET_VALUE": 20000 * 7.0}]
    return {"Inventory": pd.DataFrame(inv), "Sales": pd.DataFrame(sales), "Production": pd.DataFrame(prod),
            "Company_Valuation": pd.DataFrame(val), "Market": pd.DataFrame(mkt),
            "Production_Orders": pd.DataFrame(columns=["SIM_ELAPSED_STEPS", "MATERIAL_DESCRIPTION", "TARGET_QUANTITY", "CONFIRMED_QUANTITY",
                                                       "BEGIN_ROUND", "BEGIN_STEP", "END_ROUND", "END_STEP"]),
            "Purchase_Orders": pd.DataFrame(columns=["SIM_ELAPSED_STEPS", "MATERIAL_DESCRIPTION", "QUANTITY", "STATUS",
                                                     "GOODS_RECEIPT_ROUND", "GOODS_RECEIPT_STEP"]),
            "Current_Pricing_Conditions": pd.DataFrame([{"MATERIAL_DESCRIPTION": MIX, "DISTRIBUTION_CHANNEL": 12, "PRICE": 8.0},
                                                        {"MATERIAL_DESCRIPTION": MIX, "DISTRIBUTION_CHANNEL": 10, "PRICE": 8.5},
                                                        {"MATERIAL_DESCRIPTION": NUT, "DISTRIBUTION_CHANNEL": 12, "PRICE": 7.0}]),
            "Independent_Requirements": pd.DataFrame(columns=["SIM_ELAPSED_STEPS", "MATERIAL_NUMBER", "QUANTITY"])}


def test_forecast_number_is_whole_batches():
    st = GameState(frames(), Settings())
    plan = engine.production_plan(st)
    for row in plan["per_product"]:
        if row["md61_number"]:
            extra = row["md61_number"] - row["stock"] - row["queued"]
            assert extra % 48000 == 0 and extra > 0


def test_no_cut_when_slow_only_because_stock_is_low():
    st = GameState(frames(), Settings())
    nut = [p for p in engine.price_advice(st)["products"] if p["product"] == NUT][0]
    assert nut["signal"] != "LOWER"


def test_no_raise_right_after_a_fresh_batch():
    # ran out on day 18, a 48,000 batch landed on day 20; sells 5,000/day -> ~10 days of stock
    st = GameState(frames(mix_stock=lambda d: 0 if d in (18, 19) else (48000 if d == 20 else 20000), mix_sales=5000), Settings())
    mix = [p for p in engine.price_advice(st)["products"] if p["product"] == MIX][0]
    assert mix["signal"] != "RAISE"


def test_dc10_above_dc12_is_flagged_and_new_dc10_stays_below():
    st = GameState(frames(mix_stock=lambda d: 70000, mix_sales=2000), Settings())
    mix = [p for p in engine.price_advice(st)["products"] if p["product"] == MIX][0]
    assert any("DC10" in f for f in mix["flags"])
    if mix["new_prices"].get("10"):
        assert mix["new_prices"]["10"] < mix["new_prices"]["12"]


def test_gauge_reads_low_stock():
    st = GameState(frames(), Settings())
    assert engine.snapshot(st)["finished_goods_gauge"] == "TOO LOW"


def test_every_tool_runs():
    st = GameState(frames(), Settings())
    for name, (fn, _) in engine.TOOLS.items():
        assert isinstance(fn(st), dict), name


# ---------------------------------------------------------------- fixes (Nov 8)
def _with(fr, **extra):
    fr = dict(fr); fr.update(extra); return fr


def test_undercut_cuts_every_day_toward_rivals():
    fr = frames(mix_stock=lambda d: 30000 + 2000 * d)            # stock rising
    m = fr["Market"].copy()
    m.loc[m.MATERIAL_DESCRIPTION == MIX, "NET_VALUE"] = m.loc[m.MATERIAL_DESCRIPTION == MIX, "QUANTITY"] * 6.2
    st = GameState(_with(fr, Market=m), Settings())
    mix = [p for p in engine.price_advice(st)["products"] if p["product"] == MIX][0]
    assert mix["signal"] == "LOWER" and mix["cut_every_day"] and mix["move"] <= -0.75


def test_production_brake_pauses_the_line():
    st = GameState(frames(mix_stock=lambda d: 230000, nut_stock=lambda d: 40000, mix_sales=4000, nut_sales=800), Settings())
    plan = engine.production_plan(st)
    assert plan["production_brake"] and plan["action"].startswith("PAUSE")


def test_game_over_on_last_day():
    st = GameState(frames(days=80), Settings())
    assert engine.snapshot(st)["phase"] == "game over"
    assert all(p["signal"] == "GAME OVER" for p in engine.price_advice(st)["products"])
    assert engine.production_plan(st)["action"].startswith("Game over")


def test_new_dc10_never_below_floor():
    st = GameState(frames(mix_stock=lambda d: 70000, mix_sales=2000), Settings())
    mix = [p for p in engine.price_advice(st)["products"] if p["product"] == MIX][0]
    floor = Settings().floor(MIX)
    for ch, price in mix["new_prices"].items():
        assert price >= floor - 1e-9, (ch, price, floor)


def test_costs_come_from_game_supplier_prices():
    prices = {"Wheat": 1.12, "Oats": 0.65, "Nuts": 2.13, "Blueberries": 4.65, "Strawberries": 5.40, "Raisins": 0.87,
              "Large Box (1kg)": 0.33, "Large Bag (1kg)": 0.16, "Small Box (500g)": 0.25, "Small Bag (500g)": 0.14}
    sp = pd.DataFrame([{"VENDOR": "V01", "MATERIAL_DESCRIPTION": k, "GROSS_PRICE": v} for k, v in prices.items()])
    s = Settings()
    st = GameState(_with(frames(), Current_Suppliers_Prices=sp), s)
    expected = round(sum(q * prices[m] for m, q in s.recipe(MIX).items()), 2)
    row = st.products().set_index("product").loc[MIX]
    assert row.cost_per_box == expected and row.cost_source == "game supplier prices"


def test_text_numbers_are_cleaned():
    from copilot.odata import clean
    df = pd.DataFrame({"QUANTITY": ["5", "", "7.5"], "MATERIAL_DESCRIPTION": ["a", "b", "c"]})
    out = clean(df)
    assert out.QUANTITY.tolist() == [5.0, 0.0, 7.5] and out.MATERIAL_DESCRIPTION.tolist() == ["a", "b", "c"]
