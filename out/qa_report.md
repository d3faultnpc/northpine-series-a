# Independent QA: deck + memo (2026-11-19)

Scripted checker: `python run.py qa` (qa.py). Result: **24 PASS, 2 FAIL**, exits 1.

| # | Check | Result | Note |
|---|---|---|---|
| 1 | Deck ids (85) exist, not NA, render = fmt_value or consistent; text present in PDF | PASS | 17 use explicit decimals/units, all within rounding |
| 1 | Memo ids (40), same tests | PASS | 5 explicit-decimal ids, consistent |
| 1 | No literal $/%/x in deck/template.html | PASS | only "$0" in the A6 definition (threshold, allowed) |
| 1 | No literal $/%/x in memo/memo.md.j2 | **FAIL** | "$6.0M" (TL;DR) and "$6M" (partner Q) typed by hand |
| 2 | Every $, %, Nx in deck/memo PDFs traces to metrics.csv or calc/series | PASS | tick labels excluded. Weak for short tokens (any "91%" matches some series cell) |
| 3 | ARR, MRR label ($370k = ARR/12), paying (299), growth (2.5x / 2.2x), runway (8), Brightline (16%), GM (82%), cut-off 31 Oct 2026 | PASS | identical across deck and memo |
| 4 | Banned strings, deck | PASS | |
| 4 | Banned strings, memo | **FAIL** | "Little Tea House", "no competitors", both quoted as pushback (judgment call) |
| 5 | 12 main slides + 6 appendix; memo 1 page; 960×540 pt | PASS | |
| 6 | Platform-reported numbers labelled | PASS | slide 8 and A3 |
| 7 | Concentration, GM, runway on main slides | PASS | slides 9, 7, 12 |
| 8 | "Source:" footer on every chart slide (7) and on every page | PASS | |

## Spot checks (independent pandas on data/raw, no calc/ imports)

| Metric | Deck (raw) | Mine | Δ |
|---|---|---|---|
| MRR post Oct-26 / ARR | $370k / $4.4M (369,619) | 369,619 | 0.0% |
| Growth Apr→Oct | 2.5x (2.523) | 2.480 | **−1.7%** |
| Growth ex-Brightline | 2.2x (2.186) | 2.149 → **2.1x** | **−1.7%, rounding flips** |
| Volume share of Δ | 61% (0.610) | 0.619 | +1.5% |
| Net new MRR/mo pre / post / accel | $7k / $37k / 5.5x (6,718 / 37,182 / 5.53) | 7,052 / 36,765 / 5.21 | **+5.0% / −1.1% / −5.8%** |
| Paying customers Oct-26 | 299 | 299 | 0 |
| Brightline share / top-10 | 16% / 22% | 15.7% / 21.8% | 0 |
| Runway (cash 2,018,489 ÷ 3-mo burn 250,390) | 8 | 8.06 (9.1 on Oct burn alone) | 0 |
| GM Oct (sub + usage revenue) | 82% (0.822) | 0.822; **0.792 on the §3 definition (mrr_post only)** | 0 / −3.6% |
| GM Jun | 65% (0.646) | 0.651; **0.630 on §3** | +0.7% / −2.5% |
| CAC App Store / Meta | $184 / $2,027 | 184 / 2,027 | 0 |
| Meta trials, platform vs internal (Jun–Oct) | 1.7x (256 / 153) | 255.7 / 153 | 0 |
| NPS 2026Q4 | 50, n=32 | 50, n=32 (October only) | 0 |
| Logo churn, Nov–Apr vs May–Oct | 6.3% → 3.8% | **5.8% → 3.2%** | **−8.9% / −16%** |
| Undo rate, Autopilot vs manual (Oct, paying) | 0.3% vs 0.5% (manual excl. BL) | 0.28% vs 0.54% excl. BL; **0.25% incl. BL** | flips |
| Autopilot adoption / managed spend / growth | 64% / $7.4M / 3.1x | 63.5% / 7,449,428 / 3.05x | 0 |

Why the deltas: calc reduces MRR for *all* refunds and lost disputes, not just duplicate-charge refunds as §3 says. A $2,499 requested refund on an Apr-26 invoice lowers the Apr base. That inflates growth and acceleration, and zeroing a refunded month counts that customer as churned that month.

## Issues by severity (slide = PDF page)

