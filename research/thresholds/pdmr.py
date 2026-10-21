"""Managers' transactions (MAR Article 19): the Listing Act threshold change and filing-lag compliance.

Thresholds (Article 19(8) and (9) MAR; sources in the README):

- Sweden: EUR 5,000 per calendar year until 3 December 2024, EUR 20,000 from 4 December 2024 (the Listing Act,
  Regulation (EU) 2024/2809, entered into force that day; Finansinspektionen kept the default).
- France: EUR 20,000 since 20 November 2017 (the AMF had already used the original Article 19(9) option, so the
  Listing Act changed nothing in France in December 2024), then EUR 50,000 from 27 December 2025 (AMF General
  Regulation, Article 223-23, as amended by the arrêté of 4 December 2025, published on 26 December 2025).

Once the threshold is reached within a calendar year, every subsequent transaction must be notified; the total
is computed by adding all transactions without netting. A person whose yearly total stays below the threshold
need not notify at all. The unit of analysis for the threshold is therefore the *person-issuer-year*: one
notifying person (a manager or a closely associated person), one issuer, one calendar year.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import bunching
from calendars import business_day_lag

SOURCES = {"se_fi_pdmr": ("SE", "Europe/Stockholm"), "fr_amf_pdmr": ("FR", "Europe/Paris")}
LISTING_ACT = pd.Timestamp("2024-12-04")
FR_20K = pd.Timestamp("2017-11-20")
FR_50K = pd.Timestamp("2025-12-27")
CLASSES = [0, 5_000, 20_000, 50_000, np.inf]
CLASS_LABELS = ["< EUR 5k", "EUR 5k-20k", "EUR 20k-50k", ">= EUR 50k"]
REFERENCE_CLASS = ">= EUR 50k"
ROLE_GROUPS = {
    "ceo": "CEO/CFO",
    "cfo": "CEO/CFO",
    "chair": "Chair/board",
    "board_member": "Chair/board",
    "executive": "Other executive",
    "other": "Other",
    "unknown": "Other",
}
MARKET_NATURES = {"buy", "sell"}
SIZE_BUCKETS = [0, 5_000, 20_000, 100_000, 1_000_000, np.inf]
SIZE_LABELS = ["< EUR 5k", "EUR 5k-20k", "EUR 20k-100k", "EUR 100k-1m", ">= EUR 1m"]
DEADLINE = 3  # business days, Article 19(1) and (3)
# BDIF re-stamped about 4,000 French filings first published from February to November 2021 with the
# publication date 8 December 2021 (and some more in January 2022); the original date survives only in the
# raw listing. French publication dates are therefore used from February 2022 on.
RELIABLE_PUBLICATION_FROM = {"FR": pd.Timestamp("2022-02-01")}

LINES_SQL = """
SELECT source, record_id, published_at, transaction_date, issuer_name, issuer_lei, isin, pdmr_id, pdmr_role,
       notifier_id, closely_associated, is_amendment, nature, instrument_type, venue, amount_eur
