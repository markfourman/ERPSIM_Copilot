"""Read ERPsim game data: live from the OData feed, or from a saved Excel export.

Live:   ODataSource("https://<server>/odata/<client>", user, password)
Replay: ExcelSource("my_game.xlsx")  (sheet names = entity names)

The live feed is SAP's OData v2 service. Besides "send me the whole table", it answers small questions. Live mode
(copilot/live.py) uses two of them, both tested on the championship server (client 418):
  latest day      Company_Valuation?$top=1&$orderby=SIM_ROUND desc,SIM_STEP desc
  one day's rows  Sales?$filter=SIM_ROUND eq '04' and SIM_STEP eq '20'
Round and day arrive as text ('04', '20'), so filters quote them exactly as the server sent them.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

# Columns that must be numbers. Some feeds (and some Excel exports) deliver them as text, sometimes empty.
NUMERIC = {"SIM_ROUND", "SIM_STEP", "SIM_PERIOD", "SIM_ELAPSED_STEPS", "QUANTITY", "NET_VALUE", "NET_PRICE", "COST",
           "YIELD", "TARGET_QUANTITY", "CONFIRMED_QUANTITY", "SETUP_TIME", "BEGIN_ROUND", "BEGIN_STEP", "END_ROUND",
           "END_STEP", "GOODS_RECEIPT_ROUND", "GOODS_RECEIPT_STEP", "INVENTORY_OPENING_BALANCE", "PRICE", "AMOUNT",
           "DISTRIBUTION_CHANNEL", "COMPANY_VALUATION", "PROFIT", "BANK_CASH_ACCOUNT", "BANK_LOAN", "DEBT_LOADING",
           "ACCOUNTS_RECEIVABLE", "ACCOUNTS_PAYABLE", "COMPANY_RISK_RATE_PCT", "SETUP_TIME_INVESTMENT", "UNIT_PRICE"}
NUMERIC_ENDINGS = ("QUANTITY", "VALUE", "PRICE", "AMOUNT", "BALANCE", "YIELD", "_STEPS", "_ROUND", "_STEP", "_PERIOD")


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Turn text numbers ('48000', '7.50', '') into real numbers; blanks become 0."""
    if df is None or df.empty:
        return df
    df = df.copy()
    for col in df.columns:
        if col in NUMERIC or (df[col].dtype == object and str(col).upper().endswith(NUMERIC_ENDINGS)):
            df[col] = pd.to_numeric(df[col].replace({"": np.nan, " ": np.nan}), errors="coerce").fillna(0)
    return df


REQUIRED = ["Company_Valuation", "Sales", "Inventory", "Production", "Production_Orders",
            "Purchase_Orders", "Market", "Current_Pricing_Conditions", "Independent_Requirements"]
OPTIONAL = ["Current_Suppliers_Prices", "Marketing_Expenses", "Carbon_Emissions", "Current_Game_Rules"]
ALIASES = {"Marketing Expense": "Marketing_Expenses", "Marketing Expenses": "Marketing_Expenses",
           "Current Suppliers Prices": "Current_Suppliers_Prices", "Supplier Prices": "Current_Suppliers_Prices"}


@dataclass(frozen=True)
class GameDay:
    """One game day, with the round and step values exactly as the server sent them (used in filters)."""
    day: int
    round: object      # usually text, e.g. '04'
    step: object       # usually text, e.g. '20'

    @classmethod
    def from_row(cls, row: dict) -> "GameDay":
        rnd, step, day = row.get("SIM_ROUND"), row.get("SIM_STEP"), row.get("SIM_ELAPSED_STEPS")
        if day in (None, ""):
            day = (int(float(rnd)) - 1) * 20 + int(float(step))
        return cls(int(float(day)), rnd, step)


def literal(value) -> str:
    """An OData value: text in quotes ('04'), numbers as they are (4)."""
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(int(value))


def day_filter(day: GameDay) -> str:
    return f"SIM_ROUND eq {literal(day.round)} and SIM_STEP eq {literal(day.step)}"


def query_string(params: dict) -> str:
    """$filter=... with every space written as %20: a raw space cuts the address, and '+' isn't safe in OData."""
    return "&".join(f"{k}={quote(str(v), safe=chr(39) + ',()')}" for k, v in params.items())


