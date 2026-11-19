"""Build the deck: template + calc/metrics.csv + calc/series/ -> out/deck.html + out/deck.pdf.

    .venv/bin/python deck/build.py [--metrics PATH] [--series DIR] [--template PATH] [--out PATH]
                                   [--png DIR] [--allow-layout-issues] [--demo]

Template contract (CLAUDE.md §5):
- Numbers only as {{ m.<id> }} (formatted by unit, chartkit.fmt_value). A missing id or an NA value
  FAILS the build, naming the id. Overrides: m.fmt('id', decimals=1, sign=True), m.raw('id') -> float,
  m.pct_pts('id') -> '+5 pts' (ratio difference), m.meta('id') -> the csv row (period, caveat, ...).
- Charts only via {{ chart('name', **kw) }} (registry in deck/charts.py; charts read only series/).
- Series tables via table_rows('name', [('col', 'unit'), ...]); raw rows via series_rows('name').
- Layout macros: {% import 'slides.j2' as s %} (see slides.j2 header).
The template holds only <section class="slide"> blocks; build.py adds the document shell.

After rendering, the build fails if a headline wraps to >2 lines or anything spills past the slide
content box / into the footer, then checks the PDF (pages == slides, 960x540 pt).
"""
from __future__ import annotations

import argparse
import csv
import difflib
import importlib.util
import os
import re
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECK = ROOT / "deck"
sys.path.insert(0, str(DECK))

import chartkit  # noqa: E402
from chartkit import FormatError, fmt_value  # noqa: E402
from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError  # noqa: E402
from markupsafe import Markup  # noqa: E402

NA_VALUES = {"", "NA", "N/A", "NAN", "NONE", "NULL"}
COLS = ["id", "slide", "metric", "value", "unit", "period", "definition", "source_file", "filter",
        "formula", "caveat"]
PAGE_W_PT, PAGE_H_PT = 960.0, 540.0


class BuildError(Exception):
    pass


# ---------------------------------------------------------------- metrics


class Metrics:
    """{{ m.id }} -> formatted value. Missing id or NA value raises BuildError."""

    _RESERVED = {"fmt", "raw", "pct_pts", "meta", "ids"}

    def __init__(self, path: Path):
        if not path.exists():
            raise BuildError(f"metrics file not found: {path}")
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            missing = [c for c in ("id", "value", "unit") if c not in (reader.fieldnames or [])]
            if missing:
                raise BuildError(f"{path}: missing columns {missing}")
            rows = list(reader)
        self._path = path
        self._rows: dict[str, dict] = {}
        for r in rows:
            mid = (r.get("id") or "").strip()
            if not mid:
                continue
            if mid in self._rows:
                raise BuildError(f"{path}: duplicate metric id '{mid}'")
            if mid in self._RESERVED or mid.startswith("_"):
                raise BuildError(f"{path}: metric id '{mid}' clashes with a helper name")
            self._rows[mid] = r
        self.used: dict[str, str] = {}

    def _row(self, mid: str) -> dict:
        if mid not in self._rows:
            close = difflib.get_close_matches(mid, self._rows, n=3)
            hint = f" (did you mean {', '.join(close)}?)" if close else ""
            raise BuildError(f"metric id '{mid}' not found in {self._path}{hint}")
        r = self._rows[mid]
        if str(r.get("value", "")).strip().upper() in NA_VALUES:
            why = (r.get("caveat") or "").strip() or "no caveat given"
            raise BuildError(f"metric id '{mid}' is NA ({why}). NA never reaches a slide: "
                             f"drop or rephrase the element and note it in the memo")
        return r

    def fmt(self, mid: str, decimals: int | None = None, sign: bool = False,
            unit: str | None = None) -> str:
        r = self._row(mid)
        try:
            s = fmt_value(r["value"], unit or (r.get("unit") or "").strip(), decimals, sign)
        except FormatError as e:
            raise BuildError(f"metric id '{mid}': {e}") from None
        self.used[mid] = s
        return s

    def raw(self, mid: str) -> float:
        r = self._row(mid)
        try:
            v = float(r["value"])
        except ValueError:
            raise BuildError(f"metric id '{mid}': value '{r['value']}' is not numeric") from None
        self.used.setdefault(mid, r["value"])
        return v

    def pct_pts(self, mid: str, decimals: int = 0, sign: bool = True) -> str:
        return self.fmt(mid, decimals=decimals, sign=sign, unit="pts")

    def meta(self, mid: str) -> dict:
        return dict(self._row(mid))

    @property
    def ids(self) -> list[str]:
        return list(self._rows)

    def __getattr__(self, name: str) -> str:
        if name.startswith("_"):
            raise AttributeError(name)
        return self.fmt(name)

    def __getitem__(self, name: str) -> str:
        return self.fmt(name)


