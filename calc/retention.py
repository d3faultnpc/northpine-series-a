"""Retention / product metrics: cohorts, churn, Autopilot cohort, adoption, managed spend,
agent behaviour, NPS, quote candidates, support load.

Per-customer revenue comes ONLY from load.customer_month_mrr() (README › Definitions). Writes only
calc/series/retention_*.csv. run() returns metric rows in the §5 columns, full precision.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load import (APR26, CUTOFF, MAY26, MONTHS, OCT26, SERIES, brightline_id,  # noqa: E402
                  customer_month_mrr, load)

COLS = ["id", "slide", "metric", "value", "unit", "period", "definition", "source_file", "filter",
        "formula", "caveat"]
CM_SRC = "stripe_invoices, stripe_refunds, stripe_disputes, stripe_customers (via load.customer_month_mrr)"
SELF_SEL = ("Self-selected: customers choose to enable Autopilot; difference is association, not causal "
            "effect. Autopilot launched together with new pricing (May 26), effects overlap.")
ROWS: list[dict] = []


def add(id_, slide, metric, value, unit, period, definition, source_file, filter_, formula, caveat=""):
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        value = "NA"
    elif isinstance(value, (np.integer,)):
        value = int(value)
    elif isinstance(value, (np.floating,)):
        value = float(value)
    ROWS.append(dict(id=id_, slide=slide, metric=metric, value=value, unit=unit, period=period,
                     definition=definition, source_file=source_file, filter=filter_, formula=formula,
                     caveat=caveat))


def _div(a, b):
    return float(a) / float(b) if b else float("nan")


# ------------------------------------------------------------------ helpers
def paying_matrix() -> pd.DataFrame:
    """customer x month matrix of mrr_post (0 where absent), months Jan25..Oct26."""
    cm = customer_month_mrr()
    m = cm.pivot_table(index="customer", columns="month", values="mrr_post", aggfunc="sum")
    m = m.reindex(columns=MONTHS).fillna(0.0)
    return m.clip(lower=0.0)


def _ret_at(mat: pd.DataFrame, anchor: pd.Series, k: int) -> dict:
    """Retention of customers anchored at month `anchor` (Series customer->Period) at anchor+k."""
    rows = []
    for c, a in anchor.items():
        t = a + k
        if t > OCT26:
            continue
        rows.append((c, mat.at[c, a], mat.at[c, t]))
    if not rows:
        return dict(n=0, mrr0=np.nan, logo=np.nan, nrr=np.nan, grr=np.nan)
    d = pd.DataFrame(rows, columns=["c", "m0", "mk"])
    return dict(n=len(d), mrr0=d.m0.sum(), logo=(d.mk > 0).mean(),
                nrr=_div(d.mk.sum(), d.m0.sum()),
                grr=_div(np.minimum(d.mk, d.m0).sum(), d.m0.sum()))


# ------------------------------------------------------------------ 1. cohorts
def cohorts(mat: pd.DataFrame) -> pd.Series:
    first = (mat > 0).idxmax(axis=1)
    first = first[(mat > 0).any(axis=1)]
    first = pd.Series(pd.PeriodIndex(first, freq="M"), index=first.index)
    cust = load("stripe_customers").set_index("id")
    created = cust.loc[first.index, "created"]
    tbl = []
    groups = {}
    for m in MONTHS:
        groups[str(m)] = first[first == m]
    # quarterly cohorts (monthly n are 10-39: thin)
    q = first.map(lambda p: f"{p.year}Q{p.quarter}")
    for qq in sorted(q.unique()):
        groups[qq] = first[q == qq]
    for name, g in groups.items():
        if g.empty:
            continue
        row = dict(cohort=name, grain="quarter" if "Q" in name else "month", n=len(g),
                   start_mrr_post=mat.loc[g.index].apply(lambda r: r[g[r.name]], axis=1).sum(),
                   n_created_before_2025=int((created.loc[g.index] < "2025-01-01").sum()))
        for k in (3, 5, 6, 12):
            r = _ret_at(mat, g, k)
            full = r["n"] == len(g)
            row[f"n_m{k}"] = r["n"]
            row[f"logo_m{k}"] = r["logo"] if r["n"] else np.nan
            row[f"nrr_m{k}"] = r["nrr"]
            row[f"grr_m{k}"] = r["grr"]
            row[f"m{k}_complete"] = full
        row["left_censored"] = name in ("2025-01", "2025Q1")
        row["short_post_may26"] = (name >= "2026-05") if "Q" not in name else name >= "2026Q2"
        tbl.append(row)
    out = pd.DataFrame(tbl)
    out.to_csv(SERIES / "retention_cohorts.csv", index=False)

    base = ("Cohort = first month with mrr_post>0 (invoice-built normalized MRR). Logo = share of cohort "
            "with mrr_post>0 at month k (reactivations count). NRR = cohort mrr_post at k / at month 0. "
            "GRR = sum(min(mrr_k, mrr_0)) / sum(mrr_0).")
    flt12 = "cohorts Feb25-Oct25 (Jan25 left-censored: 44 of 46 are 2024 starters; Nov/Dec25 lack m12)"
    for label, sel, cav in [
        ("2025", [str(p) for p in pd.period_range("2025-02", "2025-10", freq="M")],
         "Jan-25 cohort excluded (left-censored: 2024 starters appear first in Jan-25). Nov/Dec-25 cohorts "
         "have no m12 yet. Monthly cohorts n=9-12 each; m0 MRR includes first-month coupons (BFCM25, "
         "AGENCYPARTNER), which slightly lifts NRR on roll-off."),
        ("2025_incl_jan", [str(p) for p in pd.period_range("2025-01", "2025-10", freq="M")],
         "Includes left-censored Jan-25 cohort (mostly 2024 starters already survived months of tenure, "
         "biases retention up)."),
    ]:
        g = first[first.astype(str).isin(sel)]
        r = _ret_at(mat, g, 12)
        f = flt12 if label == "2025" else "cohorts Jan25-Oct25"
        add(f"ret_nrr12_{label}", "6;A2", f"NRR at 12m, {label} cohorts (MRR-weighted)", r["nrr"], "ratio",
            "m12", base, CM_SRC, f, "sum mrr_post(m12) / sum mrr_post(m0) across pooled cohorts", cav)
        add(f"ret_grr12_{label}", "6;A2", f"GRR at 12m, {label} cohorts (MRR-weighted)", r["grr"], "ratio",
            "m12", base, CM_SRC, f, "sum min(mrr_m12, mrr_m0) / sum mrr_m0", cav)
        add(f"ret_logo12_{label}", "6;A2", f"Logo retention at 12m, {label} cohorts", r["logo"], "ratio",
            "m12", base, CM_SRC, f, "count(mrr_post(m12)>0) / cohort n", cav)
        add(f"ret_n12_{label}", "6;A2", f"n customers in 12m pool, {label} cohorts", r["n"], "count",
            "m12", base, CM_SRC, f, "count of cohort customers", cav)

    # pre vs post May-26 at m3
    pools = {
        "pre": (pd.period_range("2025-02", "2026-04", freq="M"), "cohorts Feb25-Apr26 (pre-launch)"),
        "pre_recent": (pd.period_range("2025-11", "2026-04", freq="M"), "cohorts Nov25-Apr26 (recent pre-launch)"),
        "post": (pd.period_range("2026-05", "2026-07", freq="M"), "cohorts May26-Jul26 (only ones with m3)"),
    }
    for key, (rng, f) in pools.items():
        g = first[first.isin(rng)]
        r = _ret_at(mat, g, 3)
        cav = ("Post-May cohorts: only 3 cohorts with m3, short history, flagged. " if key == "post" else "") + \
              "m0 includes LAUNCH50 (50% for 3 mo, from Mar-26) / SWITCH3 (100%, from Jun-26) cohorts: m0 at $0 " \
              "under SWITCH3 is excluded (not paying), LAUNCH50 roll-off at m3 lifts NRR."
        for met, nm, form in [("logo", "Logo retention", "count(mrr>0 at m3)/n"),
                              ("nrr", "NRR", "sum mrr_m3 / sum mrr_m0"),
                              ("grr", "GRR", "sum min(mrr_m3,mrr_m0) / sum mrr_m0")]:
            add(f"ret_{met}3_{key}", "6;A2", f"{nm} at m3, {f}", r[met], "ratio", "m3", base, CM_SRC, f, form, cav)
        add(f"ret_n3_{key}", "6;A2", f"n customers, {f}", r["n"], "count", "m3", base, CM_SRC, f, "count", cav)
    # m5 post (May-26 cohort only)
    g = first[first == MAY26]
    r = _ret_at(mat, g, 5)
    for met in ("logo", "nrr", "grr"):
        add(f"ret_{met}5_may26", "6;A2", f"{met.upper()} at m5, May-26 cohort", r[met], "ratio", "m5 (Oct26)",
            base, CM_SRC, "cohort May26 only", f"{met} at m5", f"Single cohort, n={r['n']}: short and thin, flagged.")
    add("ret_n5_may26", "6;A2", "n customers, May-26 cohort", r["n"], "count", "m5", base, CM_SRC,
        "cohort May26", "count", "")
    return first


# ------------------------------------------------------------------ 2. churn
def churn(mat: pd.DataFrame):
    rows = []
    for i in range(1, len(MONTHS)):
        p, t = MONTHS[i - 1], MONTHS[i]
        prev = mat[p]
        act = prev > 0
        cur = mat.loc[act, t]
        lost = (cur <= 0)
        down = np.maximum(prev[act] - cur, 0)
        rows.append(dict(month=str(t), customers_prev=int(act.sum()), churned_logos=int(lost.sum()),
                         logo_churn=_div(lost.sum(), act.sum()), mrr_prev=prev[act].sum(),
                         churned_mrr=prev[act][lost].sum(), contraction_mrr=down[~lost].sum(),
                         gross_rev_churn=_div(down.sum(), prev[act].sum()),
                         churn_only_rev=_div(prev[act][lost].sum(), prev[act].sum())))
    s = pd.DataFrame(rows)
    s.to_csv(SERIES / "retention_churn_monthly.csv", index=False)
    d = ("Logo churn(t) = customers with mrr_post>0 in t-1 and mrr_post=0 in t / customers with mrr_post>0 in "
         "t-1. Gross revenue churn(t) = sum over t-1 payers of max(mrr_{t-1}-mrr_t,0) / MRR_{t-1} (churn + "
         "contraction).")
    cav = ("Simple mean of monthly rates. A past_due customer with no paid invoice for the month shows as "
           "churned that month (load.py counts paid invoices only); reactivations are not netted.")
    for key, a, b in [("pre", "2025-11", "2026-04"), ("post", "2026-05", "2026-10")]:
        w = s[(s.month >= a) & (s.month <= b)]
        add(f"ret_logo_churn_avg_{key}", "6", f"Avg monthly logo churn {a}..{b}", w.logo_churn.mean(), "ratio",
            f"{a}..{b}", d, CM_SRC, f"months {a}..{b}", "mean(monthly logo churn)", cav)
        add(f"ret_gross_rev_churn_avg_{key}", "6", f"Avg monthly gross revenue churn {a}..{b}",
            w.gross_rev_churn.mean(), "ratio", f"{a}..{b}", d, CM_SRC, f"months {a}..{b}",
            "mean(monthly gross revenue churn)", cav)
    o = s[s.month == "2026-10"].iloc[0]
    add("ret_logo_churn_oct26", "6", "Monthly logo churn Oct26", o.logo_churn, "ratio", "2026-10", d, CM_SRC,
        "Sep26 payers", "churned/prev", cav)
    return s


# ------------------------------------------------------------------ 3. Autopilot cohort
def autopilot_cohort(mat: pd.DataFrame, first: pd.Series):
    u = load("product_usage_monthly")
    ap_months = u[u.autopilot_enabled].groupby("stripe_customer_id")["period"].apply(set)

    def classify(anchor: pd.Series) -> pd.Series:
        out = {}
        for c, a in anchor.items():
            paying = [m for m in MONTHS if m >= a and mat.at[c, m] > 0]
            second = paying[1] if len(paying) > 1 else min(a + 1, OCT26)
            out[c] = any(m <= second for m in ap_months.get(c, set()))
        return pd.Series(out)

    new = first[(first >= MAY26) & (first <= OCT26)]
    # all customers paying May26+: anchor = max(first paying month, May26), only if paying at anchor
    payers_may_on = mat.index[(mat.loc[:, [m for m in MONTHS if m >= MAY26]] > 0).any(axis=1)]
    anc = {}
    for c in payers_may_on:
        a = max(first[c], MAY26)
        if mat.at[c, a] <= 0:  # paused/reactivated: anchor at first paying month >= May26
            a = next(m for m in MONTHS if m >= a and mat.at[c, m] > 0)
        anc[c] = a
    anc = pd.Series(anc)
    bl = brightline_id()
    anc_xbl = anc.drop(bl, errors="ignore")

    defn = ("Autopilot = autopilot_enabled true in any usage month <= the customer's 2nd paying month on/after "
            "the anchor (anchor = first paying month for new; max(first paying, May26) for the all-payers "
            "group). Retention at anchor+k: logo = mrr_post>0; NRR = sum mrr_k / sum mrr_0; GRR = capped.")
    src = CM_SRC + ", product_usage_monthly"
    rows = []
    for gkey, anchor, f in [("new", new, "customers first paying May26-Oct26"),
                            ("all", anc, "all customers paying May26 or later (existing anchored at May26)"),
                            ("all_xbl", anc_xbl, "all customers paying May26 or later, ex-Brightline")]:
        flag = classify(anchor)
        for apk, apv in [("on", True), ("off", False)]:
            g = anchor[flag.reindex(anchor.index).eq(apv)]
            n_all = len(g)
            arpa0 = _div(sum(mat.at[c, a] for c, a in g.items()), n_all)
            add(f"ret_ap_{gkey}_{apk}_n", "6", f"n customers, Autopilot {apk.upper()}, {f}", n_all, "count",
                "May26..Oct26", defn, src, f, "count", SELF_SEL)
            add(f"ret_ap_{gkey}_{apk}_arpa0", "6", f"Starting ARPA (mrr_post at anchor), Autopilot {apk.upper()}, {f}",
                arpa0, "usd_per_month", "anchor month", defn, src, f, "sum mrr_post(anchor)/n",
                "Price confound check: Autopilot plans are priced higher. " + SELF_SEL)
            for k in (3, 5):
                r = _ret_at(mat, g, k)
                rows.append(dict(group=gkey, autopilot=apk, k=k, n=r["n"], logo=r["logo"], nrr=r["nrr"],
                                 grr=r["grr"], arpa0=arpa0, n_group=n_all))
                cav = f"n={r['n']} with m{k} observable. " + SELF_SEL
                if gkey == "all":
                    cav = ("Includes Brightline (OFF group: flag false in May-Jun26; MRR 9k->58k May->Oct) which "
                           "dominates OFF-group NRR; use the _all_xbl rows. ") + cav
                if gkey == "new" and k == 5:
                    cav = f"Only May-26 starters observable at m5 (n={r['n']}): very thin. " + SELF_SEL
                for met, nm in [("logo", "Logo retention"), ("nrr", "NRR"), ("grr", "GRR")]:
                    add(f"ret_ap_{gkey}_{apk}_{met}{k}", "6", f"{nm} m{k}, Autopilot {apk.upper()}, {f}", r[met],
                        "ratio", f"m{k}", defn, src, f + f"; anchor+{k} <= Oct26", nm, cav)
                add(f"ret_ap_{gkey}_{apk}_n{k}", "6", f"n observable at m{k}, Autopilot {apk.upper()}, {f}",
                    r["n"], "count", f"m{k}", defn, src, f, "count", "")
    pd.DataFrame(rows).to_csv(SERIES / "retention_autopilot_cohort.csv", index=False)


# ------------------------------------------------------------------ 4-6. adoption, spend, behaviour
def usage(mat: pd.DataFrame):
    u = load("product_usage_monthly").copy()
    u["paying"] = [mat.at[c, p] > 0 if c in mat.index else False for c, p in zip(u.stripe_customer_id, u.period)]
    g = u.groupby("period")
    s = pd.DataFrame({
        "managed_spend": g.managed_ad_spend_usd.sum(),
        "managed_spend_paying": u[u.paying].groupby("period").managed_ad_spend_usd.sum(),
        "active_customers": g.size(),
        "paying_customers": (mat > 0).sum(axis=0),
        "autopilot_customers": g.autopilot_enabled.sum(),
        "autopilot_paying": u[u.paying].groupby("period").autopilot_enabled.sum(),
        "agent_actions": g.agent_actions.sum(),
        "actions_undone": g.agent_actions_undone_by_user.sum(),
    }).reindex(MONTHS).fillna(0)
    s["autopilot_share_paying"] = s.autopilot_paying / s.paying_customers
    s["undo_rate"] = s.actions_undone / s.agent_actions
    s.index = s.index.astype(str)
    s.index.name = "month"
    s.to_csv(SERIES / "retention_usage_monthly.csv")

    src = "product_usage_monthly; " + CM_SRC
    o = u[u.period == OCT26]
    op = o[o.paying]
    add("ret_ap_adoption_oct26", "3", "Autopilot adoption among paying customers, Oct26", op.autopilot_enabled.mean(),
        "ratio", "2026-10", "Share of customers with mrr_post>0 in Oct26 whose Oct26 usage row has autopilot_enabled=true",
        src, "paying Oct26 (mrr_post>0)", "count(autopilot & paying)/count(paying)",
        "Product flag, not plan: Autopilot is enabled on legacy and non-Autopilot plans too.")
    add("ret_ap_paying_n_oct26", "3", "Autopilot-enabled paying customers, Oct26", int(op.autopilot_enabled.sum()),
        "count", "2026-10", "Paying customers with autopilot_enabled=true in Oct26 usage", src, "paying Oct26",
        "count", "")
    add("ret_paying_n_oct26_usage", "3", "Paying customers Oct26 (denominator for adoption)", len(op), "count",
        "2026-10", "Customers with mrr_post>0 in Oct26 that have an Oct26 usage row", src, "paying Oct26", "count",
        "The revenue module owns the headline paying-customer count; this is the adoption denominator.")
    add("ret_ap_enabled_all_oct26", "3", "Autopilot-enabled customers in Oct26 usage (any status)",
        int(o.autopilot_enabled.sum()), "count", "2026-10", "Usage rows Oct26 with autopilot_enabled=true", src,
        "all usage rows Oct26", "count", "Includes customers with mrr_post=0 (coupon/past_due).")
    cm = customer_month_mrr()
    oc = cm[(cm.month == OCT26) & (cm.mrr_post > 0)].set_index("customer")
    ap_ids = op.loc[op.autopilot_enabled, "stripe_customer_id"]
    pl = oc.loc[oc.index.intersection(ap_ids)]
    for k, mask, nm in [("legacy", pl.is_legacy, "legacy plans"),
                        ("ap_plan", pl.is_autopilot_plan, "'+ Autopilot' plans"),
                        ("other_new", ~pl.is_legacy & ~pl.is_autopilot_plan, "new non-Autopilot plans (Starter/Growth/Agency)")]:
        add(f"ret_ap_on_{k}_n_oct26", "3", f"Autopilot-enabled paying customers on {nm}, Oct26", int(mask.sum()),
            "count", "2026-10", "Plan from the Oct26 invoice line (load.customer_month_mrr plan)", src,
            "paying Oct26 & autopilot_enabled", "count", "")
    apo = oc[oc.is_autopilot_plan]
    add("ret_ap_plan_flag_off_n_oct26", "3", "Paying customers on '+ Autopilot' plans with autopilot_enabled=false, Oct26",
        int((~apo.index.isin(ap_ids)).sum()), "count", "2026-10", "Plan says Autopilot, usage flag off", src,
        "paying Oct26", "count", "Plan and product flag disagree; adoption uses the product flag.")

    # managed spend
    so = s.loc[str(OCT26)]
    d = "Sum of managed_ad_spend_usd across product_usage_monthly rows for the month"
    add("ret_managed_spend_oct26", "5", "Managed ad spend, Oct26", so.managed_spend, "usd", "2026-10", d,
        "product_usage_monthly", "all usage rows Oct26", "sum(managed_ad_spend_usd)",
        "Self-reported by product telemetry; not reconciled to ad-platform data.")
    add("ret_managed_spend_paying_oct26", "5", "Managed ad spend, paying customers only, Oct26",
        so.managed_spend_paying, "usd", "2026-10", d + ", restricted to mrr_post>0", src, "paying Oct26",
        "sum(managed_ad_spend_usd | paying)", "")
    add("ret_managed_spend_annualized_oct26", "5", "Managed ad spend annualized (Oct26 x12)", so.managed_spend * 12,
        "usd", "2026-10", "Oct26 managed spend x 12", "product_usage_monthly", "all usage rows Oct26",
        "managed_spend_oct26*12", "Run-rate, not a forecast.")
    add("ret_managed_spend_apr26", "5", "Managed ad spend, Apr26", s.loc[str(APR26)].managed_spend, "usd", "2026-04",
        d, "product_usage_monthly", "all usage rows Apr26", "sum", "")
    add("ret_managed_spend_growth_apr_oct26", "5", "Managed spend growth Apr26->Oct26",
        _div(so.managed_spend, s.loc[str(APR26)].managed_spend), "x", "2026-04..2026-10", d,
        "product_usage_monthly", "all rows", "spend_oct/spend_apr", "")
    bl = brightline_id()
    bl_sp = o.loc[o.stripe_customer_id == bl, "managed_ad_spend_usd"].sum()
    add("ret_managed_spend_brightline_oct26", "5;9", "Brightline managed ad spend, Oct26", bl_sp, "usd", "2026-10", d,
        "product_usage_monthly", f"customer {bl}", "sum", "One account, 41 brands.")
    add("ret_managed_spend_brightline_share_oct26", "5;9", "Brightline share of managed ad spend, Oct26",
        _div(bl_sp, so.managed_spend), "ratio", "2026-10", d, "product_usage_monthly", "Oct26",
        "brightline spend / total spend", "")
    mrr_oct = oc.mrr_post.sum()
    add("ret_take_rate_oct26", "5", "Take rate Oct26 (mrr_post / managed spend)", _div(mrr_oct, so.managed_spend),
        "ratio", "2026-10", "mrr_post Oct26 (customer_month_mrr) / managed ad spend Oct26 (all usage rows)", src,
        "Oct26", "sum mrr_post / sum managed_ad_spend_usd",
        "A ratio, not the revenue model (pricing is subscription). Recomputed from the revenue series in derived.py.")
    add("ret_take_rate_paying_oct26", "5", "Take rate Oct26 on paying-customer spend", _div(mrr_oct, so.managed_spend_paying),
        "ratio", "2026-10", "mrr_post Oct26 / managed spend of paying customers", src, "paying Oct26", "ratio", "")

    # agent behaviour Oct26
    opx = op[op.stripe_customer_id != bl]
    for k, sub in [("on", op[op.autopilot_enabled]), ("off", op[~op.autopilot_enabled]),
                   ("off_xbl", opx[~opx.autopilot_enabled])]:
        f = f"paying Oct26, autopilot_enabled={k == 'on'}" + (", ex-Brightline" if k.endswith("xbl") else "")
        add(f"ret_beh_{k}_undo_rate_custmean_oct26", "A4", f"Mean per-customer undo rate, Autopilot {k.upper()}, Oct26",
            (sub.agent_actions_undone_by_user / sub.agent_actions).mean(), "ratio", "2026-10",
            "mean over customers of agent_actions_undone_by_user/agent_actions", src, f, "mean(undone/actions)",
            SELF_SEL)
        add(f"ret_beh_{k}_undone_mean_oct26", "A4", f"Mean actions undone per customer, Autopilot {k.upper()}, Oct26",
            sub.agent_actions_undone_by_user.mean(), "count", "2026-10", "mean(agent_actions_undone_by_user)", src, f,
            "mean", SELF_SEL)
        add(f"ret_beh_{k}_n_oct26", "A4", f"n customers Autopilot {k.upper()} Oct26", len(sub), "count", "2026-10",
            "Paying Oct26 customers by Oct26 autopilot flag", src, f, "count", "")
        add(f"ret_beh_{k}_actions_mean_oct26", "A4", f"Mean agent actions per customer, Autopilot {k.upper()}, Oct26",
            sub.agent_actions.mean(), "count", "2026-10", "mean(agent_actions)", src, f, "mean", SELF_SEL)
        add(f"ret_beh_{k}_undo_rate_oct26", "A4", f"Undo rate, Autopilot {k.upper()}, Oct26",
            _div(sub.agent_actions_undone_by_user.sum(), sub.agent_actions.sum()), "ratio", "2026-10",
            "sum(agent_actions_undone_by_user)/sum(agent_actions)", src, f, "undone/actions", SELF_SEL)
        add(f"ret_beh_{k}_spend_mean_oct26", "A4", f"Mean managed spend per customer, Autopilot {k.upper()}, Oct26",
            sub.managed_ad_spend_usd.mean(), "usd", "2026-10", "mean(managed_ad_spend_usd)", src, f, "mean", SELF_SEL)
    # pooled May-Oct undo rate
    w = u[(u.period >= MAY26) & u.paying & (u.stripe_customer_id != bl)]
    for k, sub in [("on", w[w.autopilot_enabled]), ("off_xbl", w[~w.autopilot_enabled])]:
        add(f"ret_beh_{k}_undo_rate_may_oct26", "A4", f"Undo rate, Autopilot {k.upper()}, pooled May-Oct26",
            _div(sub.agent_actions_undone_by_user.sum(), sub.agent_actions.sum()), "ratio", "2026-05..2026-10",
            "sum undone / sum actions over paying customer-months", src, "paying customer-months May-Oct26, ex-Brightline",
            "undone/actions", SELF_SEL)
    return s


# ------------------------------------------------------------------ 7-8. NPS + quotes
def nps(mat: pd.DataFrame):
    n = load("nps_responses")
    n = n[n.date <= CUTOFF].copy()
    n["q"] = n.date.dt.to_period("Q").astype(str)
    rows = []
    for q, g in n.groupby("q"):
        pro, det = (g.score >= 9).mean(), (g.score <= 6).mean()
        rows.append(dict(quarter=q, n=len(g), promoters=pro, passives=1 - pro - det, detractors=det,
                         nps=100 * (pro - det), partial=q == "2026Q4"))
    s = pd.DataFrame(rows)
    s.to_csv(SERIES / "retention_nps_quarterly.csv", index=False)
    d = "NPS = 100 x (share of scores 9-10 - share of scores 0-6), responses dated <= 2026-10-31"
    for r in s.itertuples():
        cav = "2026Q4 = October only (partial quarter). " if r.partial else ""
        cav += f"n={r.n}; small samples, +/- wide."
        add(f"ret_nps_{r.quarter.lower()}", "6", f"NPS {r.quarter}", r.nps, "score", r.quarter, d, "nps_responses",
            f"responses in {r.quarter}", "100*(promoters-detractors)/n", cav)
        add(f"ret_nps_n_{r.quarter.lower()}", "6", f"NPS responses {r.quarter}", r.n, "count", r.quarter, d,
            "nps_responses", f"responses in {r.quarter}", "count", cav)

    # quotes
    cust = load("stripe_customers").set_index("id")
    u = load("product_usage_monthly")
    apo = u[u.period == OCT26].set_index("stripe_customer_id").autopilot_enabled
    payers_oct = set(mat.index[mat[OCT26] > 0])
    qd = n[(n.score >= 9) & (n.date >= "2026-06-01") & n.stripe_customer_id.isin(payers_oct)].copy()
    qd["customer_name"] = qd.stripe_customer_id.map(cust.name)
    qd["segment"] = qd.stripe_customer_id.map(cust["metadata.segment"])
    qd["autopilot_oct26"] = qd.stripe_customer_id.map(apo)
    qd = qd[["date", "stripe_customer_id", "customer_name", "segment", "score", "comment", "autopilot_oct26"]]
    qd.sort_values("date").to_csv(SERIES / "retention_quote_candidates.csv", index=False)
    add("ret_quote_candidates_n", "6", "Quote candidates (score 9-10, Jun26+, paying Oct26)", len(qd), "count",
        "2026-06..2026-10", "NPS responses score>=9 dated >=2026-06-01 from customers with mrr_post>0 in Oct26",
        "nps_responses, stripe_customers, product_usage_monthly", "see definition", "count",
        f"{int(qd.comment.notna().sum())} have a comment.")

    # Little Tea House
    lth = cust.index[cust.name.str.contains("Little Tea House", case=False, na=False)][0]
    sub = load("stripe_subscriptions")
    sub = sub[sub.customer == lth].sort_values("created").iloc[-1]
    hist = n[n.stripe_customer_id == lth].sort_values("date")
    htxt = "; ".join(f"{d:%Y-%m-%d} score {s}" for d, s in zip(hist.date, hist.score))
    last_pay = max([m for m in MONTHS if mat.at[lth, m] > 0]) if lth in mat.index else None
    add("ret_lth_last_paying_month", "none", "Little Tea House last month with mrr_post>0",
        str(last_pay) if last_pay is not None else "NA", "month", str(last_pay),
        "Last service month with mrr_post>0", CM_SRC, f"customer {lth}", "max(month | mrr_post>0)",
        f"Subscription {sub.id} status={sub.status}, canceled_at={sub.canceled_at}, ended_at={sub.ended_at}, "
        f"reason={sub['cancellation_details.reason']}, feedback={sub['cancellation_details.feedback']}. "
        f"NPS history: {htxt}. Do not use LTH quote: churned.")
    add("ret_lth_nps_responses_n", "none", "Little Tea House NPS responses", len(hist), "count", "2025-01..2026-10",
        "NPS responses from Little Tea House", "nps_responses", f"customer {lth}", "count", htxt)
    add("ret_lth_ended_month", "none", "Little Tea House subscription end month", f"{sub.ended_at:%Y-%m}", "month",
        f"{sub.ended_at:%Y-%m}", "stripe_subscriptions.ended_at", "stripe_subscriptions", f"customer {lth}",
        "ended_at", f"cancellation reason={sub['cancellation_details.reason']} (involuntary, failed payment)")
    return s


# ------------------------------------------------------------------ 9. support
def support(mat: pd.DataFrame):
    t = load("support_tickets")
    t = t[t.created <= CUTOFF].copy()
    t["q"] = t.created.dt.to_period("Q").astype(str)
    pay = (mat > 0).sum(axis=0)
    pay_q = pay.groupby(pay.index.map(lambda p: f"{p.year}Q{p.quarter}")).mean()
    cats = {"unexpected": "Agent did something unexpected", "ap_settings": "Autopilot approval settings"}
    rows = []
    for q, g in t.groupby("q"):
        r = dict(quarter=q, tickets=len(g), avg_paying=pay_q.get(q, np.nan),
                 tickets_per_paying=_div(len(g), pay_q.get(q, np.nan)),
                 median_first_response_h=g.first_response_hours.median(),
                 median_resolution_h=g.resolution_hours.median(), csat_mean=g.csat.mean(),
                 csat_n=int(g.csat.notna().sum()), partial=q == "2026Q4")
        for k, c in cats.items():
            r[f"share_{k}"] = (g.category == c).mean()
        r["share_agent_related"] = g.category.isin(cats.values()).mean()
        for c in sorted(t.category.unique()):
            r[f"n_{c}"] = int((g.category == c).sum())
        rows.append(r)
    s = pd.DataFrame(rows)
    s.to_csv(SERIES / "retention_support_quarterly.csv", index=False)
    src = "support_tickets; " + CM_SRC
    d = "Tickets by created quarter (<= cut-off)"
    for r in s.itertuples():
        ql = r.quarter.lower()
        cav = "2026Q4 = Oct only (partial)." if r.partial else ""
        add(f"ret_tickets_{ql}", "A4", f"Support tickets {r.quarter}", r.tickets, "count", r.quarter, d,
            "support_tickets", f"created in {r.quarter}", "count", cav)
        add(f"ret_tickets_per_paying_{ql}", "A4", f"Tickets per paying customer {r.quarter}", r.tickets_per_paying,
            "count", r.quarter, d + " / avg monthly paying customers (mrr_post>0) in quarter", src,
            f"created in {r.quarter}", "tickets / mean(monthly paying)",
            cav + " Tickets/customer per quarter (not per month); unit is tickets per customer.")
    for q in ("2025q3", "2026q3"):
        r = s[s.quarter.str.lower() == q].iloc[0]
        for k in ("median_first_response_h", "median_resolution_h"):
            add(f"ret_{k.replace('_h', '')}_hours_{q}", "A4", f"{k.replace('_', ' ')} {q.upper()}", r[k], "hours",
                q.upper(), "Median hours across tickets created in quarter", "support_tickets", f"created {q}",
                "median", "Unit 'hours' is not in the standard unit list.")
        add(f"ret_csat_{q}", "A4", f"Mean CSAT {q.upper()} (1-5)", r.csat_mean, "score", q.upper(),
            "Mean csat over tickets with a rating", "support_tickets", f"created {q}, csat not null", "mean",
            f"n rated={r.csat_n}")
    q3 = t[t.q == "2026Q3"]
    for k, c in cats.items():
        add(f"ret_tix_share_{k}_2026q3", "A4", f"Share of 2026Q3 tickets: '{c}'", (q3.category == c).mean(), "ratio",
            "2026Q3", f"category == '{c}'", "support_tickets", "created 2026Q3", "count/total", "")
    add("ret_tix_share_agent_related_2026q3", "A4", "Share of 2026Q3 tickets agent-related (both categories)",
        q3.category.isin(cats.values()).mean(), "ratio", "2026Q3", "category in the two agent categories",
        "support_tickets", "created 2026Q3", "count/total", "")
    q3mix = q3.category.value_counts(normalize=True)
    for c, v in q3mix.items():
        slug = c.lower().replace(" / ", "_").replace(" ", "_")
        add(f"ret_tix_mix_{slug}_2026q3", "A4", f"2026Q3 ticket mix: {c}", v, "ratio", "2026Q3", "category share",
            "support_tickets", "created 2026Q3", "count/total", "")
    bl = brightline_id()
    add("ret_tickets_brightline", "A4;9", "Brightline support tickets (Jan25-Oct26)",
        int((t.stripe_customer_id == bl).sum()), "count", "2025-01..2026-10", "Tickets from Brightline customer id",
        "support_tickets", f"customer {bl}", "count", "One customer account for 41 brands.")
    add("ret_tickets_brightline_2026q3", "A4;9", "Brightline support tickets 2026Q3",
        int(((t.stripe_customer_id == bl) & (t.q == "2026Q3")).sum()), "count", "2026Q3", "Tickets from Brightline",
        "support_tickets", f"customer {bl}, 2026Q3", "count", "")
    add("ret_tickets_growth_q3_25_to_q3_26", "A4", "Ticket growth 2025Q3->2026Q3",
        _div(s.loc[s.quarter == "2026Q3", "tickets"].iloc[0], s.loc[s.quarter == "2025Q3", "tickets"].iloc[0]), "x",
        "2025Q3..2026Q3", d, "support_tickets", "created 2025Q3 and 2026Q3", "tickets_26q3/tickets_25q3", "")
    add("ret_paying_growth_q3_25_to_q3_26", "A4", "Avg paying customers growth 2025Q3->2026Q3",
        _div(pay_q["2026Q3"], pay_q["2025Q3"]), "x", "2025Q3..2026Q3", "mean monthly paying customers per quarter",
        CM_SRC, "months 2025-07..09 and 2026-07..09", "avg_paying_26q3/avg_paying_25q3", "")
    return s


def run() -> pd.DataFrame:
    ROWS.clear()
    mat = paying_matrix()
    first = cohorts(mat)
    churn(mat)
    autopilot_cohort(mat, first)
    usage(mat)
    nps(mat)
    support(mat)
    return pd.DataFrame(ROWS, columns=COLS)


if __name__ == "__main__":
    pd.set_option("display.width", 250)
    print(run()[["id", "value", "unit", "period"]].to_string())
