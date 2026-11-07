"""Read ERPsim game data: live from the OData feed, or from a saved Excel export.

Live:   ODataSource("https://<server>/odata/<client>", user, password)
Replay: ExcelSource("my_game.xlsx")  (sheet names = entity names)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

import numpy as np

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


class ODataSource:
    """Live OData feed with HTTP Basic login. Handles OData v2 and v4 JSON, and paging."""

    def __init__(self, url: str, user: str, password: str, timeout: int = 25):
        if not url:
            raise ValueError("No OData URL. Set ERPSIM_ODATA_URL or odata_url in config.yaml.")
        self.url = url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (user, password)
        self.session.headers["Accept"] = "application/json"
        self.timeout = timeout

    def fetch(self, entity: str) -> pd.DataFrame:
        rows, next_url, params = [], f"{self.url}/{entity}", {"$format": "json"}
        while next_url:
            r = self.session.get(next_url, params=params, timeout=self.timeout)
            if r.status_code == 404:
                return pd.DataFrame()
            r.raise_for_status()
            body = r.json()
            if "value" in body:                                  # OData v4
                rows += body["value"]
                next_url = body.get("@odata.nextLink")
            else:                                                # OData v2
                d = body.get("d", {})
                rows += d.get("results", d if isinstance(d, list) else [])
                next_url = d.get("__next") if isinstance(d, dict) else None
            params = None                                        # next links already carry the query
        df = pd.DataFrame(rows)
        return clean(df.drop(columns=[c for c in df.columns if c.startswith("__") or c.startswith("@")], errors="ignore"))

    def load(self, entities=None) -> dict:
        out = {}
        for e in entities or REQUIRED + OPTIONAL:
            try:
                out[e] = self.fetch(e)
            except requests.HTTPError:
                if e in REQUIRED:
                    raise
                out[e] = pd.DataFrame()
        return out


class ExcelSource:
    """A saved workbook with one sheet per entity (e.g. your monitor file or a raw export)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

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