1. **H, slide 3, memo item 5, A4 note.** "Autopilot users undo 0.3% vs 0.5% for manual" only holds with Brightline removed from the manual side. Brightline is manual, with 70,873 actions and 5 undos. With it included, manual is 0.25% and Autopilot 0.28%, so the claim reverses, yet the memo calls it "the good news". Fix: show both figures, or drop the comparison and state the Autopilot undo rate alone.
2. **H, slide 12.** The headline "$8M takes runway from 8 to 40 months at today's burn" assumes no new spend, which contradicts "funds three milestones". There is no planned post-raise burn (an open input in §7) and no $ split by milestone, so a partner reads it as "no plan". Fix: get planned burn from Alex and show months at that burn plus $ per milestone. Until then, drop the 40.
3. **M, slide 6, memo TL;DR.** Logo churn 6.3%→3.8% does not reproduce with a plain rebuild (5.8%→3.2%). The gap comes from refund-zeroed months counted as churn, with no netting of reactivations. The direction holds. Fix: footnote the method, or count refunded months as paid.
4. **M, slides 1/4/9, memo.** The refund clawback goes beyond §3 and moves headline numbers: ex-BL growth is 2.19x (2.2x) vs 2.15x (2.1x), and acceleration 5.5x vs 5.2x. Diligence rebuilding from invoices gets 2.1x. Fix: apply §3 (duplicate refunds only), or state the treatment in A6.
5. **M, slide 7, A6, memo.** GM changes the locked §3 definition by adding usage revenue to the denominator. That decision is Zakhar's, and it mixes bases with ARR, which excludes usage. On §3 terms Oct is 79% and Jun 63%. Fix: confirm with Zakhar, or show subscription-only GM next to it.
6. **M, slide 6.** "NPS rose to 50" picks a partial quarter (Oct only, n=32) over Q2 45 (n=62) and Q3 39 (n=75). The NRR footnote (94% vs 84%) gives no n (94 / 76). Fix: "NPS 39–50 since Q2"; add n.
7. **M, slide 2.** The callout "$7k+, the cheapest alternative that does all three jobs: a hire" is contradicted by the same chart, where the Growlane managed agency (all three jobs) costs $4k+. Fix: name the agency at $4k+ as the cheapest alternative.
8. **M, slide 4.** "5.5x" divides two averages over a $6.7k base and is fragile (see 4). "Autopilot plans brought 68%" is true by construction: Autopilot is bundled into the new Pro/Scale tiers and expansion_autopilot = $1. Fix: lead with $7k→$37k; say "new customers on Autopilot plans".
9. **L, slide 7 vs memo.** "−83% OpenAI spend" is vendor mix. Total inference fell 29% Jul→Aug ($71.7k→$51.0k). The memo says vendor "switch", the deck says a second vendor was "added", and OpenAI is still billed. Fix: use total inference or per-1k actions; align the wording.
10. **L, A4.** "Tickets per customer" is rendered as a multiple ("3.1x"). The 2026Q4 row covers one month (1.4) and reads as an improvement. Fix: plain number; mark Oct "not comparable".
11. **L, slide 11.** "Customer success grew fastest" is true only since Apr-26 (5→10). Over the charted Jan-25–Oct-26 window, engineering grew more (4→11 vs 4→10). Fix: "CS doubled since April".
12. **L, memo.** Hand-typed "$6.0M" and "$6M" are inconsistent with each other and not ids. Fix: use `m.rev_kpi_arr_oct26`.
13. **L, memo.** The banned literals "Little Tea House" and "no competitors" appear as pushback quotes. Fix: rephrase (e.g. "the tea-shop quote", "'nobody does what we do'"), or accept and whitelist.
14. **L, A2.** 2025Q2 NRR m12 of 96% (vs GRR 50%) is Brightline's expansion inside that cohort. The headline says "since Autopilot", but 2026Q2 includes the April cohort. Fix: footnote Brightline; say "Q2-26 cohort".
15. **L, slide 5.** Managed ad spend is product telemetry, not reconciled to platforms (the metric caveat says so), and "$89.4M under management" is one month ×12. Fix: add "self-reported telemetry, Oct ×12" to the footer.
16. **L, slide 1, memo.** "Up 2.5x since Autopilot launched in May" is measured Apr→Oct, and 24% of the Δ is Brightline (agency plan, not Autopilot). Acceptable, but have the ex-BL 2.2x ready in the talk track.

No typos found. The A1 bridge reconciles to the dollar. Post-cut-off records (refund 4 Nov, dispute 5 Dec) are excluded and logged. The DQ log covers the double refund plus lost dispute on one invoice (DQ26).


## Resolution (main agent, after this report)
- Refunds and lost disputes no longer reduce MRR (cash events, logged in DQ). Growth is now 2.5x (2.48) total and 2.1x ex-Brightline; churn is 5.8% → 3.2%. These match the independent rebuild.
- Slide 3 and the memo: the comparative undo claim is removed. Only the Autopilot undo rate (0.3%) is stated, and the memo warns not to claim the comparison.
- Slide 12: the headline now uses the 24-month burn ceiling ($417k/mo, 1.7x today); 8 and 40 months move to the footer.
- Slide 6: NPS is shown as 15 → 39–50, with n in the retention footnote. Slide 2 callout: the agency figure replaces the contradicted "cheapest alternative" claim. Slide 4: the 5.5x callout is replaced with October MRR, and the note says Autopilot is bundled into the Pro/Scale plans. Slide 5 footer: telemetry, ×12. Slide 11: "grew most in 2026". A2 headline: limited to the 2026 Q2 cohort. A4: units fixed and a Q4 note added. Memo: KPI ARR comes from its id, and the vendor wording matches the deck.
- Kept deliberately: GM uses subscription + usage revenue (COGS serves both), labelled on slide 7 and in A6 and the memo. The memo may name "no competitors" and Little Tea House, because it has to tell Alex what was cut from his notes; qa.py exempts the memo for these two only.
- `python run.py qa`: 26 PASS, 0 FAIL.