# ---------------------------------------------------------------- series + charts


class Series:
    """Read-only access to the series directory (charts and tables read nothing else)."""

    def __init__(self, d: Path):
        self.dir = d
        self.used: set[str] = set()

    def path(self, name: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
            raise BuildError(f"bad series name '{name}' (use the file stem, e.g. revenue_monthly)")
        p = self.dir / f"{name}.csv"
        if not p.exists():
            have = sorted(x.stem for x in self.dir.glob("*.csv"))
            raise BuildError(f"series '{name}' not found: {p} (available: {', '.join(have) or 'none'})")
        self.used.add(name)
        return p

    def read(self, name: str):
        import pandas as pd
        return pd.read_csv(self.path(name))

    def rows(self, name: str) -> list[dict]:
        with self.path(name).open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))


def load_registry() -> dict:
    spec = importlib.util.spec_from_file_location("deck_charts", DECK / "charts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CHARTS


class Pager:
    def __init__(self):
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return str(self.n)


# ---------------------------------------------------------------- render

SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<base href="{base}">
<title>{title}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
{content}
</body></html>
"""


def _template_frame(exc: BaseException) -> str:
    """'template.html:42' for the innermost template frame of an exception, if any."""
    loc = ""
    for fr in traceback.extract_tb(exc.__traceback__):
        if fr.filename.endswith((".html", ".j2", ".jinja")):
            loc = f"{Path(fr.filename).name}:{fr.lineno}"
    return loc


def render(template: Path, metrics: Metrics, series: Series, charts_out: Path | None,
           title: str, html_out: Path) -> tuple[str, list[str]]:
    registry = load_registry()
    charts_used: list[str] = []

    def chart(name: str, **kw) -> Markup:
        if name not in registry:
            raise BuildError(f"chart '{name}' not registered in deck/charts.py "
                             f"(have: {', '.join(sorted(registry))})")
        try:
            out = registry[name](series, **kw)
        except BuildError:
            raise
        except Exception as e:  # noqa: BLE001
            raise BuildError(f"chart '{name}' failed: {type(e).__name__}: {e}") from e
        svg = out if isinstance(out, str) else chartkit.fig_to_svg(out, id_prefix=f"{name}{len(charts_used)}")
        charts_used.append(name)
        if charts_out is not None:
            charts_out.mkdir(parents=True, exist_ok=True)
            (charts_out / f"{name}.svg").write_text(svg + "\n")
        return Markup(svg)

    def table_rows(name: str, cols) -> list[list[str]]:
        """Rows of series `name` formatted per column: cols = [(column, unit[, decimals]), ...].
        Empty / NA cells render as an en dash (e.g. cohort too young)."""
        out = []
        for r in series.rows(name):
            row = []
            for c in cols:
                col, unit, dec = (list(c) + [None])[:3]
                if col not in r:
                    raise BuildError(f"series '{name}' has no column '{col}' (have: {', '.join(r)})")
                v = (r[col] or "").strip()
                if v.upper() in NA_VALUES:
                    row.append("–")
                else:
                    try:
                        row.append(fmt_value(v, unit, dec))
                    except FormatError as e:
                        raise BuildError(f"series '{name}' column '{col}': {e}") from None
            out.append(row)
        return out

    def fmtv(value, unit: str, decimals: int | None = None, sign: bool = False) -> str:
        try:
            return fmt_value(value, unit, decimals, sign)
        except FormatError as e:
            raise BuildError(f"fmtv: {e}") from None

    env = Environment(
        loader=FileSystemLoader([str(template.parent), str(DECK)]),
        undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=True,
        extensions=["jinja2.ext.do"],
    )
    env.globals.update(m=metrics, chart=chart, table_rows=table_rows, series_rows=series.rows,
                       next_page=Pager())
    env.filters["fmtv"] = fmtv
    try:
        content = env.get_template(template.name).render()
    except BuildError as e:
        where = _template_frame(e)
        raise BuildError(f"{e}{f'  [at {where}]' if where else ''}") from None
    except TemplateError as e:
        where = _template_frame(e) or f"{getattr(e, 'name', '') or template.name}:{getattr(e, 'lineno', '?')}"
        raise BuildError(f"template error: {e}  [at {where}]") from None
    base = Path(os.path.relpath(DECK, html_out.parent)).as_posix() + "/"
    return SHELL.format(base=base, title=title, content=content), charts_used


# ---------------------------------------------------------------- browser: layout check, PNG, PDF

LAYOUT_JS = r"""
() => {
  const issues = [];
  const fonts = ['400 12pt Inter', '600 12pt Inter', '700 28pt Inter'].filter(f => !document.fonts.check(f));
  if (fonts.length) issues.push({slide: 0, msg: 'fonts not loaded: ' + fonts.join(', ')});
  const desc = el => {
    const c = (el.className && el.className.baseVal === undefined) ? '.' + String(el.className).trim().replace(/\s+/g, '.') : '';
    const t = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 50);
    return el.tagName.toLowerCase() + c + (t ? ` "${t}"` : '');
  };
  const slides = [...document.querySelectorAll('section.slide')];
  slides.forEach((s, i) => {
    const n = i + 1, sr = s.getBoundingClientRect(), cs = getComputedStyle(s);
    const L = sr.left + parseFloat(cs.paddingLeft) - 1, R = sr.right - parseFloat(cs.paddingRight) + 1;
    const T = sr.top + parseFloat(cs.paddingTop) - 1;
    const f = s.querySelector(':scope > .footer');
    if (!f) issues.push({slide: n, msg: 'no footer'});
    const B = (f ? f.getBoundingClientRect().top : sr.bottom - parseFloat(cs.paddingBottom)) + 1;
    if (f && f.getBoundingClientRect().bottom > sr.bottom - parseFloat(cs.paddingBottom) + 2)
      issues.push({slide: n, msg: 'footer pushed past the bottom padding (content too tall)'});
    if (f && f.querySelector('.page') && !f.querySelector('.page').textContent.trim())
      issues.push({slide: n, msg: 'empty page number'});
    const h = s.querySelector('.headline');
    if (h) {
      const lh = parseFloat(getComputedStyle(h).lineHeight);
      const lines = Math.round(h.getBoundingClientRect().height / lh);
      if (lines > 2) issues.push({slide: n, msg: `headline wraps to ${lines} lines (max 2): "${h.innerText.slice(0, 80)}"`});
    }
    let reported = 0;
    s.querySelectorAll(':scope > header, :scope > header *, :scope > .body, :scope > .body *').forEach(el => {
      if (reported >= 5) return;
      if (el.closest('svg') && el.tagName.toLowerCase() !== 'svg') return;
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return;
      const over = [];
      if (r.bottom > B) over.push(`bottom +${(r.bottom - B).toFixed(0)}px into footer`);
      if (r.right > R) over.push(`right +${(r.right - R).toFixed(0)}px`);
      if (r.left < L) over.push(`left -${(L - r.left).toFixed(0)}px`);
      if (r.top < T) over.push(`top -${(T - r.top).toFixed(0)}px`);
      if (el.scrollWidth > el.clientWidth + 2 && getComputedStyle(el).overflowX !== 'visible')
        over.push('clipped horizontally');
      if (over.length) { issues.push({slide: n, msg: `${desc(el)}: ${over.join(', ')}`}); reported++; }
    });
  });
  return {slides: slides.length, issues};
}
"""


def browser_pass(html_path: Path, pdf_path: Path, png_dir: Path | None) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.emulate_media(media="print")
        page.goto(html_path.as_uri())
        page.evaluate("document.fonts.ready.then(() => true)")
        res = page.evaluate(LAYOUT_JS)
        if png_dir is not None:
            png_dir.mkdir(parents=True, exist_ok=True)
            for old in png_dir.glob("slide_*.png"):
                old.unlink()
            for i, el in enumerate(page.query_selector_all("section.slide"), 1):
                el.screenshot(path=str(png_dir / f"slide_{i:02d}.png"))
        page.pdf(path=str(pdf_path), width="13.333in", height="7.5in", print_background=True,
                 prefer_css_page_size=True)
        browser.close()
    return res


BANNED = [r"\bdraft\b", r"\bTBD\b", r"placeholder", r"\bNA\b", r"lorem", r"\[", r"\]", r"\bnan\b"]


def verify_pdf(pdf_path: Path, n_slides: int, used: dict[str, str]) -> tuple[list[str], list[str]]:
    from pypdf import PdfReader

    errors, warnings = [], []
    reader = PdfReader(str(pdf_path))
    n = len(reader.pages)
    if n != n_slides:
        errors.append(f"PDF has {n} pages, HTML has {n_slides} slides (something broke onto an extra page)")
    for i, pg in enumerate(reader.pages, 1):
        w, h = float(pg.mediabox.width), float(pg.mediabox.height)
        if abs(w - PAGE_W_PT) > 1 or abs(h - PAGE_H_PT) > 1:
            errors.append(f"page {i} is {w:.1f}x{h:.1f} pt, expected {PAGE_W_PT:.0f}x{PAGE_H_PT:.0f}")
    text = " ".join((pg.extract_text() or "") for pg in reader.pages)
    flat = re.sub(r"\s+", " ", text)
    nospace = re.sub(r"\s+", "", text)
    missing = [f"{k}={v}" for k, v in used.items() if re.sub(r"\s+", "", v) not in nospace]
    if missing:
        warnings.append(f"{len(missing)} rendered metric values not found in PDF text "
                        f"(glyph/ToUnicode issue?): {', '.join(missing[:8])}")
    for pat in BANNED:
        hits = re.findall(rf".{{0,25}}{pat}.{{0,25}}", flat, flags=0 if pat == r"\bNA\b" else re.I)
        if hits:
            warnings.append(f"PDF text matches {pat!r}: {hits[:3]}")
    return errors, warnings


def literal_number_lines(template: Path) -> list[str]:
    """Lines of the content template with number-like literals (prose or string literals; not
    identifiers like grr_3m or labels like A2). Warning only: QA decides."""
    out = []
    src = re.sub(r"\{#.*?#\}", "", template.read_text(), flags=re.S)
    for i, line in enumerate(src.splitlines(), 1):
        t = re.sub(r"<[^>]*>", "", line)
        if re.search(r"(?<![\w.#\-])[$]?\d[\d,.]*", t):
            out.append(f"{template.name}:{i}: {line.strip()[:90]}")
    return out


# ---------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--metrics", type=Path, default=ROOT / "calc" / "metrics.csv")
    ap.add_argument("--series", type=Path, default=ROOT / "calc" / "series")
    ap.add_argument("--template", type=Path, default=DECK / "template.html")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "deck.pdf")
    ap.add_argument("--png", type=Path, default=None, help="also write one PNG per slide (1280x720) here")
    ap.add_argument("--title", default="Northpine — Series A")
    ap.add_argument("--allow-layout-issues", action="store_true", help="report layout issues without failing")
    ap.add_argument("--demo", action="store_true",
                    help="fixtures: deck/fixtures/{demo.html,metrics.csv,series/} -> out/deck_demo.pdf")
    a = ap.parse_args(argv)
    if a.demo:
        fx = DECK / "fixtures"
        a.metrics, a.series, a.template = fx / "metrics.csv", fx / "series", fx / "demo.html"
        if a.out == ROOT / "out" / "deck.pdf":
            a.out = ROOT / "out" / "deck_demo.pdf"
    t0 = time.time()
    pdf = a.out.resolve()
    html = pdf.with_suffix(".html")
    pdf.parent.mkdir(parents=True, exist_ok=True)
    # chart SVGs are exported to deck/charts/ only for the real deck template
    charts_out = DECK / "charts" if a.template.resolve() == (DECK / "template.html").resolve() else None
    try:
        metrics = Metrics(a.metrics)
        series = Series(a.series)
        if not a.template.exists():
            raise BuildError(f"template not found: {a.template}")
        doc, charts_used = render(a.template.resolve(), metrics, series, charts_out, a.title, html)
    except BuildError as e:
        print(f"BUILD FAILED: {e}", file=sys.stderr)
        return 1
    html.write_text(doc)
    res = browser_pass(html, pdf, a.png)
    errors, warnings = verify_pdf(pdf, res["slides"], metrics.used)
    layout = [f"slide {x['slide']}: {x['msg']}" for x in res["issues"]]
    lits = literal_number_lines(a.template)
    if lits:
        warnings.append(f"{len(lits)} template line(s) with literal digits (should be m.<id>):\n    "
                        + "\n    ".join(lits[:10]))
    ids_csv = pdf.with_name(pdf.stem + "_ids.csv")
    with ids_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "rendered"])
        w.writerows(sorted(metrics.used.items()))

    print(f"deck: {res['slides']} slides -> {pdf.relative_to(ROOT) if pdf.is_relative_to(ROOT) else pdf}"
          f" (+ {html.name}, {ids_csv.name}) in {time.time() - t0:.1f}s")
    print(f"  metrics used: {len(metrics.used)} of {len(metrics.ids)} | charts: {len(charts_used)} "
          f"({', '.join(dict.fromkeys(charts_used)) or 'none'}) | series read: {len(series.used)}")
    if not errors:
        print(f"  PDF ok: {res['slides']} pages, {PAGE_W_PT:.0f}x{PAGE_H_PT:.0f} pt")
    if a.png:
        print(f"  PNGs: {a.png}")
    for w_ in warnings:
        print(f"  WARN {w_}")
    for l_ in layout:
        print(f"  LAYOUT {l_}")
    for e_ in errors:
        print(f"  ERROR {e_}")
    if errors or (layout and not a.allow_layout_issues):
        print("BUILD FAILED" + (" (layout issues; --allow-layout-issues to override)" if layout and not errors else ""),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
