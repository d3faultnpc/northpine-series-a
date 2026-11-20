"""Revenue block: point-in-time MRR/ARR, headline series values, growth, price vs volume,
driver decomposition, segment/plan series, concentration, ARR bridge (A1), plan mix.

Definitions: README › Definitions. MRR series comes only from load.customer_month_mrr()/monthly_totals().
run() -> DataFrame of metric rows (§5 columns) and writes calc/series/revenue_*.csv.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from load import (APR26, CUTOFF, MAY26, MONTHS, OCT26, SERIES, brightline_id, customer_month_mrr,
                  invoices_parsed, load, monthly_totals)

COLS = ["id", "slide", "metric", "value", "unit", "period", "definition", "source_file", "filter",
        "formula", "caveat"]
ATTRIB = ("accounting attribution, not causal effect; Autopilot launched together with new pricing "
          "in May 2026")
SER_SRC = "stripe_invoices.csv; stripe_customers.csv; stripe_refunds.csv; stripe_disputes.csv"
SER_DEF = ("normalized MRR from paid invoice recurring lines by service month (annual /12), "
           "post-discount, excl. one-time and usage; requested refunds and lost disputes clawed back")
SER_FILTER = "status=paid, created<=2026-10-31, recurring lines only"

COUPON_PCT = {"SWITCH3": 1.00, "AGENCYPARTNER": 0.15, "LAUNCH50": 0.50, "BFCM25": 0.25}
TIER_ORDER = ["Starter", "Growth", "Pro", "Scale"]

_rows: list[dict] = []


def _add(id_, value, unit, period, definition, formula, *, slide="", metric=None,
         source_file=SER_SRC, filter_=SER_FILTER, caveat=""):
    _rows.append(dict(id=id_, slide=slide, metric=metric or id_.removeprefix("rev_").replace("_", " "),
                      value=value, unit=unit, period=period, definition=definition,
                      source_file=source_file, filter=filter_, formula=formula, caveat=caveat))


def _f(x):
    return float(x) if x is not None and not (isinstance(x, float) and np.isnan(x)) else np.nan


# ------------------------------------------------------------------ 1. point-in-time MRR
def point_in_time() -> pd.DataFrame:
    """Live subscriptions at the cut-off with pre/post-discount recurring MRR and usage MRR."""
    s = load("stripe_subscriptions").copy()
    inv = load("stripe_invoices")
    inv = inv[inv["created"] <= CUTOFF]
    live = s[s["status"].isin(["active", "past_due"])].copy()

    # past_due: overdue days from oldest open/uncollectible invoice of that subscription
    unpaid = inv[inv["status"] != "paid"].groupby("subscription")["period_start"].min()
    live["oldest_unpaid_due"] = live["id"].map(unpaid)
    live["overdue_days"] = (CUTOFF.normalize() - live["oldest_unpaid_due"]).dt.days
    live["overdue_days"] = np.where(live["status"].eq("past_due"),
                                    live["overdue_days"].fillna(0), 0)
    live["included"] = live["status"].eq("active") | (live["overdue_days"] < 30)

    items = live["items"].astype(str)
    annual = live["plan.interval"].eq("year")
    agency = live["plan.id"].str.contains("Agency")
    downgraded = items.str.contains("(downgraded)", regex=False)
    has_usage = items.str.contains("price_2UsageTier", regex=False)
    plan_amt = live["plan.amount_usd"].astype(float)
    tot = live["items_total_usd"].astype(float)
    base = np.where(agency | downgraded, tot, plan_amt)
    base = np.where(annual, base / 12.0, base)
    usage = np.where(has_usage & ~agency & ~downgraded, tot - plan_amt, 0.0)
    live["mrr_pre"] = base
    live["usage_pre"] = usage

    # coupon: AGENCYPARTNER forever; time-limited coupons only if the sub's latest invoice
    # (<= cut-off) still carried a discount (i.e. the coupon is still running in Oct-26)
    last = inv.sort_values("created").groupby("subscription").tail(1).set_index("subscription")
    live["last_inv_discount"] = live["id"].map(last["discount_usd"]).fillna(0.0)
    cp = live["discount.coupon"]
    pct = cp.map(COUPON_PCT).fillna(0.0)
    still = cp.eq("AGENCYPARTNER") | (live["last_inv_discount"] > 0)
    live["coupon_pct"] = np.where(cp.notna() & still, pct, 0.0)
    live["mrr_post"] = live["mrr_pre"] * (1 - live["coupon_pct"])
    live["usage_post"] = live["usage_pre"] * (1 - live["coupon_pct"])
    return live


# ------------------------------------------------------------------ 5. driver decomposition
def _tier(plan: str) -> str | None:
    for t in TIER_ORDER:
        if isinstance(plan, str) and plan.startswith(t):
            return t
    return None


DRIVER_COLS = ["new_agency", "new_autopilot", "new_other", "expansion_agency", "expansion_autopilot",
               "expansion_repricing", "expansion_coupon_rolloff", "expansion_other", "reactivation",
               "contraction", "churn"]


def driver_table() -> pd.DataFrame:
    """Customer x month Δmrr_post vs previous month with a driver label."""
    cm = customer_month_mrr()
    full = (cm.set_index(["customer", "month"])
              .reindex(pd.MultiIndex.from_product([cm["customer"].unique(), MONTHS],
                                                  names=["customer", "month"])))
    full["present"] = full["mrr_pre"].notna()
    for c in ["mrr_pre", "mrr_post"]:
        full[c] = full[c].fillna(0.0)
    # coupon discount on recurring per customer x service month (pre-clawback), for labelling only
    inv = invoices_parsed()
    inv = inv[inv["is_paid"] & ~inv["is_one_time"] & ~inv["is_annual"] & (inv["created"] <= CUTOFF)]
    disc = (inv.assign(d=inv["recurring_pre"] - inv["recurring_post"])
              .groupby(["customer", "service_month"])["d"].sum())
    disc.index.names = ["customer", "month"]
    full["disc"] = disc.reindex(full.index).fillna(0.0).values
    full = full.reset_index().sort_values(["customer", "month"])
    g = full.groupby("customer")
    full["prev_disc"] = g["disc"].shift(1).fillna(0.0)
    full["prev_post"] = g["mrr_post"].shift(1).fillna(0.0)
    full["prev_pre"] = g["mrr_pre"].shift(1).fillna(0.0)
    full["prev_present"] = g["present"].shift(1).fillna(False).astype(bool)
    full["prev_plan"] = g["plan"].shift(1)
    full["prev_legacy"] = g["is_legacy"].shift(1)
    full["prev_autopilot"] = g["is_autopilot_plan"].shift(1)
    # paid (post>0) in any earlier month
    full["paid_before"] = g["mrr_post"].transform(lambda x: (x > 0).cummax().shift(1).fillna(False)).astype(bool)
    full["delta"] = full["mrr_post"] - full["prev_post"]
    full = full[full["month"] > MONTHS[0]]  # Jan-25 has no prior month in the data window

    bl = brightline_id()
    lab = pd.Series("none", index=full.index)
    cur, prev, d = full["mrr_post"], full["prev_post"], full["delta"]
    is_bl = full["customer"].eq(bl) | full["plan"].astype(str).str.startswith("Agency")
    is_ap = full["is_autopilot_plan"].fillna(False).astype(bool)
    pre_same = (full["mrr_pre"] - full["prev_pre"]).abs() < 0.5
    disc_drop = (full["prev_disc"] - full["disc"]) > 0.5
    tier_cur = full["plan"].map(_tier)
    tier_prev = full["prev_plan"].map(_tier)
    repriced = (full["prev_legacy"].eq(True) & full["is_legacy"].eq(False) & ~is_ap
                & (tier_cur == tier_prev))

    # roll-off of a 100% coupon (post 0 -> >0 while the sub was billing all along) = coupon roll-off
    rolloff_from_zero = (prev == 0) & (cur > 0) & full["prev_present"] & full["present"] & pre_same & disc_drop
    is_new = (prev == 0) & (cur > 0) & ~full["paid_before"] & ~rolloff_from_zero
    is_react = (prev == 0) & (cur > 0) & full["paid_before"] & ~rolloff_from_zero
    is_churn = (prev > 0) & (cur == 0)
    both = (prev > 0) & (cur > 0)
    is_exp = (both & (d > 0)) | rolloff_from_zero
    is_con = both & (d < 0)

    lab[is_new & is_bl] = "new_agency"
    lab[is_new & ~is_bl & is_ap] = "new_autopilot"
    lab[is_new & ~is_bl & ~is_ap] = "new_other"
    lab[is_react] = "reactivation"
    lab[is_churn] = "churn"
    lab[is_con] = "contraction"
    e_bl = is_exp & is_bl
    e_cp = is_exp & ~is_bl & pre_same & disc_drop
    e_ap = is_exp & ~is_bl & ~e_cp & is_ap
    e_rp = is_exp & ~is_bl & ~e_cp & ~e_ap & repriced
    e_ot = is_exp & ~(e_bl | e_cp | e_ap | e_rp)
    lab[e_bl] = "expansion_agency"
    lab[e_cp] = "expansion_coupon_rolloff"
    lab[e_ap] = "expansion_autopilot"
    lab[e_rp] = "expansion_repricing"
    lab[e_ot] = "expansion_other"
    full["driver"] = lab
    full["ap_from_legacy"] = e_ap & full["prev_legacy"].eq(True)
    return full


def drivers_monthly(dt: pd.DataFrame, tot: pd.DataFrame) -> pd.DataFrame:
    piv = dt[dt["driver"] != "none"].pivot_table(index="month", columns="driver", values="delta",
                                                  aggfunc="sum").reindex(MONTHS[1:]).fillna(0.0)
    for c in DRIVER_COLS:
        if c not in piv:
            piv[c] = 0.0
    piv = piv[DRIVER_COLS]
    total = tot["mrr_post"].diff().reindex(MONTHS[1:])
    piv["total_delta"] = total
    piv["residual"] = total - piv[DRIVER_COLS].sum(axis=1)
    piv.index = piv.index.astype(str)
    piv.index.name = "month"
    return piv


# ------------------------------------------------------------------ main
def run() -> pd.DataFrame:
    _rows.clear()
    cm = customer_month_mrr()
    tot = monthly_totals()
    inv = invoices_parsed()
    inv_c = inv[inv["created"] <= CUTOFF]
    bl = brightline_id()
    kpi = load("founder_kpi_sheet").set_index("period")

    # ---- 1. point-in-time
    pit = point_in_time()
    inc = pit[pit["included"]]
    pit_pre, pit_post = inc["mrr_pre"].sum(), inc["mrr_post"].sum()
    subs = load("stripe_subscriptions")
    pit_src = "stripe_subscriptions.csv; stripe_invoices.csv (coupon still running); stripe_coupons.csv"
    pit_filter = "status=active, plus past_due overdue <30d from oldest unpaid invoice; as of 2026-10-31"
    pit_def = ("point-in-time normalized MRR of live subscriptions: monthly plans at price, annual /12, "
               "downgraded = custom items total, agency = items total incl. sub-brand fees; excl. usage tier "
               "and one-time fees")
    n_pd = int((subs["status"] == "past_due").sum())
    n_pd_inc = int((pit["status"].eq("past_due") & pit["included"]).sum())
    pd_cav = (f"{n_pd} past_due subs have no open/uncollectible invoice <= cut-off (all invoices paid), so "
              f"overdue = 0 days and {n_pd_inc} are included")
    _add("rev_mrr_pre_pit", pit_pre, "usd_per_month", "2026-10-31", pit_def + "; pre-discount",
         "sum(base) over included subs", source_file=pit_src, filter_=pit_filter, caveat=pd_cav, slide="A1")
    _add("rev_mrr_post_pit", pit_post, "usd_per_month", "2026-10-31", pit_def + "; post-discount",
         "sum(base*(1-coupon_pct)); SWITCH3 100%, AGENCYPARTNER 15% forever, LAUNCH50 50%, BFCM25 25%, "
         "time-limited coupons only if the sub's latest invoice still carried a discount",
         source_file=pit_src, filter_=pit_filter, caveat=pd_cav, slide="A1")
    _add("rev_arr_pre_pit", pit_pre * 12, "usd", "2026-10-31", "point-in-time ARR pre-discount",
         "rev_mrr_pre_pit*12", source_file=pit_src, filter_=pit_filter, slide="A1")
    _add("rev_arr_post_pit", pit_post * 12, "usd", "2026-10-31", "point-in-time ARR post-discount",
         "rev_mrr_post_pit*12", source_file=pit_src, filter_=pit_filter, slide="A1")
    _add("rev_paying_customers_pit", int((inc["mrr_post"] > 0).sum()), "count", "2026-10-31",
         "customers with point-in-time post-discount MRR > 0 (trialing never counted; Brightline = 1)",
         "count(included subs with mrr_post>0)", source_file=pit_src, filter_=pit_filter,
         caveat="Brightline counts as 1 customer although it runs 41 brands; SWITCH3 (100% off) subs excluded")
    n_sw = int(((inc["mrr_post"] == 0) & (inc["mrr_pre"] > 0)).sum())
    _add("rev_live_subs_zero_post_pit", n_sw, "count", "2026-10-31",
         "included live subs whose post-discount MRR is 0 (100% coupon running)",
         "count(included subs with mrr_post=0 and mrr_pre>0)", source_file=pit_src, filter_=pit_filter)
    for st in ["active", "past_due", "trialing"]:
        _add(f"rev_subs_{st}_count", int((subs["status"] == st).sum()), "count", "2026-10-31",
             f"subscriptions with status={st} in the Stripe export", f"count(status=='{st}')",
             source_file="stripe_subscriptions.csv", filter_=f"status={st}",
             caveat="subscription export is current state; no records dated after the cut-off")
    _add("rev_usage_mrr_pre_pit", inc["usage_pre"].sum(), "usd_per_month", "2026-10-31",
         "usage tier add-on run-rate of live subs (items_total - plan price where items include "
         "price_2UsageTier), reported separately, not in MRR", "sum(items_total-plan.amount)",
         source_file=pit_src, filter_=pit_filter)
    _add("rev_usage_mrr_post_pit", inc["usage_post"].sum(), "usd_per_month", "2026-10-31",
         "usage tier run-rate after the same coupon %", "sum(usage_pre*(1-coupon_pct))",
         source_file=pit_src, filter_=pit_filter)

    # ---- 2. reconcile PIT vs series Oct
    s_oct_post, s_oct_pre = tot.loc[OCT26, "mrr_post"], tot.loc[OCT26, "mrr_pre"]
    rec = (pit_post - s_oct_post) / s_oct_post
    rec_pre = (pit_pre - s_oct_pre) / s_oct_pre
    _add("rev_pit_vs_series_delta_pct", rec, "ratio", "2026-10",
         "point-in-time post-discount MRR vs invoice-built Oct-26 series, relative delta",
         "(rev_mrr_post_pit - rev_mrr_post_oct26)/rev_mrr_post_oct26",
         caveat=("|delta| < 3%: accepted. Sources of the gap: refund/dispute clawbacks in the series, "
                 "coupon cents rounding on invoices (e.g. 15% of 2490 billed as 374), past_due inclusion")
         if abs(rec) < 0.03 else "|delta| >= 3%: see revenue findings")
    _add("rev_pit_vs_series_delta_pre_pct", rec_pre, "ratio", "2026-10",
         "point-in-time pre-discount MRR vs invoice-built Oct-26 series pre-discount, relative delta",
         "(rev_mrr_pre_pit - rev_mrr_pre_oct26)/rev_mrr_pre_oct26")

    # ---- 3. headline series values
    def bl_m(m):
        x = cm[(cm["month"] == m) & (cm["customer"] == bl)]
        return x["mrr_post"].sum(), x["mrr_pre"].sum()

    paying = lambda m, ex=False: int(cm[(cm["month"] == m) & (cm["mrr_post"] > 0)
                                       & (~cm["is_brightline"] if ex else True)]["customer"].nunique())
    v = {}
    for lab, m in [("apr26", APR26), ("oct26", OCT26)]:
        post, pre = tot.loc[m, "mrr_post"], tot.loc[m, "mrr_pre"]
        blp, blpre = bl_m(m)
        v[lab] = dict(post=post, pre=pre, ex=post - blp, bl=blp, n=paying(m), n_ex=paying(m, True),
                      usage=tot.loc[m, "usage_post"])
        per = str(m)
        _add(f"rev_mrr_post_{lab}", post, "usd_per_month", per, SER_DEF, "sum(mrr_post) for service month",
             slide="4")
        _add(f"rev_mrr_pre_{lab}", pre, "usd_per_month", per, SER_DEF.replace("post-discount", "pre-discount"),
             "sum(mrr_pre) for service month", slide="A1")
        _add(f"rev_mrr_post_ex_bl_{lab}", post - blp, "usd_per_month", per, SER_DEF + "; excluding Brightline",
             "sum(mrr_post) - Brightline mrr_post", slide="4")
        _add(f"rev_usage_post_{lab}", v[lab]["usage"], "usd_per_month", per,
             "usage tier add-on revenue by service month, post-discount (reported separately, not in MRR)",
             "sum(usage_post) for service month", slide="A1")
        _add(f"rev_paying_customers_{lab}", v[lab]["n"], "count", per,
             "customers with mrr_post > 0 in the service month (Brightline = 1)",
             "nunique(customer | mrr_post>0)", slide="1",
             caveat="series definition (service month); trialing never counted")
        _add(f"rev_arpa_{lab}", post / v[lab]["n"], "usd_per_month", per, "average MRR per paying customer",
             f"rev_mrr_post_{lab}/rev_paying_customers_{lab}", slide="2")
        _add(f"rev_arpa_ex_bl_{lab}", (post - blp) / v[lab]["n_ex"], "usd_per_month", per,
             "average MRR per paying customer excluding Brightline",
             "(mrr_post - Brightline)/(paying - 1)", slide="2")
    _add("rev_arr_post_oct26", v["oct26"]["post"] * 12, "usd", "2026-10", "ARR = Oct-26 normalized MRR x 12 "
         "(post-discount, invoice-built series)", "rev_mrr_post_oct26*12", slide="1")
    _add("rev_arr_pre_oct26", v["oct26"]["pre"] * 12, "usd", "2026-10", "ARR pre-discount",
         "rev_mrr_pre_oct26*12", slide="A1")
    g_tot = v["oct26"]["post"] / v["apr26"]["post"]
    g_ex = v["oct26"]["ex"] / v["apr26"]["ex"]
    _add("rev_growth_apr_oct", g_tot, "x", "2026-04..2026-10", "growth multiple of normalized MRR (post) Oct/Apr",
         "rev_mrr_post_oct26/rev_mrr_post_apr26", slide="1", caveat="both ends from the invoice-built series; never cash")
    _add("rev_growth_apr_oct_ex_bl", g_ex, "x", "2026-04..2026-10",
         "growth multiple of normalized MRR (post) Oct/Apr excluding Brightline",
         "rev_mrr_post_ex_bl_oct26/rev_mrr_post_ex_bl_apr26", slide="1")
    d_tot = v["oct26"]["post"] - v["apr26"]["post"]
    _add("rev_delta_mrr_apr_oct", d_tot, "usd_per_month", "2026-04..2026-10", "change in normalized MRR (post)",
         "rev_mrr_post_oct26 - rev_mrr_post_apr26", slide="4")
    _add("rev_paying_customers_ex_bl_oct26", v["oct26"]["n_ex"], "count", "2026-10",
         "paying customers excl. Brightline", "rev_paying_customers_oct26 - 1")

    # ---- 4. price vs volume
    def pv(ca, co, ma, mo, suffix, label):
        aa, ao = ma / ca, mo / co
        vol = (co - ca) * aa
        price = co * (ao - aa)
        d = mo - ma
        for k, val in [("volume", vol), ("price_mix", price)]:
            f = "(cust_Oct-cust_Apr)*ARPA_Apr" if k == "volume" else "cust_Oct*(ARPA_Oct-ARPA_Apr)"
            _add(f"rev_pv_{k}_usd{suffix}", val, "usd_per_month", "2026-04..2026-10",
                 f"{k.replace('_', '/')} component of Apr->Oct delta mrr_post{label}", f, slide="4",
                 caveat="exact two-term split, sums to delta; " + ATTRIB)
            _add(f"rev_pv_{k}_share{suffix}", val / d, "ratio", "2026-04..2026-10",
                 f"{k.replace('_', '/')} share of Apr->Oct delta mrr_post{label}", f"{f}/delta", slide="4",
                 caveat=ATTRIB)
    pv(v["apr26"]["n"], v["oct26"]["n"], v["apr26"]["post"], v["oct26"]["post"], "", "")
    pv(v["apr26"]["n_ex"], v["oct26"]["n_ex"], v["apr26"]["ex"], v["oct26"]["ex"], "_ex_bl", " excl. Brightline")

    # ---- 5. driver decomposition
    dt = driver_table()
    dm = drivers_monthly(dt, tot)
    dm.reset_index().to_csv(SERIES / "revenue_drivers_monthly.csv", index=False)
    win = dm.loc[[str(m) for m in pd.period_range(MAY26, OCT26, freq="M")]]
    win_sum = win.sum()
    _add("rev_drv_total_delta_apr_oct", win_sum["total_delta"], "usd_per_month", "2026-04..2026-10",
         "sum of monthly delta mrr_post May..Oct (= Oct - Apr)", "sum(total_delta)", slide="4", caveat=ATTRIB)
    for c in DRIVER_COLS + ["residual"]:
        _add(f"rev_drv_{c}_usd", win_sum[c], "usd_per_month", "2026-04..2026-10",
             f"driver '{c}' of Apr->Oct delta mrr_post (per customer per month classification)",
             f"sum over May..Oct of delta mrr_post for rows labelled {c}", slide="4", caveat=ATTRIB)
        _add(f"rev_drv_{c}_share", win_sum[c] / win_sum["total_delta"], "ratio", "2026-04..2026-10",
             f"driver '{c}' share of Apr->Oct delta mrr_post", f"rev_drv_{c}_usd/rev_drv_total_delta_apr_oct",
             slide="4", caveat=ATTRIB)
    wmask = (dt["month"] >= MAY26) & (dt["month"] <= OCT26)
    ap_leg = dt[wmask & dt["ap_from_legacy"]]["delta"].sum()
    _add("rev_drv_expansion_autopilot_from_legacy_usd", ap_leg, "usd_per_month", "2026-04..2026-10",
         "part of expansion_autopilot where the customer moved from a legacy plan to an Autopilot plan "
         "(repricing and Autopilot overlap)", "sum(delta | expansion_autopilot & prev plan legacy)",
         slide="4", caveat=ATTRIB)
    # grouped summary for the slide
    groups = {"agency": ["new_agency", "expansion_agency"],
              "autopilot": ["new_autopilot", "expansion_autopilot"],
              "new_other": ["new_other"], "repricing": ["expansion_repricing"],
              "coupon_rolloff": ["expansion_coupon_rolloff"], "expansion_other": ["expansion_other"],
              "reactivation": ["reactivation"], "contraction_churn": ["contraction", "churn"]}
    for gname, cols in groups.items():
        val = win_sum[cols].sum()
        _add(f"rev_drvg_{gname}_share", val / win_sum["total_delta"], "ratio", "2026-04..2026-10",
             f"grouped driver '{gname}' ({' + '.join(cols)}) share of Apr->Oct delta mrr_post",
             " + ".join(f"rev_drv_{c}_usd" for c in cols) + " / total", slide="4", caveat=ATTRIB)
        _add(f"rev_drvg_{gname}_usd", val, "usd_per_month", "2026-04..2026-10",
             f"grouped driver '{gname}' of Apr->Oct delta mrr_post", " + ".join(f"rev_drv_{c}_usd" for c in cols),
             slide="4", caveat=ATTRIB)
    _add("rev_drv_max_abs_residual_monthly", float(dm["residual"].abs().max()), "usd_per_month",
         "2025-02..2026-10", "max |residual| of the monthly driver decomposition", "max(|total - sum(drivers)|)",
         caveat=ATTRIB)

    # ---- 6. segment / plan stacked series
    cmx = cm.copy()
    cmx["plan_group"] = np.select(
        [cmx["plan"].str.startswith("Agency"), cmx["is_autopilot_plan"], cmx["is_legacy"]],
        ["agency_reseller", "autopilot", "legacy"], default="new_pricing_non_autopilot")
    by = lambda col, val: cmx[cmx[col] == val].groupby("month")["mrr_post"].sum().reindex(MONTHS).fillna(0.0)
    paid = inv_c[inv_c["is_paid"]]
    cash = paid.groupby("paid_month")["amount_paid_usd"].sum().reindex(MONTHS).fillna(0.0)
    ser = pd.DataFrame({
        "mrr_pre": tot["mrr_pre"], "mrr_post": tot["mrr_post"], "usage_post": tot["usage_post"],
        "mrr_smb": by("segment", "SMB"), "mrr_mid": by("segment", "Mid"), "mrr_agency": by("segment", "Agency"),
        "mrr_brightline": by("is_brightline", True),
        "mrr_autopilot_plans": by("plan_group", "autopilot"),
        "mrr_new_pricing_non_autopilot": by("plan_group", "new_pricing_non_autopilot"),
        "mrr_legacy": by("plan_group", "legacy"),
        "mrr_agency_reseller_plan": by("plan_group", "agency_reseller"),
        "paying_customers": tot["paying_customers"],
        "cash_collected": cash,
        "kpi_mrr": kpi["MRR"].reindex(MONTHS).astype(float),
    }, index=MONTHS)
    ser["mrr_ex_brightline"] = ser["mrr_post"] - ser["mrr_brightline"]
    ser.index = ser.index.astype(str)
    ser.index.name = "month"
    ser.reset_index().to_csv(SERIES / "revenue_mrr_monthly.csv", index=False)

    # ---- 7. concentration
    o = cm[cm["month"] == OCT26].sort_values("mrr_post", ascending=False)
    top1 = o["mrr_post"].iloc[0] / o["mrr_post"].sum()
    top10 = o["mrr_post"].head(10).sum() / o["mrr_post"].sum()
    top1_is_bl = o["customer"].iloc[0] == bl
    _add("rev_top1_share", top1, "ratio", "2026-10", "largest customer's share of Oct-26 mrr_post",
         "max(customer mrr_post)/sum(mrr_post)", slide="9",
         caveat="top-1 customer is Brightline (agency reseller, 41 brands)" if top1_is_bl else "")
    _add("rev_top10_share", top10, "ratio", "2026-10", "top-10 customers' share of Oct-26 mrr_post",
         "sum(top10 mrr_post)/sum(mrr_post)", slide="9")
    bl_oct_inv = inv_c[(inv_c["customer"] == bl) & inv_c["is_agency"] & (inv_c["service_month"] == OCT26)]
    _add("rev_brightline_mrr_oct26", v["oct26"]["bl"], "usd_per_month", "2026-10", "Brightline mrr_post Oct-26",
         "sum(mrr_post | Brightline)", slide="9")
    _add("rev_brightline_mrr_apr26", v["apr26"]["bl"], "usd_per_month", "2026-04", "Brightline mrr_post Apr-26",
         "sum(mrr_post | Brightline)", slide="9")
    _add("rev_brightline_share_oct26", v["oct26"]["bl"] / v["oct26"]["post"], "ratio", "2026-10",
         "Brightline share of Oct-26 mrr_post", "rev_brightline_mrr_oct26/rev_mrr_post_oct26", slide="9")
    _add("rev_brightline_sub_brands_oct26", float(bl_oct_inv["sub_brands"].max()), "count", "2026-10",
         "number of sub-brands on Brightline's Oct-26 agency invoice", "parse 'N sub-brands' from invoice lines",
         source_file="stripe_invoices.csv", filter_="Brightline, agency reseller line, service month Oct-26", slide="9")
    bl_apr_inv = inv_c[(inv_c["customer"] == bl) & inv_c["is_agency"] & (inv_c["service_month"] == APR26)]
    _add("rev_brightline_sub_brands_apr26", float(bl_apr_inv["sub_brands"].max()), "count", "2026-04",
         "number of sub-brands on Brightline's Apr-26 agency invoice", "parse 'N sub-brands' from invoice lines",
         source_file="stripe_invoices.csv", filter_="Brightline, agency reseller line, service month Apr-26", slide="9")
    _add("rev_brightline_growth_apr_oct", v["oct26"]["bl"] / v["apr26"]["bl"], "x", "2026-04..2026-10",
         "Brightline mrr_post Oct/Apr", "rev_brightline_mrr_oct26/rev_brightline_mrr_apr26", slide="9")
    _add("rev_brightline_share_of_delta", (v["oct26"]["bl"] - v["apr26"]["bl"]) / d_tot, "ratio",
         "2026-04..2026-10", "Brightline's share of Apr->Oct delta mrr_post",
         "(BL_Oct-BL_Apr)/rev_delta_mrr_apr_oct", slide="9", caveat=ATTRIB)
    for seg in ["SMB", "Mid", "Agency"]:
        _add(f"rev_segment_{seg.lower()}_share_oct26", ser.loc[str(OCT26), f"mrr_{seg.lower()}"] / v["oct26"]["post"],
             "ratio", "2026-10", f"share of Oct-26 mrr_post from customers with metadata.segment={seg}",
             "segment mrr_post/total", slide="9",
             caveat="segment from stripe_customers metadata; Agency segment includes Brightline plus "
                    "agency-referred direct accounts" if seg == "Agency" else "")

    # ---- 8. ARR bridge KPI -> arr_post (Oct-26), every step x12
    kpi_arr = float(kpi.loc[OCT26, "ARR"])
    kpi_mrr = float(kpi.loc[OCT26, "MRR"])
    p_oct = paid[paid["paid_month"] == OCT26]
    cash_oct = p_oct["amount_paid_usd"].sum()
    disc_oct = p_oct["discount_usd"].sum()
    one_time = p_oct["one_time_pre"].sum()
    usage = p_oct["usage_pre"].sum()
    ann = p_oct[p_oct["is_annual"]]["recurring_pre"].sum()
    # recurring_pre for service month Oct (series pre) vs recurring_pre billed in Oct (monthly + 1/12 of annual)
    billed_rec_oct = p_oct[~p_oct["is_annual"]]["recurring_pre"].sum() + ann / 12
    ann_prior = inv_c[inv_c["is_paid"] & inv_c["is_annual"] & (inv_c["paid_month"] < OCT26)
                      & (inv_c["service_month"] + 11 >= OCT26)]["recurring_pre"].sum() / 12
    timing = tot.loc[OCT26, "mrr_pre"] - billed_rec_oct - ann_prior
    # discounts on Oct service-month recurring (pre-clawback), clawbacks = rest of pre-post gap
    svc = inv_c[inv_c["is_paid"] & ~inv_c["is_one_time"]].copy()
    rows = []
    for r in svc.itertuples(index=False):
        n = 12 if r.is_annual else 1
        for k in range(n):
            if r.service_month + k == OCT26:
                rows.append((r.recurring_pre - r.recurring_post) / n)
    disc_rec_oct = float(np.sum(rows))
    claw = (tot.loc[OCT26, "mrr_pre"] - tot.loc[OCT26, "mrr_post"]) - disc_rec_oct
    steps = [
        ("kpi_arr_oct26", kpi_arr),
        ("kpi_vs_stripe_cash", (cash_oct - kpi_mrr) * 12),
        ("add_back_discounts_in_cash", disc_oct * 12),
        ("less_one_time_fees", -one_time * 12),
        ("less_usage", -usage * 12),
        ("less_annual_prepay_11_12", -(ann * 11 / 12) * 12),
        ("add_annual_prepaid_earlier_1_12", ann_prior * 12),
        ("timing_paid_vs_service_month", timing * 12),
        ("less_discounts_recurring", -disc_rec_oct * 12),
        ("less_refund_dispute_clawbacks", -claw * 12),
    ]
    end = tot.loc[OCT26, "mrr_post"] * 12
    resid = end - sum(x for _, x in steps)
    steps += [("residual", resid), ("arr_post_oct26", end)]
    br = pd.DataFrame(steps, columns=["step", "value_usd"])
    br.to_csv(SERIES / "revenue_arr_bridge.csv", index=False)
    bdesc = {
        "kpi_vs_stripe_cash": "(Stripe cash collected Oct - KPI MRR Oct) x12",
        "add_back_discounts_in_cash": "Oct cash is net of discounts; add back to reach gross billings",
        "less_one_time_fees": "onboarding / agency setup fees paid in Oct",
        "less_usage": "usage tier add-on billed in Oct (pre-discount)",
        "less_annual_prepay_11_12": "annual plans paid in Oct: keep 1/12, remove 11/12",
        "add_annual_prepaid_earlier_1_12": "1/12 of annual plans prepaid before Oct that cover Oct (no Oct cash)",
        "timing_paid_vs_service_month": "monthly recurring by service month Oct minus monthly recurring billed in Oct",
        "less_discounts_recurring": "coupon discounts on Oct service-month recurring",
        "less_refund_dispute_clawbacks": "requested refunds and lost disputes clawed back from Oct recurring",
        "residual": "explicit residual so the bridge sums exactly",
    }
    for step, val in steps:
        if step in ("kpi_arr_oct26", "arr_post_oct26"):
            continue
        _add(f"rev_bridge_{step}", val, "usd", "2026-10", "ARR bridge step (A1): " + bdesc[step],
             "monthly amount x12", slide="A1",
             source_file="founder_kpi_sheet.csv; stripe_invoices.csv; stripe_refunds.csv; stripe_disputes.csv")
    _add("rev_kpi_arr_oct26", kpi_arr, "usd", "2026-10", "ARR as typed in the founder KPI sheet",
         "founder_kpi_sheet.ARR[2026-10]", slide="A1", source_file="founder_kpi_sheet.csv", filter_="month=2026-10",
         caveat="KPI 'MRR' is cash collected incl. prepay, one-time and usage; ARR = that x12")
    _add("rev_kpi_mrr_oct26", kpi_mrr, "usd_per_month", "2026-10", "MRR as typed in the founder KPI sheet",
         "founder_kpi_sheet.MRR[2026-10]", slide="A1", source_file="founder_kpi_sheet.csv", filter_="month=2026-10")
    _add("rev_cash_collected_oct26", cash_oct, "usd", "2026-10", "Stripe cash collected: amount_paid of paid "
         "invoices by paid (created) month", "sum(amount_paid_usd | paid, created in Oct)", slide="A1",
         source_file="stripe_invoices.csv", filter_="status=paid, created in 2026-10",
         caveat="gross of refunds and disputes; cash, not MRR")
    _add("rev_cash_collected_apr26", ser.loc[str(APR26), "cash_collected"], "usd", "2026-04",
         "Stripe cash collected Apr-26", "sum(amount_paid_usd | paid, created in Apr)", slide="A1",
         source_file="stripe_invoices.csv", filter_="status=paid, created in 2026-04",
         caveat="cash, not MRR: never used for growth")
    _add("rev_kpi_arr_vs_arr_post_ratio", kpi_arr / end, "x", "2026-10", "KPI-sheet ARR / arr_post",
         "rev_kpi_arr_oct26/rev_arr_post_oct26", slide="A1")
    diff = (ser["kpi_mrr"] - ser["cash_collected"])
    _add("rev_kpi_mrr_vs_cash_max_abs_diff", float(diff.abs().max()), "usd", "2025-01..2026-10",
         "max |KPI MRR - Stripe cash collected| across 22 months", "max(|kpi_mrr - cash_collected|)",
         source_file="founder_kpi_sheet.csv; stripe_invoices.csv", slide="A1",
         caveat=f"worst month {diff.abs().idxmax()}; KPI MRR is typed by hand and rounded to $1k")
    _add("rev_kpi_mrr_vs_cash_max_abs_diff_pct", float((diff.abs() / ser["cash_collected"]).max()), "ratio",
         "2025-01..2026-10", "max |KPI MRR - cash| / cash across 22 months",
         "max(|kpi_mrr - cash_collected|/cash_collected)", source_file="founder_kpi_sheet.csv; stripe_invoices.csv",
         slide="A1")
    _add("rev_kpi_mrr_vs_series_oct26_gap", kpi_mrr - v["oct26"]["post"], "usd_per_month", "2026-10",
         "KPI 'MRR' minus normalized mrr_post Oct-26", "rev_kpi_mrr_oct26 - rev_mrr_post_oct26", slide="A1",
         source_file="founder_kpi_sheet.csv; " + SER_SRC)

    # ---- 9. plan mix at Oct-26
    oc = cmx[cmx["month"] == OCT26]
    pm = (oc.groupby("plan").agg(customers=("customer", "nunique"),
                                 paying=("mrr_post", lambda x: int((x > 0).sum())),
                                 mrr_post=("mrr_post", "sum"), mrr_pre=("mrr_pre", "sum"))
            .sort_values("mrr_post", ascending=False))
    pm.reset_index().to_csv(SERIES / "revenue_plan_mix_oct26.csv", index=False)
    n_all = oc["customer"].nunique()
    for grp in ["autopilot", "legacy", "new_pricing_non_autopilot", "agency_reseller"]:
        sub = oc[oc["plan_group"] == grp]
        npay = int((sub["mrr_post"] > 0).sum())
        _add(f"rev_plan_{grp}_customers_oct26", int(sub["customer"].nunique()), "count", "2026-10",
             f"customers billed for Oct-26 on {grp} plans (incl. 100%-coupon)", "nunique(customer | plan group)",
             slide="3")
        _add(f"rev_plan_{grp}_customer_share_oct26", sub["customer"].nunique() / n_all, "ratio", "2026-10",
             f"share of Oct-26 billed customers on {grp} plans", "group customers / all billed customers", slide="3",
             caveat="plan-based (billing), not product usage flag autopilot_enabled")
        _add(f"rev_plan_{grp}_mrr_share_oct26", sub["mrr_post"].sum() / oc["mrr_post"].sum(), "ratio", "2026-10",
             f"share of Oct-26 mrr_post on {grp} plans", "group mrr_post / total", slide="4")
        _add(f"rev_plan_{grp}_arpa_oct26", sub["mrr_post"].sum() / npay if npay else np.nan, "usd_per_month",
             "2026-10", f"ARPA of paying customers on {grp} plans", "group mrr_post / group paying", slide="2")
    newp = oc[~oc["is_legacy"] & ~oc["plan"].str.startswith("Agency")]
    leg = oc[oc["is_legacy"]]
    _add("rev_arpa_new_pricing_oct26", newp["mrr_post"].sum() / int((newp["mrr_post"] > 0).sum()), "usd_per_month",
         "2026-10", "ARPA of paying customers on the May-26 price list (Autopilot + new Starter/Growth), excl. agency",
         "mrr_post / paying", slide="2")
    _add("rev_arpa_legacy_oct26", leg["mrr_post"].sum() / int((leg["mrr_post"] > 0).sum()), "usd_per_month",
         "2026-10", "ARPA of paying customers still on legacy prices", "mrr_post / paying", slide="2")

    out = pd.DataFrame(_rows, columns=COLS)
    assert out["id"].is_unique, out[out["id"].duplicated()]["id"].tolist()
    return out


if __name__ == "__main__":
    pd.set_option("display.width", 250)
    print(run()[["id", "value", "unit", "period"]].to_string())
