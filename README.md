# Northpine — Series A materials

Chief of Staff take-home. Northpine is a fictional AI growth agent for Shopify brands raising an $8M Series A.

**Deliverables**
- [Investor deck (PDF)](out/deck.pdf): 12 slides plus appendix A1–A6
- [One-page note to the CEO (PDF, in Russian)](out/memo.pdf)
- [Calc table](out/calc_table.csv): every number in the deck and memo, with definition, source, filter and formula
- Supporting files: [data-quality log](calc/data_quality.csv), [checks vs a first-pass snapshot](calc/checks.txt), [all metrics](calc/metrics.csv), [monthly series](calc/series/)

## How this was built

I set the frame before touching the numbers: which metric definitions are locked, which decision gates the story has to pass (story A vs B, headlines, the note to the CEO) and what QA must check. Growth was computed blind, without anchoring to any expected value.

Claude Code worked as the orchestrator. It wrote the single loader, ran domain agents in parallel (revenue, retention, burn and margin, channels, data quality), merged their output into one `calc/metrics.csv` and brought a recommendation to each gate. I accepted or amended each one: for example, the note to the CEO is in Russian, it carries the fallback plan if the round slips, and slide 9 says plainly that Brightline sits inside ARR. A separate agent that never touched the calc ran the independent QA (`out/qa_report.md`, `qa.py`).

## Definitions

- **ARR** = normalized MRR after discounts × 12, at 31 Oct 2026. Monthly plans at price, annual at price/12; excludes one-time fees and usage tiers (shown separately). Refunds and disputes are cash events and do not change MRR.
- **Paying customer** = billed above $0 for October service; trials never count; an agency counts as one customer.
- **Growth** = MRR Oct 2026 / MRR Apr 2026, both from the invoice-built series, never cash. Driver split is accounting attribution, not causal effect.
- **Retention**: cohorts by first paid month; Autopilot vs non-Autopilot compared ex-Brightline (self-selected groups).
- **Gross margin** = (subscription + usage revenue − COGS) / revenue; COGS = inference + hosting + support tooling, from the bank.
- **Burn and runway** from the bank: 3-month average net burn; runway = cut-off cash / burn.
- **CAC** = bank channel spend (+ partner commissions) / new paying customers by Stripe channel tag; never platform attribution.

## Data quality

Issues are logged, never cleaned silently: `calc/data_quality.csv` (file, issue, rows, impact, treatment, severity). KPI-sheet cash and burn reconcile with the bank; KPI MRR, ARR and customers do not reconcile with Stripe (appendix A1 bridges ARR to the dollar).

## Reproduce

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
data/fetch.sh                 # 21 source files -> data/raw (not committed)
.venv/bin/python run.py all   # calc -> deck -> memo -> qa
```

## Layout

| Path | What |
|---|---|
| `data/raw/` | source files, read-only, fetched by `data/fetch.sh` |
| `calc/` | pipeline; `calc/metrics.csv` is the single source of every reported number |
| `calc/series/` | monthly series feeding the charts |
| `deck/` | HTML template, `style.css`, charts (SVG), build to PDF via Playwright/Chromium |
| `memo/` | CEO note template |
| `out/` | built PDFs and the calc table |

Every number in the deck and memo is rendered from `calc/metrics.csv` by id; the build fails on a missing or NA id.
Cut-off date: 2026-10-31.
