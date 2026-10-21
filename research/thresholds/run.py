"""Thresholds and regulation (WS7): short-position bunching, the Listing Act and filing lags.

One command, from the repository root:

    python research/thresholds/run.py --backfill --until 2026-10-20

--backfill    first ingest the six short-position registers, the Swedish FI register (since July 2016) and the
              French AMF register (since February 2021) into the lake (resumable; several hours the first time)
--lake PATH   lake root (default: ./lake); repeat to read several lakes (e.g. one per source)
--until DATE  last publication date in the sample; the committed results use --until 2026-10-20
--reps N      bootstrap replications (default 999)

Writes aggregated tables to results/ and figures to figures/ next to this file. Nothing at the level of a
person or a position is written. Not investment advice.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import figures  # noqa: E402
import pdmr  # noqa: E402
import shorts  # noqa: E402

RESULTS, FIGURES = HERE / "results", HERE / "figures"
SHORT_SOURCES = ["se_fi_shorts", "fr_amf_shorts", "de_bafin_shorts", "nl_afm_shorts", "ie_cbi_shorts", "es_cnmv_shorts"]
# Listing Act designs per country: the years compared (transactions from 1 January to 30 September), the
# post-change regimes (France did not change in 2025: a placebo), and the bunching comparison at the new
# threshold (Sweden: full calendar years; France: 1 January to 30 September, as 2026 is not over).
LISTING = {
    "SE": {
        "years": list(range(2017, 2027)),
        "post": {"post_20k": [2025, 2026]},
        "its_start": "2018-01",
        "bunching": {
            "threshold": 20_000,
            "pre": list(range(2017, 2025)),
            "post": [2025],
            "full_year": True,
            "width": 2_500,
            "upper": 100_000,
            "labels": ("2017-2024", "2025"),
        },
    },
    "FR": {
        "years": list(range(2022, 2027)),
        "post": {"post_50k": [2026], "placebo_2025": [2025]},
        "its_start": "2022-02",
        "bunching": {
            "threshold": 50_000,
            "pre": [2022, 2023, 2024, 2025],
            "post": [2026],
            "full_year": False,
            "width": 5_000,
            "upper": 200_000,
            "labels": ("2022-2025, Jan-Sep", "2026, Jan-Sep"),
        },
    },
}


def log(message: str) -> None:
    print(message, flush=True)


def backfill(lake: Path, until: str) -> None:
    env = {**os.environ, "LAKE": str(lake), "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"}
    for source in SHORT_SOURCES:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "disclosure_atlas.cli",
                "--lake",
                str(lake),
                "ingest",
                source,
                "--start",
                "2010-01-01",
                "--end",
                until,
            ],
            check=True,
            env=env,
        )
    subprocess.run(["bash", str(HERE.parent / "insider_event_study" / "backfill.sh")], check=True, env=env)
    subprocess.run(["bash", str(HERE / "backfill_fr.sh")], check=True, env=env)


def ensure_fx(lakes: list[Path]) -> None:
    """Amounts in euros need the ECB reference rates in each lake (gold leaves amount_eur empty without them)."""
    from disclosure_atlas.lake import Lake

    for lake in lakes:
        if not Lake(lake).fx_path.exists():
            subprocess.run(
                [sys.executable, "-m", "disclosure_atlas.cli", "--lake", str(lake), "reference", "fx"], check=True
            )


def covered_years(lines: pd.DataFrame, country: str, years: list[int]) -> list[int]:
    """Years whose 1 January to 30 September transactions are fully covered by the register's publications."""
    pub = lines.loc[lines["country"] == country, "published_date"]
    if pub.empty:
        return []
    return [y for y in years if pub.min() <= pd.Timestamp(f"{y}-01-10") and pub.max() >= pd.Timestamp(f"{y}-10-15")]


