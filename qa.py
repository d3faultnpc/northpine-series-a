"""Independent QA for the shipped deck + memo (QA checklist below). Run: python run.py qa

Reads only the built artifacts (out/deck.pdf, out/memo.pdf, out/deck.html, out/*_ids.csv),
calc/metrics.csv and calc/series/*.csv. Fixes nothing. Exits 1 if any check FAILs.
The only import from the project is deck/chartkit.fmt_value: the deck's formatting rule.
"""
from __future__ import annotations

import math
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
OUT, CALC = ROOT / "out", ROOT / "calc"
sys.path.insert(0, str(ROOT / "deck"))
from chartkit import fmt_value  # noqa: E402  (the formatting rule under test)

RESULTS: list[tuple[str, str, str]] = []


def record(item: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((item, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {item}" + (f" -- {detail}" if detail else ""))


def norm(s: str) -> str:
    return (s.replace(" ", " ").replace("−", "-").replace("‑", "-")
            .replace("ʼ", "'").replace("’", "'"))


def squash(s: str) -> str:
    return re.sub(r"\s+", "", norm(s))


def pdf_pages(p: Path) -> list[str]:
    return [norm(pg.extract_text() or "") for pg in PdfReader(str(p)).pages]


DECK_PAGES = pdf_pages(OUT / "deck.pdf")
MEMO_PAGES = pdf_pages(OUT / "memo.pdf")
DECK_TXT, MEMO_TXT = "\n".join(DECK_PAGES), "\n".join(MEMO_PAGES)
M = pd.read_csv(CALC / "metrics.csv", dtype={"value": str}).set_index("id")
APPX = next((i for i, t in enumerate(DECK_PAGES) if "APPENDIX" in t), len(DECK_PAGES))
MAIN_TXT = "\n".join(DECK_PAGES[:APPX])


def fnum(v):
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- deck.html: sections, ticks, sources
class DeckHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sections, self.stack, self.in_text = [], [], False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "section" and "slide" in (a.get("class") or ""):
            self.sections.append({"ticks": Counter(), "svg": 0, "src": "", "cls": a.get("class")})
        if not self.sections:
            return
        sec = self.sections[-1]
        if tag == "svg":
            sec["svg"] += 1
        if tag == "g":
            self.stack.append(a.get("id") or "")
        if tag == "text":
            self.in_text = True
        if tag == "div" and a.get("class") == "src":
            sec["_src"] = True

    def handle_endtag(self, tag):
        if tag == "g" and self.stack:
            self.stack.pop()
        if tag == "text":
            self.in_text = False
        if tag == "div" and self.sections and self.sections[-1].pop("_src", False):
            pass

    def handle_data(self, data):
        if not self.sections:
            return
        sec = self.sections[-1]
        if self.in_text and any(re.search(r"-[xy]tick_\d+$", g) for g in self.stack):
            sec["ticks"][norm(data.strip())] += 1
        if sec.get("_src"):
            sec["src"] += data


HTML = DeckHTML()
HTML.feed((OUT / "deck.html").read_text())
SECTIONS = HTML.sections

# ---------------------------------------------------------------- 1. metric ids resolve and render right
NUM_RE = re.compile(r"(-?)\$?([\d,]+(?:\.\d+)?)\s*([kM%x]?)")


def consistent(rendered: str, value: float, unit: str) -> bool:
    """Rendered number equals the raw value within the rounding the rendered text implies."""
    m = NUM_RE.search(norm(rendered).replace("+", ""))
    if not m:
        return False
    sign, digits, suf = m.groups()
    x = float(digits.replace(",", "")) * (-1 if sign else 1)
    dec = len(digits.split(".")[1]) if "." in digits else 0
    v = value
    if suf == "%":
        v = value * 100 if unit in ("ratio", "pts") else value
    elif suf == "k":
        v = value / 1e3
    elif suf == "M":
        v = value / 1e6
    return abs(x - v) <= 0.5 * 10 ** -dec + 1e-9


RU_MONTHS = [("январ",), ("феврал",), ("март",), ("апрел",), ("ма",), ("июн",), ("июл",), ("август",), ("сентябр",), ("октябр",), ("ноябр",), ("декабр",)]
EN_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def same_month_ru(rendered: str, val) -> bool:
    """Russian memo renders month metrics as 'июле 2027' / 'декабря 2026': same month and year as the raw value."""
    mm = re.fullmatch(r"([а-я]+) (\d{4})", rendered.strip())
    if not mm:
        return False
    word, year = mm.groups()
    hits = [i + 1 for i, (stem,) in enumerate(RU_MONTHS) if word.startswith(stem) and (stem != "ма" or word in ("мая", "мае"))]
    if len(hits) != 1:
        return False
    v = str(val).strip()
    if v[:4].isdigit():
        y, mo = int(v[:4]), int(v[5:7])
    elif v[:3] in EN_MONTHS:
        y, mo = int(v[-4:]), EN_MONTHS.index(v[:3]) + 1
    else:
        return False
    return (y, mo) == (int(year), hits[0])


def check_ids(ids_csv: Path, pdf_txt: str, label: str) -> None:
    ids = pd.read_csv(ids_csv, dtype=str, keep_default_na=False)
    missing, na, wrong, absent, nondefault = [], [], [], [], []
    flat = squash(pdf_txt)
    for _, r in ids.iterrows():
        if r.id not in M.index:
            missing.append(r.id)
            continue
        row = M.loc[r.id]
        val, unit = row["value"], row["unit"]
        if str(val).strip().upper() in ("", "NA", "NAN", "N/A"):
            na.append(r.id)
            continue
        rendered = norm(r.rendered)
        try:
            default = norm(fmt_value(val, unit))
        except Exception:  # noqa: BLE001
            default = None
        if rendered != default:
            v = fnum(val)
            if same_month_ru(rendered, val):
                nondefault.append(r.id)
            elif v is None or not consistent(rendered, v, unit):
                wrong.append(f"{r.id}: '{rendered}' vs raw {val} ({unit})")
            else:
                nondefault.append(r.id)
        if squash(rendered) not in flat:
            absent.append(f"{r.id}='{rendered}'")
    record(f"1 {label}: all {len(ids)} ids exist in metrics.csv, none NA", not missing and not na,
           f"missing={missing} NA={na}" if (missing or na) else "")
    record(f"1 {label}: rendered text = fmt_value(raw) or consistent within rounding", not wrong,
           "; ".join(wrong) if wrong else f"{len(nondefault)} ids use explicit decimals/units (consistent)")
    record(f"1 {label}: every rendered id text appears in the PDF", not absent, "; ".join(absent))


check_ids(OUT / "deck_ids.csv", DECK_TXT, "deck")
check_ids(OUT / "memo_ids.csv", MEMO_TXT, "memo")


def literal_numbers(tpl: Path) -> list[str]:
    t = tpl.read_text()
    t = re.sub(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", " ", t, flags=re.S)
    t = re.sub(r"<svg.*?</svg>", " ", t, flags=re.S)
    found = set(re.findall(r"-?\$\d{1,3}(?:,\d{3})*(?:\.\d+)?[kM]?|\b\d[\d,.]*%|\b\d+(?:\.\d+)?x\b", t))
    return sorted(found - {"$0"})  # "$0" = definitional threshold ("billed above $0"), not a metric


for tpl in [ROOT / "deck" / "template.html", ROOT / "memo" / "memo.md.j2"]:
    lit = literal_numbers(tpl)
    record(f"1 no literal $/%/x numbers in {tpl.relative_to(ROOT)}", not lit, ", ".join(lit))

# ---------------------------------------------------------------- 2. every $ / % / Nx in the PDFs traces
TOK_RE = re.compile(r"-?\$\d{1,3}(?:,\d{3})*(?:\.\d+)?[kM]?|-?\d[\d,]*(?:\.\d+)?%|\b\d+(?:\.\d+)?x\b")


def variants(v: float, unit: str | None) -> set[str]:
    out = set()
    units = {unit} if unit else set()
    units |= {"usd", "usd_k", "ratio", "pct", "x"}
    for u in units:
        for d in (None, 0, 1, 2):
            try:
                out.add(norm(fmt_value(v, u, decimals=d)))
            except Exception:  # noqa: BLE001
                pass
    return {s.replace(" pts", "") for s in out}


TRACE: set[str] = set()
for _id, r in M.iterrows():
    v = fnum(r["value"])
    if v is not None:
        TRACE |= variants(v, r["unit"])
        TRACE |= variants(-v, r["unit"])
for f in sorted((CALC / "series").glob("*.csv")):
    df = pd.read_csv(f)
    for c in df.columns:
        s = pd.to_numeric(df[c], errors="coerce").dropna()
        for v in s.unique():
            TRACE |= variants(float(v), None)


def untraced(pages: list[str], sections=None) -> list[str]:
    bad = []
    for i, t in enumerate(pages):
        toks = Counter(TOK_RE.findall(t.replace(",", ",")))
        if sections is not None and i < len(sections):
            toks -= sections[i]["ticks"]  # chart axis tick labels are scale, not claims
        for tok in toks:
            if tok not in TRACE and tok.lstrip("-") not in TRACE:
                bad.append(f"p{i + 1}:{tok}")
    return bad


for label, pages, secs in [("deck", DECK_PAGES, SECTIONS), ("memo", MEMO_PAGES, None)]:
    bad = untraced(pages, secs)
    record(f"2 {label}: every $-amount, % and Nx traces to metrics.csv or calc/series", not bad, ", ".join(bad))

# ---------------------------------------------------------------- 3. consistency across deck + memo
ALL = DECK_TXT + "\n" + MEMO_TXT
f = lambda i, **k: norm(fmt_value(M.loc[i, "value"], M.loc[i, "unit"], **k))  # noqa: E731

arr_ok = {f("rev_arr_post_oct26"), f("rev_kpi_arr_oct26"), f("der_arr_12m_at_pace")}
arr_seen = set(re.findall(r"(\$[\d.]+M)\s+ARR", ALL))
record("3 ARR: every '$xM ARR' is arr_post (or the quoted KPI $6.0M / 12-month extrapolation)",
       arr_seen <= arr_ok and f("rev_arr_post_oct26") in arr_seen, f"seen={sorted(arr_seen)}")

mrr_label = fmt_value(float(M.loc["rev_arr_post_oct26", "value"]) / 12, "usd")
mrr_pages = [i + 1 for i, t in enumerate(DECK_PAGES[:APPX]) if "Subscription MRR" in t]
record("3 MRR: Oct last-value chart label = ARR/12 on every MRR chart",
       all(mrr_label in DECK_PAGES[p - 1] for p in mrr_pages) and mrr_pages, f"{mrr_label} on pages {mrr_pages}")

pc = set(re.findall(r"(?<![\d,])(\d[\d,]*)\s+paying customers(?!\s+(?:are\s+)?still)", ALL))
pc = {x.rstrip(",") for x in pc} - {"2025", "2026"}  # "Oct 2026, paying customers" is a date
record("3 paying customers identical everywhere", pc == {f("rev_paying_customers_oct26")}, f"seen={sorted(pc)}")

g, gx = f("rev_growth_apr_oct"), f("rev_growth_apr_oct_ex_bl")
gseen = set(re.findall(r"(?:up|growth[^.\n]{0,40}?|MRR is up)\s+(\d+\.\dx)", ALL)) | set(
    re.findall(r"(\d+\.\dx)\s*\n?\s*(?:MRR growth|growth since April)", ALL))
record("3 growth multiples are only rev_growth_apr_oct / ex-Brightline", gseen <= {g, gx} and g in ALL and gx in ALL,
       f"seen={sorted(gseen)}; expected {g} total, {gx} ex-Brightline")

rw = set(re.findall(r"[Rr]unway (?:is |from )?(\d+)", ALL))
record("3 runway months identical everywhere", rw == {f("burn_runway_months")}, f"seen={sorted(rw)}")

bl = set(re.findall(r"Brightline[^.\n]{0,40}?(\d+%) of MRR|(\d+%)\s*\n?of MRR from Brightline", ALL))
bl = {x for t in bl for x in t if x}
record("3 Brightline share identical everywhere", bl == {f("rev_top1_share")}, f"seen={sorted(bl)}")

gm = set(re.findall(r"(?:back (?:to|at)|margin in October\.?|GM is back at)\s*(\d+%)", ALL))
record("3 Oct gross margin identical everywhere", gm == {f("der_gm_oct26")}, f"seen={sorted(gm)}")

cut = set(re.findall(r"(\d{1,2} Oct 2026)", ALL))
other_ends = set(re.findall(r"Jan 2025 – (\w{3} 2026)", ALL))
record("3 one cut-off date (31 Oct 2026; series end Oct 2026)", cut <= {"31 Oct 2026"} and other_ends <= {"Oct 2026"},
       f"dates={sorted(cut)} series_end={sorted(other_ends)}")

# ---------------------------------------------------------------- 4. banned strings
BANNED = ["6.3T", "TikTok spike", "Little Tea House", "no competitors", "250+", "draft", "TBD", "placeholder",
          "lorem", " NA ", "[", "]"]
# Main-agent decision: the memo to Alex must name what was cut from HIS notes, so the two quoted
# asks ("no competitors", the Little Tea House quote) are allowed in the memo only, never in the deck.
MEMO_ALLOWED = {"Little Tea House", "no competitors"}
for label, txt in [("deck", DECK_TXT), ("memo", MEMO_TXT)]:
    low = " " + re.sub(r"\s+", " ", txt).lower() + " "
    hits = [b for b in BANNED if b.lower() in low and not (label == "memo" and b in MEMO_ALLOWED)]
    if re.search(r"(?<![\d.,])3x\b", txt):
        hits.append("3x")
    ctx = []
    for b in hits:
        k = low.find(b.lower())
        ctx.append(f"'{b}' in \"...{low[max(0, k - 45):k + 45].strip()}...\"")
    record(f"4 {label}: banned strings absent", not hits, "; ".join(ctx))

# ---------------------------------------------------------------- 5. page counts / sizes
r = PdfReader(str(OUT / "deck.pdf"))
sizes = {(round(float(p.mediabox.width)), round(float(p.mediabox.height))) for p in r.pages}
record("5 deck main slides <= 12 (pages before first APPENDIX)", APPX <= 12, f"main={APPX}, total={len(DECK_PAGES)}")
record("5 memo = 1 page", len(MEMO_PAGES) == 1, f"pages={len(MEMO_PAGES)}")
record("5 deck pages 960x540 pt", sizes == {(960, 540)}, f"sizes={sizes}")

# ---------------------------------------------------------------- 6. platform-reported numbers labelled
bad = []
for label, pages in [("deck", DECK_PAGES), ("memo", MEMO_PAGES)]:
    for i, t in enumerate(pages):
        low = t.lower()
        mentions = re.search(r"(meta|google|platform|ads?)[^.\n]{0,60}trials|trials[^.\n]{0,60}(meta|google|platform)", low)
        has_num = re.search(r"\d", low)
        if mentions and has_num and not re.search(r"(platform|meta)[- ](reported|modelled)|reported", low):
            bad.append(f"{label} p{i + 1}")
plat_ids = [i for i in M.index if "platform" in i]
for i in plat_ids:
    v = fnum(M.loc[i, "value"])
    if v is None:
        continue
    s = norm(fmt_value(v, M.loc[i, "unit"]))
    for label, pages in [("deck", DECK_PAGES), ("memo", MEMO_PAGES)]:
        for k, t in enumerate(pages):
            # only pages that talk about trials: the same rendered value (e.g. "1.7x") can be an unrelated metric
            if "trial" in t.lower() and re.search(rf"(?<![\d.,$]){re.escape(s)}(?![\d])", t) and "reported" not in t.lower():
                bad.append(f"{label} p{k + 1}:{i}={s}")
record("6 platform-reported numbers labelled 'reported' wherever shown", not bad, ", ".join(bad))

# ---------------------------------------------------------------- 7. concentration, GM, runway in main slides
c7 = {
    "concentration (Brightline % of MRR)": f("rev_top1_share") in MAIN_TXT and "Brightline" in MAIN_TXT,
    "gross margin": f("der_gm_oct26") in MAIN_TXT and "Gross margin" in MAIN_TXT,
    "runway": "runway" in MAIN_TXT.lower() and f("burn_runway_months") in MAIN_TXT,
}
record("7 concentration, GM and runway on main slides", all(c7.values()), ", ".join(k for k, v in c7.items() if not v))

# ---------------------------------------------------------------- 8. Source footer on every chart slide
miss = [f"slide {i + 1}" for i, s in enumerate(SECTIONS) if s["svg"] and not norm(s["src"]).strip().startswith("Source:")]
pdf_miss = [f"page {i + 1}" for i, t in enumerate(DECK_PAGES) if "Source:" not in t]
nchart = sum(1 for s in SECTIONS if s["svg"])
record("8 every chart slide has a 'Source:' footer", not miss and not pdf_miss and len(SECTIONS) == len(DECK_PAGES),
       f"{nchart} chart slides; missing html={miss} pdf={pdf_miss}")

# ---------------------------------------------------------------- summary
n_fail = sum(1 for _, s, _ in RESULTS if s == "FAIL")
print(f"\nQA: {len(RESULTS) - n_fail} PASS, {n_fail} FAIL")
sys.exit(1 if n_fail else 0)
