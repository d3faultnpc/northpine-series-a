"""Main-agent metrics that combine domains: GM on total recurring revenue, payback by channel,
external inputs (raise size, competitor prices), forward-looking arithmetic (labelled).

Reads only calc/series/*.csv written by the domain modules and load.py. Runs after them.
"""
import re

import pandas as pd

from load import RAW, SERIES, APR26, OCT26, load, monthly_totals

COLS = ["id", "slide", "metric", "value", "unit", "period", "definition", "source_file", "filter", "formula", "caveat"]


def _row(id, slide, metric, value, unit, period, definition, source, flt, formula, caveat=""):
    return dict(zip(COLS, [id, slide, metric, value, unit, period, definition, source, flt, formula, caveat]))


def _price_range(s: str):
    nums = [float(x.replace(",", "")) for x in re.findall(r"\$([\d,]+)", s)]
    return (min(nums), max(nums)) if nums else (0.0, 0.0)


def run() -> pd.DataFrame:
    rows = []
    t = monthly_totals()
    gm = pd.read_csv(SERIES / "burn_gm_monthly.csv")
    gm["month"] = pd.PeriodIndex(gm["month"], freq="M")
    gm = gm.set_index("month")
    gm["usage_post"] = t["usage_post"].reindex(gm.index).values
    gm["rev_total"] = gm["mrr_post"] + gm["usage_post"]
    gm["gm_total"] = (gm["rev_total"] - gm["cogs_total"]) / gm["rev_total"]
    out = gm[["mrr_post", "usage_post", "rev_total", "cogs_total", "gm", "gm_total", "cogs_inference"]].copy()
    out.index = out.index.astype(str)
    out.to_csv(SERIES / "derived_gm_monthly.csv", index_label="month")

    gdef = ("Gross margin on total recurring revenue: (subscription mrr_post + usage-tier revenue − COGS) / "
            "(mrr_post + usage). COGS = LLM inference + hosting + Intercom (bank).")
    gcav = ("Deviation from CLAUDE.md §3 (denominator mrr_post only) taken by main agent: COGS serves usage-tier "
            "revenue too, so mrr_post-only GM understates margin. Both are in calc/series/derived_gm_monthly.csv.")
    src = "stripe_invoices.csv; bank_transactions.csv"
    may_oct = [p for p in gm.index if APR26 < p <= OCT26]
    gmin_m = min(may_oct, key=lambda p: gm.loc[p, "gm_total"])
    rows += [
        _row("der_gm_oct26", 7, "Gross margin Oct 2026 (total recurring revenue)", gm.loc[OCT26, "gm_total"], "ratio", "2026-10", gdef, src, "month=2026-10", "(rev_total−cogs)/rev_total", gcav),
        _row("der_gm_apr26", 7, "Gross margin Apr 2026 (total recurring revenue)", gm.loc[APR26, "gm_total"], "ratio", "2026-04", gdef, src, "month=2026-04", "(rev_total−cogs)/rev_total", gcav),
        _row("der_gm_min", 7, "Lowest gross margin May–Oct 2026 (total recurring revenue)", gm.loc[gmin_m, "gm_total"], "ratio", str(gmin_m), gdef, src, f"min over 2026-05..2026-10 → {gmin_m}", "min((rev_total−cogs)/rev_total)", gcav),
        _row("der_gm_min_month", 7, "Month of lowest gross margin", gmin_m.strftime("%b %Y"), "text", str(gmin_m), "Month with the lowest gm_total in May–Oct 2026", src, "", "argmin", ""),
        _row("der_gm_2025_avg", 7, "Average gross margin 2025 (total recurring revenue)", gm.loc[[p for p in gm.index if p.year == 2025], "gm_total"].mean(), "ratio", "2025", gdef, src, "months 2025-01..2025-12, simple mean", "mean(gm_total)", gcav),
        _row("der_rev_total_oct26", 1, "Total recurring revenue Oct 2026 (subscription + usage tiers)", gm.loc[OCT26, "rev_total"], "usd_per_month", "2026-10", "mrr_post + usage_post (usage tiers billed monthly)", src, "service month 2026-10", "mrr_post+usage_post", "Usage tiers are excluded from headline ARR by §3; shown separately."),
        _row("der_arr_incl_usage", 1, "Run-rate incl. usage tiers (×12)", gm.loc[OCT26, "rev_total"] * 12, "usd", "2026-10", "(mrr_post + usage_post) Oct 2026 × 12", src, "service month 2026-10", "rev_total×12", "Not the headline ARR; usage tiers move with customer ad spend."),
        _row("der_usage_arr", 1, "Usage-tier revenue run-rate (×12)", gm.loc[OCT26, "usage_post"] * 12, "usd", "2026-10", "usage_post Oct 2026 × 12", src, "service month 2026-10", "usage_post×12", "Reported separately from ARR (§3)."),
        _row("der_usage_growth", 1, "Usage-tier revenue growth Apr→Oct 2026", gm.loc[OCT26, "usage_post"] / gm.loc[APR26, "usage_post"], "x", "2026-04..2026-10", "usage_post Oct / Apr", src, "", "usage_oct/usage_apr", ""),
    ]

    # payback by channel: CAC / (new-customer ARPA × subscription GM Oct26)
    cac = pd.read_csv(SERIES / "channels_cac.csv").set_index("channel")
    gm_sub = gm.loc[OCT26, "gm"]
    pdef = "Payback months = CAC / (new-customer ARPA × gross margin). GM = subscription GM Oct 2026 (mrr_post basis, matches ARPA basis)."
    for ch in ["meta", "google", "app_store", "partners", "content", "outbound", "blended"]:
        r = cac.loc[ch]
        rows.append(_row(f"der_payback_{ch}", 8, f"CAC payback, {ch} (May–Oct 2026)", r["cac"] / (r["arpa_new"] * gm_sub), "months", "2026-05..2026-10",
                         pdef, "bank_transactions.csv; stripe_customers.csv; stripe_invoices.csv", "new paying May–Oct 2026 by Stripe channel metadata",
                         "cac/(arpa_new×gm_sub_oct26)", "Internal attribution (Stripe metadata), never platform-reported. Small n per channel; no spend-to-conversion lag."))
    cs_pay = load("bank_transactions")
    sales = cs_pay[(cs_pay["category"] == "Payroll - Sales") & (cs_pay["date"] >= "2026-05-01") & (cs_pay["date"] <= "2026-10-31")]["amount_usd"].sum()
    fl_cac = (cac.loc["blended", "spend"] - sales) / cac.loc["blended", "new_paying"]
    rows.append(_row("der_payback_blended_fully_loaded", 8, "CAC payback incl. sales payroll (May–Oct 2026)", fl_cac / (cac.loc["blended", "arpa_new"] * gm_sub), "months", "2026-05..2026-10",
                     pdef + " CAC includes bank 'Payroll - Sales'.", "bank_transactions.csv; stripe_customers.csv", "May–Oct 2026", "(mkt+sales payroll)/new_paying/(arpa×gm)", ""))
    rows.append(_row("der_gm_sub_oct26", 8, "Subscription gross margin Oct 2026 (mrr_post basis)", gm_sub, "ratio", "2026-10", "(mrr_post − COGS)/mrr_post", src, "", "", "Used only for payback."))

    # external inputs (stated in source files, not computed)
    comp = load("competitors").set_index("company")
    for key, name in [("hire", "In-house hire"), ("rally", "Rally AI"), ("growlane", "Growlane"), ("adsmith", "Adsmith"), ("funnelbird", "Funnelbird")]:
        lo, hi = _price_range(comp.loc[name, "pricing"])
        for suffix, v in [("lo", lo), ("hi", hi)]:
            rows.append(_row(f"in_price_{key}_{suffix}", 2, f"{name} price ({suffix})", v, "usd_per_month", "as listed", f"Price as listed in Alex's competitor list: '{comp.loc[name, 'pricing']}'",
                             "competitors.csv", f"company={name}", "parsed from pricing text", "Founder-provided, not verified."))
    rally_raise = float(re.search(r"\$(\d+)M", comp.loc["Rally AI", "what they do"]).group(1)) * 1e6
    rows.append(_row("in_rally_raise", 10, "Rally AI Series A size", rally_raise, "usd", "2026", "Stated in competitor list", "competitors.csv", "Rally AI", "parsed", "Founder-provided, not verified."))
    notes = (RAW / "founder_notes.md").read_text()
    raise_amt = float(re.search(r"\$(\d+)M Series A", notes).group(1)) * 1e6
    rows.append(_row("in_raise", 12, "Series A size", raise_amt, "usd", "", "Round size from founder notes", "founder_notes.md", "", "parsed", ""))
    rows.append(_row("in_features_alex", "", "Features Alex wanted on one slide", float(re.search(r"all (\d+) features", notes).group(1)), "count", "", "Count of features listed in note 5", "founder_notes.md", "", "count", ""))

    # forward-looking arithmetic, explicitly labelled
    d = t.loc[OCT26, "mrr_post"] - t.loc[APR26, "mrr_post"]
    pace = d / 6
    rows += [
        _row("der_net_new_mrr_pace", 12, "Average net new MRR per month, May–Oct 2026", pace, "usd_per_month", "2026-05..2026-10", "(mrr_post Oct − Apr)/6", "stripe_invoices.csv", "", "Δ/6", ""),
        _row("der_arr_12m_at_pace", 12, "ARR in 12 months if May–Oct net-new pace holds (linear)", (t.loc[OCT26, "mrr_post"] + 12 * pace) * 12, "usd", "2027-10", "(mrr_post Oct26 + 12 × avg monthly net new MRR) × 12",
             "stripe_invoices.csv", "", "(mrr+12×pace)×12", "EXTRAPOLATION, not a forecast: assumes flat net-new pace, no acceleration from the raise."),
    ]
    mix = pd.read_csv(SERIES / "revenue_plan_mix_oct26.csv").set_index("plan")
    prices = load("stripe_prices").set_index("nickname")["unit_amount_usd"]
    move = {"Starter (legacy)": "Starter", "Growth (legacy)": "Growth", "Pro (legacy)": "Pro + Autopilot", "Scale (legacy)": "Scale + Autopilot"}
    up = sum(mix.loc[a, "paying"] * (prices[b] - prices[a]) for a, b in move.items() if a in mix.index)
    leg_n = sum(mix.loc[a, "paying"] for a in move if a in mix.index)
    rows += [
        _row("der_legacy_customers", 12, "Paying customers still on legacy prices (Oct 2026)", float(leg_n), "count", "2026-10", "Paying customers whose Oct-26 plan is a (legacy) price", "stripe_invoices.csv", "plan contains '(legacy)'", "count", "No customer has ever changed plan in Stripe."),
        _row("der_legacy_reprice_upside", 12, "MRR uplift if legacy customers move to same-tier new prices", up, "usd_per_month", "2026-10", "Σ paying × (new list price − legacy list price), Starter→Starter, Growth→Growth, Pro→Pro+Autopilot, Scale→Scale+Autopilot",
             "stripe_prices.csv; stripe_invoices.csv", "", "Σ n×Δprice", "Scenario arithmetic at list prices; assumes zero churn from migration."),
        _row("der_legacy_scale_n", 12, "Legacy Scale customers (same price as Scale + Autopilot)", float(mix.loc["Scale (legacy)", "paying"]), "count", "2026-10", "Paying on Scale (legacy)", "stripe_invoices.csv", "", "count", "Legacy Scale $2,499 vs Scale + Autopilot $2,490: Autopilot at no extra price."),
    ]
    return pd.DataFrame(rows, columns=COLS)
