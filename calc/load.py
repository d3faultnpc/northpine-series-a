"""Shared loaders + the one normalized-MRR core. Every calc module imports from here.

Nobody else re-parses raw files or recomputes MRR. Definitions: CLAUDE.md §3.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
SERIES = ROOT / "calc" / "series"
SERIES.mkdir(parents=True, exist_ok=True)

CUTOFF = pd.Timestamp("2026-10-31 23:59:59")
CUTOFF_MONTH = pd.Period("2026-10", "M")
FIRST_MONTH = pd.Period("2025-01", "M")
MONTHS = pd.period_range(FIRST_MONTH, CUTOFF_MONTH, freq="M")
APR26, MAY26, OCT26 = pd.Period("2026-04", "M"), pd.Period("2026-05", "M"), CUTOFF_MONTH
BRIGHTLINE_NAME = "Brightline"  # matched case-insensitively on stripe_customers.name

DATE_COLS = {
    "stripe_customers": ["created"],
    "stripe_subscriptions": ["created", "trial_start", "trial_end", "current_period_start",
                             "current_period_end", "canceled_at", "ended_at"],
    "stripe_invoices": ["created", "period_start", "period_end"],
    "stripe_charges": ["created"],
    "stripe_refunds": ["created"],
    "stripe_disputes": ["created"],
    "stripe_prices": ["created"],
    "stripe_coupons": ["created"],
    "stripe_payouts": ["arrival_date"],
    "bank_transactions": ["date"],
    "signups_daily": ["date"],
    "ad_platforms_daily": ["date"],
    "nps_responses": ["date"],
    "support_tickets": ["created"],
    "team": ["start_date", "end_date"],
}
MONTH_COLS = {"founder_kpi_sheet": "month", "bank_balance_monthly": "month", "product_usage_monthly": "month"}

FILES = [p.stem for p in sorted(RAW.glob("*.csv"))]


@lru_cache(maxsize=None)
def load(name: str) -> pd.DataFrame:
    """Raw file with parsed dates. Returns a copy-safe cached frame (do not mutate in place)."""
    df = pd.read_csv(RAW / f"{name}.csv")
    for c in DATE_COLS.get(name, []):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    if name in MONTH_COLS:
        df["period"] = pd.PeriodIndex(df[MONTH_COLS[name]], freq="M")
    if name == "product_usage_monthly":
        df["autopilot_enabled"] = df["autopilot_enabled"].astype(str).str.lower().eq("true")
    return df


def load_all() -> dict[str, pd.DataFrame]:
    return {n: load(n) for n in FILES}


def after_cutoff(df: pd.DataFrame, col: str) -> pd.DataFrame:
    return df[df[col] > CUTOFF]


@lru_cache(maxsize=None)
def brightline_id() -> str:
    c = load("stripe_customers")
    hit = c[c["name"].str.contains(BRIGHTLINE_NAME, case=False, na=False)]
    assert len(hit) == 1, f"expected 1 Brightline customer, got {len(hit)}"
    return hit["id"].iloc[0]


# ---------------------------------------------------------------- invoice parsing
PRICE_BY_NICK = None


def _list_prices() -> dict[str, float]:
    p = load("stripe_prices")
    return dict(zip(p["nickname"], p["unit_amount_usd"].astype(float)))


_LINE_RE = re.compile(r"^(?P<plan>.+?)(?: x(?P<qty>\d+))?$")


@lru_cache(maxsize=None)
def invoices_parsed() -> pd.DataFrame:
    """One row per invoice with recurring / usage / one-time split, pre and post discount.

    Rules (logged in data_quality via calc/dq.py):
    - First ';'-part of `lines` is the plan; 'Usage tier add-on' part = usage; '(one-time)' = one-time.
    - Plan with usage add-on: recurring_pre = min(subtotal, plan list price); usage_pre = rest.
      Plan without usage: recurring_pre = subtotal (covers 'downgraded' custom prices).
    - Agency reseller: whole subtotal is recurring (per-sub-brand fees are contracted, not metered).
    - Discount is pro-rated across recurring and usage by pre-discount share.
    - Annual plans: recurring spread /12 over 12 service months from period_start.
    - Only status == 'paid'. 'uncollectible' are excluded (no cash, not MRR).
    """
    inv = load("stripe_invoices").copy()
    prices = _list_prices()
    parts = inv["lines"].str.split(";")
    plan_part = parts.str[0].str.strip()
    inv["plan"] = plan_part.str.replace(r" x\d+$", "", regex=True)
    inv["is_one_time"] = inv["plan"].str.contains("(one-time)", regex=False)
    inv["has_usage"] = inv["lines"].str.contains("Usage tier add-on", regex=False)
    inv["is_agency"] = inv["plan"].str.startswith("Agency reseller")
    inv["is_annual"] = inv["plan"].str.contains("(annual)", regex=False)
    inv["sub_brands"] = inv["lines"].str.extract(r"(\d+) sub-brands")[0].astype(float)
    inv["list_price"] = inv["plan"].map(prices)
    inv["is_legacy"] = inv["plan"].str.contains("(legacy)", regex=False)
    inv["is_autopilot_plan"] = inv["plan"].str.contains("Autopilot", regex=False)

    sub = inv["subtotal_usd"].astype(float)
    rec = np.where(inv["is_one_time"], 0.0,
          np.where(inv["is_agency"], sub,
          np.where(inv["has_usage"], np.minimum(sub, inv["list_price"].fillna(sub)), sub)))
    inv["recurring_pre"] = rec
    inv["one_time_pre"] = np.where(inv["is_one_time"], sub, 0.0)
    inv["usage_pre"] = np.where(inv["is_one_time"], 0.0, sub - rec)
    share = np.where(sub > 0, inv["recurring_pre"] / sub, 0.0)
    disc = inv["discount_usd"].astype(float)
    inv["recurring_post"] = inv["recurring_pre"] - disc * share
    inv["usage_post"] = inv["usage_pre"] - disc * np.where(sub > 0, inv["usage_pre"] / sub, 0.0)
    inv["one_time_post"] = inv["one_time_pre"] - disc * np.where(sub > 0, inv["one_time_pre"] / sub, 0.0)
    inv["service_month"] = inv["period_start"].dt.to_period("M")
    inv["paid_month"] = inv["created"].dt.to_period("M")
    inv["is_paid"] = inv["status"].eq("paid")
    return inv


@lru_cache(maxsize=None)
def customer_month_mrr() -> pd.DataFrame:
    """Normalized MRR by customer x service month, Jan25..Oct26. THE core series.

    Columns: customer, month, mrr_pre, mrr_post, usage_post, plan, is_legacy, is_autopilot_plan,
    is_annual, segment, channel, is_brightline.
    Monthly invoices -> their service-period start month. Annual -> /12 for 12 months.
    Paid invoices only; duplicate-charge refunds do not reduce MRR (the invoice stood);
    customer-requested refunds and lost disputes reduce that invoice's recurring amount
    pro-rata (conservative). Records after the cut-off are ignored.
    """
    inv = invoices_parsed()
    inv = inv[inv["is_paid"] & ~inv["is_one_time"] & (inv["created"] <= CUTOFF)].copy()

    # conservative reductions: requested refunds + lost disputes (dated <= cut-off)
    ref = load("stripe_refunds")
    ref = ref[(ref["reason"] != "duplicate") & (ref["created"] <= CUTOFF)]
    dsp = load("stripe_disputes")
    dsp = dsp[(dsp["status"] == "lost") & (dsp["created"] <= CUTOFF)]
    clawback = pd.concat([ref[["invoice", "amount_usd"]], dsp[["invoice", "amount_usd"]]])
    clawback = clawback.groupby("invoice")["amount_usd"].sum()
    paid_total = (inv["recurring_post"] + inv["usage_post"]).clip(lower=0.01)
    cb = inv["id"].map(clawback).fillna(0.0).clip(upper=paid_total)
    rec_share = inv["recurring_post"] / paid_total
    inv["recurring_post"] = inv["recurring_post"] - cb * rec_share
    inv["usage_post"] = inv["usage_post"] - cb * (1 - rec_share)
    inv["clawback"] = cb

    rows = []
    for r in inv.itertuples(index=False):
        n = 12 if r.is_annual else 1
        for k in range(n):
            rows.append((r.customer, r.service_month + k, r.recurring_pre / n, r.recurring_post / n,
                         r.usage_post / n, r.plan, r.is_legacy, r.is_autopilot_plan, r.is_annual))
    cm = pd.DataFrame(rows, columns=["customer", "month", "mrr_pre", "mrr_post", "usage_post", "plan",
                                     "is_legacy", "is_autopilot_plan", "is_annual"])
    cm = cm[(cm["month"] >= FIRST_MONTH) & (cm["month"] <= CUTOFF_MONTH)]
    # two invoices in one service month (plan change mid-cycle): sum amounts, keep the later plan
    cm = (cm.groupby(["customer", "month"], as_index=False)
            .agg(mrr_pre=("mrr_pre", "sum"), mrr_post=("mrr_post", "sum"), usage_post=("usage_post", "sum"),
                 plan=("plan", "last"), is_legacy=("is_legacy", "last"),
                 is_autopilot_plan=("is_autopilot_plan", "last"), is_annual=("is_annual", "last")))
    cust = load("stripe_customers").set_index("id")
    cm["segment"] = cm["customer"].map(cust["metadata.segment"])
    cm["channel"] = cm["customer"].map(cust["metadata.acquisition_channel"])
    cm["is_brightline"] = cm["customer"].eq(brightline_id())
    return cm.sort_values(["customer", "month"]).reset_index(drop=True)


def monthly_totals() -> pd.DataFrame:
    """Company-level month series from the core table (no cash)."""
    cm = customer_month_mrr()
    g = cm.groupby("month")
    out = pd.DataFrame({
        "mrr_pre": g["mrr_pre"].sum(),
        "mrr_post": g["mrr_post"].sum(),
        "usage_post": g["usage_post"].sum(),
        "paying_customers": cm[cm["mrr_post"] > 0].groupby("month")["customer"].nunique(),
    }).reindex(MONTHS).fillna(0)
    out.index.name = "month"
    return out


def inventory_md() -> str:
    lines = ["# Data inventory (generated by calc/load.py)", "",
             "| file | rows | columns | date range |", "|---|---|---|---|"]
    for n in FILES:
        df = load(n)
        dcols = [c for c in DATE_COLS.get(n, []) if c in df] or (["period"] if "period" in df else [])
        rng = ""
        if dcols:
            s = df[dcols[0]].dropna()
            rng = f"{s.min()} .. {s.max()}" if len(s) else ""
        cols = ", ".join(c for c in df.columns if c != "period")
        lines.append(f"| {n} | {len(df)} | {cols} | {rng} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "inventory.md").write_text(inventory_md())
    t = monthly_totals()
    print(t.loc[[APR26, MAY26, OCT26]].round(0))
    print("Brightline:", brightline_id())
