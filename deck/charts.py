"""Chart registry for the deck. Templates call {{ chart('name', **kwargs) }}.

Each chart is a function registered with @register('name'). build.py calls it as
    fn(S, **kwargs)
where S is the series store: S.read('revenue_monthly') -> DataFrame from <series dir>/revenue_monthly.csv.
Charts read ONLY through S (calc/series/ in a real build, deck/fixtures/series/ in --demo):
no raw files, no metrics.csv, no hand-typed numbers. Return a matplotlib Figure (build.py turns it
into inline SVG via chartkit.fig_to_svg) or an <svg> string.

Real charts go below the examples. The demo_* charts read fixture series and exist only to test
the machinery; keep them (build --demo uses them).
"""
from __future__ import annotations

import chartkit as ck

CHARTS: dict = {}


def register(name: str):
    def deco(fn):
        if name in CHARTS:
            raise ValueError(f"chart '{name}' registered twice")
        CHARTS[name] = fn
        return fn
    return deco


# ---------------------------------------------------------------- examples (fixtures only)


@register("demo_mrr_stacked")
def demo_mrr_stacked(S, size="main"):
    df = S.read("demo_mrr")
    fig, ax = ck.stacked_bar_monthly(
        df["month"], {"SMB": df["smb"], "Mid-market": df["mid"], "Agency": df["agency"]},
        size=size, unit="usd", unit_text="Normalized MRR, $ per month (fixture)", accent="Agency")
    ck.annotate_event(ax, "2026-05", "New pricing + Autopilot")
    ck.annotate_event(ax, "2025-11", "BFCM coupon", y=0.80, color=ck.INK_60)
    return fig


@register("demo_gm_line")
def demo_gm_line(S, size="half"):
    df = S.read("demo_gm")
    fig, ax = ck.line_monthly(df["month"], {"Gross margin": df["gm"]}, size=size, unit="ratio",
                              unit_text="Gross margin, % of MRR (fixture)", accent="Gross margin",
                              ylim=(0.5, 1.0))
    ck.annotate_event(ax, "2026-06", "Inference spike", y=0.25, color=ck.INK_60)
    return fig


@register("demo_spend_bars")
def demo_spend_bars(S, size="half"):
    df = S.read("demo_spend")
    fig, _ax = ck.bar_monthly(df["month"], df["spend"], size=size, unit="usd",
                              unit_text="Managed ad spend, $ per month (fixture)",
                              highlight_from="2026-05")
    return fig


# ---------------------------------------------------------------- real charts (calc/series/ only)

import numpy as np  # noqa: E402

LAUNCH = "2026-05"


@register("mrr_by_plan")
def mrr_by_plan(S, size="main"):
    df = S.read("revenue_mrr_monthly")
    fig, ax = ck.stacked_bar_monthly(
        df["month"],
        {"Legacy plans": df["mrr_legacy"],
         "New plans, no Autopilot": df["mrr_new_pricing_non_autopilot"],
         "Agency reseller": df["mrr_agency_reseller_plan"],
         "Autopilot plans": df["mrr_autopilot_plans"]},
        size=size, unit="usd", unit_text="Subscription MRR after discounts, $ per month",
        accent="Autopilot plans",
        colors={"Legacy plans": ck.INK, "New plans, no Autopilot": ck.INK_60, "Agency reseller": ck.INK_40})
    ck.annotate_event(ax, LAUNCH, "Autopilot + new pricing", y=0.98)
    return fig


@register("managed_spend")
def managed_spend(S, size="main"):
    df = S.read("retention_usage_monthly")
    fig, _ = ck.bar_monthly(df["month"], df["managed_spend"], size=size, unit="usd",
                            unit_text="Customer ad spend run through Northpine, $ per month",
                            highlight_from=LAUNCH)
    return fig


@register("logo_churn")
def logo_churn(S, size="half"):
    df = S.read("retention_churn_monthly")
    roll = df["logo_churn"].rolling(3).mean()
    fig, ax = ck.line_monthly(df["month"], {"Logo churn, 3-month average": roll}, size=size, unit="ratio",
                              unit_text="Paying customers lost per month, 3-month average",
                              accent="Logo churn, 3-month average", ylim=(0, 0.1))
    ck.percent_axis(ax, decimals=0)
    ck.annotate_event(ax, LAUNCH, "Autopilot", y=0.98)
    return fig


