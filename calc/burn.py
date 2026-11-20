"""Burn / runway / gross margin / opex / team. Bank is primary for cash. Definitions: README › Definitions.

run() -> metric rows (§5 columns). Writes calc/series/burn_*.csv.
Sign convention: series `net_burn` = money_in + money_out (negative = cash consumed, as in the bank
and KPI sheet). Metrics named `burn_*_avg*` / `burn_total_*` are reported as POSITIVE cash consumed
(= -net_burn) so slides can read "$X/mo burn" without sign juggling.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from load import CUTOFF, MONTHS, OCT26, APR26, SERIES, load, monthly_totals

RAISE = 8_000_000.0
CS_SHARE = 0.5  # ASSUMPTION for gm_incl_cs (open input; Zakhar may override)
BURN_UP = 0.5   # +50% burn sensitivity
BUFFER_MONTHS = 6
AVG3 = pd.period_range("2026-08", "2026-10", freq="M")
AVG6 = pd.period_range("2026-05", "2026-10", freq="M")
PRIOR6 = pd.period_range("2025-11", "2026-04", freq="M")
YTD26 = pd.period_range("2026-01", "2026-10", freq="M")
SUMMER = pd.period_range("2026-06", "2026-07", freq="M")
POST_MAY = pd.period_range("2026-05", "2026-10", freq="M")

BANK = "bank_balance_monthly.csv"
TX = "bank_transactions.csv"
KPI = "founder_kpi_sheet.csv"
TEAM = "team.csv"
USAGE = "product_usage_monthly.csv"
MRR_SRC = "stripe_invoices.csv (via load.monthly_totals)"

COGS_VENDORS = {"LLM inference": ["OpenAI", "Anthropic", "Together AI"],
                "Hosting": ["AWS", "Vercel"], "Support tools": ["Intercom"]}

COLS = ["id", "slide", "metric", "value", "unit", "period", "definition", "source_file", "filter",
        "formula", "caveat"]


def _bucket(cat: str) -> str:
    if cat.startswith("Cost of revenue"):
        return "cogs"
    if cat.startswith("Payroll"):
        return "payroll"
    if cat.startswith("Marketing"):
        return "marketing"
    if cat.startswith("G&A"):
        return "ga"
    if cat.startswith("Revenue"):
        return "revenue_in"
    return "other"


def _tx() -> pd.DataFrame:
    tx = load("bank_transactions").copy()
    tx = tx[tx["date"] <= CUTOFF]
    tx["month"] = tx["date"].dt.to_period("M")
    tx["bucket"] = tx["category"].map(_bucket)
    tx["line"] = tx["category"].str.split(" - ", n=1).str[1]
    return tx


def _bank() -> pd.DataFrame:
    b = load("bank_balance_monthly").set_index("period").reindex(MONTHS)
    b["net_burn"] = b["money_in_usd"] + b["money_out_usd"]
    return b


def _active(team: pd.DataFrame, month_end: pd.Timestamp) -> pd.Series:
    return (team["start_date"] <= month_end) & (team["end_date"].isna() | (team["end_date"] > month_end))


def _zero_month(cash: float, burn: float) -> pd.Period:
    """Month in which cash hits zero at constant burn, counting from the cut-off month end."""
    return OCT26 + math.ceil(cash / burn)


def run() -> pd.DataFrame:
    rows: list[dict] = []

    def add(id, slide, metric, value, unit, period, definition, source, filt, formula, caveat=""):
        rows.append(dict(id=id, slide=slide, metric=metric, value=value, unit=unit, period=period,
                         definition=definition, source_file=source, filter=filt, formula=formula,
                         caveat=caveat))

    tx = _tx()
    bank = _bank()
    t = monthly_totals()

    # ------------------------------------------------------------ 1. burn series + integrity checks
    by_b = tx.pivot_table(index="month", columns="bucket", values="amount_usd", aggfunc="sum").reindex(MONTHS).fillna(0)
    tx_in = tx[tx["amount_usd"] > 0].groupby("month")["amount_usd"].sum().reindex(MONTHS).fillna(0)
    tx_out = tx[tx["amount_usd"] < 0].groupby("month")["amount_usd"].sum().reindex(MONTHS).fillna(0)
    roll = bank["opening_balance_usd"] + bank["money_in_usd"] + bank["money_out_usd"] - bank["closing_balance_usd"]
    chain = (bank["opening_balance_usd"] - bank["closing_balance_usd"].shift(1)).iloc[1:]
    d_in = tx_in - bank["money_in_usd"]
    d_out = tx_out - bank["money_out_usd"]

    s = pd.DataFrame({
        "month": MONTHS.astype(str),
        "money_in": bank["money_in_usd"].values,
        "money_out": bank["money_out_usd"].values,
        "net_burn": bank["net_burn"].values,
        "burn": (-bank["net_burn"]).values,
        "closing_cash": bank["closing_balance_usd"].values,
        "opex_payroll": -by_b["payroll"].values,
        "opex_cogs": -by_b["cogs"].values,
        "opex_marketing": -by_b["marketing"].values,
        "opex_ga": -by_b["ga"].values,
        "tx_in_minus_bank_in": d_in.values,
        "tx_out_minus_bank_out": d_out.values,
        "rollforward_diff": roll.values,
    })
    s["burn_3m_avg"] = s["burn"].rolling(3).mean()
    s.to_csv(SERIES / "burn_monthly.csv", index=False)

    cash = float(bank.loc[OCT26, "closing_balance_usd"])
    burn3 = float(-bank.loc[AVG3, "net_burn"].mean())
    burn6 = float(-bank.loc[AVG6, "net_burn"].mean())
    runway = cash / burn3
    bdef = "Net burn = bank money_in + money_out per month, shown as positive cash consumed"
    add("burn_cash_oct26", 12, "Cash at cut-off", cash, "usd", "2026-10-31",
        "Bank closing balance at month end", BANK, "month == 2026-10", "closing_balance_usd")
    add("burn_net_oct26", 12, "Net burn Oct 2026", float(-bank.loc[OCT26, "net_burn"]), "usd_per_month", "2026-10",
        bdef, BANK, "month == 2026-10", "-(money_in_usd + money_out_usd)")
    add("burn_avg_3m", 12, "Avg monthly net burn, last 3 months", burn3, "usd_per_month", "2026-08..2026-10",
        bdef, BANK, "month in Aug-Oct 2026", "mean(-(money_in + money_out))")
    add("burn_avg_6m", 12, "Avg monthly net burn, last 6 months", burn6, "usd_per_month", "2026-05..2026-10",
        bdef, BANK, "month in May-Oct 2026", "mean(-(money_in + money_out))")
    add("burn_runway_months", 12, "Runway at current burn (no raise)", runway, "months", "as of 2026-10-31",
        "Cut-off cash / 3-month average net burn", BANK, "cash Oct 2026; burn Aug-Oct 2026",
        "burn_cash_oct26 / burn_avg_3m", "Constant-burn extrapolation; burn has been falling as cash-in grows")
    add("burn_runway_months_6m", "A5", "Runway at 6-month average burn (no raise)", cash / burn6, "months",
        "as of 2026-10-31", "Cut-off cash / 6-month average net burn", BANK, "burn May-Oct 2026",
        "burn_cash_oct26 / burn_avg_6m", "Sensitivity to the averaging window")
    add("burn_gross_out_avg_3m", "A5", "Avg monthly gross cash out, last 3 months",
        float(-bank.loc[AVG3, "money_out_usd"].mean()), "usd_per_month", "2026-08..2026-10",
        "Bank money_out per month, positive", BANK, "Aug-Oct 2026", "mean(-money_out_usd)")
    add("burn_check_rollforward_maxdiff", "none", "Max |opening + in + out - closing|", float(roll.abs().max()),
        "usd", "2025-01..2026-10", "Bank balance roll-forward integrity", BANK, "all 22 months",
        "max|opening + money_in + money_out - closing|")
    add("burn_check_chain_maxdiff", "none", "Max |opening(m) - closing(m-1)|", float(chain.abs().max()), "usd",
        "2025-02..2026-10", "Month-to-month balance continuity", BANK, "21 month pairs",
        "max|opening_m - closing_(m-1)|")
    add("burn_check_tx_vs_bank_maxdiff", "none", "Max |transactions - bank monthly in/out|",
        float(max(d_in.abs().max(), d_out.abs().max())), "usd", "2025-01..2026-10",
        "Sum of bank_transactions by sign per month vs bank_balance_monthly money_in / money_out",
        f"{TX}; {BANK}", "all 22 months", "max(|sum(tx>0) - money_in|, |sum(tx<0) - money_out|)")

    # ------------------------------------------------------------ 2. KPI sheet vs bank / team
    kpi = load("founder_kpi_sheet").set_index("period").reindex(MONTHS)
    team = load("team")
    rec = pd.DataFrame({"month": MONTHS.astype(str),
                        "kpi_net_burn": kpi["net burn"].values, "bank_net_burn": bank["net_burn"].values,
                        "kpi_cash": kpi["cash"].values, "bank_cash": bank["closing_balance_usd"].values,
                        "kpi_headcount": kpi["headcount"].values})
    rec["d_net_burn"] = rec["kpi_net_burn"] - rec["bank_net_burn"]
    rec["d_cash"] = rec["kpi_cash"] - rec["bank_cash"]
    tol_b = np.maximum(1000, 0.01 * rec["bank_net_burn"].abs())
    tol_c = np.maximum(1000, 0.01 * rec["bank_cash"].abs())
    rec["flag_net_burn"] = rec["d_net_burn"].abs() > tol_b
    rec["flag_cash"] = rec["d_cash"].abs() > tol_c
    rec["team_headcount"] = [int(_active(team, m.to_timestamp(how="end")).sum()) for m in MONTHS]
    rec["d_headcount"] = rec["kpi_headcount"] - rec["team_headcount"]
    rec.to_csv(SERIES / "burn_kpi_reconciliation.csv", index=False)
    kdef = "KPI sheet value minus bank value per month; flag if |diff| > max($1k, 1% of bank)"
    add("burn_kpi_netburn_flagged_months", "A6", "KPI net burn months off vs bank", int(rec["flag_net_burn"].sum()),
        "count", "2025-01..2026-10", kdef, f"{KPI}; {BANK}", "22 months", "count(|kpi - bank| > max(1000, 1%))")
    add("burn_kpi_netburn_maxdiff", "A6", "Max |KPI net burn - bank|", float(rec["d_net_burn"].abs().max()), "usd",
        "2025-01..2026-10", kdef, f"{KPI}; {BANK}", "22 months", "max|kpi net burn - (money_in + money_out)|",
        "KPI sheet rounds to $1k")
    add("burn_kpi_cash_flagged_months", "A6", "KPI cash months off vs bank", int(rec["flag_cash"].sum()), "count",
        "2025-01..2026-10", kdef, f"{KPI}; {BANK}", "22 months", "count(|kpi cash - closing| > max(1000, 1%))")
    add("burn_kpi_cash_maxdiff", "A6", "Max |KPI cash - bank closing|", float(rec["d_cash"].abs().max()), "usd",
        "2025-01..2026-10", kdef, f"{KPI}; {BANK}", "22 months", "max|kpi cash - closing_balance_usd|",
        "KPI sheet rounds to $1k")
    add("team_kpi_headcount_mismatch_months", "A6", "KPI headcount months != team.csv", int((rec["d_headcount"] != 0).sum()),
        "count", "2025-01..2026-10", "KPI headcount vs team.csv active at month end (start <= month end, end empty or > month end)",
        f"{KPI}; {TEAM}", "22 months", "count(kpi headcount != active staff)")
    add("team_kpi_headcount_maxdiff", "A6", "Max |KPI headcount - team.csv|", int(rec["d_headcount"].abs().max()),
        "count", "2025-01..2026-10", "As team_kpi_headcount_mismatch_months", f"{KPI}; {TEAM}", "22 months",
        "max|kpi headcount - active staff|")

    # ------------------------------------------------------------ 3. post-raise runway (no invented plan)
    post = cash + RAISE
    add("burn_postraise_runway_current", 12, "Runway after $8M at current burn", post / burn3, "months",
        "from 2026-10-31", "(cut-off cash + $8M raise) / 3-month avg net burn", BANK, "burn Aug-Oct 2026",
        "(burn_cash_oct26 + 8000000) / burn_avg_3m",
        "Not a plan: assumes burn stays at today's level; no planned post-raise burn was provided")
    add("burn_postraise_maxburn_24m", 12, "Max monthly burn for 24 months runway", post / 24, "usd_per_month",
        "from 2026-10-31", "(cut-off cash + $8M) / 24", BANK, "cash Oct 2026", "(burn_cash_oct26 + 8000000) / 24")
    add("burn_postraise_maxburn_18m", 12, "Max monthly burn for 18 months runway", post / 18, "usd_per_month",
        "from 2026-10-31", "(cut-off cash + $8M) / 18", BANK, "cash Oct 2026", "(burn_cash_oct26 + 8000000) / 18")
    add("burn_postraise_runway_burn_plus50", 12, "Runway after $8M if burn rises 50%", post / (burn3 * (1 + BURN_UP)),
        "months", "from 2026-10-31", "(cut-off cash + $8M) / (1.5 x 3-month avg net burn)", BANK, "burn Aug-Oct 2026",
        "(burn_cash_oct26 + 8000000) / (1.5 * burn_avg_3m)",
        "ASSUMPTION: +50% burn is a sensitivity, not a plan")
    add("burn_postraise_multiple_of_current", 12, "24m max burn as multiple of current burn", (post / 24) / burn3,
        "ratio", "from 2026-10-31", "burn_postraise_maxburn_24m / burn_avg_3m", BANK, "", "maxburn_24m / burn_avg_3m",
        "Unit 'ratio' here is a multiple (>1), not a 0..1 share")
    zero = _zero_month(cash, burn3)
    close_by = OCT26 + math.floor(runway - BUFFER_MONTHS)
    add("burn_zero_cash_month", 12, "Zero-cash month without the raise", str(zero), "month", "from 2026-10-31",
        "First month in which cumulative burn at the 3-month average exceeds cut-off cash", BANK, "burn Aug-Oct 2026",
        "2026-10 + ceil(burn_cash_oct26 / burn_avg_3m)", "Constant-burn extrapolation")
    add("burn_close_by_month", 12, "Round must close by (6-mo buffer)", str(close_by), "month", "from 2026-10-31",
        "Last month-end at which remaining cash still covers 6 months of burn at the 3-month average", BANK,
        "burn Aug-Oct 2026", "2026-10 + floor(burn_runway_months - 6)",
        "6-month buffer is a convention, not from data; constant burn")

    # ------------------------------------------------------------ 4. COGS + gross margin
    cg = tx[tx["bucket"] == "cogs"].pivot_table(index="month", columns="counterparty", values="amount_usd",
                                                aggfunc="sum").reindex(MONTHS).fillna(0).mul(-1)
    for vs in COGS_VENDORS.values():
        for v in vs:
            if v not in cg:
                cg[v] = 0.0
    inference = cg[COGS_VENDORS["LLM inference"]].sum(axis=1)
    hosting = cg[COGS_VENDORS["Hosting"]].sum(axis=1)
    support = cg[COGS_VENDORS["Support tools"]].sum(axis=1)
    cogs = inference + hosting + support
    cs_pay = -tx[tx["category"] == "Payroll - Customer Success"].groupby("month")["amount_usd"].sum().reindex(MONTHS).fillna(0)
    usage = load("product_usage_monthly")
    actions = usage[usage["period"] <= OCT26].groupby("period")["agent_actions"].sum().reindex(MONTHS).fillna(0)
    mrr = t["mrr_post"]
    gm = (mrr - cogs) / mrr
    gm_cs = (mrr - cogs - CS_SHARE * cs_pay) / mrr
    g = pd.DataFrame({
        "month": MONTHS.astype(str), "mrr_post": mrr.values, "paying_customers": t["paying_customers"].values,
        **{f"cogs_{v.lower().replace(' ', '_')}": cg[v].values for vs in COGS_VENDORS.values() for v in vs},
        "cogs_inference": inference.values, "cogs_hosting": hosting.values, "cogs_support": support.values,
        "cogs_total": cogs.values, "gm": gm.values, "cs_payroll": cs_pay.values, "gm_incl_cs": gm_cs.values,
        "inference_pct_mrr": (inference / mrr).values, "agent_actions": actions.values,
        "inference_per_paying": (inference / t["paying_customers"]).values,
        "inference_per_1k_actions": (inference / actions * 1000).values,
        "actions_per_paying": (actions / t["paying_customers"]).values,
    })
    g.to_csv(SERIES / "burn_gm_monthly.csv", index=False)

    gdef = "GM = (mrr_post - COGS) / mrr_post; COGS = bank LLM inference + hosting + Intercom, by payment month"
    gsrc = f"{TX}; {MRR_SRC}"
    gcav = "ASSUMPTION: bank payment month = cost month (vendor invoices paid 2nd-6th; spike timing matches usage)"
    add("gm_oct26", 7, "Gross margin Oct 2026", float(gm[OCT26]), "ratio", "2026-10", gdef, gsrc, "month == 2026-10",
        "(mrr_post - cogs) / mrr_post", gcav)
    add("gm_apr26", 7, "Gross margin Apr 2026", float(gm[APR26]), "ratio", "2026-04", gdef, gsrc, "month == 2026-04",
        "(mrr_post - cogs) / mrr_post", gcav)
    gmin_m = gm[POST_MAY].idxmin()
    add("gm_min_may_oct26", 7, "Lowest monthly GM May-Oct 2026", float(gm[gmin_m]), "ratio", str(gmin_m), gdef, gsrc,
        "min over May-Oct 2026", "min((mrr_post - cogs) / mrr_post)", gcav)
    add("gm_min_month", 7, "Month of lowest GM May-Oct 2026", str(gmin_m), "month", "2026-05..2026-10", gdef, gsrc,
        "argmin over May-Oct 2026", "argmin(gm)")
    add("gm_avg_jun_jul26", 7, "Average GM Jun-Jul 2026", float(gm[SUMMER].mean()), "ratio", "2026-06..2026-07", gdef,
        gsrc, "Jun, Jul 2026", "mean(gm_jun, gm_jul)", gcav + "; simple mean of monthly ratios")
    add("gm_cogs_oct26", "A5", "COGS Oct 2026", float(cogs[OCT26]), "usd_per_month", "2026-10",
        "Bank LLM inference + hosting + Intercom", TX, "month == 2026-10", "sum(-amount_usd) COGS categories")
    for m, lab in [(APR26, "apr26"), (gmin_m, "peak"), (OCT26, "oct26")]:
        add(f"gm_inference_pct_mrr_{lab}", 7, f"LLM inference as % of mrr_post ({m})", float(inference[m] / mrr[m]),
            "ratio", str(m), "Bank LLM inference (OpenAI + Anthropic + Together AI) / mrr_post", gsrc, f"month == {m}",
            "inference / mrr_post")
        add(f"gm_inference_per_1k_actions_{lab}", 7, f"Inference cost per 1k agent actions ({m})",
            float(inference[m] / actions[m] * 1000), "usd", str(m),
            "Bank LLM inference / sum(agent_actions) x 1000", f"{TX}; {USAGE}", f"month == {m}",
            "inference / agent_actions * 1000", "agent_actions from product_usage_monthly (all customers incl. trialing)")
        add(f"gm_actions_{lab}", 7, f"Agent actions ({m})", float(actions[m]), "count", str(m),
            "Sum of agent_actions across customers", USAGE, f"month == {m}", "sum(agent_actions)")
        add(f"gm_inference_per_paying_{lab}", 7, f"Inference cost per paying customer ({m})",
            float(inference[m] / t["paying_customers"][m]), "usd_per_month", str(m),
            "Bank LLM inference / paying customers (mrr_post > 0)", gsrc, f"month == {m}", "inference / paying_customers")
    add("gm_togetherai_first_month", 7, "First month with Together AI spend",
        str(cg.index[cg["Together AI"] > 0].min()), "month", "2025-01..2026-10",
        "First bank payment to Together AI", TX, "counterparty == Together AI", "min(month)")
    add("gm_openai_drop_jul_aug", 7, "OpenAI spend change Jul->Aug 2026",
        float(cg.loc[pd.Period("2026-08", "M"), "OpenAI"] / cg.loc[pd.Period("2026-07", "M"), "OpenAI"] - 1), "ratio",
        "2026-07..2026-08", "OpenAI bank spend Aug / Jul - 1", TX, "counterparty == OpenAI", "openai_aug / openai_jul - 1",
        "Negative = drop")
    add("gm_incl_cs_oct26", "A5", "GM Oct 2026 incl. 50% of CS payroll", float(gm_cs[OCT26]), "ratio", "2026-10",
        "(mrr_post - COGS - 50% x bank Customer Success payroll) / mrr_post", gsrc, "month == 2026-10",
        "(mrr_post - cogs - 0.5 * cs_payroll) / mrr_post",
        "ASSUMPTION: half of CS time is onboarding/support of live accounts (open input; Zakhar to confirm share)")

    # ------------------------------------------------------------ 5. opex breakdown (A5)
    ob = []
    for win, months in [("2026_ytd", YTD26), ("last_3m", AVG3)]:
        w = tx[tx["month"].isin(months) & (tx["bucket"] != "revenue_in")]
        tot = -w["amount_usd"].sum()
        for (bucket, line), v in w.groupby(["bucket", "line"])["amount_usd"].sum().items():
            ob.append(dict(window=win, months=f"{months[0]}..{months[-1]}", bucket=bucket, line=line,
                           amount_usd=-v, per_month_usd=-v / len(months), share_of_out=-v / tot))
    ob = pd.DataFrame(ob).sort_values(["window", "bucket", "amount_usd"], ascending=[True, True, False])
    ob.to_csv(SERIES / "burn_opex_breakdown.csv", index=False)
    for win, months in [("ytd26", YTD26), ("3m", AVG3)]:
        w = tx[tx["month"].isin(months) & (tx["bucket"] != "revenue_in")]
        tot = -w["amount_usd"].sum()
        per = f"{months[0]}..{months[-1]}"
        add(f"burn_out_total_{win}", "A5", f"Gross cash out {per}", tot, "usd", per,
            "Sum of bank outflows excl. Stripe payouts", TX, per, "-sum(amount_usd < 0)")
        add(f"burn_in_total_{win}", "A5", f"Cash in (Stripe payouts) {per}",
            float(tx[tx["month"].isin(months) & (tx["bucket"] == "revenue_in")]["amount_usd"].sum()), "usd", per,
            "Sum of bank Stripe payouts", TX, per, "sum(Revenue - Stripe payout)")
        for b in ["payroll", "cogs", "marketing", "ga"]:
            v = -w[w["bucket"] == b]["amount_usd"].sum()
            add(f"burn_{b}_share_{win}", "A5", f"{b} share of cash out {per}", v / tot, "ratio", per,
                f"Bank {b} outflows / total outflows", TX, per, f"sum({b}) / sum(out)")
            add(f"burn_{b}_{win}", "A5", f"{b} cash out {per}", v, "usd", per, f"Bank {b} outflows", TX, per,
                f"-sum(amount_usd) where bucket == {b}")
    w3 = tx[tx["month"].isin(AVG3)]
    add("burn_marketing_avg_3m", "A5", "Marketing spend per month, last 3 months",
        float(-w3[w3["bucket"] == "marketing"]["amount_usd"].sum() / 3), "usd_per_month", "2026-08..2026-10",
        "Bank Marketing-* outflows incl. partner commissions, monthly average", TX, "Aug-Oct 2026", "-sum / 3")

    # ------------------------------------------------------------ 6. team
    end = CUTOFF.normalize()
    act = team[_active(team, CUTOFF)]
    add("team_hc_total", 11, "Headcount at cut-off", int(len(act)), "count", "2026-10-31",
        "Staff with start_date <= cut-off and end_date empty or > cut-off", TEAM, "active at 2026-10-31", "count(rows)")
    for dept, n in act.groupby("department").size().items():
        add(f"team_hc_{dept.lower().replace(' ', '_')}", 11, f"Headcount {dept}", int(n), "count", "2026-10-31",
            "Active staff by department", TEAM, f"active, department == {dept}", "count(rows)")
    gone = team[team["end_date"].notna() & (team["end_date"] <= CUTOFF)]
    add("team_departed", 11, "Departed staff (all time)", int(len(gone)), "count", "to 2026-10-31",
        "Rows with end_date <= cut-off", TEAM, "end_date <= 2026-10-31", "count(rows)")
    gone26 = gone[gone["end_date"] >= pd.Timestamp("2026-01-01")]
    add("team_departed_2026", "A5", "Departed staff in 2026", int(len(gone26)), "count", "2026-01..2026-10",
        "Rows with end_date in 2026 up to cut-off", TEAM, "2026-01-01 <= end_date <= cut-off", "count(rows)")
    add("team_hires_2026", 11, "Hires in 2026", int(((team["start_date"] >= "2026-01-01") & (team["start_date"] <= CUTOFF)).sum()),
        "count", "2026-01..2026-10", "Rows with start_date in 2026", TEAM, "2026 start", "count(rows)",
        "Includes hires who already left")
    base = float(act["base_salary_usd"].sum())
    add("team_base_payroll_annual", "A5", "Annual base payroll, active staff", base, "usd", "2026-10-31",
        "Sum of base_salary_usd for active staff", TEAM, "active at cut-off", "sum(base_salary_usd)",
        "Base only: excludes taxes, benefits, bonus, equity")
    founders = act[act["title"].isin(["CEO", "CTO"])]
    add("team_founders", 11, "Founders (CEO, CTO) active", int(len(founders)), "count", "2026-10-31",
        "Active staff titled CEO or CTO", TEAM, "title in (CEO, CTO)", "count(rows)",
        "Founder status inferred from title + 2023-02-01 start; no explicit founder flag")
    pay_bank = -tx[tx["bucket"] == "payroll"].groupby("month")["amount_usd"].sum().reindex(MONTHS).fillna(0)
    base_m = pd.Series([team.loc[_active(team, m.to_timestamp(how="end")), "base_salary_usd"].sum() / 12 for m in MONTHS],
                       index=MONTHS)
    load_f = pay_bank / base_m
    add("team_payroll_bank_oct26", "A5", "Payroll cash out Oct 2026", float(pay_bank[OCT26]), "usd_per_month", "2026-10",
        "Bank Payroll-* outflows (Gusto)", TX, "month == 2026-10", "-sum(Payroll-*)")
    add("team_payroll_load_factor_oct26", "A5", "Bank payroll / (base/12), Oct 2026", float(load_f[OCT26]), "ratio",
        "2026-10", "Bank payroll for the month / sum of active base salaries / 12", f"{TX}; {TEAM}", "month == 2026-10",
        "pay_bank / (sum(base)/12)", "Multiple (>1) = employer taxes/benefits/bonus; not a 0..1 share")
    add("team_payroll_load_factor_avg", "A5", "Bank payroll / (base/12), avg all months", float(load_f.mean()), "ratio",
        "2025-01..2026-10", "Mean over months of bank payroll / (active base / 12)", f"{TX}; {TEAM}", "22 months",
        "mean(pay_bank / (sum(base)/12))", "Range %.3f-%.3f" % (load_f.min(), load_f.max()))
    tm = pd.DataFrame({"month": MONTHS.astype(str), "headcount": rec["team_headcount"].values,
                       "payroll_bank": pay_bank.values, "base_monthly": base_m.values, "load_factor": load_f.values})
    for dept in sorted(team["department"].unique()):
        tm[f"hc_{dept.lower().replace(' ', '_')}"] = [
            int((_active(team, m.to_timestamp(how="end")) & team["department"].eq(dept)).sum()) for m in MONTHS]
    tm.to_csv(SERIES / "burn_team_monthly.csv", index=False)

    # ------------------------------------------------------------ 7. burn multiple
    def _bm(months: pd.PeriodIndex, start: pd.Period, stop: pd.Period, id_, label):
        b = float(-bank.loc[months, "net_burn"].sum())
        nn = float((mrr[stop] - mrr[start]) * 12)
        add(id_, 12, f"Burn multiple {label}", b / nn if nn > 0 else "NA", "ratio",
            f"{months[0]}..{months[-1]}", "Total bank net burn / net new ARR; net new ARR = (mrr_post end - mrr_post start) x 12",
            f"{BANK}; {MRR_SRC}", f"burn {months[0]}..{months[-1]}; ARR {start}->{stop}",
            f"sum(burn) / ((mrr_post_{stop} - mrr_post_{start}) * 12)",
            "Multiple (>1 possible), not a 0..1 share; lower is better")
        add(id_.replace("multiple", "total"), "A5", f"Total net burn {label}", b, "usd",
            f"{months[0]}..{months[-1]}", bdef, BANK, f"{months[0]}..{months[-1]}", "sum(-(money_in + money_out))")
        add(id_.replace("burn_multiple", "net_new_arr"), "A5", f"Net new ARR {label}", nn, "usd",
            f"{start}->{stop}", "(mrr_post end - mrr_post start) x 12", MRR_SRC, f"{start}->{stop}",
            f"(mrr_post_{stop} - mrr_post_{start}) * 12")

    _bm(POST_MAY, APR26, OCT26, "burn_multiple_may_oct26", "May-Oct 2026")
    _bm(PRIOR6, pd.Period("2025-10", "M"), APR26, "burn_multiple_nov25_apr26", "Nov 2025-Apr 2026")

    return pd.DataFrame(rows, columns=COLS)


if __name__ == "__main__":
    pd.set_option("display.width", 250)
    print(run()[["id", "value", "unit", "period"]].to_string())
