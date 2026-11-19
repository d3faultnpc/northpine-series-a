"""House chart style for the Northpine deck (matplotlib -> inline SVG) + the one value formatter.

- Inter registered from deck/fonts, svg.fonttype=none: chart text stays text and is drawn by the
  page's Inter (selectable, searchable in the PDF).
- Colors: ink + accent, everything else an ink tint (deck/style.css).
- Charts carry axis units (unit_label) but no source line: the source goes in the slide footer.
- fmt_value() is the single number formatter for slides AND chart labels (one rule per unit).

Figure sizes match the slide areas they land in, so 11pt in the figure is 11pt on the slide:
  SIZES["main"]  chart next to a callout column   SIZES["full"]  full-width chart
  SIZES["half"]  one of two side-by-side charts     SIZES["short"] full width, shorter
"""
from __future__ import annotations

import math
import re
from decimal import ROUND_HALF_UP, Decimal
from io import StringIO
from pathlib import Path

import matplotlib

matplotlib.use("svg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from cycler import cycler  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
from matplotlib.transforms import blended_transform_factory  # noqa: E402

DECK = Path(__file__).resolve().parent

INK = "#16202c"
ACCENT = "#0b7a5e"
INK_80 = "#45505c"
INK_60 = "#737b84"
INK_40 = "#a2a6ab"
INK_15 = "#dcdee0"
INK_05 = "#f3f4f4"
PAPER = "#ffffff"
TINTS = [INK, INK_60, INK_40, INK_15]  # dark -> light, for non-accent layers

SIZES = {
    "main": (8.7, 4.3),
    "full": (12.1, 4.3),
    "half": (5.85, 3.9),
    "short": (12.1, 3.4),
}

HEADROOM = 1.2  # y-axis top = max * HEADROOM: room for the last-value label under event labels

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# ---------------------------------------------------------------- formatting (one rule per unit)

UNITS = ("usd", "usd_per_month", "ratio", "pct", "pts", "x", "count", "months", "score", "text", "date", "month", "hours", "usd_k")


class FormatError(ValueError):
    pass


def _q(v: float, d: int) -> str:
    """Round half-up (not banker's) to d decimals, return plain string."""
    q = Decimal(1).scaleb(-d) if d > 0 else Decimal(1)
    return str(Decimal(repr(float(v))).quantize(q, rounding=ROUND_HALF_UP))


def _num(v: float, d: int, sep: bool = True) -> str:
    s = _q(abs(v), d)
    if sep:
        ip, _, fp = s.partition(".")
        s = f"{int(ip):,}" + (f".{fp}" if fp else "")
    return s


def fmt_usd(v: float, decimals: int | None = None) -> str:
    """$5.4M for >=1M (1 dec), $503k for >=10k (0 dec), $1,490 below (0 dec)."""
    a = abs(v)
    sign = "-" if v < 0 else ""
    if float(_q(a, 0)) >= 1e6 or float(_q(a / 1e3, 0)) >= 1000:
        d = 1 if decimals is None else decimals
        return f"{sign}${_num(a / 1e6, d)}M"
    if float(_q(a, 0)) >= 1e4:
        d = 0 if decimals is None else decimals
        return f"{sign}${_num(a / 1e3, d)}k"
    d = 0 if decimals is None else decimals
    return f"{sign}${_num(a, d)}"


def fmt_value(value, unit: str, decimals: int | None = None, sign: bool = False) -> str:
    """Format a raw value by unit. The deck's only number formatter.

    usd / usd_per_month: $5.4M | $503k | $1,490      ratio: 0.64 -> 64%      pct: 64 -> 64%
    pts: ratio difference 0.05 -> 5 pts (no-break space)              x: 2.47 -> 2.5x
    count: 1,234   months: 9   score: 50              text / date: passed through unchanged
    sign=True prefixes '+' on positive values (negative always gets '-').
    """
    if unit in ("text", "date"):
        return str(value)
    if unit == "month":  # 'YYYY-MM' -> 'Jul 2027'
        y, mo = str(value)[:7].split("-")
        return f"{MONTH_ABBR[int(mo) - 1]} {y}"
    if unit not in UNITS:
        raise FormatError(f"unknown unit '{unit}' (known: {', '.join(UNITS)})")
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise FormatError(f"value '{value}' is not numeric (unit '{unit}')") from None
    if math.isnan(v):
        raise FormatError("value is NaN")
    if unit in ("usd", "usd_per_month"):
        s = fmt_usd(v, decimals)
    elif unit == "usd_k":  # explicit thousands, for ranges and headlines: $7k
        s = ("-" if v < 0 else "") + "$" + _num(abs(v) / 1e3, 0 if decimals is None else decimals) + "k"
    elif unit == "ratio":
        s = ("-" if v < 0 else "") + _num(v * 100, 0 if decimals is None else decimals) + "%"
    elif unit == "pct":
        s = ("-" if v < 0 else "") + _num(v, 0 if decimals is None else decimals) + "%"
    elif unit == "pts":
        s = ("-" if v < 0 else "") + _num(v * 100, 0 if decimals is None else decimals) + "\u00a0pts"
    elif unit == "x":
        s = ("-" if v < 0 else "") + _num(v, 1 if decimals is None else decimals) + "x"
    else:  # count, months, score, hours
        s = ("-" if v < 0 else "") + _num(v, 0 if decimals is None else decimals)
    if s.startswith("-") and float(re.sub(r"[^0-9.]", "", s) or 0) == 0:
        s = s[1:]  # no "-0%"
    if sign and v > 0 and not s.startswith("-"):
        s = "+" + s
    return s


# ---------------------------------------------------------------- style


def _setup() -> None:
    for ttf in sorted((DECK / "fonts").glob("Inter-*.ttf")):
        font_manager.fontManager.addfont(str(ttf))
    plt.rcParams.update({
        "font.family": "Inter",
        "font.size": 11,
        "svg.fonttype": "none",
        "svg.hashsalt": "northpine",           # deterministic ids -> stable diffs
        "axes.edgecolor": INK_40,
        "axes.labelcolor": INK_80,
        "axes.labelsize": 11,
        "axes.titlesize": 11,
        "axes.titlelocation": "left",
        "axes.titlecolor": INK_60,
        "axes.titlepad": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.spines.left": False,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "axes.axisbelow": True,
        "axes.prop_cycle": cycler(color=[ACCENT, INK, INK_60, INK_40]),
        "axes.unicode_minus": False,
        "grid.color": INK_15,
        "grid.linewidth": 0.8,
        "xtick.color": INK_80,
        "ytick.color": INK_80,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "xtick.major.size": 0,
        "ytick.major.size": 0,
        "xtick.major.pad": 6,
        "ytick.major.pad": 4,
        "lines.linewidth": 2,
        "legend.frameon": False,
        "legend.fontsize": 10,
        "savefig.transparent": True,
    })


_setup()


def new_fig(size="main"):
    """Figure + axes at a slide-area size (key of SIZES or (w, h) inches), constrained layout."""
    w, h = SIZES[size] if isinstance(size, str) else size
    fig, ax = plt.subplots(figsize=(w, h), layout="constrained")
    fig.get_layout_engine().set(w_pad=0.04, h_pad=0.04)
    return fig, ax


def fig_to_svg(fig, id_prefix: str = "c") -> str:
    """Figure -> inline <svg>: no XML header/metadata, no fixed width/height (viewBox kept, so it
    scales to its box), ids prefixed so several charts can share one HTML page."""
    buf = StringIO()
    fig.savefig(buf, format="svg", transparent=True, metadata={"Date": None, "Creator": None})
    plt.close(fig)
    svg = buf.getvalue()
    svg = svg[svg.index("<svg"):]
    svg = re.sub(r"<metadata>.*?</metadata>\s*", "", svg, flags=re.S)
    head_end = svg.index(">")
    head = svg[:head_end]
    head = re.sub(r'\s(width|height)="[^"]*"', "", head)
    head += f' preserveAspectRatio="xMinYMin meet" class="np-chart" role="img"'
    svg = head + svg[head_end:]
    ids = set(re.findall(r'\sid="([^"]+)"', svg))
    pfx = re.sub(r"[^A-Za-z0-9_-]", "_", id_prefix)

    def _sub(mo):
        return mo.group(1) + (f"{pfx}-{mo.group(2)}" if mo.group(2) in ids else mo.group(2))

    svg = re.sub(r'(\sid="|url\(#|href="#)([^")]+)', _sub, svg)
    return svg.strip()


# ---------------------------------------------------------------- axes helpers


def _arr(v) -> np.ndarray:
    return np.asarray(list(v), dtype=float)


def _month_key(m) -> str:
    s = str(m)
    return s[:7]


def set_months(ax, months, every: int = 3) -> None:
    """Categorical month axis (positions 0..n-1) labelled 'Jan\\n2025' on January / first tick,
    'Apr' elsewhere, every `every` months. months: 'YYYY-MM' strings, Periods or Timestamps."""
    keys = [_month_key(m) for m in months]
    ax._np_months = keys
    pos, labels = [], []
    for i, k in enumerate(keys):
        y, mo = int(k[:4]), int(k[5:7])
        if (mo - 1) % every != 0 and i != 0:
            continue
        pos.append(i)
        labels.append(f"{MONTH_ABBR[mo - 1]}\n{y}" if (mo == 1 or not labels) else MONTH_ABBR[mo - 1])
    ax.set_xticks(pos, labels)
    ax.set_xlim(-0.6, len(keys) - 0.4)


def _xpos(ax, x) -> float:
    if isinstance(x, (int, float, np.integer, np.floating)):
        return float(x)
    keys = getattr(ax, "_np_months", None)
    k = _month_key(x)
    if keys is None or k not in keys:
        raise ValueError(f"month {x!r} not on this axis (call set_months first)")
    return float(keys.index(k))


def unit_label(ax, text: str) -> None:
    """Axis unit, top-left above the plot (e.g. 'MRR, $ per month')."""
    ax.set_title(text, loc="left", fontsize=10, color=INK_60)


def _scale_for(top: float):
    if top >= 1.5e6:
        return 1e6, "M"
    if top >= 1.5e3:
        return 1e3, "k"
    return 1.0, ""


def _auto_dec(step: float) -> int:
    for d in range(0, 3):
        if abs(step * 10**d - round(step * 10**d)) < 1e-6:
            return d
    return 2


def money_axis(ax, axis: str = "y", decimals: int | None = None) -> None:
    """Tick labels as $0, $200k, $1.5M — scale chosen from the axis range."""
    a = ax.yaxis if axis == "y" else ax.xaxis

    def f(v, _pos):
        lo, hi = (ax.get_ylim() if axis == "y" else ax.get_xlim())
        scale, suf = _scale_for(max(abs(lo), abs(hi)))
        ticks = a.get_majorticklocs()
        step = abs(ticks[1] - ticks[0]) / scale if len(ticks) > 1 else 1
        d = _auto_dec(step) if decimals is None else decimals
        if abs(v) < 1e-9:
            return "$0"
        return f"{'-' if v < 0 else ''}${abs(v) / scale:,.{d}f}{suf}"

    a.set_major_formatter(FuncFormatter(f))


def percent_axis(ax, axis: str = "y", decimals: int = 0, of: float = 1.0) -> None:
    """Tick labels as 64% (values are ratios by default; of=100 if values are already percent)."""
    a = ax.yaxis if axis == "y" else ax.xaxis
    a.set_major_formatter(FuncFormatter(lambda v, _p: f"{v / of * 100:.{decimals}f}%"))


def count_axis(ax, axis: str = "y") -> None:
    a = ax.yaxis if axis == "y" else ax.xaxis
    a.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:,.0f}"))