def run_shorts(lakes: list[Path], until: str | None, reps: int) -> dict:
    cross = shorts.crossings(shorts.load_positions(lakes, until))
    out = shorts.run(cross, reps=reps, log=log)
    out["estimates"].round(6).to_csv(RESULTS / "shorts_bunching.csv", index=False)
    out["profiles"].round(6).to_csv(RESULTS / "shorts_profile_by_threshold.csv", index=False)
    out["distance"].to_csv(RESULTS / "shorts_crossing_distance.csv", index=False)
    out["coverage"].to_csv(RESULTS / "shorts_coverage.csv", index=False)
    out["exit_depth"].to_csv(RESULTS / "shorts_exit_values.csv", index=False)
    figures.reported_values(cross, FIGURES / "shorts_reported_values.png")
    figures.crossing_shape(out["distance"], FIGURES / "shorts_crossing_shape.png")
    figures.bunching_by_level(out["profiles"], out["estimates"], FIGURES / "shorts_bunching_by_threshold.png")
    figures.country_estimates(out["estimates"], FIGURES / "shorts_bunching_by_country.png")
    return {"short_notifications": len(cross), "short_holders": int(cross["holder"].nunique())}


def run_listing_act(lines: pd.DataFrame, reps: int, last_month: str) -> dict:
    notes = pdmr.notifications(lines)
    monthly = pdmr.monthly_counts(notes, last_month)
    monthly.assign(month=monthly["month"].astype(str)).to_csv(
        RESULTS / "listing_monthly_notifications.csv", index=False
    )
    its, did, bunch, densities, placebos, coverage = [], [], [], [], [], {}
    for country, design in LISTING.items():
        years = covered_years(lines, country, design["years"])
        post = {k: [y for y in v if y in years] for k, v in design["post"].items()}
        pre_years = [y for y in years if not any(y in v for v in post.values())]
        coverage[country] = {"years": years, "pre_years": pre_years}
        if len(pre_years) < 2 or not any(post.values()):
            log(f"listing act: {country} skipped (covered years: {years})")
            continue
        post = {k: v for k, v in post.items() if v}
        its.append(pdmr.interrupted_time_series(monthly, country, design["its_start"]))
        units = pdmr.person_years(lines)
        did.append(pdmr.listing_act_did(units, country, years, post, reps))
        placebos.append(pdmr.placebo_dates(units, country, pre_years))
        # Heterogeneity: who and what stopped being notified.
        subgroups = {
            "Role: CEO/CFO": units["role_group"] == "CEO/CFO",
            "Role: chair/board": units["role_group"] == "Chair/board",
            "Role: other": units["role_group"].isin(["Other executive", "Other"]),
            "Closely associated persons": units["closely_associated"],
            "Managers themselves": ~units["closely_associated"],
            "Market trades only": units["market_only"],
            "Involves grants, exercises, subscriptions...": ~units["market_only"],
        }
        for name, mask in subgroups.items():
            did.append(pdmr.listing_act_did(units[mask], country, years, post, reps, subgroup=name))
        did.append(
            pdmr.listing_act_did(
                pdmr.person_years(lines, by="pdmr_id"),
                country,
                years,
                post,
                reps,
                subgroup="Manager and associates pooled",
            )
        )
        b = design["bunching"]
        b_pre = [y for y in b["pre"] if y in years or b["full_year"]]
        if b_pre and b["post"][0] in years:
            totals = pdmr.person_years(lines, full_year=b["full_year"])
            table, density = pdmr.threshold_bunching(
                totals,
                country,
                b_pre,
                b["post"],
                b["threshold"],
                reps,
                width=b["width"],
                upper=b["upper"],
                labels=b["labels"],
            )
            bunch.append(table)
            densities.append(density)
        log(f"listing act: {country} done ({years[0]}-{years[-1]})")
    its, did = pd.concat(its, ignore_index=True), pd.concat(did, ignore_index=True)
    its.round(6).to_csv(RESULTS / "listing_its.csv", index=False)
    pd.concat(placebos, ignore_index=True).round(6).to_csv(RESULTS / "listing_placebo_dates.csv", index=False)
    did.round(6).to_csv(RESULTS / "listing_did.csv", index=False)
    if bunch:
        pd.concat(bunch).round(6).to_csv(RESULTS / "listing_bunching_new_threshold.csv", index=False)
        pd.concat(densities).to_csv(RESULTS / "listing_totals_density.csv", index=False)
        figures.totals_density(pd.concat(densities), FIGURES / "listing_totals_density.png")
    periods = {"Jan 2023 - Nov 2024": ("2023-01-01", "2024-11-30"), "Jan 2025 - Sep 2026": ("2025-01-01", "2026-09-30")}
    if "FR" in set(lines["country"]):
        periods = {
            **periods,
            "Jan 2025 - Dec 2025": ("2025-01-01", "2025-12-26"),
            "Jan 2026 - Sep 2026": ("2026-01-01", "2026-09-30"),
        }
    pdmr.composition(notes, periods).round(6).to_csv(RESULTS / "listing_composition.csv", index=False)
    figures.monthly_notifications(monthly, FIGURES / "listing_monthly_notifications.png")
    figures.did_event_study(did, FIGURES / "listing_did_event_study.png")
    return {"listing_act_years": coverage}


