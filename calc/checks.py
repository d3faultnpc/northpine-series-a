"""Compare computed metrics with a first-pass snapshot of expected values AFTER the blind calc.
Writes calc/checks.txt. |Δ| > 3% is FLAGged with a reason; nothing is tuned toward §9."""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

# id, expected (§9), reason if it differs by design
EXPECTED = [
    ("rev_kpi_mrr_oct26", 503000, ""),
    ("rev_cash_collected_apr26", 158000, ""),
    ("rev_cash_collected_oct26", 503000, ""),
    ("rev_mrr_pre_pit", 448000, "snapshot's 448k includes usage tiers (~63k pre-discount); §3 excludes usage"),
    ("rev_subs_active_count", 296, ""),
    ("rev_subs_past_due_count", 6, ""),
    ("rev_subs_trialing_count", 37, ""),
    ("rev_paying_customers_oct26", 290, "snapshot counts paid invoices by paid month; ours = mrr_post>0 in service month incl. annual spread"),
    ("ret_ap_enabled_all_oct26", 192, ""),
    ("ret_nps_2026q4", 50, ""),
    ("ret_managed_spend_oct26", 7400000, ""),
    ("burn_cash_oct26", 2018000, ""),
    ("burn_net_oct26", 222000, ""),
    ("gm_cogs_oct26", 77000, ""),
    ("der_gm_oct26", 0.83, "denominator incl. usage tiers; Oct COGS 76.8k"),
    ("ch_meta_trials_platform_reported", 256, ""),
    ("ch_meta_trials_internal", 153, ""),
    ("ch_tiktok_mar_signups", 1055, ""),
    ("ch_tiktok_mar_trials", 33, ""),
    ("team_hc_total", 31, ""),
    ("team_base_payroll_annual", 4660000, ""),
    ("dq_refunds_total", 27900, ""),
    ("dq_disputes_n", 5, ""),
]


def run():
    m = pd.read_csv(ROOT / "calc" / "metrics.csv").set_index("id")
    out = ["id | expected | actual | delta_pct | status | reason", "---"]
    nflag = 0
    for i, exp, why in EXPECTED:
        if i not in m.index:
            out.append(f"{i} | {exp} | MISSING | | FLAG | id not produced")
            nflag += 1
            continue
        try:
            act = float(m.loc[i, "value"])
        except ValueError:
            out.append(f"{i} | {exp} | {m.loc[i, 'value']} | | FLAG | non-numeric")
            nflag += 1
            continue
        d = (act - exp) / exp
        st = "FLAG" if abs(d) > 0.03 else "ok"
        nflag += st == "FLAG"
        out.append(f"{i} | {exp} | {act:.4g} | {d:+.1%} | {st} | {why if st == 'FLAG' else ('accepted with caveat' if abs(d) > 0.005 else '')}")
    out.append(f"\n{nflag} flagged of {len(EXPECTED)}. Growth has no expected value: snapshot's 2.8x divided normalized Oct by cash Apr.")
    (ROOT / "calc" / "checks.txt").write_text("\n".join(out) + "\n")
    print(f"[checks] {nflag} flagged of {len(EXPECTED)} → calc/checks.txt")