def value_axis(ax, unit: str, axis: str = "y") -> None:
    """Pick the tick formatter matching a metrics unit."""
    if unit in ("usd", "usd_per_month"):
        money_axis(ax, axis)
    elif unit == "ratio":
        percent_axis(ax, axis)
    elif unit == "pct":
        percent_axis(ax, axis, of=100)
    else:
        count_axis(ax, axis)


def annotate_event(ax, x, label: str, *, y: float = 0.98, at: str = "start",
                   color: str = INK, ha: str = "left") -> None:
    """Vertical dashed marker at month x ('2026-05' or index). at='start' puts it on the boundary
    before that month's bar, at='center' through the bar. y = text height in axes fraction."""
    xp = _xpos(ax, x) - (0.5 if at == "start" else 0.0)
    ax.axvline(xp, color=color, lw=1, ls=(0, (3, 3)), zorder=3)
    tr = blended_transform_factory(ax.transData, ax.transAxes)
    dx = 0.12 if ha == "left" else -0.12
    ax.text(xp + dx, y, label, transform=tr, ha=ha, va="top", fontsize=10, color=color, zorder=4,
            bbox=dict(boxstyle="square,pad=0.15", fc=PAPER, ec="none"))


def label_last(ax, x, y, text: str, *, kind: str = "line", color: str = INK,
               weight: str = "semibold", fontsize: float = 11) -> None:
    """Direct label for the last value: right of the point (line) or above the bar (bar)."""
    xp = _xpos(ax, x)
    if kind == "bar":
        ax.annotate(text, (xp, y), xytext=(0, 4), textcoords="offset points", ha="center",
                    va="bottom", color=color, fontsize=fontsize, fontweight=weight,
                    annotation_clip=False)
    else:
        ax.annotate(text, (xp, y), xytext=(6, 0), textcoords="offset points", ha="left",
                    va="center", color=color, fontsize=fontsize, fontweight=weight,
                    annotation_clip=False)


