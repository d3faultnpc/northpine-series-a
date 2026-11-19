# Northpine — Series A materials

Chief of Staff take-home. Northpine is a fictional AI growth agent for Shopify brands raising an $8M Series A.

**Deliverables**
- [Investor deck (PDF)](out/deck.pdf): 12 slides plus appendix A1–A6
- [One-page note to the CEO (PDF, in Russian)](out/memo.pdf)
- [Calc table](out/calc_table.csv): every number in the deck and memo, with definition, source, filter and formula
- Supporting files: [data-quality log](calc/data_quality.csv), [checks vs the founder's snapshot](calc/checks.txt), [all metrics](calc/metrics.csv), [monthly series](calc/series/)

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