FROM insider_transactions
WHERE source IN ('se_fi_pdmr', 'fr_amf_pdmr')
"""


def load_lines(lakes: list[Path], until: str | None = None) -> pd.DataFrame:
    """Transaction lines of the Swedish and French registers with local publication dates and helper columns."""
    from disclosure_atlas.lake import Lake

    frames = []
    for lake in lakes:
        con = Lake(lake).connect()
        if "insider_transactions" in set(con.sql("SHOW TABLES").df()["name"]):
            frames.append(con.sql(LINES_SQL).df())
    lines = pd.concat(frames, ignore_index=True).drop_duplicates("record_id")
    lines["country"] = lines["source"].map(lambda s: SOURCES[s][0])
    published = pd.Series(pd.NaT, index=lines.index, dtype="datetime64[ns]")
    for source, (_, tz) in SOURCES.items():
        mask = lines["source"] == source
        published[mask] = lines.loc[mask, "published_at"].dt.tz_convert(tz).dt.tz_localize(None)
    lines["published_local"] = published
    lines["published_date"] = lines["published_local"].dt.normalize()
    # Typing errors in the transaction date (e.g. year 3202) become missing.
    trade = pd.Series(lines["transaction_date"], dtype="object")
    valid = trade.map(lambda d: d is not None and not pd.isna(d) and 2000 <= d.year <= 2100)
    lines["transaction_date"] = pd.to_datetime(trade.where(valid))
    if until:
        lines = lines[lines["published_date"] <= pd.Timestamp(until)]
    start = lines["country"].map(RELIABLE_PUBLICATION_FROM)
    lines["reliable_publication"] = start.isna() | (lines["published_date"] >= start)
    lines["issuer"] = lines["issuer_lei"].fillna(lines["issuer_name"].str.upper().str.strip())
    lines["role_group"] = lines["pdmr_role"].map(ROLE_GROUPS).fillna("Other")
    lines["market_trade"] = lines["nature"].isin(MARKET_NATURES)
    lines["gross_eur"] = lines["amount_eur"].abs()
    return lines.reset_index(drop=True)


# ---------------------------------------------------------------------------------------------------------------
# Listing Act: volume and composition
# ---------------------------------------------------------------------------------------------------------------


def notifications(lines: pd.DataFrame) -> pd.DataFrame:
    """One row per notification: notifier, issuer and publication instant."""
    return lines.groupby(["country", "notifier_id", "issuer", "published_local"], as_index=False).agg(
        published_date=("published_date", "first"),
        lines=("record_id", "size"),
        reliable_publication=("reliable_publication", "all"),
        gross_eur=("gross_eur", lambda s: s.sum(min_count=1)),
        closely_associated=("closely_associated", "max"),
        market_trade=("market_trade", "all"),
        role_group=("role_group", "first"),
        is_amendment=("is_amendment", "max"),
    )


def monthly_counts(notes: pd.DataFrame, last_month: str) -> pd.DataFrame:
    notes = notes[notes["reliable_publication"]]
    month = notes["published_date"].dt.to_period("M")
    counts = notes[month <= pd.Period(last_month)].groupby(["country", month.rename("month")]).size()
    return counts.rename("notifications").reset_index()


def interrupted_time_series(monthly: pd.DataFrame, country: str, start: str) -> pd.DataFrame:
    """Poisson regression of monthly notifications on calendar-month dummies, a linear trend and regime dummies.

    December 2024 straddles the change and is dropped. Standard errors are Newey-West (HAC, 6 lags).
    """
    import statsmodels.api as sm

    m = monthly[(monthly["country"] == country) & (monthly["month"] >= pd.Period(start))].copy()
    m = m[m["month"] != pd.Period("2024-12")]
    ts = m["month"].dt.to_timestamp()
    x = pd.get_dummies(ts.dt.month, prefix="m", drop_first=True, dtype=float)
    x["trend"] = np.arange(len(m)) / 12
    if country == "FR":  # no change in France at the Listing Act: 2025 is a placebo
        x["placebo_2025"] = (ts >= pd.Timestamp("2025-01-01")).astype(float)
        x["post_50k"] = (ts >= pd.Timestamp("2026-01-01")).astype(float)
    else:
        x["post_20k"] = (ts >= pd.Timestamp("2025-01-01")).astype(float)
    x = sm.add_constant(x.set_index(m.index))
    fit = sm.GLM(m["notifications"].to_numpy(), x, family=sm.families.Poisson()).fit(
        cov_type="HAC", cov_kwds={"maxlags": 6}
    )
    rows = []
    for term in [c for c in x.columns if c.startswith(("post", "placebo"))]:
        b, se = fit.params[term], fit.bse[term]
        rows.append(
            {
                "country": country,
                "term": term,
                "coef": b,
                "se": se,
                "effect": np.exp(b) - 1,
                "ci_low": np.exp(b - 1.96 * se) - 1,
                "ci_high": np.exp(b + 1.96 * se) - 1,
                "p": fit.pvalues[term],
                "months": len(m),
                "first_month": str(m["month"].min()),
                "last_month": str(m["month"].max()),
            }
        )
    return pd.DataFrame(rows)


def person_years(
    lines: pd.DataFrame, by: str = "notifier_id", last_day: str = "09-30", full_year: bool = False
) -> pd.DataFrame:
    """Gross amounts per person, issuer and year (transactions from 1 January to ``last_day``).

    ``by='notifier_id'`` treats managers and closely associated persons separately; ``by='pdmr_id'`` adds the
    transactions of closely associated persons to their manager's total. Amendments replace the original
    record in gold, so every transaction counts once.
    """
    t = lines[lines["transaction_date"].notna()].copy()
    t["year"] = t["transaction_date"].dt.year
    if not full_year:
        cutoff = pd.to_datetime(t["year"].astype(str) + "-" + last_day)
        t = t[t["transaction_date"] <= cutoff]
    t["role_rank"] = t["role_group"].map({"CEO/CFO": 0, "Chair/board": 1, "Other executive": 2, "Other": 3})
    units = t.groupby(["country", "issuer", by, "year"], as_index=False).agg(
        total_eur=("gross_eur", lambda s: s.sum(min_count=1)),
        lines=("record_id", "size"),
        role_rank=("role_rank", "min"),
        closely_associated=("closely_associated", "all"),
        market_only=("market_trade", "all"),
        missing_amount=("gross_eur", lambda s: s.isna().any()),
    )
    units["total_eur"] = units["total_eur"].fillna(0.0)
    units["role_group"] = units["role_rank"].map({0: "CEO/CFO", 1: "Chair/board", 2: "Other executive", 3: "Other"})
    units["size_class"] = pd.cut(units["total_eur"], CLASSES, labels=CLASS_LABELS, right=False)
    return units


def class_counts(units: pd.DataFrame, years: list[int]) -> np.ndarray:
    """Counts (issuers, classes, years) of person-issuer-years, for the issuer-cluster bootstrap."""
    u = units[units["year"].isin(years)]
    ids = pd.factorize(u["issuer"])[0]
    out = np.zeros((ids.max() + 1, len(CLASS_LABELS), len(years)))
    np.add.at(out, (ids, u["size_class"].cat.codes.to_numpy(), np.searchsorted(years, u["year"])), 1)
    return out


def did_estimator(years: list[int], post: dict[str, list[int]]) -> callable:
    """Difference in log counts: (post - pre) for each class minus the same for the reference class.

    ``post`` maps a regime name to its years; the pre years are all the others. Returns, per class and regime,
    exp(DiD) - 1: the proportional change in that class relative to the >= EUR 50k class.
    """
    ref = CLASS_LABELS.index(REFERENCE_CLASS)
    pre = [i for i, y in enumerate(years) if not any(y in v for v in post.values())]

    def estimator(counts: np.ndarray) -> dict[str, float]:
        logs = np.log(np.maximum(counts, 0.5))
        base = logs[:, pre].mean(axis=1)
        out = {}
        for regime, regime_years in post.items():
            idx = [years.index(y) for y in regime_years]
            change = logs[:, idx].mean(axis=1) - base
            for c, label in enumerate(CLASS_LABELS):
                if c != ref:
                    out[f"{regime}|{label}"] = float(np.exp(change[c] - change[ref]) - 1)
        for i, y in enumerate(years):  # event-study path relative to the pre-period mean
            change = logs[:, i] - base
            for c, label in enumerate(CLASS_LABELS):
                if c != ref:
                    out[f"year {y}|{label}"] = float(change[c] - change[ref])
        return out

    return estimator


def listing_act_did(
    units: pd.DataFrame, country: str, years: list[int], post: dict[str, list[int]], reps: int, subgroup: str = "All"
) -> pd.DataFrame:
    counts = class_counts(units[units["country"] == country], years)
    estimator = did_estimator(years, post)
    point = estimator(counts.sum(axis=0))
    draws = bunching.cluster_bootstrap(counts, estimator, reps=reps, seed=11)
    rows = []
    for key, value in point.items():
        term, label = key.split("|")
        rows.append(
            {
                "country": country,
                "subgroup": subgroup,
                "term": term,
                "class": label,
                **bunching.summarize(value, draws[key]),
            }
        )
    table = pd.DataFrame(rows)
    totals = counts.sum(axis=0)
    for c, label in enumerate(CLASS_LABELS):
        for i, y in enumerate(years):
            table.loc[len(table)] = {
                "country": country,
                "subgroup": subgroup,
                "term": f"count {y}",
                "class": label,
                "estimate": totals[c, i],
            }
    return table


def placebo_dates(
    units: pd.DataFrame, country: str, pre_years: list[int], length: int = 2, min_pre: int = 2
) -> pd.DataFrame:
    """The same DiD with fake change dates inside the pre-period (years before the real change only).

    For each fake first year F, the "post" years are F to F + length - 1 and the "pre" years all earlier ones.
    The spread of these placebo estimates shows how large a two-year swing between size classes is without
    any rule change: the issuer bootstrap does not capture such year-level shocks.
    """
    rows = []
    for first in pre_years[min_pre:]:
        post = [y for y in range(first, first + length) if y in pre_years]
        if len(post) < length:
            continue
        years = [y for y in pre_years if y < first] + post
        point = did_estimator(years, {"placebo": post})(
            class_counts(units[units["country"] == country], years).sum(axis=0)
        )
        rows += [
            {"country": country, "fake_first_year": first, "class": key.split("|")[1], "estimate": value}
            for key, value in point.items()
            if key.startswith("placebo|")
        ]
    return pd.DataFrame(rows)


def threshold_bunching(
    units: pd.DataFrame,
    country: str,
    pre: list[int],
    post: list[int],
    threshold: float,
    reps: int,
    width: float = 2_500,
    upper: float = 100_000,
    labels: tuple[str, str] = ("Before", "After"),
) -> pd.DataFrame:
    """Difference in bunching over time around a yearly threshold.

    Distribution of person-issuer-year totals in bins of ``width`` from 0 to ``upper``. If people who would
    total just above the new threshold now stay below it, the share of [threshold, threshold + 2 bins) among all
    totals in [threshold, upper) falls after the change ("missing mass above"). Totals below the threshold after
    the change are voluntary notifications.
    """
    u = units[(units["country"] == country) & units["year"].isin(pre + post) & (units["total_eur"] < upper)]
    edges = np.arange(0, upper + width, width)
    u = u.assign(bin=np.digitize(u["total_eur"], edges) - 1, post=u["year"].isin(post))
    ids = pd.factorize(u["issuer"])[0]
    counts = np.zeros((ids.max() + 1, 2, len(edges) - 1))
    np.add.at(counts, (ids, u["post"].astype(int).to_numpy(), u["bin"].to_numpy()), 1)
    first = int(threshold // width)

    def estimator(total: np.ndarray) -> dict[str, float]:
        above = total[:, first:]
        share = above[:, :2].sum(axis=1) / np.maximum(above.sum(axis=1), 1)
        below = total[:, first - 2 : first].sum(axis=1) / np.maximum(total.sum(axis=1), 1)
        return {
            "share_just_above_pre": share[0],
            "share_just_above_post": share[1],
            "difference_just_above": share[1] - share[0],
            "share_just_below_pre": below[0],
            "share_just_below_post": below[1],
            "difference_just_below": below[1] - below[0],
        }

    point = estimator(counts.sum(axis=0))
    draws = bunching.cluster_bootstrap(counts, estimator, reps=reps, seed=13)
    rows = [
        {"country": country, "threshold": threshold, "statistic": key, **bunching.summarize(v, draws[key])}
        for key, v in point.items()
    ]
    density = pd.DataFrame(
        {
            "country": country,
            "threshold": threshold,
            "width": width,
            "bin_low": edges[:-1],
            "pre": counts.sum(axis=0)[0],
            "post": counts.sum(axis=0)[1],
            "pre_label": labels[0],
            "post_label": labels[1],
        }
    )
    return pd.DataFrame(rows), density


def composition(notes: pd.DataFrame, periods: dict[str, tuple[str, str]]) -> pd.DataFrame:
    """Shares of notifications by type in each period (published date, inclusive)."""
    rows = []
    for country, g in notes.groupby("country"):
        for label, (start, end) in periods.items():
            p = g[g["published_date"].between(pd.Timestamp(start), pd.Timestamp(end))]
            if p.empty:
                continue
            months = (pd.Timestamp(end).to_period("M") - pd.Timestamp(start).to_period("M")).n + 1
            gross = p["gross_eur"]
            rows.append(
                {
                    "country": country,
                    "period": label,
                    "notifications": len(p),
                    "per_month": len(p) / months,
                    "share_closely_associated": p["closely_associated"].mean(),
                    "share_market_trades": p["market_trade"].mean(),
                    "share_ceo_cfo": (p["role_group"] == "CEO/CFO").mean(),
                    "share_below_5k": (gross < 5_000).mean(),
                    "share_below_20k": (gross < 20_000).mean(),
                    "median_eur": gross.median(),
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------------------------
# Filing lags
# ---------------------------------------------------------------------------------------------------------------


def threshold_in_force(country: pd.Series, when: pd.Series) -> pd.Series:
    """Yearly notification threshold (EUR) on a date, per country (see the module docstring)."""
    out = pd.Series(5_000.0, index=when.index)
    out[when >= LISTING_ACT] = 20_000.0
    out[(country == "FR") & (when >= FR_20K)] = 20_000.0
    out[(country == "FR") & (when >= FR_50K)] = 50_000.0
    return out


def add_lags(lines: pd.DataFrame) -> pd.DataFrame:
    """Business-day lag from the transaction to publication on the authority's register, per line."""
    out = lines.copy()
    out["lag_bd"] = np.nan
    for country in out["country"].unique():
        mask = out["country"] == country
        out.loc[mask, "lag_bd"] = business_day_lag(
            out.loc[mask, "transaction_date"], out.loc[mask, "published_date"], country
        )
    out["lag_days"] = (out["published_date"] - out["transaction_date"]).dt.days
    out["negative_lag"] = out["lag_days"] < 0
    out["late"] = out["lag_bd"] > DEADLINE
    out["very_late"] = out["lag_bd"] > 20
    out["implausible"] = out["lag_days"] > 365
    out["size_bucket"] = pd.cut(out["gross_eur"], SIZE_BUCKETS, labels=SIZE_LABELS, right=False)
    out["segment"] = issuer_segment(out)
    # Mandatory: the person's yearly total in this issuer reaches the threshold in force. Below it, notifying
    # is voluntary and no deadline applies, so compliance is measured on mandatory notifications.
    year = out["transaction_date"].dt.year
    yearly = out.groupby(["country", "issuer", "notifier_id", year])["gross_eur"].transform("sum")
    out["mandatory"] = yearly >= threshold_in_force(out["country"], out["transaction_date"])
    # The deadline runs from the transaction that triggers the obligation. When earlier, smaller transactions of
    # the year are notified together with the one that reaches the threshold, the line-level lag overstates
    # lateness; the notification-level lag, measured from the latest transaction in the notification, does not.
    key = ["country", "notifier_id", "issuer", "published_local"]
    out["lag_bd_notification"] = out.groupby(key)["lag_bd"].transform("min")
    out["late_notification"] = out["lag_bd_notification"] > DEADLINE
    return out