def _spread(ys, min_gap):
    """Nudge sorted label y positions apart by at least min_gap (keeps order)."""
    order = np.argsort(ys)
    out = np.array(ys, dtype=float)
    for j in range(1, len(order)):
        a, b = order[j - 1], order[j]
        if out[b] - out[a] < min_gap:
            out[b] = out[a] + min_gap
    return out


# ---------------------------------------------------------------- chart builders


def bar_monthly(months, values, *, size="main", unit: str = "usd", unit_text: str | None = None,
                highlight_from=None, color: str = INK, label_last_value: bool = True,
                every: int = 3, width: float = 0.68):
    """Monthly bars. highlight_from='2026-05': earlier bars ink-40, from then on accent."""
    fig, ax = new_fig(size)
    v = _arr(values)
    keys = [_month_key(m) for m in months]
    if highlight_from is not None:
        h = _month_key(highlight_from)
        colors = [ACCENT if k >= h else INK_40 for k in keys]
    else:
        colors = [color] * len(keys)
    ax.bar(np.arange(len(v)), v, color=colors, width=width, zorder=2)
    set_months(ax, keys, every)
    value_axis(ax, unit)
    ax.set_ylim(bottom=min(0, np.nanmin(v) * 1.1), top=np.nanmax(v) * HEADROOM)
    if unit_text:
        unit_label(ax, unit_text)
    if label_last_value:
        i = int(np.where(~np.isnan(v))[0][-1])
        label_last(ax, i, v[i], fmt_value(v[i], unit), kind="bar", color=colors[i])
    return fig, ax


