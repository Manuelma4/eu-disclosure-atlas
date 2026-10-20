"""Write a synthetic lake for end-to-end tests and performance checks of the website.

    python web/e2e/fixture_lake.py <lake-dir> [--rows 4000] [--issuers 40] [--seed 7]

Every name is invented and every person is a pseudonymous id, as in the real silver layer. Dates are relative
to today, so "last 30 days" figures are never empty. With --rows 1200000 it doubles as the explorer's
million-row benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import random
import shutil
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from disclosure_atlas.lake import Lake
from disclosure_atlas.models import Dataset, InsiderTransaction, arrow_schema
from disclosure_atlas.normalize import _alnum_to_digits
from disclosure_atlas.reference.fx import SCHEMA as FX_SCHEMA

WORDS = [
    "Robotics", "Nordic Timber", "Baltic Shipping", "Aurora Biotech", "Fjord Energy", "Lumen Software",
    "Granite Mining", "Polar Foods", "Helix Medical", "Vega Payments", "Saga Real Estate", "Orbit Telecom",
    "Tundra Gaming", "Cobalt Batteries", "Meridian Bank", "Harbor Logistics", "Juniper Fashion", "Stellar Media",
    "Northwind Retail", "Kestrel Defence", "Alder Pharma", "Boreal Paper", "Cirrus Cloud", "Delta Hydrogen",
    "Ember Steel", "Falcon Aerospace", "Glacier Water", "Hearth Housing", "Iris Optics", "Jade Insurance",
    "Kelp Aquaculture", "Lynx Security", "Moss Packaging", "Nimbus Analytics", "Onyx Semiconductors",
    "Pine Forestry", "Quartz Glass", "Raven Engineering", "Spruce Hotels", "Tern Airlines",
]
ROLES = [("ceo", 0.12), ("cfo", 0.09), ("chair", 0.07), ("executive", 0.27), ("board_member", 0.38), ("other", 0.07)]
NATURES = [("buy", 0.42), ("sell", 0.22), ("grant", 0.1), ("exercise", 0.08), ("subscription", 0.08),
           ("gift", 0.03), ("pledge", 0.02), ("other", 0.05)]
DIRECTION = {"buy": 1, "sell": -1, "grant": 1, "exercise": 1, "subscription": 1, "gift": -1, "pledge": 0, "other": 0}


def make_lei(prefix18: str) -> str:
    return f"{prefix18}{98 - int(_alnum_to_digits(prefix18 + '00')) % 97:02d}"


def make_isin(prefix11: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(_alnum_to_digits(prefix11))):
        n = int(ch) * (2 if i % 2 == 0 else 1)
        total += n - 9 if n > 9 else n
    return f"{prefix11}{(10 - total % 10) % 10}"


def pick(rng: random.Random, weighted: list[tuple[str, float]]) -> str:
    return rng.choices([w[0] for w in weighted], weights=[w[1] for w in weighted])[0]


def build(root: Path, rows: int, issuers: int, seed: int) -> None:
    rng = random.Random(seed)
    today = date.today()
    if root.exists():
        shutil.rmtree(root)
    lake = Lake(root)

    companies = []
    for i in range(issuers):
        word = WORDS[i % len(WORDS)] + ("" if i < len(WORDS) else f" {i // len(WORDS) + 1}")
        swedish = i % 5 != 4
        companies.append({
            "name": f"Example {word} {'AB (publ)' if swedish else 'Oyj'}",
            "lei": make_lei(f"549300EXAMPLE{i:05d}"),
            "isin": make_isin(f"{'SE' if swedish else 'FI'}000EX{i:04d}"),
            "currency": "SEK" if swedish else "EUR",
            "price": rng.uniform(15, 400) * (10 if swedish else 1),
            "weight": 1 / (i + 1) ** 0.8,  # a few issuers file most of the notifications
            "insiders": [f"{hashlib.sha256(f'{seed}-{i}-{k}'.encode()).hexdigest()[:16]}" for k in range(12)],
        })
    weights = [c["weight"] for c in companies]

    columns: dict[str, list] = {name: [] for name in arrow_schema(InsiderTransaction).names}
    span_days = 730
    for n in range(rows):
        c = rng.choices(companies, weights=weights)[0]
        # More filings recently, so recent windows look alive.
        age = int(span_days * (rng.random() ** 1.6))
        published = datetime.combine(today - timedelta(days=age), time(rng.randint(7, 19), rng.randint(0, 59)),
                                     tzinfo=UTC)
        lag = min(int(rng.expovariate(0.6)), 40)
        nature = pick(rng, NATURES)
        role = pick(rng, ROLES)
        share = nature in ("buy", "sell") or rng.random() < 0.5
        volume = float(max(1, round(math.exp(rng.gauss(7.2, 1.6)))))
        price = round(c["price"] * math.exp(rng.gauss(0, 0.15)), 2)
        person = c["insiders"][rng.randrange(len(c["insiders"]))]
        values = {
            "record_id": hashlib.sha256(f"{seed}:{n}".encode()).hexdigest()[:24],
            "source": "se_fi_pdmr",
            "jurisdiction": "SE",
            "source_url": None,
            "published_at": published,
            "transaction_date": published.date() - timedelta(days=lag),
            "issuer_name": c["name"],
            "issuer_lei": c["lei"],
            "pdmr_id": person,
            "pdmr_role": role,
            "pdmr_role_raw": role.replace("_", " ").title(),
            "notifier_id": person,
            "notifier_name": None,
            "notifier_is_legal_entity": False,
            "closely_associated": rng.random() < 0.15,
            "is_amendment": rng.random() < 0.03,
            "amendment_details": None,
            "linked_to_share_option_programme": nature in ("grant", "exercise"),
            "nature": nature,
            "nature_raw": nature.title(),
            "direction": DIRECTION[nature],
            "instrument_type": "share" if share else rng.choice(["option", "warrant", "subscription_right"]),
            "instrument_type_raw": None,
            "instrument_name": c["name"],
            "isin": c["isin"] if share else None,
            "volume": volume,
            "price": price,
            "currency": c["currency"],
            "amount": round(volume * price, 2),
            "venue": "Example Exchange" if nature in ("buy", "sell") else None,
            "on_venue": nature in ("buy", "sell") and rng.random() < 0.9,
            "is_superseded": False,
            "status_raw": "Current",
            "fetched_at": published + timedelta(minutes=rng.randint(1, 30)),
            "parser": "fixture",
        }
        for key, value in values.items():
            columns[key].append(value)

    folder = lake.silver / Dataset.INSIDER_TRANSACTIONS.value / "se_fi_pdmr"
    folder.mkdir(parents=True, exist_ok=True)
    table = pa.table(columns, schema=arrow_schema(InsiderTransaction))
    pq.write_table(table, folder / "fixture.parquet", compression="zstd")

    fx_days = [today - timedelta(days=d) for d in range(span_days + 60)]
    fx = [{"date": d, "currency": "SEK", "rate": round(11.2 + 0.4 * math.sin(i / 40), 4)}
          for i, d in enumerate(fx_days)]
    lake.fx_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(fx, schema=FX_SCHEMA), lake.fx_path)
    # Commit the file into the DuckLake catalog (one snapshot), as the pre-DuckLake migration path does.
    lake.migrate()
    lake.close()
    print(f"{rows} synthetic filings for {issuers} issuers -> {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path)
    parser.add_argument("--rows", type=int, default=4000)
    parser.add_argument("--issuers", type=int, default=40)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    build(args.root, args.rows, args.issuers, args.seed)