def segment(venue: str | None) -> str | None:
    """Market segment of a trading venue: main regulated market or growth market / MTF (None otherwise)."""
    v = (venue or "").upper()
    if any(
        w in v
        for w in (
            "FIRST NORTH",
            "GROWTH",
            "ALTERNEXT",
            "SPOTLIGHT",
            "AKTIETORGET",
            "NORDIC SME",
            "NORDIC MTF",
            "EURONEXT ACCESS",
            "MARCHE LIBRE",
            "MARCHÉ LIBRE",
        )
    ):
        return "Growth market / MTF"
    if any(w in v for w in ("NASDAQ STOCKHOLM", "OMX NORDIC EXCHANGE STOCKHOLM", "EURONEXT PARIS")) or v in {
        "XSTO",
        "XPAR",
    }:
        return "Main market"
    return None


def issuer_segment(lines: pd.DataFrame) -> pd.Series:
    """Each issuer's most frequent segment over its on-venue lines: a proxy for issuer size known to all."""
    seg = lines["venue"].map(segment)
    modal = seg.groupby(lines["issuer"]).agg(lambda s: s.mode().iat[0] if s.notna().any() else "Unknown")
    return lines["issuer"].map(modal).fillna("Unknown")


def lag_sample(lines: pd.DataFrame) -> pd.DataFrame:
    """Lines usable for the compliance analysis: original notifications with a valid, plausible lag."""
    with_lag = add_lags(lines)
    return with_lag[
        with_lag["reliable_publication"]
        & ~with_lag["is_amendment"]
        & with_lag["lag_bd"].notna()
        & ~with_lag["implausible"]
    ]


