"""The co-pilot's calculator. Deterministic rules; the chat agent explains and combines them.

Every function returns a plain dict (JSON-safe) so the agent can read it as a tool result.
"""
from __future__ import annotations

import math

import pandas as pd

from .elasticity import best_price, price_response
from .model import GameState


def _py(x):
    if isinstance(x, dict):
        return {str(k): _py(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_py(v) for v in x]
    if hasattr(x, "item"):
        x = x.item()
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x


def _q(x: float) -> float:
    """Round to the nearest €0.25."""
    return round(round(x * 4) / 4, 2)


# ------------------------------------------------------------------ snapshot
def snapshot(st: GameState) -> dict:
    r, v = st.r, st.valuation_row()
    fg = st.finished_goods()
    days = max(1, min(3, st.day))
    made3, sold3 = st.made(days=3), st.sold(days=3)
    over = st.day >= r.total_days
    running = st.products_running()
    if over:
        gauge = "GAME OVER"
    elif st.day >= r.sell_down_from:
        left = sum(max(0.0, x.stock - x.sold_per_day * max(0, r.total_days - st.day)) for x in st.products().itertuples())
        gauge = "TOO MUCH FOR DAYS LEFT" if left > 5000 else "ON TRACK TO SELL OUT"
    else:
        gauge = "START-UP" if st.startup() else ("TOO LOW" if fg < r.fg_low else ("TOO HIGH" if fg > r.fg_high else "IN BAND"))
    po = st.f.get("Production_Orders", pd.DataFrame())
    small = late = 0
    if not po.empty:
        po = po[po.SIM_ELAPSED_STEPS <= st.day]
        open_ = po.TARGET_QUANTITY > po.CONFIRMED_QUANTITY
        small = int(((po.TARGET_QUANTITY < r.batch) & open_).sum())
        late = int((((po.BEGIN_ROUND - 1) * 20 + po.BEGIN_STEP > r.last_convert_day) & open_).sum())
    return _py({
        "day": st.day, "round": st.round, "day_in_round": st.day_in_round, "team_code": st.team_prefix,
        "phase": "game over" if over else ("start-up" if st.startup() else ("sell-down" if st.day >= r.sell_down_from else ("round 4 finish" if st.day > 60 else "running"))),
        "valuation": v.get("COMPANY_VALUATION"), "profit": v.get("PROFIT"), "rating": v.get("CREDIT_RATING"),
        "debt_loading": v.get("DEBT_LOADING"), "cash": v.get("BANK_CASH_ACCOUNT"), "loan": v.get("BANK_LOAN"),
        "market_share_last_week": round(st.market_share(), 3),
        "factory_use_3d": round(made3 / (days * r.capacity), 2) if not st.startup() else None,
        "sold_per_day_3d": round(sold3 / days), "made_per_day_3d": round(made3 / days),
        "finished_goods": round(fg), "finished_goods_gauge": gauge, "gauge_band": [r.fg_low, r.fg_target, r.fg_high],
        "storage_cost_per_day": st.storage_cost_per_day(),
        "products_running": len(running), "costs_from": "game supplier prices" if st.supplier_prices() else "config.yaml",
        "alerts": {"small_batches_queued": small, "batches_after_last_conversion_day": late,
                   "more_products_than_max": len(running) if len(running) > r.max_products else 0,
                   "factory_idle_yesterday": bool(not st.startup() and st.day <= r.last_convert_day + 2 and st.made(upto=st.day - 1) == 0)},
    })


# ------------------------------------------------------------------ prices
def price_advice(st: GameState, product: str | None = None) -> dict:
    r = st.r
    tab = st.products()
    fg = st.finished_goods()
    shares = st.mix_shares()
    out = []
    for x in tab.itertuples():
        if product and product.lower() not in (x.product.lower(), str(x.code).lower()):
            continue
        if not x.active and not product:
            continue
        if st.replay:   # the saved price list is the end-of-game one, so use what we actually charged
            cur = x.price_got_3d or st.price_last(x.product) or x.prices.get(12) or 0.0
        else:
            cur = x.prices.get(12) or x.price_got_3d or st.price_last(x.product) or 0.0
        gap = (cur - x.rivals_avg_price) if (x.rivals_avg_price and cur) else 0.0
        undercut = (x.stock >= r.min_stock_to_cut and gap >= r.undercut_gap
                    and (x.stock > x.stock_3_days_ago or x.pace < 1))
        stock_target = r.fg_target * shares.get(x.product, 0.0)
        ratio = x.stock / stock_target if stock_target else 0.0
        days_left = max(1, r.total_days - st.day)
        why, move = "", 0.0
        if st.day >= r.total_days:
            sig, why = "GAME OVER", "The game has ended: no more price changes."
        elif not x.started:
            sig, why = "STARTING", "No stock yet: set opening prices before the first boxes arrive."
        elif st.day >= r.sell_down_from:
            if x.stock == 0:
                sig, why = "SOLD OUT", "Nothing left to price."
            elif x.sold_per_day == 0 or x.stock / max(x.sold_per_day, 1) > days_left + 0.5:
                k = x.days_of_stock / days_left
                move = -(0.75 if k >= 2.5 else 0.5 if k >= 1.5 else 0.25)
                sig, why = "LOWER", f"{x.stock:,.0f} boxes will not sell by day {r.total_days} at {x.sold_per_day:,}/day."
            else:
                sig, why = "HOLD", "On track to sell out by the end."
        elif undercut:
            move = -min(r.max_move, max(0.25, _q(gap - r.undercut_target)))
            sig = "LOWER"
            why = (f"UNDERCUT: rivals average €{x.rivals_avg_price:.2f}, we are €{gap:.2f} higher and "
                   + ("our stock is rising" if x.stock > x.stock_3_days_ago else "we sell below our share")
                   + f". Cut again every day (not every 2 days) until within €{r.undercut_target:.2f}.")
        elif x.stock >= r.min_stock_to_cut and x.sold_last_2d == 0:
            sig, why, move = "LOWER", "Nothing sold in 2 days.", -0.5
        elif x.stock > r.overstock_batches * r.batch or (fg > r.fg_high and ratio >= 1.6 and x.days_of_stock >= 5):
            sig, why, move = "LOWER", f"{x.stock:,.0f} boxes on hand (more than its share of the stock target).", -0.5
        elif x.days_in_stock_last_3 >= 3 and x.target_per_day and x.pace < r.slow_share and x.days_of_stock >= 3 and ratio >= 0.5:
            sig, why, move = "LOWER", f"Selling {x.pace:.1f}x its share while in stock.", -0.25
        else:
            wants_raise = (x.target_per_day and x.pace >= r.raise_at) or x.ran_out_recently or (fg < r.fg_low and x.pace >= 1)
            if wants_raise and (x.days_of_stock >= r.raise_max_days or (ratio >= 1.2 and fg > r.fg_high)):
                sig, why = "HOLD", f"Selling fast, but it already has {x.days_of_stock:.0f} days of stock: let it sell down first."
            elif wants_raise:
                step = 1.0 if x.pace >= 2.2 else 0.75 if x.pace >= 1.8 else 0.5 if x.pace >= 1.5 else 0.25
                sig, move = "RAISE", step
                why = f"Selling {x.pace:.1f}x its share" if x.pace >= r.raise_at else "Ran out recently / stock is low overall"
            elif x.days_in_stock_last_3 >= 3 and x.pace < r.slow_share:
                sig, why = "HOLD", "Slow only because stock is low: no cut."
            else:
                sig, why = "HOLD", "Selling about its share."
        # size cuts against rivals, cap raises against rivals and ceiling, never past the floor
        if move < 0 and not undercut and x.rivals_avg_price and cur > x.rivals_avg_price:
            move = -min(r.max_move, max(0.25, _q(cur - x.rivals_avg_price - 0.25)))
        if move > 0:
            cap = x.ceiling - cur
            if x.rivals_avg_price:
                cap = min(cap, x.rivals_avg_price + r.rival_premium - cur)
            move = max(0.0, min(move, r.max_move, _q(cap) if cap > 0 else 0.0))
            if move == 0:
                sig, why = "HOLD", "Already at the ceiling or €1 above rivals."
        if move < 0:
            move = -min(-move, r.max_move, max(0.0, _q(cur - x.floor)))
            if move == 0:
                sig, why = "HOLD", "Already at the floor."
        model = price_response(st, x.product)
        target = best_price(model, x.cost_per_box, max(x.target_per_day, 1), x.floor, x.ceiling) if model["sensitivity"] is not None else {"price": None}
        if move and not undercut and target.get("price") and model["quality"] in ("good", "fair"):
            gap = target["price"] - cur
            if (gap > 0) == (move > 0) and abs(gap) >= 0.25:
                move = math.copysign(min(abs(move), _q(abs(gap))), move)       # do not overshoot the model's best price
        new = {}
        if move and st.replay:
            new["12"] = round(cur + move, 2)               # replay: the price list is today's, so only DC12 is meaningful
        elif move:
            n12 = round(cur + move, 2)
            new["12"] = n12
            if 10 in x.prices:   # DC10 stays below DC12 and never under the floor
                new["10"] = round(max(x.floor, min(x.prices[10] + move, n12 - 0.25)), 2)
            if 14 in x.prices:
                new["14"] = round(max(x.floor, x.prices[14] + move), 2)
        flags = []
        if 10 in x.prices and 12 in x.prices and x.prices[10] > x.prices[12]:
            flags.append("DC10 is priced above DC12: hypermarkets buy on price, keep DC10 €0.25–0.50 below DC12.")
        for ch in (12, 10, 14):
            if x.prices.get(ch) and x.prices[ch] < x.floor and not st.replay:
                flags.append(f"DC{ch} price €{x.prices[ch]:.2f} is below the floor (€{x.floor}): never set in VK32, or cut too far.")
        out.append({"product": x.product, "code": x.code, "signal": sig if move or sig not in ("RAISE", "LOWER") else "HOLD",
                    "move": move, "new_prices": new, "why": why, "current_prices": x.prices, "price_got_3d": x.price_got_3d,
                    "rivals_avg_price": x.rivals_avg_price, "stock": x.stock, "days_of_stock": x.days_of_stock, "pace": x.pace,
                    "price_model": {k: model.get(k) for k in ("source", "sensitivity", "quality", "n", "r2")},
                    "model_best_price": target.get("price"), "model_expected_boxes_per_day": target.get("expected_boxes_per_day"),
                    "flags": flags, "cut_every_day": bool(undercut and move), "cost_per_box": x.cost_per_box,
                    "cost_source": x.cost_source})
    return _py({"day": st.day, "rule": "one move per product per 2 days, except UNDERCUT cuts: every day until within €0.50 of rivals. Type the new prices in VK32 for each channel.",
                "products": out})


# ------------------------------------------------------------------ production
def production_plan(st: GameState) -> dict:
    r = st.r
    tab = st.products()
    act = tab[tab.active]
    prices = {p["product"]: p for p in price_advice(st)["products"]}
    rows = []
    for x in act.itertuples():
        slow = prices.get(x.product, {}).get("signal") == "LOWER"
        rate = x.sold_per_day if slow else max(x.sold_per_day, x.target_per_day)
        rate = max(rate, 1)
        cover = (x.stock + x.queued) / rate
        blocked = ""
        if st.day > r.last_convert_day:
            blocked = "no more conversions after round 4 day 15"
        elif x.stock > r.overstock_batches * r.batch or (slow and x.stock > x.target_per_day):
            blocked = "too much stock or selling slowly: skip it"
        elif st.day > 60 and x.stock + x.queued >= rate * (r.total_days - st.day):
            blocked = "enough to sell until day 80"
        if st.day > r.last_forecast_day:
            batches = 0
        elif st.day > 60:
            batches = max(0, round((rate * (r.total_days - st.day) - x.stock - x.queued) / r.batch))
        else:
            batches = max(0, math.ceil((rate * r.cover_days - x.stock - x.queued) / r.batch))
        rows.append({"product": x.product, "code": x.code, "stock": x.stock, "queued": x.queued, "sales_rate_used": round(rate),
                     "cover_days": round(cover, 1), "blocked": blocked, "new_batches": batches,
                     "md61_number": round(x.stock + x.queued + batches * r.batch) if batches else None,
                     "days_of_stock": x.days_of_stock})
    rows.sort(key=lambda d: (d["blocked"] != "", d["cover_days"]))
    nxt = next((d for d in rows if not d["blocked"]), None)
    line_days = sum(d["queued"] for d in rows) / r.capacity
    mats = st.materials(nxt["product"] if nxt else None)
    short = mats[mats.status == "ORDER NOW"].material.tolist()
    arriving = mats[mats.status == "ARRIVING"][["material", "first_arrival_day"]].to_dict("records")
    checkpoint = st.day_in_round in (1, 6, 11, 16) and st.day <= r.last_forecast_day
    fg = st.finished_goods()
    stocked = [d for d in rows if d["stock"] > 0]
    brake = bool(stocked) and fg > r.brake_fg and all(d["days_of_stock"] > r.brake_days for d in stocked)
    running = st.products_running()
    warning = (f"We are making {len(running)} products (max {r.max_products}): every extra product adds setups and stock-outs. "
               "Do not add more; let the weakest run out.") if len(running) > r.max_products else ""
    if st.day >= r.total_days:
        action = "Game over: nothing more to make."
    elif brake:
        action = (f"PAUSE THE LINE: {fg:,.0f} boxes in stock and every product has more than {r.brake_days:.0f} days. "
                  "Do not convert anything; cut prices first (see price advice). Never add products to fix a price problem.")
        nxt = None
    elif st.day > r.last_convert_day:
        action = "No more conversions. Sell what is left (see price advice)."
    elif not nxt:
        action = "No product needs a batch now: stock is high or sales are slow. Fix prices first."
    elif st.startup() and st.queued() == 0:
        action = f"Start-up: convert ONE 48,000 batch of {nxt['code']} ({nxt['product']}) as soon as its materials are in."
    elif line_days >= 1:
        action = f"Line busy for {line_days:.1f} days. Next up: {nxt['code']} {nxt['product']} (cover {nxt['cover_days']} days)."
    elif short and nxt["days_of_stock"] >= r.small_batch_days:
        action = (f"Materials short for a full batch of {nxt['code']} ({', '.join(short)}). It has {nxt['days_of_stock']:.0f} days of stock: "
                  "WAIT, no small batch. MD01 → ME59N now.")
    elif short:
        action = f"{nxt['code']} is low ({nxt['days_of_stock']:.0f} days) but materials only cover a smaller order: convert it, then MD01 → ME59N."
    else:
        action = f"CO41: convert one 48,000 batch of {nxt['code']} ({nxt['product']}) now."
    return _py({"day": st.day, "action": action, "production_brake": brake, "warning": warning,
                "next_product": nxt, "line_days_of_work": round(line_days, 1),
                "checkpoint_day": checkpoint,
                "forecast_rule": "MD61 number = stock + queued + whole 48,000 batches, so MRP only creates full batches. Enter it only on days 1, 6, 11, 16 (not after round 4 day 11).",
                "per_product": rows, "materials_short": short, "materials_arriving": arriving})


# ------------------------------------------------------------------ stock
def inventory_advice(st: GameState) -> dict:
    snap = snapshot(st)
    prices = price_advice(st)["products"]
    over = sorted([p for p in prices if p["days_of_stock"] and p["stock"] > 0], key=lambda p: -p["days_of_stock"])
    actions = []
    g = snap["finished_goods_gauge"]
    if g == "GAME OVER":
        actions.append("The game has ended. Leftover boxes: " + f"{snap['finished_goods']:,}.")
    elif g in ("TOO HIGH", "TOO MUCH FOR DAYS LEFT"):
        for p in over[:3]:
            if p["signal"] == "LOWER":
                actions.append(f"{p['code']}: cut €{-p['move']:.2f} → {p['new_prices']} ({p['why']})")
            if p["days_of_stock"] >= 7 and st.day <= st.r.last_convert_day:
                actions.append(f"{p['code']}: skip its next batch ({p['days_of_stock']:.0f} days of stock).")
        at_floor = [p["code"] for p in over if "floor" in p["why"]]
        if at_floor:
            actions.append("Marketing only as a test: " + ", ".join(at_floor) +
                           " is at its floor and still slow. Try €5–10k in ONE region for 5 days, same price, and compare regions.")
    elif g == "TOO LOW":
        actions.append("Stock is low: keep the line full; raise prices only on products that sell fast and have under 7 days of stock.")
    else:
        actions.append("Stock is in the band: hold. Fix single products only (see price advice).")
    return _py({"finished_goods": snap["finished_goods"], "gauge": g, "band": snap["gauge_band"],
                "storage_cost_per_day": snap["storage_cost_per_day"],
                "by_product": [{"code": p["code"], "product": p["product"], "stock": p["stock"], "days_of_stock": p["days_of_stock"],
                                "price_signal": p["signal"]} for p in over],
                "actions": actions,
                "note": "Total finished goods around 80k–140k (≈ 4–5 days of sales) is healthy with 4–5 products in 48,000 batches."})


# ------------------------------------------------------------------ money
def capacity_vs_loan(st: GameState, amount: float = 1_000_000) -> dict:
    r = st.r
    days_prod = max(0, r.last_convert_day + 2 - st.day)
    days_total = max(0, r.total_days - st.day)
    sold, rev = st.sold(days=10), st.revenue(days=10)
    avg_price = rev / sold if sold else 7.0
    tab = st.products()
    costs = tab.loc[tab.active, "cost_per_box"]
    margin = avg_price - (float(costs.mean()) if len(costs) else (r.cost_per_box_1kg + r.cost_per_box_500g) / 2)
    use = min(1.0, st.made(days=5) / (5 * r.capacity)) if st.day > 5 else 0.9
    cap_profit = max(0.0, (amount / 1e6 * 1000 * use * margin - amount / 1e6 * 10000 / 24) * days_prod)
    risk = st.value_on("COMPANY_RISK_RATE_PCT", st.day, 5.25) / 100
    loan = -st.value_on("BANK_LOAN", st.day)
    saved = min(amount, max(0.0, loan)) * ((1 + risk + 0.03) ** (days_total / 240) - 1)
    proj = st.debt_loading_projection()
    full_days = sum(1 for d in range(st.day - 4, st.day + 1) if st.made(upto=d) >= 0.95 * r.capacity)
    verdict = ("Too late for capacity: repay the loan if cash allows (keep €1M)." if days_prod == 0 else
               "Capacity would push day-80 debt loading under the AAA+ line (−€1M): buy less or repay the loan." if proj - amount < -1_000_000 + 500_000 else
               "The line has not been running full: capacity would sit idle. Fix flow first." if full_days < 3 else
               "Capacity wins." if cap_profit > saved else "Repay the loan.")
    return _py({"amount": amount, "capacity_extra_profit": round(cap_profit), "capacity_score_effect": round(cap_profit * 30),
                "loan_interest_saved": round(saved), "loan_score_effect": round(saved * 30),
                "projected_day80_debt_loading_after_capacity": round(proj - amount), "line_full_days_last_5": full_days,
                "margin_per_box_used": round(margin, 2), "verdict": verdict,
                "notes": "€1M of capacity = +1,000 boxes/day (batch size unchanged). Score ≈ 30 × profit at AAA+. Only the day-80 rating counts; loan repayment does not change it. The debt-loading projection uses the last 20 days, so it is cautious: profits usually grow later in the game."})


def marketing_check(st: GameState) -> dict:
    s, m, me = st.f.get("Sales", pd.DataFrame()), st.f.get("Market", pd.DataFrame()), st.f.get("Marketing_Expenses", pd.DataFrame())
    wk = st.market_week()
    out = {"week": wk, "regions": [], "spend_last_week": None}
    if wk >= 2 and not s.empty and not m.empty and "AREA" in s:
        for area in sorted(set(m.AREA.dropna())):
            def share(w):
                tot = m.loc[(m.AREA == area) & (m.SIM_PERIOD == w), "NET_VALUE"].sum()
                return float(s.loc[(s.AREA == area) & (s.SIM_PERIOD == w), "NET_VALUE"].sum() / tot) if tot else 0.0
            out["regions"].append({"region": area, "share_last_week": round(share(wk), 3), "share_week_before": round(share(wk - 1), 3),
                                   "change_points": round((share(wk) - share(wk - 1)) * 100, 1)})
        ch = [x["change_points"] for x in out["regions"]]
        for x in out["regions"]:
            others = (sum(ch) - x["change_points"]) / max(1, len(ch) - 1)
            x["stands_out"] = x["change_points"] >= 5 and x["change_points"] - others >= 10
    if not me.empty and "AMOUNT" in me:
        w = me[(me.SIM_ELAPSED_STEPS > (wk - 1) * 5) & (me.SIM_ELAPSED_STEPS <= wk * 5)]
        out["spend_last_week"] = {f"{k[0]} / {k[1]}": round(v) for k, v in w.groupby(["MATERIAL_DESCRIPTION", "AREA"]).AMOUNT.sum().items() if v}
    out["how_to_test"] = ("Spend €5–10k on ONE product in ONE region for 5 days with a steady price and enough stock. "
                          "Keep it only if that region's extra boxes × margin beat the spend (≈1,750 boxes per €10k).")
    return _py(out)


def endgame_plan(st: GameState) -> dict:
    r = st.r
    left = r.total_days - st.day
    risk = []
    for x in st.products().itertuples():
        if x.stock > 0:
            extra = max(0.0, x.stock - x.sold_per_day * left)
            if extra > 2000:
                risk.append({"code": x.code, "product": x.product, "boxes_at_risk": round(extra)})
    steps = [f"Last forecast (MD61): game day {r.last_forecast_day}", f"Last purchase orders (ME59N): game day {r.last_po_day}",
             f"Last conversion (CO41): game day {r.last_convert_day}", f"Days {r.sell_down_from}–{r.total_days}: price moves only, step by step"]
    if left <= 0:
        return _py({"day": st.day, "days_left": 0, "advice": "Game over. Unsold boxes: " +
                    (", ".join(f"{x['code']} {x['boxes_at_risk']:,}" for x in risk) if risk else "none") + "."})
    return _py({"day": st.day, "days_left": left, "cutoffs": steps, "leftover_risk": risk,
                "advice": "Start stepping prices down on the products listed as soon as they appear, not on the last day." if risk else "On track to sell out."})


# ------------------------------------------------------------------ today's to-do list (live alerts)
LEVELS = {"act": 0, "do": 1, "watch": 2}   # act = type it now (sound), do = today's routine, watch = keep an eye on it


def _channel_prices(new: dict) -> str:
    return " · ".join(f"DC{ch} €{p:.2f}" for ch, p in sorted(new.items(), key=lambda kv: kv[0] != "12"))


def daily_alerts(st: GameState, plan: dict | None = None, prices: list | None = None, snap: dict | None = None) -> list[dict]:
    """What needs doing today, most urgent first. Each item: key, level (act/do/watch), text, short (for the brief)."""
    r = st.r
    if not st.day:
        return []
    if st.day >= r.total_days:
        return [{"key": "game_over", "level": "watch", "text": "Game over: nothing more to type.", "short": "game over"}]
    snap = snap or snapshot(st)
    plan = plan or production_plan(st)
    prices = prices if prices is not None else price_advice(st)["products"]
    out = []

    def add(key, level, text, short):
        out.append({"key": key, "level": level, "text": text, "short": short})

    if plan.get("production_brake"):
        add("brake", "act", plan["action"], "pause the line (no CO41)")
    for p in prices:
        if p.get("cut_every_day") and p.get("new_prices"):
            new12 = p["new_prices"].get("12")
            add(f"undercut:{p['code']}", "act",
                f"UNDERCUT {p['code']} ({p['product']}): rivals average €{p['rivals_avg_price']:.2f}. "
                f"VK32 → {_channel_prices(p['new_prices'])}. Cut again tomorrow until within €{r.undercut_target:.2f}.",
                f"cut {p['code']} to €{new12:.2f}" if new12 else f"cut {p['code']}")
    if snap["alerts"].get("factory_idle_yesterday"):
        add("idle", "act", "The factory made nothing yesterday: convert a batch now (CO41).", "factory idle: convert now")
    if st.day <= r.last_convert_day:
        tab = st.products()
        for x in tab[tab.active].itertuples():
            if x.sold_per_day <= 0 or x.queued > 0 or st.s.product(x.product).get("plan") in ("never", "reserve"):
                continue
            left = x.stock / x.sold_per_day
            if left < 2:
                when = "is sold out" if x.stock <= 0 else f"runs out in about {left:.1f} days"
                add(f"stockout:{x.code}", "act", f"{x.code} ({x.product}) {when} and nothing is queued: convert it next (CO41).",
                    f"{x.code} {'sold out' if x.stock <= 0 else f'out in {left:.1f} d'}")
    nxt = plan.get("next_product")
    if plan.get("materials_short") and nxt and st.day <= r.last_po_day:
        add("materials", "act", f"Materials short for the next batch of {nxt['code']}: {', '.join(plan['materials_short'])}. "
            "Run MD01, then ME59N.", "order materials (MD01 → ME59N)")
    if plan.get("checkpoint_day"):
        add("forecast", "do", "Forecast day: type the MD61 numbers from the Make tab (whole 48,000 batches).", "MD61 forecast day")
    if nxt and not plan.get("production_brake") and plan["action"].startswith(("CO41", "Start-up")):
        add("make", "do", plan["action"], f"CO41 {nxt['code']}")
    moves = [p for p in prices if p.get("move") and p.get("new_prices") and not p.get("cut_every_day")]
    if moves:
        add("prices", "do", "Price changes in VK32: " + "; ".join(
            f"{p['code']} {'+' if p['move'] > 0 else '−'}€{abs(p['move']):.2f} → {_channel_prices(p['new_prices'])}" for p in moves),
            "VK32: " + ", ".join(f"{p['code']} €{p['new_prices'].get('12', 0):.2f}" for p in moves[:3]))
    for day, what in ((r.last_forecast_day, "MD61 forecasts"), (r.last_po_day, "purchase orders (ME59N)"),
                      (r.last_convert_day, "conversions (CO41)")):
        if st.day == day:
            add(f"cutoff:{day}", "do", f"Last day for {what}.", f"last day for {what.split(' (')[0]}")
    a = snap["alerts"]
    if snap["finished_goods_gauge"] in ("TOO HIGH", "TOO MUCH FOR DAYS LEFT") and not plan.get("production_brake"):
        add("stock_high", "watch", f"Finished goods {snap['finished_goods']:,} boxes: {snap['finished_goods_gauge'].lower()}. "
            "Cut prices before making more.", "stock too high")
    if a.get("small_batches_queued"):
        add("small_batches", "watch", f"{a['small_batches_queued']} production order(s) under 48,000 are queued: extra setups.",
            "small batches queued")
    if a.get("batches_after_last_conversion_day"):
        add("late_batches", "watch", f"{a['batches_after_last_conversion_day']} order(s) start after the last conversion day.",
            "late batches")
    if plan.get("warning"):
        add("too_many_products", "watch", plan["warning"], "too many products")
    out.sort(key=lambda x: LEVELS[x["level"]])
    return out


def brief_line(alerts: list[dict], day: int, most: int = 3) -> str:
    """The rules-based one-line brief (no AI needed)."""
    items = [a["short"] for a in alerts if a["level"] != "watch"][:most] or [a["short"] for a in alerts][:1]
    return f"Day {day}: " + ("; ".join(items) if items else "nothing urgent, hold steady") + "."


def brief_facts(st: GameState, snap: dict, alerts: list[dict]) -> dict:
    """What the AI brief gets to see: headline numbers and today's to-do list."""
    return _py({"day": st.day, "round": st.round, "day_in_round": st.day_in_round, "phase": snap["phase"],
                "valuation_eur": snap["valuation"], "profit_eur": snap["profit"], "finished_goods": snap["finished_goods"],
                "stock_gauge": snap["finished_goods_gauge"], "factory_use_3d": snap["factory_use_3d"],
                "to_do": [f"[{a['level']}] {a['text']}" for a in alerts]})


def today(st: GameState) -> dict:
    alerts = daily_alerts(st)
    return _py({"day": st.day, "brief": brief_line(alerts, st.day), "alerts": alerts})


TOOLS = {
    "todays_alerts": (today, "Today's to-do list, most urgent first: undercuts, production brake, stock-outs, materials, forecast day, price moves."),
    "get_snapshot": (snapshot, "Today's headline numbers: day, valuation, profit, rating, cash, finished-goods gauge, alerts."),
    "plan_production": (production_plan, "What to convert next (CO41), the whole-batch MD61 number per product, materials status."),
    "price_advice": (price_advice, "Stock-aware price moves per product with the exact new prices per channel; learned price sensitivity."),
    "inventory_advice": (inventory_advice, "Finished-goods gauge, overstocked products, what to cut or skip, when marketing makes sense."),
    "capacity_vs_loan": (capacity_vs_loan, "Compare buying capacity with repaying the loan, in profit and score terms."),
    "marketing_check": (marketing_check, "Regional share changes week to week, marketing spend if available, how to test marketing."),
    "endgame_plan": (endgame_plan, "Round-4 cut-offs and products at risk of being left over on day 80."),
}
