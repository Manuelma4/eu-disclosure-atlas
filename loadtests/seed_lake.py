"""Seed a lake with synthetic insider transactions for load tests, DAST scans and the observability demo.

Every name and identifier is generated: no real person or company appears. Usage:
    python loadtests/seed_lake.py /tmp/atlas-lake --transactions 20000
Prints the issuer LEIs it created (one per line) to loadtests/issuers.txt-style output with --issuers-out.
"""

from __future__ import annotations

import argparse
import hashlib
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from disclosure_atlas.lake import Lake
from disclosure_atlas.models import Dataset, InsiderTransaction

ROLES = ["ceo", "cfo", "chair", "executive", "board_member", "other"]
NATURES = [("buy", 1), ("sell", -1), ("grant", 0), ("exercise", 0)]
SOURCE = "se_fi_pdmr"  # a registered source, so /v1/sources reports it


def _lei(i: int) -> str:
    return f"9845{hashlib.sha1(f'issuer-{i}'.encode()).hexdigest()[:14].upper()}"


def build(n: int, issuers: int, seed: int = 7) -> list[InsiderTransaction]:
    rng = random.Random(seed)
    now = datetime.now(UTC)
    records = []
    for i in range(n):
        issuer = rng.randrange(issuers)
        nature, direction = rng.choices(NATURES, weights=[5, 3, 1, 1])[0]
        published = now - timedelta(minutes=rng.randint(1, 365 * 24 * 60))
        # Most filings are picked up within minutes; a few are late, as on a bad day.
        lag = timedelta(minutes=rng.choice([1, 2, 3, 5, 8, 12]) if rng.random() < 0.97 else rng.randint(16, 600))
        volume = float(rng.randint(100, 50_000))
        price = round(rng.uniform(5, 400), 2)
        records.append(InsiderTransaction(
            record_id=f"synthetic-{i}",
            source=SOURCE,
            jurisdiction="SE",
            published_at=published,
            transaction_date=(published - timedelta(days=rng.randint(0, 6))).date(),
            issuer_name=f"Synthetic Issuer {issuer} AB",
            issuer_lei=_lei(issuer),
            pdmr_id=f"p_{hashlib.sha1(f'person-{issuer}-{rng.randrange(8)}'.encode()).hexdigest()[:16]}",
            pdmr_role=rng.choice(ROLES),
            notifier_id=f"n_{i}",
            nature=nature,
            nature_raw=nature,
            direction=direction,
            instrument_type="share",
            isin=f"SE{issuer:010d}",
            volume=volume,
            price=price,
            currency="EUR",
            amount=volume * price,
            venue="Synthetic Exchange",
            on_venue=True,
            fetched_at=min(published + lag, now),
            parser="synthetic",
        ))
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("lake", type=Path)
    parser.add_argument("--transactions", type=int, default=20_000)
    parser.add_argument("--issuers", type=int, default=400)
    parser.add_argument("--issuers-out", type=Path, help="Write the generated issuer LEIs to this file")
    args = parser.parse_args()
    lake = Lake(args.lake)
    lake.write_silver(Dataset.INSIDER_TRANSACTIONS, SOURCE, build(args.transactions, args.issuers))
    if args.issuers_out:
        args.issuers_out.write_text("\n".join(_lei(i) for i in range(args.issuers)) + "\n")
    print(f"Seeded {args.transactions} synthetic transactions for {args.issuers} issuers into {args.lake}")


if __name__ == "__main__":
    main()