class ODataSource:
    """Live OData feed with HTTP Basic login. Handles OData v2 and v4 JSON, and paging."""
    replay = False

    def __init__(self, url: str, user: str, password: str, timeout: int = 25):
        if not url:
            raise ValueError("No OData URL. Set ERPSIM_ODATA_URL or odata_url in config.yaml.")
        self.url = url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (user, password)
        self.session.headers["Accept"] = "application/json"
        self.timeout = timeout
        self.requests = 0                  # running totals, shown in the app's live bar
        self.bytes = 0
        self.missing: set[str] = set()     # tables this server doesn't have (404)

    def _get(self, url: str, timeout=None) -> requests.Response:
        r = self.session.get(url, timeout=timeout or self.timeout)
        self.requests += 1
        self.bytes += len(r.content)
        return r

    def rows(self, entity: str, params: dict | None = None, timeout=None) -> list | None:
        """Raw rows, values exactly as sent. None if the table doesn't exist on this server."""
        next_url = f"{self.url}/{entity}?{query_string({'$format': 'json', **(params or {})})}"
        rows = []
        while next_url:
            if not next_url.startswith("http"):
                next_url = f"{self.url}/{next_url.lstrip('/')}"
            r = self._get(next_url, timeout)
            if r.status_code == 404:
                self.missing.add(entity)
                return None
            r.raise_for_status()
            body = r.json()
            if "value" in body:                                  # OData v4
                rows += body["value"]
                next_url = body.get("@odata.nextLink")
            else:                                                # OData v2
                d = body.get("d", {})
                rows += d.get("results", d if isinstance(d, list) else [])
                next_url = d.get("__next") if isinstance(d, dict) else None
        return rows

    @staticmethod
    def frame(rows) -> pd.DataFrame:
        df = pd.DataFrame(rows or [])
        return clean(df.drop(columns=[c for c in df.columns if str(c).startswith(("__", "@"))], errors="ignore"))

    def fetch(self, entity: str, params: dict | None = None, timeout=None) -> pd.DataFrame:
        return self.frame(self.rows(entity, params, timeout))

    def load(self, entities=None) -> dict:
        out = {}
        for e in entities or REQUIRED + OPTIONAL:
            try:
                out[e] = self.fetch(e)
            except requests.HTTPError:
                if e in REQUIRED:
                    raise
                self.missing.add(e)
                out[e] = pd.DataFrame()
        return out

    # ---------------------------------------------------------------- small questions (live mode)
    def latest(self, timeout=8) -> GameDay | None:
        """The heartbeat: one row, the latest game day. None before day 1."""
        rows = self.rows("Company_Valuation", {"$top": 1, "$orderby": "SIM_ROUND desc,SIM_STEP desc"}, timeout)
        return GameDay.from_row(rows[0]) if rows else None

    def recent(self, n: int, timeout=None) -> tuple[list[GameDay], pd.DataFrame]:
        """The last n days' valuation rows, plus each day's round/step for the filters."""
        rows = self.rows("Company_Valuation", {"$top": n, "$orderby": "SIM_ROUND desc,SIM_STEP desc"}, timeout) or []
        return sorted((GameDay.from_row(r) for r in rows), key=lambda d: d.day), self.frame(rows)

    def fetch_day(self, entity: str, day: GameDay, timeout=None) -> pd.DataFrame:
        """Only one day's rows of a daily table."""
        return self.fetch(entity, {"$filter": day_filter(day)}, timeout)


class ExcelSource:
    """A saved workbook with one sheet per entity (e.g. your monitor file or a raw export)."""

    def __init__(self, path: str | Path):
        self.path = Path(path) if isinstance(path, str) else path

    def load(self, entities=None) -> dict:
        sheets = pd.read_excel(self.path, sheet_name=None)
        out = {}
        for name, df in sheets.items():
            out[ALIASES.get(name, name)] = clean(df)
        for e in entities or REQUIRED + OPTIONAL:
            out.setdefault(e, pd.DataFrame())
        return out


def cut_to_day(frames: dict, day: int) -> dict:
    """Replay helper: hide everything that happened after `day`."""
    out = {}
    for name, df in frames.items():
        if df is None or df.empty:
            out[name] = df
            continue
        if name == "Market" and "SIM_PERIOD" in df:
            out[name] = df[df["SIM_PERIOD"] <= day // 5]
        elif "SIM_ELAPSED_STEPS" in df:
            out[name] = df[df["SIM_ELAPSED_STEPS"] <= day]
        else:
            out[name] = df
    return out
