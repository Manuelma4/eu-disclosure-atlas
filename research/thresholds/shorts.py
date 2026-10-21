"""Bunching at the 0.5% public-disclosure threshold for net short positions (SSR Article 6).

What the public data can and cannot show
----------------------------------------
Public registers only list positions at or above 0.5% (and, in most countries, the notification that a
position fell below 0.5%, with its value). Positions held just below the threshold are reported only to the
authority (Article 5, 0.2% and, since 31 January 2022, 0.1%), so the static excess mass below 0.5% that Jank,
Roling and Smajlbegovic (2021) measure with confidential German data cannot be computed from public files.

Public files do show every *crossing*: a notification is due each time a position crosses 0.5%, 0.6%, 0.7%...
upwards or downwards (Article 6(2)). If holders dislike being public, two traces follow, both relative to the
same crossings at higher thresholds, where the holder is already public and nothing changes:

- **Exits** (downward crossings of 0.5%): holders who want to go dark but keep the position cut it to just
  under 0.5%, so exit values bunch in [0.49%, 0.50%) more than downward crossings of 0.6%, 0.7%... bunch just
  below those thresholds.
- **Entries** (upward crossings of 0.5%): crossing is costly, so holders who would sit just above 0.5% stay
  below (a notch, Kleven and Waseem, 2013); entry values should show *missing* mass just above 0.5%.

For each crossing we take the distance, in hundredths of a percentage point, between the reported value and
the threshold crossed (k = 0 means within 0.01 pp), keep crossings that land within one 0.1 pp band of the
threshold, and estimate the excess mass at k = 0 at every threshold from 0.5% to 1.5%. The bunching at 0.5%
minus its extrapolation from 0.6% to 1.5% is the estimate (``bunching.difference_in_bunching``).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import bunching

JURISDICTIONS = ["DE", "ES", "FR", "IE", "NL", "SE"]
LEVELS = np.arange(50, 151, 10)  # thresholds in hundredths of a percent: 0.5% ... 1.5%
K = 10  # distance bins of 0.01 pp within one 0.1 pp band
EPS = 1e-6
MIN_AT_THRESHOLD = 30  # crossings at 0.5% needed to report an estimate

POSITIONS_SQL = """
SELECT jurisdiction, holder_name, isin, issuer_name, position_date, published_at, position_pct, below_threshold
FROM net_short_positions
"""


def load_positions(lakes: list[Path], until: str | None = None) -> pd.DataFrame:
    from disclosure_atlas.lake import Lake

    frames = []
    for lake in lakes:
        con = Lake(lake).connect()
        tables = set(con.sql("SHOW TABLES").df()["name"])
        if "net_short_positions" in tables:
            frames.append(con.sql(POSITIONS_SQL).df())
    positions = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    positions["position_date"] = pd.to_datetime(positions["position_date"])
    if until:
        positions = positions[positions["position_date"] <= pd.Timestamp(until)]
    return positions.drop_duplicates()


def crossings(positions: pd.DataFrame) -> pd.DataFrame:
    """One row per notification with the threshold it crossed and its distance from it.

    Positions are tracked per jurisdiction, holder and instrument (ISIN, or issuer name when the ISIN is
    missing). ``direction`` is 'up' (crossing one or more thresholds upwards, including the first appearance and
    re-entries after an exit), 'down' (downwards, including exits), 'exit_no_value' (an exit published as
    "< 0.5%") or 'none' (no threshold crossed: corrections and duplicates). For crossings, ``level`` is the
    threshold nearest to the reported value on the side it came from (in hundredths of a percent) and ``k`` the
    distance in 0.01 pp bins.
    """
    d = positions.copy()
    d["holder"] = d["holder_name"].str.upper().str.replace(r"[^A-Z0-9]+", " ", regex=True).str.strip()
    d["instrument"] = d["isin"].fillna("NAME:" + d["issuer_name"].str.upper().str.strip())
    # Full sort key, so ties (same-day notifications without a publication time) resolve the same way every run.
    d = d.sort_values(
        [
            "jurisdiction",
            "holder",
            "instrument",
            "position_date",
            "published_at",
            "below_threshold",
            "position_pct",
            "holder_name",
            "issuer_name",
        ],
        na_position="first",
        kind="mergesort",
    )
    # Several notifications on one position date: keep the last published (a correction).
    d = d.drop_duplicates(["jurisdiction", "holder", "instrument", "position_date"], keep="last")
    pct = d["position_pct"]
    hundredths = np.floor(pct * 100 + EPS)
    band = np.floor(pct * 10 + EPS)
    group = d.groupby(["jurisdiction", "holder", "instrument"], sort=False)
    first = group.cumcount() == 0
    prev_pct = group["position_pct"].shift()
    prev_below = group["below_threshold"].shift().fillna(False).astype(bool)
    # Band of the previous notification; anything below 0.5% (first appearance, earlier exit) is band 4.
    prev_band = np.floor(prev_pct * 10 + EPS)
    prev_band = prev_band.where(~first & ~(prev_below | (prev_pct < 0.5)), 4.0)
    prev_band = prev_band.fillna(4.0)

    up = pct.ge(0.5) & (band > prev_band)
    down = ~first & (prev_band >= 5) & pct.notna() & (band < prev_band)
    no_value = d["below_threshold"] & pct.isna() & ~first
    d["direction"] = np.select([up, down, no_value], ["up", "down", "exit_no_value"], "none")
    level = np.where(up, band * 10, np.where(down, np.maximum(band + 1, 5) * 10, np.nan))
    k = np.where(up, hundredths - level, np.where(down, level - 1 - hundredths, np.nan))
    d["level"], d["k"] = level, k
    d["in_band"] = d["direction"].isin(["up", "down"]) & (d["k"] >= 0) & (d["k"] < K)
    d["first_appearance"] = first
    # Re-entry: an upward crossing of 0.5% whose previous notification was an exit with a value in [0.4, 0.5).
    d["reentry_from_known"] = up & (level == 50) & ~first & prev_pct.between(0.4, 0.5 - EPS)
    d["year"] = d["position_date"].dt.year
    return d.reset_index(drop=True)


def cluster_counts(x: pd.DataFrame, cluster: str = "holder") -> np.ndarray:
    """Counts array (clusters, thresholds, K) of in-band crossings."""
    x = x[x["in_band"] & x["level"].isin(LEVELS)]
    ids = pd.factorize(x[cluster])[0]
    out = np.zeros((max(ids.max() + 1, 1) if len(ids) else 1, len(LEVELS), K))
    np.add.at(out, (ids, np.searchsorted(LEVELS, x["level"].astype(int)), x["k"].astype(int)), 1)
    return out


SPECS = {
    # name: (window, degree, trend_degree, max control level, statistic)
    "main": (1, 2, 1, 150, "b"),
    "window 2 bins": (2, 2, 1, 150, "b"),
    "linear in k": (1, 1, 1, 150, "b"),
    "quadratic trend": (1, 2, 2, 150, "b"),
    "controls 0.6-1.0%": (1, 2, 1, 100, "b"),
    "share within 0.01 pp": (1, 2, 1, 150, "share"),
    "share, quadratic trend": (1, 2, 2, 150, "share"),
    "share, controls 0.6-1.0%": (1, 2, 1, 100, "share"),
}


def estimate(x: pd.DataFrame, spec: str = "main", reps: int = 999, seed: int = 7) -> dict[str, float]:
    window, degree, trend, max_level, stat = SPECS[spec]
    counts = cluster_counts(x)
    keep = max_level >= LEVELS

    def estimator(total: np.ndarray) -> dict[str, float]:
        return bunching.difference_in_bunching(
            LEVELS[keep] / 100, total[keep], 0.5, window=window, degree=degree, trend_degree=trend, stat=stat
        )

    point = estimator(counts.sum(axis=0))
    n50 = int(counts.sum(axis=(0, 2))[0])
    if n50 < MIN_AT_THRESHOLD:  # e.g. Sweden publishes exits as "< 0.5%" without a value
        point = {"actual": np.nan, "counterfactual": np.nan, "difference": np.nan}
        draws = {"difference": np.array([])}
    else:
        draws = bunching.cluster_bootstrap(counts, estimator, reps=reps, seed=seed)
    summary = bunching.summarize(point["difference"], draws["difference"])
    return {
        "actual": point["actual"],
        "counterfactual": point["counterfactual"],
        **summary,
        "n_at_0.5": n50,
        "holders": int((counts.sum(axis=(1, 2)) > 0).sum()),
    }


def estimate_contrast(
    x: pd.DataFrame, spec: str = "share within 0.01 pp", reps: int = 999, seed: int = 7
) -> dict[str, float]:
    """Exit bunching minus entry bunching at 0.5%, each net of its own trend, from one joint bootstrap.

    Both directions share whatever makes crossings at 0.5% different for mechanical reasons (smaller position
    changes at smaller positions, beyond the fitted trend). Avoidance predicts excess mass at exits and missing
    mass at entries, so the contrast should be positive.
    """
    window, degree, trend, max_level, stat = SPECS[spec]
    x = x[x["in_band"] & x["level"].isin(LEVELS) & x["direction"].isin(["down", "up"])]
    ids = pd.factorize(x["holder"])[0]
    counts = np.zeros((ids.max() + 1, 2, len(LEVELS), K))
    np.add.at(
        counts,
        (
            ids,
            (x["direction"] == "up").astype(int).to_numpy(),
            np.searchsorted(LEVELS, x["level"].astype(int)),
            x["k"].astype(int),
        ),
        1,
    )
    keep = max_level >= LEVELS

    def estimator(total: np.ndarray) -> dict[str, float]:
        diffs = [
            bunching.difference_in_bunching(
                LEVELS[keep] / 100, total[d][keep], 0.5, window=window, degree=degree, trend_degree=trend, stat=stat
            )["difference"]
            for d in (0, 1)
        ]
        return {"difference": diffs[0] - diffs[1]}

    point = estimator(counts.sum(axis=0))
    draws = bunching.cluster_bootstrap(counts, estimator, reps=reps, seed=seed)
    return {
        **bunching.summarize(point["difference"], draws["difference"]),
        "n_at_0.5": int(counts.sum(axis=(0, 3))[:, 0].sum()),
        "holders": int(ids.max() + 1),
    }


def profile(x: pd.DataFrame, spec: str = "main") -> pd.DataFrame:
    """The bunching statistic at every threshold level (for the figures)."""
    window, degree, *_ = SPECS[spec]
    total = cluster_counts(x).sum(axis=0)
    return pd.DataFrame(
        [{"level": lv / 100, **bunching.excess_mass(c, window, degree)} for lv, c in zip(LEVELS, total, strict=True)]
    )


def run(cross: pd.DataFrame, reps: int = 999, log=print) -> dict[str, pd.DataFrame]:
    groups = {j: cross[cross["jurisdiction"] == j] for j in JURISDICTIONS}
    groups["All six"] = cross
    rows = []
    for name, g in groups.items():
        for direction in ("down", "up"):
            side = g[g["direction"] == direction]
            if side.empty:
                continue
            for spec in SPECS:
                if spec not in ("main", "share within 0.01 pp") and name not in ("All six", "DE", "FR"):
                    continue
                rows.append(
                    {"jurisdiction": name, "direction": direction, "spec": spec, **estimate(side, spec, reps=reps)}
                )
        log(f"shorts: bunching estimated for {name}")
    # Sub-samples for the pooled data: periods, and re-entries from a known value below 0.5%.
    pooled = cross
    for label, (start, end) in {
        "2010-2016": (2010, 2016),
        "2017-2021": (2017, 2021),
        "2022-2026": (2022, 2026),
    }.items():
        for direction in ("down", "up"):
            side = pooled[(pooled["direction"] == direction) & pooled["year"].between(start, end)]
            rows.append(
                {
                    "jurisdiction": "All six",
                    "direction": direction,
                    "spec": f"period {label}",
                    **estimate(side, "main", reps=reps),
                }
            )
    for name in ("All six", "DE", "FR", "NL"):
        g = groups[name]
        for spec in ("share within 0.01 pp", "main"):
            rows.append(
                {
                    "jurisdiction": name,
                    "direction": "exits minus entries",
                    "spec": spec,
                    **estimate_contrast(g, spec, reps=reps),
                }
            )
    reentry = pooled[(pooled["direction"] == "up") & ((pooled["level"] != 50) | pooled["reentry_from_known"])]
    rows.append(
        {
            "jurisdiction": "All six",
            "direction": "up",
            "spec": "0.5% entries: re-entries from a known value",
            **estimate(reentry, "main", reps=reps),
        }
    )
    estimates = pd.DataFrame(rows)

    profiles = []
    for name, g in groups.items():
        for direction in ("down", "up"):
            side = g[g["direction"] == direction]
            if not side.empty:
                profiles.append(profile(side).assign(jurisdiction=name, direction=direction))
    distance = (
        cross[cross["in_band"] & cross["level"].isin(LEVELS)]
        .groupby(["direction", "level", "k"])
        .size()
        .rename("n")
        .reset_index()
    )
    coverage = (
        cross.groupby("jurisdiction")
        .agg(
            notifications=("direction", "size"),
            first_date=("position_date", "min"),
            last_date=("position_date", "max"),
            holders=("holder", "nunique"),
            positions=("instrument", "size"),
        )
        .join(pd.crosstab(cross["jurisdiction"], cross["direction"]))
    )
    coverage["positions"] = cross.groupby("jurisdiction").apply(
        lambda g: g[["holder", "instrument"]].drop_duplicates().shape[0], include_groups=False
    )
    exits = cross[((cross["direction"] == "down") & (cross["level"] == 50)) | (cross["direction"] == "exit_no_value")]
    exit_depth = exits.assign(
        depth=pd.cut(exits["position_pct"], [0.0, 0.005, 0.2, 0.4, 0.45, 0.48, 0.49, 0.5], right=False)
        .astype(str)
        .where(exits["position_pct"].notna(), "no value")
    ).pipe(lambda e: pd.crosstab(e["depth"], e["jurisdiction"]))
    return {
        "estimates": estimates,
        "profiles": pd.concat(profiles, ignore_index=True),
        "distance": distance,
        "coverage": coverage.reset_index(),
        "exit_depth": exit_depth.reset_index(),
    }
