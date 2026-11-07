"""Settings for ERPsim Co-pilot.

Everything team-specific lives in config.yaml (copy config.example.yaml).
Secrets come from environment variables or a .env file, never from the repo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

try:  # optional: load a local .env file if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent

# Default ERPsim muesli products (names as they appear in the OData feed).
# plan: core | reserve | never | "" (blank = let the data decide)
DEFAULT_PRODUCTS = [
    {"name": "1kg Mixed Fruit Muesli", "code": "F16", "size": "1kg", "list_price": 5.06},
    {"name": "1kg Nut Muesli", "code": "F11", "size": "1kg", "list_price": 4.61},
    {"name": "1kg Blueberry Muesli", "code": "F12", "size": "1kg", "list_price": 5.26},
    {"name": "1kg Strawberry Muesli", "code": "F13", "size": "1kg", "list_price": 5.27},
    {"name": "1kg Raisin Muesli", "code": "F14", "size": "1kg", "list_price": 4.38},
    {"name": "1kg Original Muesli", "code": "F15", "size": "1kg", "list_price": 4.35},
    {"name": "500g Nut Muesli", "code": "F01", "size": "500g", "list_price": 3.90},
    {"name": "500g Blueberry Muesli", "code": "F02", "size": "500g", "list_price": 4.23},
    {"name": "500g Strawberry Muesli", "code": "F03", "size": "500g", "list_price": 4.23},
    {"name": "500g Raisin Muesli", "code": "F04", "size": "500g", "list_price": 3.79},
    {"name": "500g Original Muesli", "code": "F05", "size": "500g", "list_price": 4.50},
    {"name": "500g Mixed Fruit Muesli", "code": "F06", "size": "500g", "list_price": 4.13},
]

# Chat models. Names change often: check https://ai.google.dev/gemini-api/docs/models
# and pick one marked free on https://ai.google.dev/gemini-api/docs/pricing
DEFAULT_MODELS = {"gemini": "gemini-3.5-flash-lite", "claude": "claude-sonnet-5-5"}

MATERIALS = ["Oats", "Wheat", "Nuts", "Blueberries", "Strawberries", "Raisins",
             "Large Box (1kg)", "Large Bag (1kg)", "Small Box (500g)", "Small Bag (500g)"]

# The game's default recipe (kg per box). Teams change recipes in ZCS02; put yours in config.yaml.
def default_recipe(name: str) -> dict:
    big = name.startswith("1kg")
    w = 1.0 if big else 0.5
    pack = {"Large Box (1kg)": 1, "Large Bag (1kg)": 1} if big else {"Small Box (500g)": 1, "Small Bag (500g)": 1}
    fruit = {"Nut": "Nuts", "Blueberry": "Blueberries", "Strawberry": "Strawberries", "Raisin": "Raisins"}
    r = {"Wheat": 0.35 * w, "Oats": 0.35 * w}
    if "Mixed" in name:
        r.update({"Wheat": 0.30 * w, "Oats": 0.30 * w, "Nuts": 0.10 * w, "Blueberries": 0.10 * w,
                  "Strawberries": 0.10 * w, "Raisins": 0.10 * w})
    elif "Original" in name:
        r.update({"Wheat": 0.50 * w, "Oats": 0.50 * w})
    else:
        for key, mat in fruit.items():
            if key in name:
                r[mat] = 0.30 * w
    r.update(pack)
    return r


@dataclass
class Rules:
    capacity: int = 24000            # boxes per day (raise it if you buy capacity)
    batch: int = 48000               # max production order (lot size)
    target_use: float = 0.90         # planned factory use
    cover_days: int = 12             # forecast covers this many days of sales
    fg_low: int = 80000              # finished-goods gauge
    fg_target: int = 110000
    fg_high: int = 140000
    raise_at: float = 1.2            # pace vs share that triggers a raise
    slow_share: float = 0.75         # pace below this (while in stock) triggers a cut
    max_move: float = 1.00           # largest price move suggested at once
    rival_premium: float = 1.00      # never more than this above rivals' average
    raise_max_days: float = 7        # no raise with this many days of stock
    min_stock_to_cut: int = 5000     # no "not selling" cut below this many boxes
    overstock_batches: float = 1.25  # more than this many batches on hand = cut
    small_batch_days: float = 5      # small order only below this many days of stock
    total_days: int = 80
    last_forecast_day: int = 71
    last_po_day: int = 74
    last_convert_day: int = 75
    sell_down_from: int = 76
    cost_per_box_1kg: float = 1.50   # ingredients + packaging, used for margins
    cost_per_box_500g: float = 1.05
    brake_fg: int = 250000           # production brake: finished goods above this ...
    brake_days: float = 10           # ... and every product above this many days of stock -> pause the line
    undercut_gap: float = 1.00       # rivals this much cheaper (and our stock rising) -> cut every day
    undercut_target: float = 0.50    # ... until we are within this of rivals
    max_products: int = 5            # warn when making more products than this
    floor_x_list: float = 1.25       # floor = list price x this
    ceiling_x_list: float = 2.00     # ceiling = list price x this


@dataclass
class Settings:
    odata_url: str = ""
    odata_user: str = ""
    odata_password: str = ""
    provider: str = "gemini"         # gemini (free tier) or claude
    model: str = ""                  # blank = the default for the provider (see chat_model)
    memory_dir: Path = ROOT / "memory" / "private"
    history_dir: Path = ROOT / "history"
    teams_active: int = 6
    rules: Rules = field(default_factory=Rules)
    products: list = field(default_factory=lambda: [dict(p) for p in DEFAULT_PRODUCTS])

    def chat_model(self) -> str:
        """The chat model to use. A model from the other provider is ignored (avoids mix-ups)."""
        m = (self.model or "").strip()
        if self.provider == "claude":
            return m if m.startswith("claude") else DEFAULT_MODELS["claude"]
        return m if m.startswith("gemini") else DEFAULT_MODELS["gemini"]

    def chat_key_name(self) -> str:
        return "ANTHROPIC_API_KEY" if self.provider == "claude" else "GEMINI_API_KEY"

    def has_chat_key(self) -> bool:
        if self.provider == "claude":
            return bool(os.getenv("ANTHROPIC_API_KEY"))
        return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))

    def product(self, name: str) -> dict:
        for p in self.products:
            if p["name"] == name:
                return p
        return {"name": name, "code": "?", "size": "1kg" if name.startswith("1kg") else "500g", "list_price": 4.5}

    def recipe(self, name: str) -> dict:
        p = self.product(name)
        return p.get("recipe") or default_recipe(name)

    def floor(self, name: str) -> float:
        return round(self.product(name)["list_price"] * self.rules.floor_x_list, 2)

    def ceiling(self, name: str) -> float:
        return round(self.product(name)["list_price"] * self.rules.ceiling_x_list, 2)

    def cost(self, name: str) -> float:
        p = self.product(name)
        if p.get("cost_per_box"):
            return float(p["cost_per_box"])
        return self.rules.cost_per_box_1kg if name.startswith("1kg") else self.rules.cost_per_box_500g


def load_settings(path: str | Path | None = None) -> Settings:
    s = Settings()
    path = Path(path) if path else ROOT / "config.yaml"
    if path.exists():
        cfg = yaml.safe_load(path.read_text()) or {}
        for k, v in (cfg.get("rules") or {}).items():
            if hasattr(s.rules, k):
                setattr(s.rules, k, type(getattr(s.rules, k))(v))
        if cfg.get("products"):
            by_name = {p["name"]: p for p in s.products}
            for p in cfg["products"]:
                by_name.setdefault(p["name"], {"name": p["name"], "code": "?", "size": "1kg", "list_price": 4.5}).update(p)
            s.products = list(by_name.values())
        for key in ("odata_url", "provider", "model", "teams_active"):
            if cfg.get(key):
                setattr(s, key, cfg[key])
        if cfg.get("memory_dir"):
            s.memory_dir = (ROOT / cfg["memory_dir"]).resolve()
        if cfg.get("history_dir"):
            s.history_dir = (ROOT / cfg["history_dir"]).resolve()
    s.odata_url = os.getenv("ERPSIM_ODATA_URL", s.odata_url)
    s.odata_user = os.getenv("ERPSIM_USER", s.odata_user)
    s.odata_password = os.getenv("ERPSIM_PASSWORD", s.odata_password)
    s.provider = os.getenv("COPILOT_PROVIDER", s.provider).strip().lower() or "gemini"
    s.model = os.getenv("COPILOT_MODEL", s.model)
    return s