@register("nps_quarterly")
def nps_quarterly(S, size="half"):
    df = S.read("retention_nps_quarterly")
    fig, ax = ck.new_fig(size)
    x = np.arange(len(df))
    colors = [ck.ACCENT if q >= "2026Q2" else ck.INK_40 for q in df["quarter"]]
    ax.bar(x, df["nps"], color=colors, width=0.62, zorder=2)
    for i, (v, n, p) in enumerate(zip(df["nps"], df["n"], df["partial"])):
        ax.annotate(f"{v:.0f}", (i, max(v, 0)), xytext=(0, 3), textcoords="offset points", ha="center",
                    va="bottom", fontsize=10, fontweight="semibold", color=colors[i])
    labels = [f"{q[2:4]} {q[4:]}\nn={n}" + ("*" if p else "") for q, n, p in zip(df["quarter"], df["n"], df["partial"])]
    ax.set_xticks(x, labels, fontsize=9)
    ax.axhline(0, color=ck.INK_40, lw=0.8)
    ax.set_ylim(min(0, df["nps"].min()) * 1.3 - 5, df["nps"].max() * 1.25)
    ck.unit_label(ax, "NPS by quarter (promoters − detractors, points); n = responses")
    return fig


@register("gross_margin")
def gross_margin(S, size="main"):
    df = S.read("derived_gm_monthly")
    fig, ax = ck.line_monthly(df["month"], {"Gross margin": df["gm_total"]}, size=size, unit="ratio",
                              unit_text="Gross margin, % of recurring revenue (subscription + usage tiers)",
                              accent="Gross margin", ylim=(0.5, 1.0))
    ck.annotate_event(ax, LAUNCH, "Autopilot launch", y=0.98)
    ck.annotate_event(ax, "2026-08", "Inference vendor added", y=0.22, color=ck.INK_60)
    return fig


@register("brightline_share")
def brightline_share(S, size="main"):
    df = S.read("revenue_mrr_monthly")
    fig, ax = ck.stacked_bar_monthly(df["month"], {"All other customers": df["mrr_ex_brightline"], "Brightline": df["mrr_brightline"]},
                                     size=size, unit="usd", unit_text="Subscription MRR after discounts, $ per month",
                                     accent="Brightline", colors={"All other customers": ck.INK_40})
    return fig


@register("headcount")
def headcount(S, size="main"):
    df = S.read("burn_team_monthly")
    fig, ax = ck.stacked_bar_monthly(
        df["month"],
        {"Engineering": df["hc_engineering"], "Product": df["hc_product"], "Leadership": df["hc_leadership"],
         "Sales": df["hc_sales"], "Customer success": df["hc_customer_success"]},
        size=size, unit="count", unit_text="Employees at month end, by function", accent="Customer success",
        colors={"Engineering": ck.INK, "Product": ck.INK_60, "Leadership": ck.INK_40, "Sales": ck.INK_15})
    return fig


@register("price_ladder")
def price_ladder(S, size="main"):
    df = S.read("derived_price_ladder")
    fig, ax = ck.new_fig(size)
    y = np.arange(len(df))[::-1]
    for yi, (_, r) in zip(y, df.iterrows()):
        c = ck.ACCENT if r["color"] == "accent" else ck.INK_40
        lo, hi = r["low"], r["high"]
        if hi > lo:
            ax.barh(yi, hi - lo, left=lo, height=0.5, color=c, zorder=2)
            ax.barh(yi, lo, left=0, height=0.5, color=ck.INK_15, zorder=1)
            u = "usd" if lo < 1000 else "usd_k"
            txt = f"{ck.fmt_value(lo, u)}–{ck.fmt_value(hi, u)}"
        else:
            ax.barh(yi, hi, left=0, height=0.5, color=c if r["color"] == "accent" else ck.INK_40, zorder=2)
            txt = (ck.fmt_value(hi, "usd_k") + "+") if r["color"] != "accent" else ck.fmt_value(hi, "usd")
        ax.annotate(txt.replace("$", r"\$"), (hi, yi), xytext=(8, 0), textcoords="offset points", va="center", ha="left",
                    fontsize=12, fontweight="semibold", color=c if r["color"] == "accent" else ck.INK)
    ax.set_yticks(y, df["option"], fontsize=12)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=ck.INK_15, lw=0.8)
    ax.spines["left"].set_visible(False)
    ck.money_axis(ax, axis="x")
    ax.set_xlim(0, df["high"].max() * 1.22)
    ck.unit_label(ax, "Monthly cost to the brand, $ per month")
    return fig
