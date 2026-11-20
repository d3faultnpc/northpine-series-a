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
    gcav = ("Deviation from the original definition (denominator mrr_post only), proposed by the orchestrator and accepted by Zakhar: COGS serves usage-tier "
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
    # price ladder (slide 2) and positioning matrix (slide 10) series
    arpa = pd.read_csv(SERIES / "revenue_mrr_monthly.csv").set_index("month").loc[str(OCT26)]
    arpa_xbl = arpa["mrr_ex_brightline"] / (arpa["paying_customers"] - 1)
    ladder = [("In-house growth hire", *_price_range(comp.loc["In-house hire", "pricing"]), "ink"),
              ("Growth agency (Growlane)", *_price_range(comp.loc["Growlane", "pricing"]), "ink"),
              ("Rally AI (ads only)", *_price_range(comp.loc["Rally AI", "pricing"]), "ink"),
              ("Northpine, average customer", arpa_xbl, arpa_xbl, "accent")]
    pd.DataFrame(ladder, columns=["option", "low", "high", "color"]).to_csv(SERIES / "derived_price_ladder.csv", index=False)
    # Qualitative positions (0–100): x = scope of the growth loop (one job → ads+SEO+email),
    # y = autonomy (human runs it → agent runs it). Judgment from competitors.csv descriptions.
    mx = [
        ("Northpine", 88, 82, True, "bottom", "ads + SEO + email, Autopilot"),
        ("Rally AI", 38, 78, False, "right", "AI media buyer, Meta + Google"),
        ("Funnelbird", 14, 64, False, "right", "SEO autopilot only"),
        ("Adsmith", 14, 26, False, "right", "ad creatives only"),
        ("Shopify Magic", 44, 12, False, "right", "built-in AI helpers"),
        ("Agencies, in-house hires", 84, 26, False, "top", "full scope, human-run"),
    ]
    pd.DataFrame(mx, columns=["label", "x", "y", "accent", "pos", "sub"]).to_csv(SERIES / "derived_matrix.csv", index=False)

    _appendix_series()

    rally_raise = float(re.search(r"\$(\d+)M", comp.loc["Rally AI", "what they do"]).group(1)) * 1e6
    rows.append(_row("in_rally_raise", 10, "Rally AI Series A size", rally_raise, "usd", "2026", "Stated in competitor list", "competitors.csv", "Rally AI", "parsed", "Founder-provided, not verified."))
    notes = (RAW / "founder_notes.md").read_text()
    raise_amt = float(re.search(r"\$(\d+)M Series A", notes).group(1)) * 1e6
    rows.append(_row("in_raise", 12, "Series A size", raise_amt, "usd", "", "Round size from founder notes", "founder_notes.md", "", "parsed", ""))
    rows.append(_row("in_features_alex", "", "Features Alex wanted on one slide", float(re.search(r"all (\d+) features", notes).group(1)), "count", "", "Count of features listed in note 5", "founder_notes.md", "", "count", ""))

    # forward-looking arithmetic, explicitly labelled
    d = t.loc[OCT26, "mrr_post"] - t.loc[APR26, "mrr_post"]
    pace = d / 6
    oct25 = pd.Period("2025-10", "M")
    pre = (t.loc[APR26, "mrr_post"] - t.loc[oct25, "mrr_post"]) / 6
    rows += [
        _row("der_net_new_mrr_pre", 4, "Average net new MRR per month, Nov 2025–Apr 2026", pre, "usd_per_month", "2025-11..2026-04", "(mrr_post Apr26 − Oct25)/6", "stripe_invoices.csv", "", "Δ/6", ""),
        _row("der_net_new_mrr_accel", 4, "Net new MRR per month, May–Oct vs Nov–Apr", pace / pre, "x", "", "pace / pre", "stripe_invoices.csv", "", "", ""),
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
    from load import customer_month_mrr, brightline_id
    cm = customer_month_mrr()
    bl_oct = cm[(cm["month"] == OCT26) & (cm["customer"] == brightline_id())]["mrr_post"].sum()
    rows += [
        _row("der_brightline_arr", 9, "Brightline ARR inside the headline ARR (Oct 2026)", bl_oct * 12, "usd", "2026-10", "Brightline mrr_post Oct 2026 × 12", "stripe_invoices.csv", "customer = Brightline", "mrr×12", "Included in the headline ARR; Brightline counts as 1 of the paying customers."),
        _row("der_arr_post_ex_bl", 9, "ARR excluding Brightline (Oct 2026)", (t.loc[OCT26, "mrr_post"] - bl_oct) * 12, "usd", "2026-10", "(mrr_post Oct 2026 − Brightline mrr_post) × 12", "stripe_invoices.csv", "customer ≠ Brightline", "(mrr−bl)×12", ""),
    ]
    rows += _agent_work_rows()
    rows += _noraise_rows()
    return pd.DataFrame(rows, columns=COLS)


def _appendix_series():
    """Presentation-ready slices of domain series for appendix tables (no new numbers)."""
    b = pd.read_csv(SERIES / "revenue_arr_bridge.csv").set_index("step")["value_usd"]
    labels = [
        ("kpi_arr_oct26", "KPI-sheet ARR (Oct 2026 'MRR' × 12)"),
        ("kpi_vs_stripe_cash", "KPI rounding vs Stripe cash collected"),
        ("less_annual_prepay_11_12", "Less annual prepay collected in Oct (11/12 not yet earned)"),
        ("add_annual_prepaid_earlier_1_12", "Add 1/12 of annual plans prepaid in earlier months"),
        ("less_usage", "Less usage-tier fees (reported separately)"),
        ("less_one_time_fees", "Less one-time onboarding and agency setup fees"),
        ("add_back_discounts_in_cash", "Add back discounts already netted in cash"),
        ("less_discounts_recurring", "Less discounts on recurring plans"),
        ("timing_paid_vs_service_month", "Timing: paid month vs service month"),
        ("less_refund_dispute_clawbacks", "Refunds and lost disputes in Oct"),
        ("residual", "Unexplained residual"),
        ("arr_post_oct26", "ARR, normalized, post-discount"),
    ]
    pd.DataFrame([(lab, round(b[k], 2)) for k, lab in labels], columns=["step", "usd"]).to_csv(SERIES / "derived_a1_bridge.csv", index=False)

    c = pd.read_csv(SERIES / "retention_cohorts.csv")
    c = c[c["grain"] == "quarter"].copy()
    for k in ["3", "6", "12"]:
        for col in [f"logo_m{k}", f"nrr_m{k}", f"grr_m{k}"]:
            c.loc[~c[f"m{k}_complete"].astype(bool), col] = None
    c["label"] = c["cohort"] + c["left_censored"].map({True: " *", False: ""})
    c[["label", "n", "start_mrr_post", "logo_m3", "nrr_m3", "logo_m6", "nrr_m6", "logo_m12", "grr_m12", "nrr_m12"]].to_csv(SERIES / "derived_a2_cohorts.csv", index=False)

    a = pd.read_csv(SERIES / "channels_attribution_monthly.csv")
    a = a[a["month"] >= "2026-06"]
    g = a.groupby("platform")[["platform_reported_trials", "internal_trials", "platform_spend"]].sum().reset_index()
    g["ratio"] = g["platform_reported_trials"] / g["internal_trials"]
    g["platform"] = g["platform"].str.capitalize()
    g.to_csv(SERIES / "derived_a3_attribution.csv", index=False)

    s = pd.read_csv(SERIES / "retention_support_quarterly.csv")
    s["label"] = s["quarter"] + s["partial"].map({True: " (Oct)", False: ""})
    s[["label", "tickets", "avg_paying", "tickets_per_paying", "share_agent_related", "median_first_response_h", "csat_mean"]].to_csv(SERIES / "derived_a4_support.csv", index=False)

    o = pd.read_csv(SERIES / "burn_opex_breakdown.csv")
    o = o[o["window"] == "last_3m"]
    names = {"payroll": "Payroll", "cogs": "Cost of revenue", "marketing": "Marketing", "ga": "G&A"}
    rows = []
    for bucket in ["payroll", "cogs", "marketing", "ga"]:
        sub = o[o["bucket"] == bucket].sort_values("per_month_usd", ascending=False)
        top = ", ".join(sub["line"].head(3).str.replace(r" \(20% of first-year MRR\)", "", regex=True))
        rows.append((names[bucket], top, sub["per_month_usd"].sum(), sub["share_of_out"].sum()))
    rows.append(("Total cash out", "", o["per_month_usd"].sum(), o["share_of_out"].sum()))
    pd.DataFrame(rows, columns=["bucket", "largest_lines", "per_month", "share"]).to_csv(SERIES / "derived_a5_opex.csv", index=False)


def _agent_work_rows():
    from load import customer_month_mrr
    u = load("product_usage_monthly")
    cm = customer_month_mrr()
    paying = set(cm[(cm["month"] == OCT26) & (cm["mrr_post"] > 0)]["customer"])
    o = u[(u["period"] == OCT26) & u["stripe_customer_id"].isin(paying)]
    out = []
    for col, name, slide in [("ad_variations_launched", "Ad variations launched by the agent", 3),
                             ("articles_published", "SEO articles published by the agent", 3),
                             ("emails_sent_by_agent", "Emails sent by the agent", 3),
                             ("agent_actions", "Agent actions", 3)]:
        out.append(_row(f"der_oct_{col}", slide, f"{name}, Oct 2026 (paying customers)", float(o[col].sum()), "count", "2026-10",
                        f"Sum of {col} over paying customers in Oct 2026", "product_usage_monthly.csv", "month=2026-10, mrr_post>0", f"sum({col})", ""))
    return out


def _noraise_rows():
    """What happens without the round: simple monthly projection from Oct 2026, labelled ASSUMPTION.
    revenue = subscription + usage; grows linearly by a net-new pace; COGS = Oct COGS share of revenue;
    other cash costs (payroll, marketing, G&A) either frozen at the Aug–Oct average or growing at the
    Apr→Oct 2026 monthly increase. Cash in = revenue (ignores annual-prepay timing: conservative)."""
    t = monthly_totals()
    b = pd.read_csv(SERIES / "burn_monthly.csv")
    b["month"] = pd.PeriodIndex(b["month"], freq="M")
    b = b.set_index("month")
    b["opex_other"] = b["opex_payroll"] + b["opex_marketing"] + b["opex_ga"]
    last3 = [OCT26 - 2, OCT26 - 1, OCT26]
    opex0 = b.loc[last3, "opex_other"].mean()
    opex_step = (b.loc[OCT26, "opex_other"] - b.loc[APR26, "opex_other"]) / 6
    rev0 = t.loc[OCT26, "mrr_post"] + t.loc[OCT26, "usage_post"]
    cogs_share = b.loc[OCT26, "opex_cogs"] / rev0
    pace = (t.loc[OCT26, "mrr_post"] - t.loc[APR26, "mrr_post"]) / 6
    cash0 = b.loc[OCT26, "closing_cash"]
    growth = {"pace": pace, "half": pace / 2, "flat": 0.0}
    costs = {"freeze": 0.0, "grow": opex_step}
    rows, series = [], []
    src = "bank_balance_monthly.csv; bank_transactions.csv; stripe_invoices.csv"
    for gk, g in growth.items():
        for ck_, step in costs.items():
            cash, be, low, out = cash0, None, (cash0, str(OCT26)), None
            for k in range(1, 37):
                mth = OCT26 + k
                rev = rev0 + g * k
                net = rev * (1 - cogs_share) - (opex0 + step * k)
                cash += net
                series.append((gk, ck_, str(mth), rev, net, cash))
                if be is None and net >= 0:
                    be = mth
                if cash < low[0]:
                    low = (cash, str(mth))
                if out is None and cash < 0:
                    out = mth
            name = f"{gk}_{ck_}"
            cav = "ASSUMPTION/scenario, not a forecast. Linear growth, no churn shock, no prepay timing; for internal briefing only."
            rows += [
                _row(f"der_scn_{name}_breakeven", "", f"No-raise scenario {name}: first month cash-flow ≥ 0", be.strftime("%Y-%m") if be else "none in 36 months", "text", "2026-11..2029-10",
                     f"growth={gk} ({g:.0f}/mo net new revenue), other costs={ck_} (+{step:.0f}/mo)", src, "", "first k: rev_k×(1−cogs%) − opex_k ≥ 0", cav),
                _row(f"der_scn_{name}_min_cash", "", f"No-raise scenario {name}: lowest cash balance", low[0], "usd", low[1], "minimum projected bank cash", src, "", "min cumulative", cav),
                _row(f"der_scn_{name}_cash_out", "", f"No-raise scenario {name}: month cash goes negative", out.strftime("%Y-%m") if out else "never in 36 months", "text", "", "first month projected cash < 0", src, "", "", cav),
            ]
    rows += [
        _row("der_scn_opex0", "", "Other cash costs per month (payroll+marketing+G&A), Aug–Oct 2026 avg", opex0, "usd_per_month", "2026-08..2026-10", "bank buckets", src, "", "mean", ""),
        _row("der_scn_opex_step", "", "Monthly increase of other cash costs, Apr→Oct 2026", opex_step, "usd_per_month", "2026-04..2026-10", "(Oct − Apr)/6", src, "", "", ""),
        _row("der_scn_cogs_share", "", "COGS share of revenue, Oct 2026", cogs_share, "ratio", "2026-10", "COGS / (mrr_post+usage)", src, "", "", ""),
        _row("der_scn_gp_step", "", "Monthly increase of gross profit at May–Oct pace", pace * (1 - cogs_share), "usd_per_month", "2026-05..2026-10", "net new MRR pace × (1 − COGS share)", src, "", "pace×(1−cogs%)", "ASSUMPTION: linear pace."),
        _row("der_scn_net_step", "", "Monthly burn reduction if costs keep growing as in 2026", pace * (1 - cogs_share) - opex_step, "usd_per_month", "", "gross-profit step − other-cost step", src, "", "", "ASSUMPTION: linear pace."),
        _row("der_scn_rev0", "", "Recurring revenue Oct 2026 (subscription + usage)", rev0, "usd_per_month", "2026-10", "", src, "", "", ""),
    ]
    pd.DataFrame(series, columns=["growth", "costs", "month", "revenue", "net_cash_flow", "cash"]).to_csv(SERIES / "derived_noraise_scenarios.csv", index=False)
    return rows