def stacked_bar_monthly(months, layers: dict, *, size="main", unit: str = "usd",
                        unit_text: str | None = None, accent: str | None = None,
                        colors: dict | None = None, direct_labels: bool = True,
                        label_total: bool = True, net_marker: bool = False,
                        every: int = 3, width: float = 0.68):
    """Stacked monthly bars, layers bottom->top {name: values}. Negative values stack below zero
    (e.g. churn in a driver decomposition). accent=name colors that layer pine, others ink tints.
    direct_labels: layer names right of the last bar; label_total: total above the last bar;
    net_marker: ink tick at the net value of each month (for +/- decompositions)."""
    fig, ax = new_fig(size)
    names = list(layers)
    data = {k: np.nan_to_num(_arr(v)) for k, v in layers.items()}
    keys = [_month_key(m) for m in months]
    x = np.arange(len(keys))
    tints = iter(TINTS)
    col = {}
    for k in names:
        if colors and k in colors:
            col[k] = colors[k]
        elif k == accent:
            col[k] = ACCENT
        else:
            col[k] = next(tints, INK_15)
    pos_base = np.zeros(len(x))
    neg_base = np.zeros(len(x))
    mids = {}
    for k in names:
        v = data[k]
        p, n = np.where(v > 0, v, 0), np.where(v < 0, v, 0)
        ax.bar(x, p, bottom=pos_base, color=col[k], width=width, zorder=2, linewidth=0)
        ax.bar(x, n, bottom=neg_base, color=col[k], width=width, zorder=2, linewidth=0)
        last = v[-1]
        mids[k] = (pos_base[-1] + last / 2) if last >= 0 else (neg_base[-1] + last / 2)
        pos_base += p
        neg_base += n
    set_months(ax, keys, every)
    value_axis(ax, unit)
    lo = min(0.0, neg_base.min() * 1.1)
    hi = max(pos_base.max() * HEADROOM, 1e-9)
    ax.set_ylim(lo, hi)
    if net_marker:
        net = pos_base + neg_base
        ax.scatter(x, net, marker="_", s=width * 260, color=INK, linewidths=2, zorder=3)
    if neg_base.min() < 0:
        ax.axhline(0, color=INK_40, lw=0.8, zorder=2)
    if unit_text:
        unit_label(ax, unit_text)
    if label_total:
        label_last(ax, x[-1], pos_base[-1], fmt_value(pos_base[-1] + (neg_base[-1] if net_marker else 0), unit),
                   kind="bar")
    if direct_labels:
        shown = [k for k in names if abs(data[k][-1]) > 0]
        ys = _spread([mids[k] for k in shown], (hi - lo) * 0.075)
        for k, yv in zip(shown, ys):
            ax.annotate(k, (x[-1] + width / 2, yv), xytext=(6, 0), textcoords="offset points",
                        ha="left", va="center", fontsize=10,
                        color=INK_60 if col[k] in (INK_15, INK_05) else col[k],
                        annotation_clip=False)
    else:
        ax.legend(names, loc="upper left", ncols=len(names), handlelength=1, handleheight=1)
    return fig, ax


