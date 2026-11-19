"""CEO memo: memo/memo.md.j2 (numbers as {{ m.<id> }}) -> out/memo.html -> out/memo.pdf (must be 1 page)."""
import sys
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deck"))
from build import Metrics  # noqa: E402  (same metric loader and formatter as the deck)

CSS = """
@font-face { font-family: "Inter"; src: url("../deck/fonts/Inter-Regular.ttf"); font-weight: 400; }
@font-face { font-family: "Inter"; src: url("../deck/fonts/Inter-SemiBold.ttf"); font-weight: 600; }
@font-face { font-family: "Inter"; src: url("../deck/fonts/Inter-Bold.ttf"); font-weight: 700; }
@page { size: Letter; margin: 0; }
* { box-sizing: border-box; }
html, body { background: #ffffff; }
body { font-family: "Inter", sans-serif; font-feature-settings: "calt" 0; color: #16202c; font-size: 8.9pt; line-height: 1.36; margin: 0; padding: 0.5in 0.6in; }
h1 { font-size: 13.5pt; font-weight: 700; margin: 0 0 0.05in; letter-spacing: -0.01em; }
.meta { color: #737b84; font-size: 8pt; margin-bottom: 0.08in; }
h2 { font-size: 9.6pt; font-weight: 700; color: #0b7a5e; margin: 0.1in 0 0.03in; text-transform: uppercase; letter-spacing: 0.04em; }
p { margin: 0 0 0.05in; }
ul, ol { margin: 0 0 0.04in; padding-left: 0.17in; }
li { margin-bottom: 0.025in; }
strong { font-weight: 600; }
"""


MONTHS_RU = {
    "gen": ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"],
    "prep": ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"],
}
MONTH_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def month_ru(m, mid: str, case: str) -> str:
    """Month-valued metric ('2027-07' or 'Jul 2027') -> Russian month in the given case: 'июле 2027'."""
    v = str(m._row(mid)["value"]).strip()
    if v[:4].isdigit():
        y, mo = int(v[:4]), int(v[5:7])
    else:
        mo, y = MONTH_EN.index(v[:3]) + 1, int(v[-4:])
    s = f"{MONTHS_RU[case][mo - 1]} {y}"
    m.used[mid] = s
    return s


def main():
    m = Metrics(ROOT / "calc" / "metrics.csv")
    env = Environment(loader=FileSystemLoader(str(ROOT / "memo")), undefined=StrictUndefined, autoescape=False)
    md = env.get_template("memo.md.j2").render(m=m, mon=lambda mid, case="prep": month_ru(m, mid, case))
    body = markdown.markdown(md)
    body = body.replace("</h1>", "</h1><div class='meta'>От: Захар, Chief of Staff · Кому: Alex · Данные по 31 октября 2026 · Всё в репо: github.com/d3faultnpc/northpine-series-a</div>", 1)
    html = f"<!doctype html><html lang='ru'><head><meta charset='utf-8'><title>Записка Alex</title><style>{CSS}</style></head><body>{body}</body></html>"
    out = ROOT / "out"
    (out / "memo.html").write_text(html)
    (out / "memo.md").write_text(md)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto((out / "memo.html").as_uri())
        pg.evaluate("document.fonts.ready")
        pg.pdf(path=str(out / "memo.pdf"), format="Letter", print_background=True, prefer_css_page_size=True)
        b.close()
    with open(out / "memo_ids.csv", "w") as f:
        f.write("id,rendered\n" + "".join(f'{k},"{v}"\n' for k, v in m.used.items()))
    n = len(PdfReader(str(out / "memo.pdf")).pages)
    print(f"memo: out/memo.pdf, {n} page(s); {len(m.used)} metric ids used")
    if n != 1:
        raise SystemExit(f"MEMO FAILED: {n} pages, must be 1")


if __name__ == "__main__":
    main()
