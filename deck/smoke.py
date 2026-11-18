"""Smoke test: one 16:9 slide (headline + number + matplotlib SVG chart) -> out/test.pdf.

Placeholder numbers only — this checks the toolchain, not the data.
Run: .venv/bin/python deck/smoke.py
"""
from io import StringIO
from pathlib import Path

import matplotlib
matplotlib.use("svg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from jinja2 import Template
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DECK = ROOT / "deck"
OUT = ROOT / "out"

INK, ACCENT, INK_40, INK_15 = "#16202c", "#0b7a5e", "#a2a6ab", "#dcdee0"

for ttf in (DECK / "fonts").glob("Inter-*.ttf"):
    font_manager.fontManager.addfont(str(ttf))
plt.rcParams.update({
    "font.family": "Inter",
    "svg.fonttype": "none",          # keep text as text -> rendered with the page's Inter
    "axes.edgecolor": INK_40, "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 11,
})


def chart_svg() -> str:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct"]
    vals = [10, 11, 12, 13, 17, 21, 24, 27, 30, 33]  # placeholder
    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    ax.bar(months, vals, color=[INK_40] * 4 + [ACCENT] * 6, width=0.65)
    ax.axvline(3.5, color=INK, lw=1, ls=(0, (3, 3)))
    ax.text(3.6, max(vals) * 0.98, "Launch", color=INK, va="top", fontsize=10)
    ax.set_ylabel("Placeholder, $k")
    ax.grid(axis="y", color=INK_15, lw=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    buf = StringIO()
    fig.savefig(buf, format="svg", transparent=True)
    plt.close(fig)
    svg = buf.getvalue()
    return svg[svg.index("<svg"):]


TEMPLATE = Template("""<!doctype html>
<html><head><meta charset="utf-8"><link rel="stylesheet" href="style.css"></head>
<body>
<section class="slide">
  <header>
    <div class="kicker">Smoke test</div>
    <h1 class="headline">{{ headline }}</h1>
    <p class="sub">{{ sub }}</p>
  </header>
  <div class="body split">
    <div class="tiles">
      <div class="tile"><div class="value accent">{{ m.big }}</div><div class="label">Placeholder metric</div></div>
      <div class="tile"><div class="value">{{ m.small }}</div><div class="label">Second tile</div></div>
    </div>
    <div class="chart">{{ chart | safe }}</div>
  </div>
  <footer class="footer"><span>Source: placeholder series, toolchain check only</span><span class="page">1</span></footer>
</section>
</body></html>""")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    html = TEMPLATE.render(
        headline="Takeaway headline that reads without the chart",
        sub="Inter, ink + pine accent, 13.333 × 7.5 in, SVG chart from matplotlib",
        m={"big": "$0.0M", "small": "0.0x"},
        chart=chart_svg(),
    )
    page_path = DECK / "_smoke.html"
    page_path.write_text(html)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(page_path.as_uri())
        page.evaluate("document.fonts.ready")
        page.pdf(path=str(OUT / "test.pdf"), width="13.333in", height="7.5in",
                 print_background=True, prefer_css_page_size=True)
        browser.close()
    print("wrote", OUT / "test.pdf")


if __name__ == "__main__":
    main()
