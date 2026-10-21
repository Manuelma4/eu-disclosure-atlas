"""Static figures for the write-up (matplotlib, PNG), in the style of the WS6 event study."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID, NEUTRAL = "#0b0b0b", "#52514e", "#e4e3df", "#898781"
SURFACE = "#fcfcfb"
COUNTRY_COLORS = {"SE": BLUE, "FR": ORANGE}


def _figure(ncols: int = 1, width: float = 8, height: float = 4.5):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, ncols, figsize=(width, height), dpi=150, squeeze=False)
    fig.patch.set_facecolor(SURFACE)
    for ax in axes[0]:
        ax.set_facecolor(SURFACE)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)
        ax.tick_params(colors=MUTED, labelsize=9)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
    return fig, list(axes[0])


def _title(ax, title: str, ylabel: str = "", xlabel: str = "") -> None:
    ax.set_title(title, loc="left", color=INK, fontsize=11)
    ax.set_ylabel(ylabel, color=MUTED, fontsize=9)
    ax.set_xlabel(xlabel, color=MUTED, fontsize=9)


def _percent(ax, decimals: int = 0, axis: str = "y") -> None:
    import matplotlib.ticker as mticker

    fmt = mticker.PercentFormatter(1.0, decimals=decimals)
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def _label(ax, x, y, text: str, **kwargs) -> None:
    ax.annotate(text, (x, y), xytext=(5, 0), textcoords="offset points", color=INK, fontsize=8.5, va="center", **kwargs)


def _labels(ax, items: list[tuple[float, float, str]], gap: float = 0.06) -> None:
    """Direct labels at line ends, nudged apart vertically (``gap`` as a share of the y range)."""
    low, high = ax.get_ylim()
    step = gap * (high - low)
    placed: list[float] = []
    for x, y, text in sorted(items, key=lambda item: item[1]):
        if placed and y - placed[-1] < step:
            y = placed[-1] + step
        placed.append(y)
        _label(ax, x, y, text)


def _save(fig, out: Path) -> None:
    fig.tight_layout()
    fig.savefig(out, facecolor=SURFACE)
    import matplotlib.pyplot as plt

    plt.close(fig)


# --- short positions --------------------------------------------------------------------------------------------


def reported_values(cross: pd.DataFrame, out: Path) -> None:
    """Histogram of every published value between 0.40% and 1.20% (all six countries)."""
    values = cross["position_pct"].dropna()
    values = values[(values >= 0.4) & (values < 1.2)]
    bins = np.floor(values * 100 + 1e-6).astype(int)
    counts = bins.value_counts().reindex(range(40, 120), fill_value=0)
    fig, (ax,) = _figure()
    colors = [BLUE if b in (49, 50) else NEUTRAL for b in counts.index]
    ax.bar(counts.index / 100 + 0.005, counts.to_numpy(), width=0.008, color=colors, linewidth=0)
    ax.axvline(0.5, color=INK, linewidth=0.8, linestyle="--")
    _label(ax, 0.51, counts.max() * 0.95, "0.5%: public disclosure threshold")
    _title(
        ax,
        "Published net short positions by value, six jurisdictions",
        "Notifications per 0.01 pp bin",
        "Position (% of issued share capital). Below 0.5%: exit notifications that report the value",
    )
    ax.set_xlim(0.4, 1.2)
    _save(fig, out)


def crossing_shape(distance: pd.DataFrame, out: Path) -> None:
    """Share of crossings by distance from the threshold: 0.5% versus the 0.6%-1.5% thresholds."""
    fig, axes = _figure(ncols=2, width=10, height=4.2)
    titles = {"down": "Exits and downward crossings", "up": "Entries and upward crossings"}
    for ax, direction in zip(axes, ("down", "up"), strict=True):
        d = distance[distance["direction"] == direction]
        shares = d.pivot_table(index="k", columns="level", values="n", aggfunc="sum").fillna(0)
        shares = shares / shares.sum()
        at_half = shares[50]
        controls = shares[[c for c in shares.columns if c != 50]].mean(axis=1)
        k = shares.index.to_numpy() / 100
        ax.plot(k, controls.to_numpy(), color=NEUTRAL, linewidth=2, marker="o", markersize=4)
        ax.plot(k, at_half.to_numpy(), color=BLUE, linewidth=2, marker="o", markersize=4)
        _label(ax, k[0] + 0.002, at_half.iloc[0], "0.5% threshold")
        _label(ax, k[0] + 0.002, controls.iloc[0], "0.6%-1.5% thresholds (mean)")
        _percent(ax)
        side = "below" if direction == "down" else "above"
        _title(
            ax,
            titles[direction],
            "Share of crossings landing in the band",
            f"Distance {side} the threshold crossed (pp, 0.01 bins)",
        )
    _save(fig, out)


def bunching_by_level(profiles: pd.DataFrame, estimates: pd.DataFrame, out: Path) -> None:
    """Share of crossings within 0.01 pp of each threshold, with the trend extrapolated from 0.6%-1.5%."""
    fig, axes = _figure(ncols=2, width=10, height=4.2)
    titles = {"down": "Exits and downward crossings", "up": "Entries and upward crossings"}
    for ax, direction in zip(axes, ("down", "up"), strict=True):
        p = profiles[(profiles["jurisdiction"] == "All six") & (profiles["direction"] == direction)]
        control = p[p["level"] > 0.5]
        ax.scatter(control["level"], control["share"], s=np.sqrt(control["n"]) * 1.5, color=NEUTRAL, zorder=3)
        treated = p[p["level"] == 0.5]
        ax.scatter(treated["level"], treated["share"], s=np.sqrt(treated["n"]) * 1.5, color=BLUE, zorder=3)
        coef = np.polyfit(control["level"], control["share"], 1, w=np.sqrt(control["n"]))
        grid = np.linspace(0.5, 1.5, 50)
        ax.plot(grid, np.polyval(coef, grid), color=NEUTRAL, linewidth=1, linestyle="--")
        e = estimates[
            (estimates["jurisdiction"] == "All six")
            & (estimates["direction"] == direction)
            & (estimates["spec"] == "share within 0.01 pp")
        ].iloc[0]
        _label(
            ax,
            0.5,
            float(treated["share"].iloc[0]),
            f"0.5%: {100 * e['estimate']:+.1f} pp vs trend (95% CI {100 * e['ci_low']:+.1f} to "
            f"{100 * e['ci_high']:+.1f})",
        )
        _percent(ax)
        ax.set_xlim(0.45, 1.55)
        _title(
            ax,
            titles[direction],
            "Share within 0.01 pp of the threshold",
            "Threshold crossed (% of share capital); dot area: number of crossings",
        )
    _save(fig, out)


def country_estimates(estimates: pd.DataFrame, out: Path) -> None:
    """Difference in bunching at 0.5% by jurisdiction (share within 0.01 pp, percentage points)."""
    e = estimates[
        (estimates["spec"] == "share within 0.01 pp") & estimates["estimate"].notna() & (estimates["n_at_0.5"] >= 200)
    ]  # Ireland is too small to show (see the tables)
    order = ["All six", "DE", "FR", "NL", "SE", "ES", "IE"]
    fig, axes = _figure(ncols=2, width=10, height=4.2)
    titles = {"down": "Exits: excess just below 0.5%", "up": "Entries: excess just above 0.5%"}
    for ax, direction in zip(axes, ("down", "up"), strict=True):
        d = (
            e[e["direction"] == direction]
            .set_index("jurisdiction")
            .reindex([j for j in order if j in set(e.loc[e["direction"] == direction, "jurisdiction"])])
        )
        y = np.arange(len(d))[::-1]
        for yi, (name, r) in zip(y, d.iterrows(), strict=True):
            color = BLUE if name == "All six" else NEUTRAL
            ax.plot([r["ci_low"], r["ci_high"]], [yi, yi], color=color, linewidth=2)
            ax.scatter([r["estimate"]], [yi], color=color, s=36, zorder=3)
        ax.axvline(0, color=MUTED, linewidth=0.8)
        ax.set_yticks(y, [f"{j} (N={int(n):,})" for j, n in zip(d.index, d["n_at_0.5"], strict=True)])
        import matplotlib.ticker as mticker

        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{100 * v:+.0f}"))
        ax.grid(axis="y", visible=False)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        _title(ax, titles[direction], "", "Excess share within 0.01 pp vs the 0.6%-1.5% trend (pp, 95% CI)")
    _save(fig, out)


# --- Listing Act ------------------------------------------------------------------------------------------------


def monthly_notifications(monthly: pd.DataFrame, out: Path) -> None:
    fig, (ax,) = _figure()
    for country, g in monthly.groupby("country"):
        x = g["month"].dt.to_timestamp()
        smooth = g["notifications"].rolling(3, center=True, min_periods=2).mean()
        ax.plot(x, g["notifications"], color=COUNTRY_COLORS.get(country, NEUTRAL), linewidth=0.8, alpha=0.35)
        ax.plot(x, smooth, color=COUNTRY_COLORS.get(country, NEUTRAL), linewidth=2)
        _label(ax, x.iloc[-1], smooth.iloc[-1], {"SE": "Sweden (FI)", "FR": "France (AMF)"}.get(country, country))
    ax.axvline(pd.Timestamp("2024-12-04"), color=INK, linewidth=0.8, linestyle="--")
    ymax = monthly["notifications"].max()
    ax.annotate(
        "Sweden: EUR 20,000 (4 Dec 2024)",
        (pd.Timestamp("2024-12-04"), ymax),
        xytext=(-4, 0),
        textcoords="offset points",
        ha="right",
        color=MUTED,
        fontsize=8,
    )
    if "FR" in set(monthly["country"]):
        ax.axvline(pd.Timestamp("2025-12-27"), color=ORANGE, linewidth=0.8, linestyle=":")
        ax.annotate(
            "France: EUR 50,000",
            (pd.Timestamp("2025-12-27"), ymax * 0.9),
            xytext=(4, 0),
            textcoords="offset points",
            color=MUTED,
            fontsize=8,
        )
    ax.set_ylim(bottom=0)
    ax.set_xlim(right=monthly["month"].max().to_timestamp() + pd.Timedelta(days=330))
    _title(
        ax,
        "Managers' transaction notifications per month",
        "Notifications",
        "Publication month; thick line: 3-month centred mean",
    )
    _save(fig, out)


def did_event_study(did: pd.DataFrame, out: Path) -> None:
    countries = [c for c in ("SE", "FR") if c in set(did["country"])]
    fig, axes = _figure(ncols=len(countries), width=6 * len(countries), height=4.4)
    colors = {"EUR 5k-20k": BLUE, "< EUR 5k": ORANGE, "EUR 20k-50k": AQUA}
    for ax, country in zip(axes, countries, strict=True):
        d = did[(did["country"] == country) & (did["subgroup"] == "All") & did["term"].str.startswith("year ")]
        ends = []
        for label, color in colors.items():
            g = d[d["class"] == label].assign(year=lambda x: x["term"].str[5:].astype(int)).sort_values("year")
            offset = {"< EUR 5k": -0.15, "EUR 5k-20k": 0, "EUR 20k-50k": 0.15}[label]
            ax.vlines(g["year"] + offset, g["ci_low"], g["ci_high"], color=color, linewidth=1.5)
            ax.plot(g["year"] + offset, g["estimate"], color=color, marker="o", markersize=4, linewidth=1.2)
            ends.append((g["year"].iloc[-1] + 0.2, g["estimate"].iloc[-1], label))
        ax.axhline(0, color=MUTED, linewidth=0.8)
        _labels(ax, ends, gap=0.07)
        ax.axvline(2025.5 if country == "FR" else 2024.5, color=INK, linewidth=0.8, linestyle="--")
        years = sorted(d["term"].str[5:].astype(int).unique())
        ax.set_xticks(years, [str(y)[2:] if len(years) > 6 else str(y) for y in years])
        ax.set_xlim(years[0] - 0.5, years[-1] + 1.6)
        name = {"SE": "Sweden", "FR": "France"}[country]
        _title(
            ax,
            f"{name}: person-issuer-years by yearly total, relative to >= EUR 50k",
            "Log count ratio, minus its pre-change mean",
            "Year (transactions 1 Jan to 30 Sep); bars: 95% issuer-bootstrap CI",
        )
    _save(fig, out)


def totals_density(density: pd.DataFrame, out: Path) -> None:
    countries = list(dict.fromkeys(density["country"]))
    fig, axes = _figure(ncols=len(countries), width=5.4 * len(countries), height=4.2)
    old = {"SE": 5_000, "FR": 20_000}
    for ax, country in zip(axes, countries, strict=True):
        d = density[density["country"] == country]
        threshold, width = float(d["threshold"].iloc[0]), float(d["width"].iloc[0])
        x = (d["bin_low"] + width / 2) / 1_000
        pre, post = d["pre"] / d["pre"].sum(), d["post"] / d["post"].sum()
        ax.plot(x, pre, color=NEUTRAL, linewidth=2, marker="o", markersize=3, label=f"{d['pre_label'].iloc[0]}")
        ax.plot(x, post, color=BLUE, linewidth=2, marker="o", markersize=3, label=f"{d['post_label'].iloc[0]}")
        ax.legend(frameon=False, fontsize=8.5, loc="upper right", labelcolor=INK)
        top = ax.get_ylim()[1]
        for level, style, text in ((old[country], ":", "old"), (threshold, "--", "new")):
            ax.axvline(level / 1_000, color=INK if style == "--" else MUTED, linewidth=0.8, linestyle=style)
            ax.annotate(
                f"{text} EUR {level / 1_000:.0f}k",
                (level / 1_000, top * 0.97),
                xytext=(3, 0),
                textcoords="offset points",
                color=MUTED,
                fontsize=8,
            )
        _percent(ax, 1)
        name = {"SE": "Sweden: yearly totals", "FR": "France: January-September totals"}[country]
        _title(
            ax,
            f"{name} per person-issuer",
            "Share of person-issuer-years",
            f"Gross total (EUR thousand, {width / 1_000:g}k bins)",
        )
        ax.set_xlim(0, threshold * 3.1 / 1_000)
    _save(fig, out)


# --- filing lags ------------------------------------------------------------------------------------------------


def lag_cdf(cdf: pd.DataFrame, out: Path) -> None:
    fig, (ax,) = _figure()
    for country, g in cdf.groupby("country"):
        color = COUNTRY_COLORS.get(country, NEUTRAL)
        ax.step(g["lag_bd"], g["share_published"], where="post", color=color, linewidth=2)
        _label(
            ax,
            15,
            g.loc[g["lag_bd"] == 15, "share_published"].iloc[0] - 0.04,
            {"SE": "Sweden", "FR": "France"}.get(country, country),
        )
    ax.axvline(3, color=INK, linewidth=0.8, linestyle="--")
    ax.annotate("3 business days", (3, 0.12), xytext=(4, 0), textcoords="offset points", color=MUTED, fontsize=8)
    _percent(ax)
    ax.set_xlim(0, 20)
    ax.set_xticks(range(0, 21, 2))
    ax.set_ylim(0, 1.02)
    _title(
        ax,
        "Share of transactions published within k business days",
        "Cumulative share of lines",
        "Business days from the transaction to publication on the authority's register",
    )
    _save(fig, out)


def late_by_year(by_year: pd.DataFrame, out: Path) -> None:
    fig, (ax,) = _figure()
    for country, g in by_year.groupby("country"):
        color = COUNTRY_COLORS.get(country, NEUTRAL)
        ax.plot(g["year"], g["share_late"], color=color, linewidth=2, marker="o", markersize=4)
        ax.plot(g["year"], g["share_late_notification_level"], color=color, linewidth=1, linestyle="--")
        _label(ax, g["year"].iloc[-1], g["share_late"].iloc[-1], {"SE": "Sweden", "FR": "France"}.get(country))
    _percent(ax)
    ax.set_ylim(bottom=0)
    _title(
        ax,
        "Share published more than three business days after the transaction",
        "Share of lines",
        "Publication year; dashed: lag from the latest transaction of the notification",
    )
    _save(fig, out)