def run_lags(lines: pd.DataFrame) -> dict:
    sample = pdmr.lag_sample(lines)
    tables = pdmr.lag_tables(sample)
    splits = pd.concat([tables[k] for k in tables if k.startswith("by_") and k != "by_year"], ignore_index=True)
    splits.round(6).to_csv(RESULTS / "lags_by_group.csv", index=False)
    tables["by_year"].round(6).to_csv(RESULTS / "lags_by_year.csv", index=False)
    tables["overall"].round(6).to_csv(RESULTS / "lags_overall.csv", index=False)
    tables["cdf"].round(6).to_csv(RESULTS / "lags_cdf.csv", index=False)
    pdmr.lag_logit(sample).round(6).to_csv(RESULTS / "lags_models.csv", index=False)
    figures.lag_cdf(tables["cdf"], FIGURES / "lags_cdf.png")
    figures.late_by_year(tables["by_year"], FIGURES / "lags_late_by_year.png")
    all_lines = pdmr.add_lags(lines)
    return {
        "lag_lines": int(len(sample)),
        "lag_excluded_amendments": int(all_lines["is_amendment"].sum()),
        "lag_excluded_implausible_over_365_days": int(all_lines["implausible"].sum()),
        "lag_excluded_restamped_publication_dates": int((~all_lines["reliable_publication"]).sum()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lake", type=Path, action="append")
    parser.add_argument("--backfill", action="store_true", help="ingest all sources first (resumable)")
    parser.add_argument("--until", help="last publication date in the sample (YYYY-MM-DD)")
    parser.add_argument("--reps", type=int, default=999)
    parser.add_argument("--part", choices=["all", "shorts", "listing", "lags"], default="all")
    args = parser.parse_args(argv)
    lakes = args.lake or [Path("lake")]
    until = args.until or pd.Timestamp.today().strftime("%Y-%m-%d")
    if args.backfill:
        backfill(lakes[0], until)
    RESULTS.mkdir(exist_ok=True)
    FIGURES.mkdir(exist_ok=True)

    summary_path = RESULTS / "coverage.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    summary["sample_until"] = until
    if args.part in ("all", "shorts"):
        summary.update(run_shorts(lakes, until, args.reps))
    if args.part in ("all", "listing", "lags"):
        ensure_fx(lakes)
        lines = pdmr.load_lines(lakes, until)
        summary["pdmr_lines"] = lines["country"].value_counts().to_dict()
        summary["pdmr_first_publication"] = lines.groupby("country")["published_date"].min().astype(str).to_dict()
        last_month = (pd.Timestamp(until).to_period("M") - 1).strftime("%Y-%m")  # last complete month
        if args.part in ("all", "listing"):
            summary.update(run_listing_act(lines, args.reps, last_month))
        if args.part in ("all", "lags"):
            summary.update(run_lags(lines))
    summary_path.write_text(json.dumps(summary, indent=2, default=str) + "\n")
    log(json.dumps(summary, indent=2, default=str))
    log(f"results -> {RESULTS}, figures -> {FIGURES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
