"""GameState: today's numbers, computed from the raw OData tables."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import MATERIALS, Settings


def _num(x, default=0.0):
    try:
        x = float(x)
        return default if math.isnan(x) else x
    except (TypeError, ValueError):
        return default


class GameState:
    def __init__(self, frames: dict, settings: Settings, replay_day: int | None = None):
        self.f = {k: (v if v is not None else pd.DataFrame()) for k, v in frames.items()}
        self.s = settings
        self.r = settings.rules
        self.replay = replay_day is not None
        v = self.f.get("Company_Valuation", pd.DataFrame())
        self.day = int(replay_day) if replay_day else (int(v["SIM_ELAPSED_STEPS"].max()) if not v.empty else 0)
        self.round = 0 if self.day == 0 else (self.day - 1) // 20 + 1
        self.day_in_round = 0 if self.day == 0 else self.day - (self.round - 1) * 20
        self.codes = self._codes()

    # ------------------------------------------------------------ helpers
    def _df(self, name):
        return self.f.get(name, pd.DataFrame())

    def _codes(self) -> dict:
        inv = self._df("Inventory")
        if inv.empty or "MATERIAL_NUMBER" not in inv:
            return {}
        pairs = inv[["MATERIAL_DESCRIPTION", "MATERIAL_NUMBER"]].drop_duplicates()
        return dict(zip(pairs.MATERIAL_DESCRIPTION, pairs.MATERIAL_NUMBER))

    def code(self, name: str) -> str:
        return self.codes.get(name) or self.s.product(name).get("code", "?")

    @property
    def team_prefix(self) -> str:
        for c in self.codes.values():
            if isinstance(c, str) and "-" in c:
                return c.split("-")[0]
        return "??"

    def valuation_row(self) -> dict:
        v = self._df("Company_Valuation")
        if v.empty:
            return {}
        row = v[v["SIM_ELAPSED_STEPS"] == self.day]
        return (row.iloc[-1] if not row.empty else v.iloc[-1]).to_dict()

    def value_on(self, col: str, day: int, default=0.0):
        v = self._df("Company_Valuation")
        if v.empty or col not in v:
            return default
        row = v[v["SIM_ELAPSED_STEPS"] == day]
        return _num(row[col].iloc[-1], default) if not row.empty else default

    # ------------------------------------------------------------ flows
    def sold(self, name=None, days=3) -> float:
        s = self._df("Sales")
        if s.empty:
            return 0.0
        m = (s.SIM_ELAPSED_STEPS > self.day - days) & (s.SIM_ELAPSED_STEPS <= self.day)
        if name:
            m &= s.MATERIAL_DESCRIPTION == name
        return float(s.loc[m, "QUANTITY"].sum())

    def revenue(self, name=None, days=3) -> float:
        s = self._df("Sales")
        if s.empty:
            return 0.0
        m = (s.SIM_ELAPSED_STEPS > self.day - days) & (s.SIM_ELAPSED_STEPS <= self.day)
        if name:
            m &= s.MATERIAL_DESCRIPTION == name
        return float(s.loc[m, "NET_VALUE"].sum())

    def made(self, name=None, days=1, upto=None) -> float:
        p = self._df("Production")
        if p.empty:
            return 0.0
        end = self.day if upto is None else upto
        m = (p.SIM_ELAPSED_STEPS > end - days) & (p.SIM_ELAPSED_STEPS <= end)
        if name:
            m &= p.MATERIAL_DESCRIPTION == name
        return float(p.loc[m, "YIELD"].sum())

    def stock(self, name: str, day=None) -> float:
        inv = self._df("Inventory")
        if inv.empty:
            return 0.0
        d = self.day if day is None else day
        return float(inv.loc[(inv.MATERIAL_DESCRIPTION == name) & (inv.SIM_ELAPSED_STEPS == d), "INVENTORY_OPENING_BALANCE"].sum())

    def first_stock_day(self, name: str) -> int:
        inv = self._df("Inventory")
        if inv.empty:
            return 0
        x = inv[(inv.MATERIAL_DESCRIPTION == name) & (inv.INVENTORY_OPENING_BALANCE > 0)]
        return int(x.SIM_ELAPSED_STEPS.min()) if not x.empty else 0

    def days_in_stock(self, name: str, days=3) -> int:
        inv = self._df("Inventory")
        if inv.empty:
            return 0
        m = (inv.MATERIAL_DESCRIPTION == name) & (inv.SIM_ELAPSED_STEPS > self.day - days) & (inv.SIM_ELAPSED_STEPS <= self.day)
        return int((inv.loc[m, "INVENTORY_OPENING_BALANCE"] > 0).sum())

    def ran_out_recently(self, name: str, days=3) -> bool:
        first = self.first_stock_day(name)
        if not first or first > self.day:
            return False
        inv = self._df("Inventory")
        m = (inv.MATERIAL_DESCRIPTION == name) & (inv.SIM_ELAPSED_STEPS > max(first, self.day - days)) & (inv.SIM_ELAPSED_STEPS <= self.day)
        return bool((inv.loc[m, "INVENTORY_OPENING_BALANCE"] == 0).any())

    def queued(self, name=None) -> float:
        po = self._df("Production_Orders")
        if po.empty:
            return 0.0
        po = po[po.SIM_ELAPSED_STEPS <= self.day]
        if name:
            po = po[po.MATERIAL_DESCRIPTION == name]
        if self.replay:
            end = (po.END_ROUND - 1) * 20 + po.END_STEP
            return float(po.loc[end > self.day, "TARGET_QUANTITY"].sum())
        return float((po.TARGET_QUANTITY - po.CONFIRMED_QUANTITY).clip(lower=0).sum())

    def price_list(self, name: str) -> dict:
        cp = self._df("Current_Pricing_Conditions")
        if cp.empty:
            return {}
        rows = cp[cp.MATERIAL_DESCRIPTION == name]
        return {int(r.DISTRIBUTION_CHANNEL): round(_num(r.PRICE), 2) for r in rows.itertuples()}

    def price_last(self, name: str) -> float:
        """Average price on the most recent day this product sold anything (0 if never)."""
        s = self._df("Sales")
        if s.empty:
            return 0.0
        x = s[(s.MATERIAL_DESCRIPTION == name) & (s.SIM_ELAPSED_STEPS <= self.day) & (s.QUANTITY > 0)]
        if x.empty:
            return 0.0
        last = x[x.SIM_ELAPSED_STEPS == x.SIM_ELAPSED_STEPS.max()]
        return round(float(last.NET_VALUE.sum() / last.QUANTITY.sum()), 2)

    def supplier_prices(self) -> dict:
        """Material -> price per kg / unit from the game's Current_Suppliers_Prices table (cheapest vendor)."""
        sp = self._df("Current_Suppliers_Prices")
        if sp.empty:
            return {}
        cols = list(sp.columns)
        price_col = next((c for c in ("GROSS_PRICE", "NET_PRICE", "PRICE", "UNIT_PRICE") if c in cols), None) \
            or next((c for c in cols if "PRICE" in str(c).upper()), None)
        if not price_col:
            return {}
        if "MATERIAL_DESCRIPTION" in cols:
            keys = sp["MATERIAL_DESCRIPTION"]
        else:
            num_col = next((c for c in cols if "MATERIAL" in str(c).upper()), None)
            if not num_col:
                return {}
            by_number = {v: k for k, v in self.codes.items()}
            keys = sp[num_col].map(lambda n: by_number.get(n, n))
        prices = pd.to_numeric(sp[price_col], errors="coerce")
        out = {}
        for k, p in zip(keys, prices):
            if isinstance(k, str) and p == p and p > 0:
                out[k] = min(out.get(k, p), float(p))
        return out

    def cost(self, name: str) -> tuple[float, str]:
        """Cost per box: recipe x the game's own supplier prices when available, else config."""
        prices = self.supplier_prices()
        recipe = self.s.recipe(name)
        if prices and all(m in prices for m in recipe):
            return round(sum(q * prices[m] for m, q in recipe.items()), 2), "game supplier prices"
        return self.s.cost(name), "config"

    def products_running(self, days: int = 10) -> list:
        """Products made, queued or converted in the last `days` days."""
        names = set()
        for p in self.s.products:
            n = p["name"]
            if self.made(n, days) > 0 or self.queued(n) > 0:
                names.add(n)
        return sorted(names)

    def price_got(self, name: str, days=3) -> float:
        q = self.sold(name, days)
        return round(self.revenue(name, days) / q, 2) if q else 0.0

    def market_week(self) -> int:
        m = self._df("Market")
        if m.empty:
            return 0
        w = m.loc[m.SIM_PERIOD <= self.day // 5, "SIM_PERIOD"]
        return int(w.max()) if not w.empty else 0

    def rivals(self, name: str, week=None) -> tuple[float, float]:
        """(boxes, average price) sold by all other teams in a week = market minus us."""
        m, s = self._df("Market"), self._df("Sales")
        wk = self.market_week() if week is None else week
        if m.empty or not wk:
            return 0.0, 0.0
        mq = m.loc[(m.MATERIAL_DESCRIPTION == name) & (m.SIM_PERIOD == wk), "QUANTITY"].sum()
        mv = m.loc[(m.MATERIAL_DESCRIPTION == name) & (m.SIM_PERIOD == wk), "NET_VALUE"].sum()
        oq = s.loc[(s.MATERIAL_DESCRIPTION == name) & (s.SIM_PERIOD == wk), "QUANTITY"].sum() if not s.empty else 0
        ov = s.loc[(s.MATERIAL_DESCRIPTION == name) & (s.SIM_PERIOD == wk), "NET_VALUE"].sum() if not s.empty else 0
        rq = max(0.0, float(mq - oq))
        return rq, (round(float(mv - ov) / rq, 2) if rq > 500 else 0.0)

    def market_share(self, week=None) -> float:
        m, s = self._df("Market"), self._df("Sales")
        wk = self.market_week() if week is None else week
        if m.empty or s.empty or not wk:
            return 0.0
        tot = m.loc[m.SIM_PERIOD == wk, "NET_VALUE"].sum()
        return float(s.loc[s.SIM_PERIOD == wk, "NET_VALUE"].sum() / tot) if tot else 0.0

    # ------------------------------------------------------------ product table
    def active_products(self) -> list:
        names = set()
        for p in self.s.products:
            if p.get("plan") == "core":
                names.add(p["name"])
        for name in {p["name"] for p in self.s.products}:
            if self.stock(name) > 0 or self.sold(name, 10) > 0 or self.made(name, 10) > 0 or self.queued(name) > 0:
                names.add(name)
        return sorted(names)

    def mix_shares(self) -> dict:
        act = self.active_products()
        raw = {}
        for name in act:
            share = self.s.product(name).get("mix")
            raw[name] = float(share) if share not in (None, "") else 1.0
        tot = sum(raw.values()) or 1.0
        return {k: v / tot for k, v in raw.items()}

    def products(self) -> pd.DataFrame:
        shares = self.mix_shares()
        rows = []
        for name in sorted({p["name"] for p in self.s.products} | set(self.codes) & {p["name"] for p in self.s.products}):
            stock = self.stock(name)
            sold_day = self.sold(name, 3) / max(1, min(3, self.day)) if self.day else 0.0
            target = self.r.capacity * self.r.target_use * shares.get(name, 0.0)
            rq, rp = self.rivals(name)
            rows.append({
                "product": name, "code": self.code(name), "plan": self.s.product(name).get("plan", ""),
                "active": name in shares, "stock": stock, "sold_per_day": round(sold_day),
                "days_of_stock": round(stock / sold_day, 1) if sold_day > 0 else (99.0 if stock > 0 else 0.0),
                "queued": self.queued(name), "target_per_day": round(target),
                "pace": round(sold_day / target, 2) if target else 0.0,
                "prices": self.price_list(name), "price_got_3d": self.price_got(name),
                "rivals_boxes_last_week": round(rq), "rivals_avg_price": rp,
                "sold_last_2d": self.sold(name, 2), "days_in_stock_last_3": self.days_in_stock(name),
                "ran_out_recently": self.ran_out_recently(name), "started": 0 < self.first_stock_day(name) <= self.day,
                "floor": self.s.floor(name), "ceiling": self.s.ceiling(name), "cost_per_box": self.cost(name)[0],
                "cost_source": self.cost(name)[1], "stock_3_days_ago": self.stock(name, self.day - 3),
            })
        return pd.DataFrame(rows)

    # ------------------------------------------------------------ materials
    def materials(self, for_product: str | None = None) -> pd.DataFrame:
        queued = {name: self.queued(name) for name in self.active_products()}
        pu = self._df("Purchase_Orders")
        rows = []
        for mat in MATERIALS:
            stock = self.stock(mat)
            reserved = sum(q * self.s.recipe(n).get(mat, 0) for n, q in queued.items())
            on_order, first_arrival = 0.0, None
            if not pu.empty:
                p = pu[(pu.MATERIAL_DESCRIPTION == mat) & (pu.SIM_ELAPSED_STEPS <= self.day)]
                arrive = (p.GOODS_RECEIPT_ROUND - 1) * 20 + p.GOODS_RECEIPT_STEP
                open_ = (arrive >= self.day) if self.replay else ((p.STATUS != "Delivered") | (arrive == self.day))
                on_order = float(p.loc[open_, "QUANTITY"].sum())
                arr = arrive[open_ & (arrive > 0)]
                first_arrival = int(arr.min()) if not arr.empty else None
            need = (self.s.recipe(for_product).get(mat, 0) * self.r.batch) if for_product else 0.0
            free = stock - reserved
            status = "—" if not need else ("OK" if free >= need else ("ARRIVING" if free + on_order >= need else "ORDER NOW"))
            rows.append({"material": mat, "code": self.codes.get(mat, ""), "stock": stock, "reserved": round(reserved),
                         "free": round(free), "on_order": on_order, "first_arrival_day": first_arrival,
                         "need_for_batch": round(need), "status": status})
        return pd.DataFrame(rows)

    # ------------------------------------------------------------ money
    def finished_goods(self) -> float:
        return float(sum(self.stock(p["name"]) for p in self.s.products))

    def raw_stock(self) -> float:
        return float(sum(self.stock(m) for m in MATERIALS[:6]))

    def pack_stock(self) -> float:
        return float(sum(self.stock(m) for m in MATERIALS[6:]))

    def storage_cost_per_day(self) -> float:
        over = lambda x, free: math.ceil(max(0.0, x - free) / 50000)
        return over(self.finished_goods(), 250000) * 500 + over(self.raw_stock(), 250000) * 1000 + over(self.pack_stock(), 750000) * 100

    def debt_loading_projection(self) -> float:
        dl = self.value_on("DEBT_LOADING", self.day)
        if self.day <= 20:
            return dl
        rate = (dl - self.value_on("DEBT_LOADING", self.day - 20, dl)) / 20
        return dl + rate * (self.r.total_days - self.day)

    def startup(self) -> bool:
        p = self._df("Production")
        if p.empty:
            return True
        first = p.loc[p.YIELD > 0, "SIM_ELAPSED_STEPS"]
        return first.empty or int(first.min()) > self.day
