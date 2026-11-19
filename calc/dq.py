"""Data-quality log (CLAUDE.md §4). Writes calc/data_quality.csv; returns dq_* metric rows (§5 columns).

Everything here is computed from data/raw via calc/load.py. Issues are logged, never cleaned.
"""
from __future__ import annotations

import pandas as pd
import numpy as np

from load import (ROOT, CUTOFF, CUTOFF_MONTH, MONTHS, MAY26, OCT26, DATE_COLS, FILES, load,
                  invoices_parsed, customer_month_mrr, monthly_totals, brightline_id)

OUT = ROOT / "calc" / "data_quality.csv"
JUN26 = pd.Timestamp("2026-06-01")
MAR26 = pd.Period("2026-03", "M")


def _usd(x: float) -> str:
    return f"${x:,.0f}"


def run() -> pd.DataFrame:
    issues: list[dict] = []
    metrics: list[dict] = []

    def issue(file, text, rows, impact, treatment, sev, surfaced):
        issues.append(dict(file=file, issue=text, rows_affected=rows, metric_impact=impact,
                           treatment=treatment, severity=sev, surfaced_in=surfaced))

    def metric(id_, metric_, value, unit, period, definition, source, filter_, formula, caveat="", slide="memo"):
        metrics.append(dict(id=id_, slide=slide, metric=metric_, value=value, unit=unit, period=period,
                            definition=definition, source_file=source, filter=filter_, formula=formula,
                            caveat=caveat))

    inv = invoices_parsed()
    sub = load("stripe_subscriptions")
    cust = load("stripe_customers")
    cust_i = cust.set_index("id")
    kpi = load("founder_kpi_sheet").set_index("period")
    bank_m = load("bank_balance_monthly").set_index("period")
    bank_t = load("bank_transactions")
    ch = load("stripe_charges")
    ref = load("stripe_refunds")
    dsp = load("stripe_disputes")
    mt = monthly_totals()
    cm = customer_month_mrr()
    paid_inv = inv[inv["is_paid"]]
    paid_customers_ever = set(paid_inv.loc[paid_inv["amount_paid_usd"] > 0, "customer"])

    # ------------------------------------------------------------ 1. KPI MRR = cash collected
    cash = paid_inv[paid_inv["created"] <= CUTOFF].groupby("paid_month")["amount_paid_usd"].sum().reindex(MONTHS)
    d = (kpi["MRR"] - cash)
    n_match = int((d.abs() <= 500).sum())
    arr_ok = int((kpi["ARR"] == kpi["MRR"] * 12).sum())
    oct_cash = float(cash[OCT26])
    oct_annual = float(paid_inv[(paid_inv["paid_month"] == OCT26) & paid_inv["is_annual"]]["amount_paid_usd"].sum())
    oct_onetime = float(paid_inv[(paid_inv["paid_month"] == OCT26) & paid_inv["is_one_time"]]["amount_paid_usd"].sum())
    oct_mrr_post = float(mt.loc[OCT26, "mrr_post"])
    issue("founder_kpi_sheet", f"KPI 'MRR' = Stripe cash collected (paid invoices by created month) in {n_match}/22 months "
          f"within $500 (max |delta| {_usd(d.abs().max())}, i.e. rounded to $k). ARR = MRR x12 in {arr_ok}/22 months.",
          22, f"Oct-26 KPI MRR {_usd(kpi.loc[OCT26,'MRR'])} vs normalized mrr_post {_usd(oct_mrr_post)}; cash includes "
          f"annual prepay {_usd(oct_annual)} and one-time fees {_usd(oct_onetime)} in Oct-26",
          "Headline uses normalized mrr_post; KPI figure shown only in bridge A1", "H", "memo")
    metric("dq_kpi_mrr_oct26", "KPI-sheet 'MRR' Oct-26", float(kpi.loc[OCT26, "MRR"]), "usd", "2026-10",
           "As typed in founder KPI sheet", "founder_kpi_sheet.csv", "month=2026-10", "MRR", "Is cash collected, not MRR")
    metric("dq_kpi_arr_oct26", "KPI-sheet 'ARR' Oct-26", float(kpi.loc[OCT26, "ARR"]), "usd", "2026-10",
           "As typed in founder KPI sheet", "founder_kpi_sheet.csv", "month=2026-10", "ARR (=MRR x12)",
           "Annualized cash incl. prepay and one-time")
    metric("dq_cash_collected_oct26", "Stripe cash collected Oct-26", oct_cash, "usd", "2026-10",
           "Sum amount_paid of paid invoices created in month", "stripe_invoices.csv", "status=paid, created in Oct-26",
           "sum(amount_paid_usd)", "Before refunds/disputes")
    metric("dq_kpi_vs_cash_max_abs_delta", "Max |KPI MRR - Stripe cash| over 22 months", float(d.abs().max()), "usd",
           "2025-01..2026-10", "KPI MRR minus Stripe cash collected", "founder_kpi_sheet.csv; stripe_invoices.csv",
           "all months", "max(abs(MRR - cash))", "KPI rounded to $1k")
    metric("dq_oct26_annual_prepay_cash", "Annual prepay cash collected Oct-26", oct_annual, "usd", "2026-10",
           "Paid annual-plan invoices created in Oct-26", "stripe_invoices.csv", "plan contains (annual)",
           "sum(amount_paid_usd)", "Counted in full by KPI 'MRR'")
    metric("dq_oct26_one_time_cash", "One-time fee cash collected Oct-26", oct_onetime, "usd", "2026-10",
           "Paid one-time invoices (onboarding, agency setup) created in Oct-26", "stripe_invoices.csv",
           "plan contains (one-time)", "sum(amount_paid_usd)", "Not recurring")

    # ------------------------------------------------------------ 2/3. KPI customers
    me = OCT26.end_time
    st_counts = sub["status"].value_counts()
    n_active, n_pd, n_trial = int(st_counts.get("active", 0)), int(st_counts.get("past_due", 0)), int(st_counts.get("trialing", 0))
    paying_oct = int(mt.loc[OCT26, "paying_customers"])
    paid_inv_oct = int(paid_inv[(paid_inv["paid_month"] == OCT26) & (paid_inv["recurring_post"] > 0)
                                & ~paid_inv["is_one_time"]]["customer"].nunique())

    def live_subs(m):
        e = m.end_time
        live = (sub["created"] <= e) & (sub["ended_at"].isna() | (sub["ended_at"] > e))
        trial_only = sub["cancellation_details.reason"].eq("trial_ended_without_payment_method") | sub["status"].eq("trialing")
        in_trial = live & (trial_only | (sub["trial_end"] > e))
        return int(live.sum()), int(in_trial.sum())

    live_oct, trial_oct = live_subs(OCT26)
    issue("founder_kpi_sheet", f"KPI customers Oct-26 = {kpi.loc[OCT26,'customers']} includes trials: Stripe subs "
          f"active {n_active} + past_due {n_pd} + trialing {n_trial} = {n_active+n_pd+n_trial}; paying (mrr_post>0) = {paying_oct}; "
          f"customers with a paid recurring invoice in Oct-26 = {paid_inv_oct}, with any paid invoice = "
          f"{int(paid_inv[paid_inv['paid_month'] == OCT26]['customer'].nunique())}; {int(kpi.loc[OCT26,'customers']) - (n_active+n_pd+n_trial)} "
          f"of the KPI 344 unexplained by any Stripe count",
          int(kpi.loc[OCT26, "customers"]), f"Overstates paying customers by {int(kpi.loc[OCT26,'customers']) - paying_oct} "
          f"vs mrr_post>0 count", "Use paying customers (§3); trialing never counted", "H", "memo")
    kc = kpi["customers"]
    gap = (kc - mt["paying_customers"]).astype(int)
    issue("founder_kpi_sheet", f"KPI customers exceed Stripe paying customers (mrr_post>0) in {(gap>0).sum()}/22 months; "
          f"gap Jan-25 {gap.iloc[0]}, Apr-26 {gap[pd.Period('2026-04','M')]}, Oct-26 {gap[OCT26]}",
          22, "KPI customer series not reconcilable to Stripe paying", "Report Stripe paying series; gap per month in appendix",
          "M", "appendix")
    mar_live, mar_trial = live_subs(MAR26)
    feb, apr = pd.Period("2026-02", "M"), pd.Period("2026-04", "M")
    issue("founder_kpi_sheet", f"KPI Mar-26 customers = {kc[MAR26]} vs neighbours {kc[feb]} / {kc[apr]}. Stripe live subs at "
          f"Mar-26 month-end = {mar_live}, of which {mar_trial} in trial (TikTok trial wave); paying (mrr_post>0) = "
          f"{int(mt.loc[MAR26,'paying_customers'])}. Jump is mostly in-flight trials, not only a typo",
          1, "Spike would appear on any customers chart from KPI sheet", "Never use KPI customers; chart Stripe paying",
          "M", "memo")
    metric("dq_kpi_customers_oct26", "KPI-sheet customers Oct-26", float(kc[OCT26]), "count", "2026-10",
           "As typed in founder KPI sheet", "founder_kpi_sheet.csv", "month=2026-10", "customers", "Includes trialing")
    metric("dq_active_subs_oct26", "Stripe subscriptions active at cut-off", float(n_active), "count", "2026-10-31",
           "status=active", "stripe_subscriptions.csv", "status=active", "count")
    metric("dq_past_due_oct26", "Stripe subscriptions past_due at cut-off", float(n_pd), "count", "2026-10-31",
           "status=past_due", "stripe_subscriptions.csv", "status=past_due", "count")
    metric("dq_trialing_oct26", "Stripe subscriptions trialing at cut-off", float(n_trial), "count", "2026-10-31",
           "status=trialing", "stripe_subscriptions.csv", "status=trialing", "count", "Never counted as customers")
    metric("dq_kpi_mar26_customers", "KPI-sheet customers Mar-26", float(kc[MAR26]), "count", "2026-03",
           "As typed in founder KPI sheet", "founder_kpi_sheet.csv", "month=2026-03", "customers",
           f"Neighbours {kc[feb]} / {kc[apr]}")
    metric("dq_live_subs_mar26", "Stripe live subscriptions incl. trials, Mar-26 month-end", float(mar_live), "count",
           "2026-03", "created<=month-end and not ended by month-end", "stripe_subscriptions.csv", "all statuses",
           "count", f"{mar_trial} of them in trial")
    metric("dq_in_trial_mar26", "Stripe subscriptions in trial at Mar-26 month-end", float(mar_trial), "count", "2026-03",
           "live at month-end and trial-only or trial_end>month-end", "stripe_subscriptions.csv", "", "count")

    # KPI new / churned
    first_paid = paid_inv[(paid_inv["amount_paid_usd"] > 0) & ~paid_inv["is_one_time"]].groupby("customer")["paid_month"].min()
    new_s = first_paid.value_counts().reindex(MONTHS).fillna(0).astype(int)
    churn_s = (sub[sub["customer"].isin(paid_customers_ever) & sub["status"].eq("canceled")]["ended_at"]
               .dt.to_period("M").value_counts().reindex(MONTHS).fillna(0).astype(int))
    nm = [str(m) for m in MONTHS[1:] if kpi.loc[m, "new customers"] != new_s[m]]
    cmis = [str(m) for m in MONTHS if kpi.loc[m, "churned"] != churn_s[m]]
    issue("founder_kpi_sheet", f"KPI new customers vs Stripe first-paid month: mismatch in {nm or 'none'} (Feb-25..Oct-26); "
          f"KPI churned vs Stripe canceled paying subs: mismatch in {cmis or 'none'}",
          len(nm) + len(cmis), "Minor; new/churn flows broadly reconcile", "Use Stripe-derived flows", "L", "appendix")

    # ------------------------------------------------------------ 4. KPI cash / burn vs bank
    bank_burn = bank_m["money_in_usd"] + bank_m["money_out_usd"]
    dcash = kpi["cash"] - bank_m["closing_balance_usd"]
    dburn = kpi["net burn"] - bank_burn
    tol_c = np.maximum(1000, 0.01 * bank_m["closing_balance_usd"].abs())
    tol_b = np.maximum(1000, 0.01 * bank_burn.abs())
    bad_c = [str(m) for m in MONTHS if abs(dcash[m]) > tol_c[m]]
    bad_b = [str(m) for m in MONTHS if abs(dburn[m]) > tol_b[m]]
    issue("founder_kpi_sheet; bank_balance_monthly", f"KPI cash vs bank closing balance: {22-len(bad_c)}/22 within "
          f"max($1k,1%) (max |delta| {_usd(dcash.abs().max())}); KPI net burn vs bank in+out: {22-len(bad_b)}/22 within "
          f"tolerance (max |delta| {_usd(dburn.abs().max())}). Flagged: cash {bad_c or 'none'}, burn {bad_b or 'none'}",
          22, "KPI cash/burn reconcile with bank (rounded to $k)", "Bank is primary for burn/runway", "M", "memo")
    metric("dq_kpi_bank_cash_months_ok", "Months KPI cash reconciles to bank (of 22)", float(22 - len(bad_c)), "count",
           "2025-01..2026-10", "|KPI cash - bank closing| <= max($1k,1%)", "founder_kpi_sheet.csv; bank_balance_monthly.csv",
           "", "count")
    metric("dq_kpi_bank_burn_months_ok", "Months KPI net burn reconciles to bank (of 22)", float(22 - len(bad_b)), "count",
           "2025-01..2026-10", "|KPI burn - (money_in+money_out)| <= max($1k,1%)",
           "founder_kpi_sheet.csv; bank_balance_monthly.csv", "", "count")
    # bank internal consistency
    roll = (bank_m["opening_balance_usd"] + bank_m["money_in_usd"] + bank_m["money_out_usd"] - bank_m["closing_balance_usd"]).abs()
    chain = (bank_m["opening_balance_usd"].iloc[1:].values - bank_m["closing_balance_usd"].iloc[:-1].values)
    bt = bank_t[bank_t["date"] <= CUTOFF]
    bt_in = bt[bt["amount_usd"] > 0].groupby(bt["date"].dt.to_period("M"))["amount_usd"].sum().reindex(MONTHS).fillna(0)
    bt_out = bt[bt["amount_usd"] < 0].groupby(bt["date"].dt.to_period("M"))["amount_usd"].sum().reindex(MONTHS).fillna(0)
    din = (bt_in - bank_m["money_in_usd"]).abs().max()
    dout = (bt_out - bank_m["money_out_usd"]).abs().max()
    issue("bank_balance_monthly; bank_transactions", f"Bank roll-forward: max |open+in+out-close| {_usd(roll.max())}; "
          f"max |open - prior close| {_usd(np.abs(chain).max())}; transactions vs monthly money_in max |delta| {_usd(din)}, "
          f"money_out {_usd(dout)}", 22, "Bank internally consistent" if max(roll.max(), din, dout) < 1 else "Bank does not tie",
          "None needed" if max(roll.max(), din, dout) < 1 else "Use monthly file", "L", "none")
    po = load("stripe_payouts")
    bpo = bt[bt["category"] == "Revenue - Stripe payout"]
    issue("stripe_payouts; bank_transactions", f"Stripe payouts {len(po)} rows {_usd(po['amount_usd'].sum())} vs bank Stripe "
          f"payout txns {len(bpo)} rows {_usd(bpo['amount_usd'].sum())}; payout statuses {po['status'].value_counts().to_dict()}",
          len(po), f"delta {_usd(po['amount_usd'].sum() - bpo['amount_usd'].sum())}", "Bank is primary", "L", "none")

    # KPI MRR/ARR/customers vs Stripe per month: quantified gap
    gap_mrr = (kpi["MRR"] - mt["mrr_post"])
    issue("founder_kpi_sheet; stripe_invoices", f"KPI 'MRR' vs normalized mrr_post: gap Apr-26 {_usd(gap_mrr[apr])}, "
          f"Jun-26 {_usd(gap_mrr[pd.Period('2026-06','M')])}, Oct-26 {_usd(gap_mrr[OCT26])} "
          f"({gap_mrr[OCT26]/mt.loc[OCT26,'mrr_post']:.1%} of mrr_post); gap widens after May-26 (annual prepay, one-time fees, usage)",
          22, "KPI MRR/ARR do not reconcile with Stripe recurring revenue", "Bridge A1 shows each component per month",
          "H", "memo")

    # KPI headcount vs team
    t = load("team")
    hc = pd.Series({m: int(((t["start_date"] <= m.end_time) & (t["end_date"].isna() | (t["end_date"] > m.end_time))).sum())
                    for m in MONTHS})
    hmis = [str(m) for m in MONTHS if hc[m] != kpi.loc[m, "headcount"]]
    issue("founder_kpi_sheet; team", f"KPI headcount vs team.csv active at month-end: mismatch in {hmis or 'none'}; "
          f"Oct-26 team = {hc[OCT26]}", len(hmis), "Headcount reconciles", "None", "L", "none")

    # ------------------------------------------------------------ 5. records after cut-off
    after = {}
    for n in FILES:
        df = load(n)
        cols = [c for c in DATE_COLS.get(n, []) if c in df]
        if n == "stripe_subscriptions":
            cols = ["created", "canceled_at", "ended_at"]  # period/trial ends in the future are normal state
        if n == "team":
            cols = ["start_date"]
        if n == "stripe_invoices":
            cols = ["created"]  # period_end of an Oct service period is naturally in Nov
        k = int(sum((df[c] > CUTOFF).sum() for c in cols))
        if "period" in df and n in ("founder_kpi_sheet", "bank_balance_monthly", "product_usage_monthly"):
            k += int((df["period"] > CUTOFF_MONTH).sum())
        if k:
            after[n] = k
    n_after = sum(after.values())
    late_ref = ref[ref["created"] > CUTOFF]
    late_dsp = dsp[dsp["created"] > CUTOFF]
    issue(", ".join(after) or "all", f"Records dated after cut-off 2026-10-31: {after or 'none'} "
          f"(refund {_usd(late_ref['amount_usd'].sum())} dated {', '.join(str(x.date()) for x in late_ref['created'])}; dispute "
          f"{_usd(late_dsp['amount_usd'].sum())} dated {', '.join(str(x.date()) for x in late_dsp['created'])}, later than the data export)",
          n_after, "Excluded from all metrics", "Excluded and logged", "M", "memo")
    trial_future = int((sub["trial_end"] > CUTOFF).sum())
    issue("stripe_subscriptions; stripe_invoices", f"{trial_future} trial_end, {int((sub['current_period_end'] > CUTOFF).sum())} "
          f"current_period_end and {int((inv['period_end'] > CUTOFF).sum())} invoice period_end values after cut-off",
          trial_future, "None (scheduled state, not events)",
          "Kept; not records", "L", "none")
    metric("dq_records_after_cutoff", "Records dated after cut-off", float(n_after), "count", "after 2026-10-31",
           "Event-dated rows later than 2026-10-31 across all files", "all", "event dates > cut-off", "count",
           "; ".join(f"{k}={v}" for k, v in after.items()))

    # ------------------------------------------------------------ 6. refunds / failed charges / disputes
    ref_in = ref[ref["created"] <= CUTOFF]
    by_reason = ref.groupby("reason")["amount_usd"].agg(["count", "sum"])
    dup = by_reason.loc["duplicate"] if "duplicate" in by_reason.index else pd.Series({"count": 0, "sum": 0})
    req = by_reason.loc["requested_by_customer"]
    succ_per_inv = ch[ch["status"] == "succeeded"].groupby("invoice").size()
    dup_r = ref[ref["reason"] == "duplicate"]
    dup_unmatched = int((dup_r["invoice"].map(succ_per_inv).fillna(0) < 2).sum())
    inv_paid = inv.set_index("id")["amount_paid_usd"]
    partial = int((ref["amount_usd"] < ref["invoice"].map(inv_paid) - 0.01).sum())
    issue("stripe_refunds", f"Refunds {len(ref)} = {_usd(ref['amount_usd'].sum())}: duplicate {int(dup['count'])} = "
          f"{_usd(dup['sum'])}, requested_by_customer {int(req['count'])} = {_usd(req['sum'])}; {partial} partial; "
          f"{len(late_ref)} after cut-off", len(ref), "Requested refunds reduce invoice MRR pro-rata (load.py); duplicate do not",
          "Logged; cut-off refund excluded", "L", "appendix")
    issue("stripe_refunds; stripe_charges", f"All {dup_unmatched}/{len(dup_r)} 'duplicate' refunds have only one succeeded "
          f"charge on the invoice (no second charge in the data)", len(dup_r),
          f"{_usd(dup_r['amount_usd'].sum())} of refunds kept in MRR by load.py although cash was returned",
          "Kept per §3 (remove duplicate-charge refunds); question for main agent: treat as clawback if no duplicate charge?",
          "M", "appendix")
    fl = ch[ch["status"] == "failed"]
    fl_inv = inv[inv["id"].isin(fl["invoice"])]
    unc = inv[inv["status"] == "uncollectible"]
    issue("stripe_charges", f"Failed charges {len(fl)} ({_usd(fl['amount_usd'].sum())}) on {fl['invoice'].nunique()} invoices; "
          f"codes {fl['failure_code'].value_counts().to_dict()}; {int((fl_inv['status']=='paid').sum())} invoices recovered "
          f"on retry, {int((fl_inv['status']=='uncollectible').sum())} uncollectible", len(fl),
          "Recovered ones are in MRR; uncollectible excluded", "Logged", "L", "appendix")
    issue("stripe_invoices", f"Uncollectible invoices {len(unc)} = {_usd(unc['amount_due_usd'].sum())} (amount_paid 0, "
          f"attempt_count {sorted(unc['attempt_count'].unique().tolist())}); {int(sub['cancellation_details.reason'].eq('payment_failed').sum())} "
          f"subs canceled for payment_failed", len(unc), "Excluded from MRR and cash", "Excluded (no cash)", "L", "appendix")
    dsp_lost = dsp[dsp["status"] == "lost"]
    issue("stripe_disputes", f"Disputes {len(dsp)} = {_usd(dsp['amount_usd'].sum())}, statuses {dsp['status'].value_counts().to_dict()}, "
          f"reasons {dsp['reason'].value_counts().to_dict()}, dated {dsp['created'].min().date()}..{dsp['created'].max().date()}; "
          f"{(dsp['created'] > CUTOFF).sum()} after cut-off; {int(dsp['created'].dt.to_period('M').isin([pd.Period('2026-09','M'), OCT26]).sum())} "
          f"in Sep-Oct 26 (fraud cluster)", len(dsp), "Lost disputes (<= cut-off) reduce invoice MRR (load.py)",
          "Logged; future-dated dispute excluded", "M", "memo")
    both = set(dsp["invoice"]) & set(ref["invoice"])
    if both:
        bi = sorted(both)
        tot = float(dsp[dsp["invoice"].isin(bi)]["amount_usd"].sum() + ref[ref["invoice"].isin(bi)]["amount_usd"].sum())
        issue("stripe_disputes; stripe_refunds", f"Invoice(s) {bi} both refunded and lost in dispute: {_usd(tot)} returned "
              f"vs {_usd(inv_paid.reindex(bi).sum())} paid", len(bi), "Cash returned twice; MRR clawback capped at paid amount",
              "Logged", "L", "appendix")
    fraud_cust = dsp[dsp["reason"] == "fraudulent"]["customer"]
    still = sub[sub["customer"].isin(fraud_cust) & sub["status"].isin(["active", "past_due"])]
    if len(still):
        issue("stripe_disputes; stripe_subscriptions", f"{len(still)} customer(s) with a lost 'fraudulent' dispute still have "
              f"live subs ({still['status'].value_counts().to_dict()})", len(still),
              "Their MRR counts in Oct-26; risk of further chargebacks", "Kept in MRR; flagged", "L", "appendix")
    metric("dq_refunds_total", "Refunds total (all, incl. after cut-off)", float(ref["amount_usd"].sum()), "usd",
           "2025-01..2026-11", "Sum of stripe_refunds", "stripe_refunds.csv", "all", "sum(amount_usd)",
           f"{len(late_ref)} refund after cut-off = {_usd(late_ref['amount_usd'].sum())}")
    metric("dq_refunds_total_to_cutoff", "Refunds total to cut-off", float(ref_in["amount_usd"].sum()), "usd",
           "2025-01..2026-10", "Sum of stripe_refunds created <= cut-off", "stripe_refunds.csv", "created<=2026-10-31",
           "sum(amount_usd)")
    metric("dq_refunds_n", "Refunds count", float(len(ref)), "count", "2025-01..2026-11", "Rows in stripe_refunds",
           "stripe_refunds.csv", "all", "count")
    metric("dq_refunds_duplicate", "Refunds, reason duplicate", float(dup["sum"]), "usd", "2025-01..2026-10",
           "reason=duplicate", "stripe_refunds.csv", "reason=duplicate", "sum(amount_usd)",
           "No second succeeded charge found on these invoices")
    metric("dq_refunds_requested", "Refunds, requested by customer", float(req["sum"]), "usd", "2025-01..2026-11",
           "reason=requested_by_customer", "stripe_refunds.csv", "reason=requested_by_customer", "sum(amount_usd)")
    metric("dq_failed_charges", "Failed card charges", float(len(fl)), "count", "2025-01..2026-10", "status=failed",
           "stripe_charges.csv", "status=failed", "count")
    metric("dq_uncollectible_usd", "Uncollectible invoices amount", float(unc["amount_due_usd"].sum()), "usd",
           "2025-01..2026-10", "status=uncollectible", "stripe_invoices.csv", "status=uncollectible", "sum(amount_due_usd)")
    metric("dq_disputes_n", "Disputes (all lost)", float(len(dsp)), "count", "2025-07..2026-12", "Rows in stripe_disputes",
           "stripe_disputes.csv", "all", "count", f"{(dsp['created'] > CUTOFF).sum()} dated after cut-off")
    metric("dq_disputes_lost_usd", "Disputes lost amount", float(dsp_lost["amount_usd"].sum()), "usd", "2025-07..2026-12",
           "status=lost", "stripe_disputes.csv", "status=lost", "sum(amount_usd)",
           f"Incl. {_usd(late_dsp['amount_usd'].sum())} dated after cut-off")

    # ------------------------------------------------------------ invoice parsing
    usage_inv = inv[inv["has_usage"]]
    usage_below = int((usage_inv["subtotal_usd"] < usage_inv["list_price"] - 0.01).sum())
    oct_usage = float(mt.loc[OCT26, "usage_post"])
    issue("stripe_invoices", f"{len(usage_inv)} invoices carry 'Usage tier add-on'; usage (subtotal above plan list price) "
          f"is excluded from MRR by definition; Oct-26 usage_post = {_usd(oct_usage)}", len(usage_inv),
          f"Oct-26 usage {oct_usage/ (oct_mrr_post + oct_usage):.1%} of recurring+usage", "Reported separately (§3)",
          "M", "appendix")
    metric("dq_usage_oct26", "Usage add-on revenue Oct-26 (excluded from MRR)", oct_usage, "usd", "2026-10",
           "Invoice subtotal above plan list price on usage invoices, post-discount, by service month",
           "stripe_invoices.csv; stripe_prices.csv", "has usage add-on", "sum(usage_post)", "Split rule in load.py")
    dg = sub[sub["items"].str.contains("downgraded", na=False)]
    issue("stripe_subscriptions; stripe_invoices", f"{len(dg)} subs 'downgraded' to custom below-list prices "
          f"({dg['status'].value_counts().to_dict()}); their invoices are labelled 'Usage tier add-on' with subtotal below "
          f"list ({usage_below} invoices). Invoice lines never say 'downgraded'", usage_below,
          "Without min(subtotal, list) rule these would be mislabelled usage", "recurring = min(subtotal, list price); usage = 0",
          "L", "appendix")
    x = inv[~inv["has_usage"] & ~inv["is_agency"] & ~inv["is_one_time"]]
    qty = x["lines"].str.extract(r" x(\d+)")[0].astype(float)
    off = x[(x["subtotal_usd"] - x["list_price"] * qty).abs() > 0.01]
    issue("stripe_invoices", f"{len(off)} non-usage plan invoices priced off list: "
          + "; ".join(f"{r.lines} at {_usd(r.subtotal_usd)} vs list {_usd(r.list_price)}" for r in off.itertuples()),
          len(off), "Counted at invoiced subtotal", "Kept as billed", "L", "none")
    dmis = int(((inv["subtotal_usd"] - inv["discount_usd"] - inv["amount_due_usd"]).abs() > 0.01).sum())
    pmis = int((paid_inv["amount_paid_usd"] - paid_inv["amount_due_usd"]).abs().gt(0.01).sum())
    issue("stripe_invoices", f"amount_due != subtotal - discount on {dmis} invoices; paid != due on {pmis} paid invoices. "
          f"Answer to §3: subtotal is pre-discount, amount_due/amount_paid are net of discount "
          f"({int((inv['discount_usd']>0).sum())} discounted invoices)", dmis, "mrr_pre from subtotal, mrr_post net",
          "None needed", "L", "appendix")
    multi = paid_inv[~paid_inv["is_one_time"]].groupby(["customer", "service_month"]).size()
    issue("stripe_invoices", f"Customers with >1 paid recurring invoice in one service month: {int((multi>1).sum())}; "
          f"invoices without subscription: {int(inv['subscription'].isna().sum())} (all one-time: "
          f"{bool(inv[inv['subscription'].isna()]['is_one_time'].all())}); $0 paid invoices: "
          f"{int((paid_inv['amount_paid_usd']==0).sum())} (SWITCH3 100% coupon)", int((multi > 1).sum()),
          "None", "Summed per customer-month in load.py if any", "L", "none")
    nc = paid_inv[~paid_inv["id"].isin(ch[ch["status"] == "succeeded"]["invoice"])]
    off_m = ch[ch["status"] == "succeeded"].merge(inv[["id", "paid_month"]], left_on="invoice", right_on="id")
    off_m = int((off_m["created"].dt.to_period("M") != off_m["paid_month"]).sum())
    issue("stripe_charges; stripe_invoices", f"{len(nc)} paid invoices have no succeeded charge (total {_usd(nc['amount_paid_usd'].sum())}); "
          f"{off_m} succeeded charges fall in a different month than their invoice; charges with unknown invoice: "
          f"{int((~ch['invoice'].isin(inv['id'])).sum())}", len(nc) + off_m, "Cash-by-month differs slightly charges vs invoices",
          "Cash = paid invoices by created month (§3)", "L", "none")
    cp = load("stripe_coupons").set_index("id")
    cp_rows = []
    for cpn in cp.index:
        s = sub[sub["discount.coupon"] == cpn]
        cp_rows.append(f"{cpn} subs {len(s)} vs times_redeemed {cp.loc[cpn,'times_redeemed']}")
    issue("stripe_coupons; stripe_subscriptions", "Coupon redemptions vs subs carrying coupon: " + "; ".join(cp_rows)
          + f"; discounted invoices without a coupon sub: "
          f"{int(((inv['discount_usd']>0) & ~inv['subscription'].isin(sub[sub['discount.coupon'].notna()]['id'])).sum())}",
          int(sub["discount.coupon"].notna().sum()), "Coupons consistent", "Applied via invoice discount_usd", "L", "none")

    # ------------------------------------------------------------ subscriptions
    pdue = sub[sub["status"] == "past_due"]
    open_inv = inv[inv["subscription"].isin(pdue["id"]) & (inv["status"] != "paid")]
    days = (CUTOFF.normalize() - pdue["current_period_start"]).dt.days
    issue("stripe_subscriptions", f"{len(pdue)} past_due subs but no open/unpaid invoice exists for them "
          f"({len(open_inv)} found); days since current_period_start at cut-off {int(days.min())}-{int(days.max())}; "
          f"delinquent flag = True on {int(cust['delinquent'].sum())} customers (matches)", len(pdue),
          "Overdue < 30 days by any measure, so included in point-in-time MRR",
          "Include (§3: past_due <30d); overdue measured from current_period_start as proxy", "L", "appendix")
    reasons = sub[sub["status"] == "canceled"]["cancellation_details.reason"].value_counts(dropna=False).to_dict()
    issue("stripe_subscriptions", f"Canceled subs {int((sub['status']=='canceled').sum())} by reason {reasons}",
          int((sub["status"] == "canceled").sum()), "Trial-ended dominate; payment_failed = involuntary churn",
          "Logged", "L", "none")
    no_inv = sub[~sub["customer"].isin(inv["customer"])]
    no_sub = set(inv["customer"]) - set(sub["customer"])
    issue("stripe_subscriptions; stripe_invoices", f"Subs whose customer has no invoice: {len(no_inv)} "
          f"({no_inv['status'].value_counts().to_dict()}; reasons {no_inv['cancellation_details.reason'].value_counts().to_dict()}); "
          f"customers with invoices but no sub: {len(no_sub)}; max subs per customer {sub['customer'].value_counts().max()}",
          len(no_inv), "Trial-only accounts; never counted", "Excluded from customers", "L", "none")
    act = sub[sub["status"].isin(["active", "past_due"])]
    act_no_paid = act[~act["customer"].isin(paid_customers_ever)]
    act_no_oct = act[~act["customer"].isin(cm[(cm["month"] == OCT26) & (cm["mrr_post"] > 0)]["customer"])]
    issue("stripe_subscriptions", f"Active/past_due subs with no paid invoice ever: {len(act_no_paid)}; "
          f"with no mrr_post>0 in Oct-26: {len(act_no_oct)} (e.g. 100% coupon)", len(act_no_oct),
          "Point-in-time paying count < live sub count", "Paying = mrr_post>0", "L", "appendix")
    lag = (sub["created"] - sub["customer"].map(cust_i["created"]))
    issue("stripe_subscriptions; stripe_customers", f"{int((lag < pd.Timedelta(0)).sum())} subs created before their customer "
          f"(max {abs(lag.min())}); {int((sub['trial_start'] < sub['created']).sum())} trial_start before sub created",
          int((lag < pd.Timedelta(0)).sum()), "None (same-day timestamp noise)", "Ignored", "L", "none")

    # ------------------------------------------------------------ customers
    nn = cust["name"].isna()
    nn_paid = int(cust[nn]["id"].isin(paid_customers_ever).sum())
    meta_null = int(cust[["metadata.segment", "metadata.acquisition_channel", "metadata.store_gmv_band"]].isna().any(axis=1).sum())
    dupe = cust[cust["email"].duplicated(keep=False)]
    testy = cust[cust["email"].str.contains(r"test|example|demo|fake|northpine", case=False, na=False)
                 | cust["name"].str.contains(r"test|demo", case=False, na=False)]
    issue("stripe_customers", f"Name null on {int(nn.sum())} customers ({nn_paid} of them ever paid); missing segment/channel/"
          f"GMV metadata: {meta_null}; currency {cust['currency'].unique().tolist()}", int(nn.sum()),
          "Trial-only accounts have no name; metadata complete", "None", "L", "none")
    issue("stripe_customers", f"{dupe['email'].nunique()} emails appear on {len(dupe)} customer records "
          f"({int(dupe['id'].isin(paid_customers_ever).sum())} ever paid; repeat trials); test-looking accounts: {len(testy)}",
          len(dupe), "None on paying counts", "Kept", "L", "none")
    tt = cust[cust["metadata.acquisition_channel"] == "TikTok"]
    s_all = load("signups_daily")
    tk = s_all[s_all["utm_source"] == "tiktok_organic"]
    tk_mar = tk[tk["date"].dt.to_period("M") == MAR26]
    issue("stripe_customers; signups_daily", f"Stripe has {len(tt)} TikTok-channel customers, all created "
          f"{tt['created'].min().date()}..{tt['created'].max().date()}, {int(tt['id'].isin(paid_customers_ever).sum())} ever paid; "
          f"signups_daily shows {int(tk_mar['trials_started'].sum())} TikTok trials in Mar-26 ({int(tk['trials_started'].sum())} all-time)",
          len(tt), "Internal sources disagree on TikTok trials; either way zero paying", "TikTok excluded from channel slide",
          "M", "appendix")
    trials_m = s_all.groupby(s_all["date"].dt.to_period("M"))["trials_started"].sum().reindex(MONTHS)
    subs_m = sub.groupby(sub["created"].dt.to_period("M")).size().reindex(MONTHS).fillna(0)
    issue("signups_daily; stripe_subscriptions", f"Trials started (signups_daily) {int(trials_m.sum())} vs Stripe subs created "
          f"Jan-25..Oct-26 {int(subs_m.sum())}; Mar-26 {int(trials_m[MAR26])} vs {int(subs_m[MAR26])}",
          int(abs(trials_m.sum() - subs_m.sum())), "Funnel counts differ by source", "Use Stripe for customers, signups_daily for funnel",
          "L", "appendix")

    # ------------------------------------------------------------ cross-file ids
    id_msgs = []
    for nm_, col in [("product_usage_monthly", "stripe_customer_id"), ("nps_responses", "stripe_customer_id"),
                     ("support_tickets", "stripe_customer_id")]:
        df = load(nm_)
        id_msgs.append(f"{nm_} {int((~df[col].isin(cust['id'])).sum())} unknown / {int(df[col].isna().sum())} null")
    issue("product_usage_monthly; nps_responses; support_tickets", "IDs not in stripe_customers: " + "; ".join(id_msgs),
          0, "None", "None", "L", "none")
    pu = load("product_usage_monthly")
    paying_cm = set(zip(cm.loc[cm["mrr_post"] > 0, "customer"], cm.loc[cm["mrr_post"] > 0, "month"]))
    pu["paying"] = [(c, m) in paying_cm for c, m in zip(pu["stripe_customer_id"], pu["period"])]
    pu_oct = pu[pu["period"] == OCT26]
    nonpay_spend = float(pu_oct.loc[~pu_oct["paying"], "managed_ad_spend_usd"].sum())
    issue("product_usage_monthly", f"{int((~pu['paying']).sum())} usage rows for customer-months without mrr_post>0 "
          f"(Oct-26: {int((~pu_oct['paying']).sum())} rows, managed spend {_usd(nonpay_spend)} of {_usd(pu_oct['managed_ad_spend_usd'].sum())}); "
          f"duplicates {int(pu.duplicated(['stripe_customer_id','month']).sum())}; autopilot_enabled before May-26: "
          f"{int((pu['autopilot_enabled'] & (pu['period'] < MAY26)).sum())}; max managed spend/customer-month "
          f"{_usd(pu['managed_ad_spend_usd'].max())}", int((~pu["paying"]).sum()),
          "Managed spend / take rate may include non-paying accounts", "Take rate: decide filter (main agent)", "M", "appendix")
    pu_bl = pu[(pu["stripe_customer_id"] == brightline_id()) & (pu["period"] == OCT26)]
    if len(pu_bl):
        issue("product_usage_monthly", f"Brightline Oct-26: stores_connected {int(pu_bl['stores_connected'].iloc[0])}, managed spend "
              f"{_usd(pu_bl['managed_ad_spend_usd'].iloc[0])} ({pu_bl['managed_ad_spend_usd'].iloc[0]/pu_oct['managed_ad_spend_usd'].sum():.1%} of Oct total)",
              1, "Concentration in managed spend too", "Disclose with concentration", "M", "appendix")

    # ------------------------------------------------------------ NPS / support
    nps = load("nps_responses")
    issue("nps_responses", f"{len(nps)} responses, score range {nps['score'].min()}-{nps['score'].max()}, "
          f"{int(nps['comment'].isna().sum())} without comment, {int((~nps['stripe_customer_id'].isin(paid_customers_ever)).sum())} "
          f"from never-paying accounts, {int(nps.duplicated(['stripe_customer_id','date']).sum())} duplicates", len(nps),
          "Small n per quarter", "Always show n", "L", "none")
    stk = load("support_tickets")
    issue("support_tickets", f"{len(stk)} tickets; csat missing on {int(stk['csat'].isna().sum())}; "
          f"resolution < first response: {int((stk['resolution_hours'] < stk['first_response_hours']).sum())}; "
          f"dup ids {int(stk['ticket_id'].duplicated().sum())}", int(stk["csat"].isna().sum()), "CSAT has response bias",
          "Report response rate with CSAT", "L", "none")

    # ------------------------------------------------------------ 10. Little Tea House
    lth = cust[cust["name"].str.contains("Little Tea House", case=False, na=False)]
    if len(lth):
        lid = lth["id"].iloc[0]
        hist = nps[nps["stripe_customer_id"] == lid].sort_values("date")
        ls = sub[sub["customer"] == lid].iloc[0]
        h = "; ".join(f"{r.date.date()} {r.score}" for r in hist.itertuples())
        issue("nps_responses; stripe_subscriptions", f"Little Tea House NPS history: {h}; subscription {ls['status']} on "
              f"{pd.Timestamp(ls['canceled_at']).date()} ({ls['cancellation_details.reason']})", len(hist),
              "Alex's 'best quote' customer has churned", "Quote not used; pick a current NPS 9-10 customer", "H", "memo")
        metric("dq_lth_canceled_month", "Little Tea House cancellation month", str(pd.Timestamp(ls["canceled_at"]).to_period("M")),
               "month", "2026", "canceled_at of LTH subscription", "stripe_subscriptions.csv", f"customer={lid}",
               "canceled_at", str(ls["cancellation_details.reason"]))

    # ------------------------------------------------------------ 7. Meta platform vs internal trials
    ad = load("ad_platforms_daily")
    meta_pf = float(ad[(ad["platform"] == "meta") & (ad["date"] >= JUN26) & (ad["date"] <= CUTOFF)]["platform_reported_trials"].sum())
    meta_int = int(s_all[(s_all["utm_source"] == "meta_ads") & (s_all["date"] >= JUN26) & (s_all["date"] <= CUTOFF)]["trials_started"].sum())
    g_pf = float(ad[(ad["platform"] == "google") & (ad["date"] >= JUN26)]["platform_reported_trials"].sum())
    g_int = int(s_all[(s_all["utm_source"] == "google_ads") & (s_all["date"] >= JUN26)]["trials_started"].sum())
    frac = int((ad["platform_reported_trials"] % 1 != 0).sum())
    issue("ad_platforms_daily; signups_daily", f"Meta-reported trials Jun-Oct 26 = {meta_pf:.1f} vs internal meta_ads trials "
          f"{meta_int} ({meta_pf/meta_int:.2f}x); Google {g_pf:.1f} vs {g_int} ({g_pf/g_int:.2f}x); {frac} rows have "
          f"fractional (modelled) platform trials", meta_int, "'Meta 250+ trials' overstates", "Use internal trials; label platform numbers",
          "H", "memo")
    metric("dq_meta_trials_platform_jun_oct26", "Meta platform-reported trials Jun-Oct 26", meta_pf, "count", "2026-06..2026-10",
           "Sum platform_reported_trials, platform=meta", "ad_platforms_daily.csv", "date 2026-06-01..2026-10-31",
           "sum", "Platform attribution, fractional")
    metric("dq_meta_trials_internal_jun_oct26", "Internal Meta trials Jun-Oct 26", float(meta_int), "count", "2026-06..2026-10",
           "Sum trials_started, utm_source=meta_ads", "signups_daily.csv", "date 2026-06-01..2026-10-31", "sum")
    metric("dq_meta_overstatement_x", "Meta platform / internal trials", meta_pf / meta_int, "ratio", "2026-06..2026-10",
           "platform-reported / internal", "ad_platforms_daily.csv; signups_daily.csv", "", "ratio")

    # ------------------------------------------------------------ 8. TikTok March
    tk_spike = tk[(tk["date"] >= "2026-03-09") & (tk["date"] <= "2026-03-13")]
    issue("signups_daily", f"TikTok organic Mar 9-13 2026: {int(tk_spike['signups'].sum())} signups (the often-quoted "
          f"{int(tk_mar['signups'].sum())} is the full month), "
          f"{int(tk_spike['trials_started'].sum())} trials; March total {int(tk_mar['signups'].sum())} signups / "
          f"{int(tk_mar['trials_started'].sum())} trials ({tk_mar['trials_started'].sum()/tk_mar['signups'].sum():.1%}); "
          f"0 paying TikTok customers ever", int(tk_spike["signups"].sum()), "'TikTok spike' is low-quality traffic",
          "Removed from deck", "H", "memo")
    metric("dq_tiktok_signups_mar9_13", "TikTok signups Mar 9-13 2026", float(tk_spike["signups"].sum()), "count",
           "2026-03-09..13", "utm_source=tiktok_organic", "signups_daily.csv", "date 2026-03-09..2026-03-13", "sum(signups)")
    metric("dq_tiktok_signups_mar26", "TikTok signups March 2026", float(tk_mar["signups"].sum()), "count", "2026-03",
           "utm_source=tiktok_organic", "signups_daily.csv", "Mar-26", "sum(signups)", "Of which Mar 9-13 = spike")
    metric("dq_tiktok_trials_mar26", "TikTok trials March 2026", float(tk_mar["trials_started"].sum()), "count", "2026-03",
           "utm_source=tiktok_organic", "signups_daily.csv", "Mar-26", "sum(trials_started)",
           f"Stripe shows {len(tt)} TikTok-channel trial accounts")
    metric("dq_tiktok_paying_customers", "TikTok-channel customers ever paid", float(tt["id"].isin(paid_customers_ever).sum()),
           "count", "2025-01..2026-10", "metadata.acquisition_channel=TikTok with paid invoice > 0", "stripe_customers.csv; stripe_invoices.csv",
           "", "count")

    # ------------------------------------------------------------ 9. bank marketing vs ad platforms
    bm = bt[bt["category"].str.startswith("Marketing")]
    bmm = bm.groupby([bm["date"].dt.to_period("M"), "category"])["amount_usd"].sum().unstack().reindex(MONTHS).fillna(0)
    am = ad.groupby([ad["date"].dt.to_period("M"), "platform"])["spend_usd"].sum().unstack().reindex(MONTHS).fillna(0)
    dm = (bmm["Marketing - Paid social"].abs() - am["meta"]).abs().max()
    dg_ = (bmm["Marketing - Paid search"].abs() - am["google"]).abs().max()
    issue("bank_transactions; ad_platforms_daily", f"Bank Paid social vs Meta platform spend: max monthly |delta| {_usd(dm)} "
          f"(total {_usd(-bmm['Marketing - Paid social'].sum())} vs {_usd(am['meta'].sum())}); Paid search vs Google: max "
          f"|delta| {_usd(dg_)}. Bank-only marketing lines: App Store ads, Content, Outbound tools, Partner commissions; "
          f"no TikTok spend line", 22, "Spend reconciles; CAC uses bank", "None", "L", "appendix")
    tg = bt[bt["counterparty"] == "Together AI"]
    issue("bank_transactions", f"New inference vendor Together AI: {len(tg)} payments {_usd(-tg['amount_usd'].sum())} "
          f"({', '.join(str(d.date()) for d in tg['date'])}) on top of OpenAI/Anthropic", len(tg),
          "Drives COGS step-up in GM", "In COGS (LLM inference)", "M", "memo")

    # ------------------------------------------------------------ 11. generic nulls / dups / negatives
    gen = []
    for n in FILES:
        df = load(n)
        idc = next((c for c in ["id", "ticket_id"] if c in df), None)
        dups = int(df[idc].duplicated().sum()) if idc else int(df.duplicated().sum())
        gen.append(f"{n}: dup {dups}")
    neg = int((inv[["subtotal_usd", "discount_usd", "amount_due_usd", "amount_paid_usd"]] < 0).any(axis=1).sum())
    issue("all", "Duplicate ids/rows per file: " + "; ".join(g for g in gen if not g.endswith(" 0")) if any(not g.endswith(" 0") for g in gen)
          else f"No duplicate ids/rows in {len(FILES)} files; negative invoice amounts: {neg}; invoice period_end<period_start: "
          f"{int((inv['period_end'] < inv['period_start']).sum())}", 0, "None", "None", "L", "none")

    dq = pd.DataFrame(issues)
    for c in ["metric_impact", "treatment"]:
        dq[c] = dq[c].replace({"None": "No action", "None needed": "No action"})
    dq.insert(0, "dq_id", [f"DQ{i+1:02d}" for i in range(len(dq))])
    sev_order = {"H": 0, "M": 1, "L": 2}
    dq = dq.sort_values("severity", key=lambda s: s.map(sev_order), kind="stable")
    dq["dq_id"] = [f"DQ{i+1:02d}" for i in range(len(dq))]
    dq.to_csv(OUT, index=False)
    return pd.DataFrame(metrics, columns=["id", "slide", "metric", "value", "unit", "period", "definition",
                                          "source_file", "filter", "formula", "caveat"])


if __name__ == "__main__":
    print(run().to_string())