def lag_tables(sample: pd.DataFrame) -> dict[str, pd.DataFrame]:
    def summary(g: pd.DataFrame) -> pd.Series:
        n = len(g)
        late = g["late"].mean()
        se = np.sqrt(late * (1 - late) / n) if n else np.nan
        return pd.Series(
            {
                "lines": n,
                "share_late": late,
                "se": se,
                "share_late_notification_level": g["late_notification"].mean(),
                "share_late_over_20bd": g["very_late"].mean(),
                "median_lag_bd": g["lag_bd"].median(),
                "p90_lag_bd": g["lag_bd"].quantile(0.9),
            }
        )

    sample = sample.assign(year=sample["published_date"].dt.year)
    mandatory = sample[sample["mandatory"]]
    tables = {"by_year": mandatory.groupby(["country", "year"]).apply(summary, include_groups=False).reset_index()}
    for split in ("role_group", "size_bucket", "closely_associated", "market_trade", "segment"):
        tables[f"by_{split}"] = (
            mandatory.groupby(["country", split], observed=True)
            .apply(summary, include_groups=False)
            .reset_index()
            .rename(columns={split: "group"})
            .assign(split=split)
        )
    tables["by_mandatory"] = (
        sample.groupby(["country", "mandatory"])
        .apply(summary, include_groups=False)
        .reset_index()
        .rename(columns={"mandatory": "group"})
        .assign(split="mandatory")
    )
    tables["overall"] = (
        sample[sample["mandatory"]].groupby("country").apply(summary, include_groups=False).reset_index()
    )
    cdf = []
    for country, g in mandatory.groupby("country"):
        for k in range(0, 31):
            cdf.append({"country": country, "lag_bd": k, "share_published": (g["lag_bd"] <= k).mean()})
    tables["cdf"] = pd.DataFrame(cdf)
    return tables


