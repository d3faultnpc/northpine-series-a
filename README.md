# Northpine — Series A materials

Chief of Staff take-home. Northpine is a fictional AI growth agent for Shopify brands raising an $8M Series A.

**Deliverables** (in `out/` once built): investor deck (PDF), one-page note to the CEO, and a calc table that derives every number in both.

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

Every number in the deck and memo is rendered from `calc/metrics.csv` by id; templates contain no literal numbers.
Cut-off date: 2026-10-31.
