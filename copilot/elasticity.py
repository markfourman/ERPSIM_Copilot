"""How does each product's sales react to price? Learned from this game's own data.

We fit  ln(boxes per day) = a + b * ln(price)  on recent days when the product was
really available (stock did not cap sales). b is the price sensitivity:
  b = -2  -> 10% higher price = about 20% fewer boxes.
If our own data has too little price variation, we fall back to the whole market
(all teams, weekly), which is noisier but better than nothing.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _fit(prices, qty):
    p, q = np.asarray(prices, float), np.asarray(qty, float)
    ok = (p > 0) & (q > 0)
    p, q = p[ok], q[ok]
    if len(p) < 4 or np.std(np.log(p)) < 0.015:
        return None
    X = np.vstack([np.ones(len(p)), np.log(p)]).T
    coef, *_ = np.linalg.lstsq(X, np.log(q), rcond=None)
    pred = X @ coef
    ss_res = float(((np.log(q) - pred) ** 2).sum())
    ss_tot = float(((np.log(q) - np.log(q).mean()) ** 2).sum()) or 1e-9
    return {"a": float(coef[0]), "b": float(coef[1]), "n": int(len(p)), "r2": round(1 - ss_res / ss_tot, 2)}


def price_response(state, name: str, window: int = 20) -> dict:
    s, inv = state.f.get("Sales", pd.DataFrame()), state.f.get("Inventory", pd.DataFrame())
    out = {"product": name, "source": "none", "sensitivity": None, "quality": "too little data"}
    if not s.empty:
        d = s[(s.MATERIAL_DESCRIPTION == name) & (s.SIM_ELAPSED_STEPS > state.day - window) & (s.SIM_ELAPSED_STEPS <= state.day)]
        daily = d.groupby("SIM_ELAPSED_STEPS").agg(q=("QUANTITY", "sum"), v=("NET_VALUE", "sum"))
        if not daily.empty and not inv.empty:
            st = inv[inv.MATERIAL_DESCRIPTION == name].set_index("SIM_ELAPSED_STEPS")["INVENTORY_OPENING_BALANCE"]
            daily["stock"] = st.reindex(daily.index).fillna(0)
            daily = daily[(daily.q > 0) & (daily.stock >= 1.5 * daily.q)]          # sales not capped by stock
            fit = _fit(daily.v / daily.q, daily.q)
            if fit and fit["b"] < -0.2:
                out.update(source="our sales (last %d days)" % window, **fit)
    if out["source"] == "none":
        m = state.f.get("Market", pd.DataFrame())
        if not m.empty:
            w = m[(m.MATERIAL_DESCRIPTION == name) & (m.SIM_PERIOD <= state.day // 5)]
            wk = w.groupby("SIM_PERIOD").agg(q=("QUANTITY", "sum"), v=("NET_VALUE", "sum"))
            wk = wk[wk.q > 0]
            fit = _fit(wk.v / wk.q, wk.q / 5)
            if fit and fit["b"] < -0.2:
                out.update(source="whole market (weekly)", **fit)
    if out["source"] != "none":
        out["sensitivity"] = round(out["b"], 2)
        out["quality"] = "good" if out["r2"] >= 0.5 and out["n"] >= 6 else ("fair" if out["r2"] >= 0.25 else "weak")
    return out


def predicted_sales(model: dict, price: float) -> float:
    if not model or model.get("sensitivity") is None or price <= 0:
        return float("nan")
    return math.exp(model["a"] + model["b"] * math.log(price))


def price_for_sales(model: dict, boxes_per_day: float) -> float:
    """The price at which the model expects to sell `boxes_per_day`."""
    if not model or model.get("sensitivity") is None or boxes_per_day <= 0:
        return float("nan")
    return math.exp((math.log(boxes_per_day) - model["a"]) / model["b"])


def best_price(model: dict, unit_cost: float, target_per_day: float, floor: float, ceiling: float) -> dict:
    """Highest-earning price that still sells about what the factory makes.

    - price that sells our planned share (target_per_day)
    - profit-maximising price for this sensitivity (only exists when b < -1)
    Take the higher of the two (no point selling faster than we can make), clamp to floor/ceiling.
    """
    if not model or model.get("sensitivity") is None:
        return {"price": None, "why": "not enough price history yet"}
    b = model["b"]
    p_target = price_for_sales(model, target_per_day)
    p_profit = unit_cost * b / (1 + b) if b < -1 else float("nan")
    cands = [x for x in (p_target, p_profit) if x == x and x > 0]
    if not cands:
        return {"price": None, "why": "model gave no usable price"}
    p = min(max(max(cands), floor), ceiling)
    return {"price": round(p * 4) / 4, "price_for_target_sales": round(p_target, 2) if p_target == p_target else None,
            "profit_max_price": round(p_profit, 2) if p_profit == p_profit else None,
            "expected_boxes_per_day": round(predicted_sales(model, p))}