def line_monthly(months, lines: dict, *, size="main", unit: str = "usd",
                 unit_text: str | None = None, accent: str | None = None,
                 label_last_value: bool = True, name_in_label: bool | None = None,
                 ylim=None, every: int = 3, markers: bool = False):
    """Monthly lines {name: values}. accent=name draws that line pine and heavier; others ink tints.
    Last non-NaN value labelled at the line end ('name value' when several lines)."""
    fig, ax = new_fig(size)
    keys = [_month_key(m) for m in months]
    x = np.arange(len(keys))
    tints = iter([INK, INK_60, INK_40])
    multi = len(lines) > 1 if name_in_label is None else name_in_label
    ends = []
    for k, vals in lines.items():
        v = _arr(vals)
        c = ACCENT if k == accent else next(tints, INK_40)
        lw = 2.5 if k == accent or len(lines) == 1 else 1.75
        ax.plot(x, v, color=c, lw=lw, zorder=3, marker="o" if markers else None, ms=3.5)
        ok = np.where(~np.isnan(v))[0]
        if len(ok):
            i = int(ok[-1])
            ends.append((k, i, v[i], c))
    set_months(ax, keys, every)
    value_axis(ax, unit)
    if ylim is not None:
        ax.set_ylim(*ylim)
    if label_last_value and ends:
        lo, hi = ax.get_ylim()
        ys = _spread([e[2] for e in ends], (hi - lo) * 0.07)
        for (k, i, val, c), yv in zip(ends, ys):
            txt = f"{k} {fmt_value(val, unit)}" if multi else fmt_value(val, unit)
            ax.annotate(txt, (i, yv), xytext=(6, 0), textcoords="offset points", ha="left",
                        va="center", color=c, fontsize=11, fontweight="semibold",
                        annotation_clip=False)
    if unit_text:
        unit_label(ax, unit_text)
    return fig, ax