def lag_logit(sample: pd.DataFrame) -> pd.DataFrame:
    """Linear probability and logit models of a late publication, standard errors clustered by issuer."""
    import statsmodels.formula.api as smf

    data = (
        sample[sample["mandatory"]]
        .dropna(subset=["size_bucket"])
        .assign(
            year=lambda d: d["published_date"].dt.year.astype(str),
            size=lambda d: d["size_bucket"].astype(str),
            late=lambda d: d["late"].astype(float),
            associated=lambda d: d["closely_associated"].astype(float),
            market=lambda d: d["market_trade"].astype(float),
        )
    )
    formula = (
        "late ~ C(country) + C(role_group, Treatment('CEO/CFO')) + C(size, Treatment('EUR 20k-100k'))"
        " + associated + market + C(segment, Treatment('Main market')) + C(year)"
    )
    groups = pd.factorize(data["issuer"])[0]
    rows = []
    models = {"LPM": smf.ols(formula, data), "Logit (average marginal effects)": smf.logit(formula, data)}
    for name, model in models.items():
        fit = model.fit(cov_type="cluster", cov_kwds={"groups": groups}, **({"disp": 0} if "Logit" in name else {}))
        if name == "LPM":
            params, ses, ps = fit.params, fit.bse, fit.pvalues
        else:
            margeff = fit.get_margeff()
            frame = margeff.summary_frame()
            params, ses, ps = frame["dy/dx"], frame["Std. Err."], frame["Pr(>|z|)"]
        for term in params.index:
            if term.startswith(("Intercept", "C(year)")):
                continue
            rows.append(
                {"model": name, "term": term, "coef": params[term], "se": ses[term], "p": ps[term], "n": int(fit.nobs)}
            )
    return pd.DataFrame(rows)
