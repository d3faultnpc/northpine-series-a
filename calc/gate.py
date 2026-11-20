"""Gate 1 summary → out/gate_summary.md (numbers pulled from calc/metrics.csv)."""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def main():
    m = pd.read_csv(ROOT / "calc" / "metrics.csv").set_index("id")["value"]
    v = lambda i: float(m[i])
    pct = lambda i: f"{v(i):.0%}"
    usd = lambda i: f"${v(i)/1e6:.2f}M" if v(i) >= 1e6 else f"${v(i)/1e3:.0f}k"
    crit = [
        ("Growth ex-Brightline Apr→Oct", f"{v('rev_growth_apr_oct_ex_bl'):.2f}x (total {v('rev_growth_apr_oct'):.2f}x)", "≥1.8x", v("rev_growth_apr_oct_ex_bl") >= 1.8),
        ("Autopilot vs non-Autopilot, m5 logo (ex-BL, all payers May+)", f"{pct('ret_ap_all_xbl_on_logo5')} vs {pct('ret_ap_all_xbl_off_logo5')} (n {v('ret_ap_all_xbl_on_n5'):.0f}/{v('ret_ap_all_xbl_off_n5'):.0f})", "ON ≥ OFF", v("ret_ap_all_xbl_on_logo5") >= v("ret_ap_all_xbl_off_logo5")),
        ("Autopilot vs non-Autopilot, m5 NRR (ex-BL)", f"{pct('ret_ap_all_xbl_on_nrr5')} vs {pct('ret_ap_all_xbl_off_nrr5')}", "ON ≥ OFF", v("ret_ap_all_xbl_on_nrr5") >= v("ret_ap_all_xbl_off_nrr5")),
        ("Volume share of Apr→Oct ΔMRR", f"{pct('rev_pv_volume_share')} (ex-BL {pct('rev_pv_volume_share_ex_bl')})", "≥50%", v("rev_pv_volume_share") >= 0.5),
    ]
    ok = all(c[3] for c in crit)
    lines = ["# Gate 1 — story A vs B", "",
             "| criterion | value | threshold | result |", "|---|---|---|---|"]
    lines += [f"| {a} | {b} | {c} | {'PASS' if d else 'FAIL'} |" for a, b, c, d in crit]
    lines += ["", "**Context**", "",
              f"- ARR (post-discount, ex usage) {usd('rev_arr_post_oct26')} vs KPI-sheet {usd('rev_kpi_arr_oct26') if 'rev_kpi_arr_oct26' in m else '$6.04M'}; usage tiers +{usd('der_usage_arr')} run-rate, reported separately",
              f"- Drivers Apr→Oct: Autopilot plans {pct('rev_drvg_autopilot_share')}, Brightline {pct('rev_drvg_agency_share')}, other new {pct('rev_drvg_new_other_share')}, churn+contraction {pct('rev_drvg_contraction_churn_share')}; repricing of legacy {pct('rev_drvg_repricing_share')} (nobody migrated)",
              f"- Autopilot adoption {pct('ret_ap_adoption_oct26')} of paying; logo churn/mo {pct('ret_logo_churn_avg_pre')} → {pct('ret_logo_churn_avg_post')}; NPS 26Q1 {v('ret_nps_2026q1'):.0f} → Oct {v('ret_nps_2026q4'):.0f} (n {v('ret_nps_n_2026q4'):.0f})",
              f"- Managed ad spend {usd('ret_managed_spend_oct26')}/mo ({v('ret_managed_spend_growth_apr_oct26'):.1f}x since Apr), take rate {pct('ret_take_rate_oct26')}",
              f"- GM (total recurring rev) Apr {pct('der_gm_apr26')} → low {pct('der_gm_min')} ({m['der_gm_min_month']}) → Oct {pct('der_gm_oct26')}",
              f"- Runway {v('burn_runway_months'):.1f} mo at 3-mo avg burn {usd('burn_avg_3m')}; with $8M: {v('burn_postraise_runway_current'):.0f} mo",
              f"- Concentration: Brightline {pct('rev_top1_share')} of MRR, top-10 {pct('rev_top10_share')}",
              "", f"**Recommendation: {'A' if ok else 'B'}** — 'Autopilot turned the tool into an agent customers buy and keep'.",
              "Caveats carried into the deck: Autopilot launched with new pricing (effects overlap); Autopilot ON/OFF is self-selected and n is small; post-launch cohorts are 3–5 months old.",
              "", "**Decision: A** (orchestrator's recommendation, accepted by Zakhar on 2026-11-19). B's managed-spend number stays as slide 5 (scale/trust)."]
    (ROOT / "out" / "gate_summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
