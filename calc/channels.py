"""Channels / CAC (README › Definitions: CAC/payback; data-quality items on attribution).

CAC = (bank channel spend + partner commissions) / new paying by Stripe metadata channel, May-Oct 26.
Never platform attribution for CAC. No bank spend line -> CAC = NA ("no direct spend").
Payback is NOT computed here (needs GM; computed by the orchestrator in derived.py): payback = CAC / (ARPA_new x GM).

Writes: calc/series/channels_mapping.csv, channels_funnel_monthly.csv,
        channels_attribution_monthly.csv, channels_spend_monthly.csv, channels_cac.csv
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from load import MAY26, OCT26, SERIES, customer_month_mrr, load

WIN = (MAY26, OCT26)                      # CAC window, first paying month inclusive
ATTR = (pd.Timestamp("2026-06-01"), pd.Timestamp("2026-10-31"))   # Meta/Google attribution window
TT_SPIKE = (pd.Timestamp("2026-03-09"), pd.Timestamp("2026-03-13"))

# ---------------------------------------------------------------- vocab mapping
# channel key | Stripe metadata.acquisition_channel | signups_daily.utm_source | ad_platforms platform | bank category
MAPPING = pd.DataFrame([
    ("meta",      "Meta ads",            "meta_ads",          "meta",   "Marketing - Paid social"),
    ("google",    "Google ads",          "google_ads",        "google", "Marketing - Paid search"),
    ("app_store", "Shopify App Store",   "shopify_app_store", None,     "Marketing - App Store ads"),
    ("content",   "Content / SEO",       "organic_search",    None,     "Marketing - Content"),
    ("partners",  "Partners & agencies", "partner_referral",  None,
     "Marketing - Partner commissions (20% of first-year MRR)"),
    ("outbound",  "Outbound",            None,                None,     "Marketing - Outbound tools"),
    ("tiktok",    "TikTok",              "tiktok_organic",    None,     None),
    ("direct",    None,                  "direct",            None,     None),
], columns=["channel", "stripe_channel", "utm_source", "ad_platform", "bank_category"])
MAPPING = MAPPING.astype(object).where(MAPPING.notna(), None)
MAPPING["mapping_note"] = [
    "exact", "exact", "no platform data file; bank line = Shopify App Store ads",
    "organic_search UTM assumed = Content/SEO (SEO content drives organic search)",
    "bank = PartnerStack commissions, 20% of first-year MRR of partner-sourced customers",
    "no UTM source for outbound (sales-sourced, not web signup)",
    "organic UTM; no bank spend line -> no direct spend",
    "web UTM only; no Stripe channel value and no spend",
]
SALES_PAYROLL = "Payroll - Sales"
SRC = {"sig": "signups_daily.csv", "ad": "ad_platforms_daily.csv", "bank": "bank_transactions.csv",
       "stripe": "stripe_invoices.csv (via load.customer_month_mrr), stripe_customers.csv"}


def _m(mapcol, val, outcol):
    hit = MAPPING[MAPPING[mapcol] == val]
    return hit[outcol].iloc[0] if len(hit) else None


# ---------------------------------------------------------------- building blocks
def funnel_monthly() -> pd.DataFrame:
    s = load("signups_daily").assign(month=lambda d: d["date"].dt.to_period("M"))
    f = s.groupby(["month", "utm_source"], as_index=False)[["signups", "trials_started"]].sum()
    f["trial_rate"] = np.where(f["signups"] > 0, f["trials_started"] / f["signups"], np.nan)
    f["channel"] = f["utm_source"].map(dict(zip(MAPPING["utm_source"], MAPPING["channel"])))
    return f


def attribution_monthly() -> pd.DataFrame:
    a = load("ad_platforms_daily").assign(month=lambda d: d["date"].dt.to_period("M"))
    ap = a.groupby(["month", "platform"], as_index=False).agg(
        platform_reported_trials=("platform_reported_trials", "sum"), platform_spend=("spend_usd", "sum"),
        clicks=("clicks", "sum"), impressions=("impressions", "sum"))
    s = load("signups_daily").assign(month=lambda d: d["date"].dt.to_period("M"))
    utm = {"meta": "meta_ads", "google": "google_ads"}
    internal = (s[s["utm_source"].isin(utm.values())]
                .groupby(["month", "utm_source"], as_index=False)[["signups", "trials_started"]].sum())
    internal["platform"] = internal["utm_source"].map({v: k for k, v in utm.items()})
    out = ap.merge(internal.rename(columns={"trials_started": "internal_trials", "signups": "internal_signups"}),
                   on=["month", "platform"], how="left")
    out["platform_to_internal_ratio"] = out["platform_reported_trials"] / out["internal_trials"].replace(0, np.nan)
    return out.drop(columns="utm_source")


def bank_marketing_monthly() -> pd.DataFrame:
    b = load("bank_transactions")
    b = b[b["category"].str.startswith("Marketing") | b["category"].eq(SALES_PAYROLL)].copy()
    b["month"] = b["date"].dt.to_period("M")
    b["spend"] = -b["amount_usd"]          # bank outflows are negative
    return b.groupby(["month", "category"], as_index=False)["spend"].sum()


def spend_monthly() -> pd.DataFrame:
    """Platform-reported spend vs bank, by month, Meta and Google."""
    att = attribution_monthly()
    bank = bank_marketing_monthly()
    rows = []
    for plat, cat in [("meta", "Marketing - Paid social"), ("google", "Marketing - Paid search")]:
        p = att[att["platform"] == plat].set_index("month")["platform_spend"]
        k = bank[bank["category"] == cat].set_index("month")["spend"]
        df = pd.DataFrame({"platform_spend": p, "bank_spend": k}).fillna(0.0)
        df["diff_platform_minus_bank"] = df["platform_spend"] - df["bank_spend"]
        df["platform"] = plat
        rows.append(df.reset_index())
    return pd.concat(rows, ignore_index=True)


def new_paying() -> pd.DataFrame:
    """One row per customer: first paying month (first month mrr_post > 0) and ARPA measures."""
    cm = customer_month_mrr()
    p = cm[cm["mrr_post"] > 0].sort_values(["customer", "month"])
    first = p.groupby("customer").first()[["month", "mrr_post", "channel", "segment", "is_brightline"]]
    first = first.rename(columns={"month": "first_paying_month", "mrr_post": "mrr_first_month"})
    second = p.groupby("customer").nth(1).set_index("customer")["mrr_post"]
    last = p.groupby("customer").last()[["month", "mrr_post"]]
    first["mrr_second_month"] = second
    first["last_paying_month"] = last["month"]
    first["mrr_latest"] = last["mrr_post"]                                 # Oct26 if still paying
    oct_ = cm[cm["month"] == OCT26].set_index("customer")["mrr_post"]
    first["mrr_oct26"] = oct_.reindex(first.index).fillna(0.0)
    first["paying_oct26"] = first["mrr_oct26"] > 0
    return first.reset_index()


# ---------------------------------------------------------------- metrics
def run() -> pd.DataFrame:
    rows: list[dict] = []

    def add(id_, metric, value, unit, period, definition, source, filt, formula, caveat="", slide="8"):
        rows.append(dict(id=id_, slide=slide, metric=metric,
                         value=(value if isinstance(value, str) else float(value)),
                         unit=unit, period=period, definition=definition, source_file=source,
                         filter=filt, formula=formula, caveat=caveat))

    MAPPING.to_csv(SERIES / "channels_mapping.csv", index=False)

    # 1. funnel ---------------------------------------------------------------
    f = funnel_monthly()
    f.assign(month=f["month"].astype(str)).to_csv(SERIES / "channels_funnel_monthly.csv", index=False)
    s = load("signups_daily")
    tt = s[s["utm_source"] == "tiktok_organic"]
    spike = tt[tt["date"].between(*TT_SPIKE)]
    mar = tt[tt["date"].dt.to_period("M") == pd.Period("2026-03", "M")]
    tt_other = tt[tt["date"].dt.to_period("M") != pd.Period("2026-03", "M")]
    tt_m = tt_other.groupby(tt_other["date"].dt.to_period("M"))[["signups", "trials_started"]].sum()
    non_tt = s[(s["utm_source"] != "tiktok_organic")]
    sp, st = spike["signups"].sum(), spike["trials_started"].sum()
    flt_tt = "utm_source == tiktok_organic"
    add("ch_tiktok_spike_signups", "TikTok signups, Mar 9-13 2026", sp, "count", "2026-03-09..2026-03-13",
        "Website signups attributed to TikTok (UTM)", SRC["sig"], flt_tt + "; date in Mar 9-13 2026",
        "sum(signups)", "Excluded from deck (cut list): spike did not convert", slide="appendix")
    add("ch_tiktok_spike_trials", "TikTok trials, Mar 9-13 2026", st, "count", "2026-03-09..2026-03-13",
        "Trials started attributed to TikTok (UTM)", SRC["sig"], flt_tt + "; date in Mar 9-13 2026",
        "sum(trials_started)", slide="appendix")
    add("ch_tiktok_spike_trial_rate", "TikTok trial rate, Mar 9-13 2026", st / sp, "ratio", "2026-03-09..2026-03-13",
        "Trials / signups", SRC["sig"], flt_tt + "; date in Mar 9-13 2026", "sum(trials)/sum(signups)",
        slide="appendix")
    add("ch_tiktok_mar_signups", "TikTok signups, Mar 2026", mar["signups"].sum(), "count", "2026-03",
        "Website signups attributed to TikTok (UTM)", SRC["sig"], flt_tt + "; month 2026-03", "sum(signups)",
        slide="appendix")
    add("ch_tiktok_mar_trials", "TikTok trials, Mar 2026", mar["trials_started"].sum(), "count", "2026-03",
        "Trials started attributed to TikTok (UTM)", SRC["sig"], flt_tt + "; month 2026-03",
        "sum(trials_started)", slide="appendix")
    add("ch_tiktok_other_months_avg_signups", "TikTok signups, avg of other months", tt_m["signups"].mean(),
        "count", "2025-01..2026-10 ex 2026-03", "Mean monthly TikTok signups excluding Mar 2026", SRC["sig"],
        flt_tt + "; month != 2026-03", "mean over 21 months of monthly sum(signups)", slide="appendix")
    add("ch_tiktok_other_months_avg_trials", "TikTok trials, avg of other months", tt_m["trials_started"].mean(),
        "count", "2025-01..2026-10 ex 2026-03", "Mean monthly TikTok trials excluding Mar 2026", SRC["sig"],
        flt_tt + "; month != 2026-03", "mean over 21 months of monthly sum(trials_started)", slide="appendix")
    add("ch_trial_rate_tiktok_ex_spike", "TikTok trial rate ex Mar 9-13",
        tt[~tt["date"].between(*TT_SPIKE)]["trials_started"].sum() / tt[~tt["date"].between(*TT_SPIKE)]["signups"].sum(),
        "ratio", "2025-01..2026-10 ex Mar 9-13 2026", "Trials / signups, TikTok UTM", SRC["sig"],
        flt_tt + "; date not in Mar 9-13 2026", "sum(trials)/sum(signups)", slide="appendix")
    add("ch_trial_rate_non_tiktok", "Trial rate, all other sources", non_tt["trials_started"].sum() / non_tt["signups"].sum(),
        "ratio", "2025-01..2026-10", "Trials / signups, all UTM sources except TikTok", SRC["sig"],
        "utm_source != tiktok_organic", "sum(trials)/sum(signups)", slide="appendix")

    # 2. attribution ------------------------------------------------------------
    att = attribution_monthly()
    att.assign(month=att["month"].astype(str)).to_csv(SERIES / "channels_attribution_monthly.csv", index=False)
    a = load("ad_platforms_daily")
    aw = a[a["date"].between(*ATTR)]
    sw = s[s["date"].between(*ATTR)]
    for plat, utm, name in [("meta", "meta_ads", "Meta"), ("google", "google_ads", "Google")]:
        pr = aw[aw["platform"] == plat]["platform_reported_trials"].sum()
        it = sw[sw["utm_source"] == utm]["trials_started"].sum()
        sl = "appendix"
        add(f"ch_{plat}_trials_platform_reported", f"{name} trials, platform-reported", pr, "count",
            "2026-06-01..2026-10-31", f"Trials {name} Ads claims (modeled, fractional per campaign-day)", SRC["ad"],
            f"platform == {plat}; date Jun 1-Oct 31 2026", "sum(platform_reported_trials)",
            "platform-reported; platform attribution window and modeled conversions; never used for CAC", slide=sl)
        add(f"ch_{plat}_trials_internal", f"{name} trials, internal (UTM)", it, "count", "2026-06-01..2026-10-31",
            f"Trials started on site with utm_source={utm}", SRC["sig"], f"utm_source == {utm}; date Jun 1-Oct 31 2026",
            "sum(trials_started)", "last-touch UTM; may miss untagged traffic", slide=sl)
        add(f"ch_{plat}_trials_platform_vs_internal", f"{name} platform-reported / internal trials", pr / it, "x",
            "2026-06-01..2026-10-31", "Overstatement of platform-reported vs internal trials", f"{SRC['ad']}, {SRC['sig']}",
            "date Jun 1-Oct 31 2026", f"ch_{plat}_trials_platform_reported / ch_{plat}_trials_internal",
            "platform-reported numerator", slide=sl)

    # 3. spend platform vs bank -------------------------------------------------
    spm = spend_monthly()
    spm.assign(month=spm["month"].astype(str)).to_csv(SERIES / "channels_spend_monthly.csv", index=False)
    for plat, name in [("meta", "Meta"), ("google", "Google")]:
        d = spm[spm["platform"] == plat]
        for lbl, lo, hi in [("2025", "2025-01", "2025-12"), ("2026ytd", "2026-01", "2026-10")]:
            dd = d[(d["month"] >= pd.Period(lo, "M")) & (d["month"] <= pd.Period(hi, "M"))]
            per = f"{lo}..{hi}"
            add(f"ch_{plat}_spend_bank_{lbl}", f"{name} spend, bank", dd["bank_spend"].sum(), "usd", per,
                f"Bank outflows to {name} ads", SRC["bank"], f"category == {_m('ad_platform', plat, 'bank_category')}",
                "-sum(amount_usd)", slide="appendix")
            add(f"ch_{plat}_spend_platform_reported_{lbl}", f"{name} spend, platform-reported",
                dd["platform_spend"].sum(), "usd", per, f"Spend per {name} Ads export", SRC["ad"],
                f"platform == {plat}", "sum(spend_usd)", "platform-reported", slide="appendix")
        add(f"ch_{plat}_spend_max_abs_monthly_diff", f"{name} spend: max |platform-reported - bank| by month",
            d["diff_platform_minus_bank"].abs().max(), "usd", "2025-01..2026-10",
            "Largest monthly gap between platform-reported spend and bank", f"{SRC['ad']}, {SRC['bank']}",
            f"platform == {plat}", "max_m |sum(spend_usd) - bank spend|", "platform-reported vs bank",
            slide="appendix")

    # 4-6. new paying, trials, CAC, ARPA -----------------------------------------
    np_ = new_paying()
    w = np_[(np_["first_paying_month"] >= WIN[0]) & (np_["first_paying_month"] <= WIN[1])]
    bank = bank_marketing_monthly()
    bw = bank[(bank["month"] >= WIN[0]) & (bank["month"] <= WIN[1])]
    bank_by_cat = bw.groupby("category")["spend"].sum()
    trials_w = s[(s["date"].dt.to_period("M") >= WIN[0]) & (s["date"].dt.to_period("M") <= WIN[1])]
    trials_by_utm = trials_w.groupby("utm_source")["trials_started"].sum()
    cust = load("stripe_customers")
    cust_w = cust[(cust["created"].dt.to_period("M") >= WIN[0]) & (cust["created"].dt.to_period("M") <= WIN[1])]
    per = "2026-05..2026-10"
    flt_new = "first month with mrr_post > 0 in May-Oct 2026"
    # ARPA basis: mrr_latest = Oct26 mrr_post if still paying, else last paying month's mrr_post
    cac_rows = []
    for r in MAPPING.itertuples(index=False):
        g = w[w["channel"] == r.stripe_channel] if r.stripe_channel else w.iloc[0:0]
        created = (int(cust_w["metadata.acquisition_channel"].eq(r.stripe_channel).sum())
                   if r.stripe_channel else np.nan)
        n = len(g)
        spend = bank_by_cat.get(r.bank_category, 0.0) if r.bank_category else np.nan
        trials = trials_by_utm.get(r.utm_source, np.nan) if r.utm_source else np.nan
        cac = spend / n if (r.bank_category and n > 0) else np.nan
        cac_rows.append(dict(
            channel=r.channel, stripe_channel=r.stripe_channel, bank_category=r.bank_category,
            spend=spend, new_paying=n, cac=cac,
            arpa_new=g["mrr_latest"].mean() if n else np.nan,
            arpa_new_first_month=g["mrr_first_month"].mean() if n else np.nan,
            new_still_paying_oct26=int(g["paying_oct26"].sum()),
            stripe_customers_created=created,
            trials=trials, trial_to_paid=(n / trials if (r.stripe_channel and trials and trials > 0) else np.nan),
            cac_note=("no direct spend" if not r.bank_category else
                      "no new paying customers" if n == 0 else
                      "commissions = 20% of first-year MRR, paid on customers acquired up to 12m earlier"
                      if r.channel == "partners" else "")))
    cac = pd.DataFrame(cac_rows)
    # blended
    mkt_total = bw[bw["category"].str.startswith("Marketing")]["spend"].sum()
    sales_pay = bw[bw["category"] == SALES_PAYROLL]["spend"].sum()
    n_all = len(w)
    blended = dict(channel="blended", stripe_channel="all", bank_category="all Marketing - *",
                   spend=mkt_total, new_paying=n_all, cac=mkt_total / n_all,
                   arpa_new=w["mrr_latest"].mean(), arpa_new_first_month=w["mrr_first_month"].mean(),
                   new_still_paying_oct26=int(w["paying_oct26"].sum()),
                   stripe_customers_created=len(cust_w), trials=trials_by_utm.sum(), trial_to_paid=n_all / trials_by_utm.sum(), cac_note="")
    cac = pd.concat([cac, pd.DataFrame([blended])], ignore_index=True)
    cac.to_csv(SERIES / "channels_cac.csv", index=False)

    cav_cac = ("bank spend in window / customers whose first paying month is in window; no lag between spend "
               "and conversion; small n per channel; Stripe metadata channel, never platform attribution")
    arpa_def = ("mean per new customer of mrr_post in Oct 2026 if still paying, else in its last paying month")
    for r in cac.itertuples(index=False):
        ch = r.channel
        lab = r.stripe_channel if ch != "blended" else "all channels"
        if ch == "direct":
            continue
        add(f"ch_new_paying_{ch}", f"New paying customers, {lab}", r.new_paying, "count", per,
            "Customers whose first month with mrr_post > 0 falls in May-Oct 2026, by Stripe metadata channel",
            SRC["stripe"], flt_new + ("" if ch == "blended" else f"; metadata.acquisition_channel == {lab}"),
            "count(customer)", "first paying month from invoice-built series; Brightline not in window")
        if ch == "blended":
            add("ch_cac_blended", "Blended CAC (all marketing spend)", r.cac, "usd", per,
                "All bank Marketing - * spend (paid social, paid search, App Store, content, outbound, partner "
                "commissions) / all new paying", SRC["bank"] + ", " + SRC["stripe"],
                "category startswith 'Marketing'; " + flt_new, "sum(spend) / ch_new_paying_blended", cav_cac)
            add("ch_cac_blended_fully_loaded", "Blended CAC incl. Sales payroll", (mkt_total + sales_pay) / n_all,
                "usd", per, "(All bank marketing spend + bank 'Payroll - Sales') / all new paying",
                SRC["bank"] + ", " + SRC["stripe"], "category startswith 'Marketing' or == 'Payroll - Sales'; " + flt_new,
                "(sum(marketing) + sum(sales payroll)) / ch_new_paying_blended",
                cav_cac + "; sales payroll is base salary only (Gusto), no benefits/commission split")
            add("ch_spend_marketing_total", "Marketing spend, bank", mkt_total, "usd", per,
                "Sum of all bank Marketing - * outflows", SRC["bank"], "category startswith 'Marketing'", "-sum(amount_usd)")
            add("ch_spend_sales_payroll", "Sales payroll, bank", sales_pay, "usd", per,
                "Bank 'Payroll - Sales' outflows", SRC["bank"], "category == 'Payroll - Sales'", "-sum(amount_usd)")
            add("ch_trials_total", "Trials started, all sources", r.trials, "count", per,
                "Trials started, all UTM sources", SRC["sig"], "date May-Oct 2026", "sum(trials_started)")
            add("ch_trial_to_paid_blended", "Trial-to-paid, all channels", r.trial_to_paid, "ratio", per,
                "New paying (Stripe) / trials started (signups_daily), same window", SRC["sig"] + ", " + SRC["stripe"],
                flt_new + "; trials dated May-Oct 2026", "ch_new_paying_blended / ch_trials_total",
                "different systems; no lag (trial in month m converts m+0/m+1); outbound customers have no web trial")
            add("ch_arpa_new_blended", "ARPA of new customers, all channels", r.arpa_new, "usd_per_month", per,
                arpa_def, SRC["stripe"], flt_new, "mean(mrr_latest)", "mrr_post (post-discount); for payback")
            continue
        spend_id = f"ch_spend_{ch}"
        if isinstance(r.bank_category, str):
            add(spend_id, f"Channel spend, {lab} (bank)", r.spend, "usd", per,
                f"Bank outflows in category '{r.bank_category}'", SRC["bank"], f"category == {r.bank_category}",
                "-sum(amount_usd)", r.cac_note)
            add(f"ch_cac_{ch}", f"CAC, {lab}", r.cac if not np.isnan(r.cac) else "NA", "usd", per,
                "Bank channel spend / new paying in that Stripe channel", SRC["bank"] + ", " + SRC["stripe"],
                f"category == {r.bank_category}; " + flt_new + f"; channel == {lab}",
                f"{spend_id} / ch_new_paying_{ch}", "; ".join(x for x in [cav_cac, r.cac_note] if x))
        else:
            add(f"ch_cac_{ch}", f"CAC, {lab}", "NA", "usd", per,
                "Bank channel spend / new paying in that Stripe channel", SRC["bank"] + ", " + SRC["stripe"],
                f"no bank category maps to {lab}", "n/a", "no direct spend (no bank spend line); not 0")
        if r.new_paying:
            add(f"ch_arpa_new_{ch}", f"ARPA of new customers, {lab}", r.arpa_new, "usd_per_month", per,
                arpa_def, SRC["stripe"], flt_new + f"; channel == {lab}", "mean(mrr_latest)",
                f"n={r.new_paying}; mrr_post (post-discount); for payback")
        if not np.isnan(r.trial_to_paid):
            add(f"ch_trial_to_paid_{ch}", f"Trial-to-paid, {lab}", r.trial_to_paid, "ratio", per,
                "New paying by Stripe channel / trials by mapped UTM source, same window",
                SRC["sig"] + ", " + SRC["stripe"], flt_new + f"; utm_source == {_m('channel', ch, 'utm_source')}",
                f"ch_new_paying_{ch} / trials", "vocabularies differ (UTM vs Stripe metadata); no lag; "
                "see channels_mapping.csv" + ("; organic_search assumed = Content/SEO" if ch == "content" else ""))

    # Meta CAC trend: Jan-Apr 2026 vs window
    lo, hi = pd.Period("2026-01", "M"), pd.Period("2026-04", "M")
    m_sp = bank[(bank["month"] >= lo) & (bank["month"] <= hi) & (bank["category"] == "Marketing - Paid social")]["spend"].sum()
    m_n = int(((np_["first_paying_month"] >= lo) & (np_["first_paying_month"] <= hi) & (np_["channel"] == "Meta ads")).sum())
    add("ch_cac_meta_janapr26", "CAC, Meta ads, Jan-Apr 2026", m_sp / m_n, "usd", "2026-01..2026-04",
        "Bank paid social / new paying Meta ads customers, Jan-Apr 2026 (trend reference)",
        SRC["bank"] + ", " + SRC["stripe"], "first paying month Jan-Apr 2026; channel == Meta ads",
        "sum(paid social Jan-Apr) / count(new paying Meta Jan-Apr)", cav_cac + f"; n={m_n}")

    # spend share and volume share of Meta (founder claim check)
    meta = cac[cac["channel"] == "meta"].iloc[0]
    add("ch_meta_share_new_paying", "Meta share of new paying customers", meta["new_paying"] / n_all, "ratio", per,
        "Meta ads new paying / all new paying", SRC["stripe"], flt_new, "ch_new_paying_meta / ch_new_paying_blended")
    add("ch_meta_share_spend", "Meta share of marketing spend", meta["spend"] / mkt_total, "ratio", per,
        "Bank paid social / all bank marketing spend", SRC["bank"], "category startswith 'Marketing'",
        "ch_spend_meta / ch_spend_marketing_total")

    out = pd.DataFrame(rows, columns=["id", "slide", "metric", "value", "unit", "period", "definition",
                                      "source_file", "filter", "formula", "caveat"])
    assert out["id"].is_unique
    return out


if __name__ == "__main__":
    pd.set_option("display.width", 250)
    print(run()[["id", "value", "unit"]].to_string())
    print(pd.read_csv(SERIES / "channels_cac.csv").to_string())
