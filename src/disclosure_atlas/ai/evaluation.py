"""Evaluation support for Ask the Atlas: a deterministic synthetic lake and execution-accuracy scoring.

The synthetic lake is fully fictitious (issuers, funds and managers are invented; managers are opaque ids)
and fixed by a seed, so golden answers computed on it are stable. See evals/ask/README.md.
"""

from __future__ import annotations

import math
import random
import re
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ..lake import Lake
from ..models import Dataset, InsiderTransaction, NetShortPosition
from ..normalize import _alnum_to_digits

SEED = 20261020
START, END = date(2025, 1, 1), date(2026, 9, 30)
FETCHED_AT = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)

# (name, country, currency, base price, legal entity used by closely associated persons)
ISSUERS = [
    ("Nordvik Robotics AB", "SE", "SEK", 180.0, "Nordvik Family Holding AB"),
    ("Fjallberg Energy AB", "SE", "SEK", 64.0, None),
    ("Solvik Pharma AB", "SE", "SEK", 412.0, "Solvik Invest AB"),
    ("Atelier Lumiere SA", "FR", "EUR", 38.5, "Lumiere Participations SAS"),
    ("Vignoble Industries SA", "FR", "EUR", 121.0, None),
    ("Rheinwerk Systems AG", "DE", "EUR", 87.0, "Rheinwerk Beteiligungs GmbH"),
    ("Bergmann Logistik AG", "DE", "EUR", 23.4, None),
    ("Polderdam Holding NV", "NL", "EUR", 55.0, None),
    ("Grachtenbank NV", "NL", "EUR", 14.2, "Gracht Capital BV"),
    ("Iberluz Renovables SA", "ES", "EUR", 9.8, None),
]
FUNDS = [
    "Aurora Capital Partners LLP", "Bluefin Asset Management Ltd", "Cormorant Fund Management LP",
    "Driftwood Investments LLC", "Eastgate Capital Management Ltd", "Foxglove Partners LLP",
]
ROLES = ["ceo", "cfo", "chair", "executive", "board_member", "board_member", "executive", "other"]
# nature, direction, instrument, on venue, weight
NATURES = [
    ("buy", 1, "share", True, 40), ("sell", -1, "share", True, 26), ("grant", 1, "option", False, 9),
    ("exercise", 1, "option", False, 6), ("subscription", 1, "share", False, 6), ("gift", -1, "share", False, 4),
    ("buy", 1, "share", False, 4), ("pledge", 0, "share", False, 2), ("sell", -1, "bond", True, 3),
]
LAG_BY_COUNTRY = {"SE": [1, 1, 2, 2, 3, 3, 4, 7], "FR": [2, 3, 3, 4, 5, 6, 9, 14], "DE": [1, 2, 2, 3, 3, 4, 6],
                  "NL": [1, 1, 2, 3, 3, 5], "ES": [2, 3, 4, 5, 6, 8, 12]}
SEK_PER_EUR = {2025: 11.2, 2026: 10.9}


def make_lei(prefix18: str) -> str:
    check = 98 - int(_alnum_to_digits(prefix18 + "00")) % 97
    return f"{prefix18}{check:02d}"


def make_isin(prefix11: str) -> str:
    digits = _alnum_to_digits(prefix11)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch) * (2 if i % 2 == 0 else 1)
        total += n - 9 if n > 9 else n
    return f"{prefix11}{(10 - total % 10) % 10}"


def issuer_lei(index: int) -> str:
    return make_lei(f"549300SYNTHATLS{index:03d}")


def _weekday_after(day: date, rng: random.Random, country: str) -> date:
    published = day + timedelta(days=rng.choice(LAG_BY_COUNTRY[country]))
    while published.weekday() >= 5:
        published += timedelta(days=1)
    return published


def synthetic_records() -> tuple[list[InsiderTransaction], list[NetShortPosition]]:
    rng = random.Random(SEED)
    span = (END - START).days
    transactions: list[InsiderTransaction] = []
    for i, (name, country, currency, base_price, holding) in enumerate(ISSUERS):
        lei = issuer_lei(i)
        isin = make_isin(f"{country}00SYN{i:04d}")
        managers = [(f"p{i:02d}{j:02d}", ROLES[j % len(ROLES)]) for j in range(4 + i % 4)]
        for n in range(30 + 5 * (i % 5)):
            day = START + timedelta(days=rng.randrange(span))
            while day.weekday() >= 5:
                day -= timedelta(days=1)
            nature, direction, instrument, on_venue, _ = rng.choices(NATURES, weights=[w[-1] for w in NATURES])[0]
            pdmr, role = rng.choice(managers)
            associated = holding is not None and rng.random() < 0.12
            volume = float(rng.choice([100, 250, 500, 1000, 2000, 5000, 10000, 25000]))
            price = round(base_price * (0.8 + 0.4 * rng.random()), 2)
            published = _weekday_after(day, rng, country)
            transactions.append(InsiderTransaction(
                record_id=f"syn-{i:02d}-{n:03d}",
                source=f"{country.lower()}_synthetic_pdmr",
                jurisdiction=country,
                source_url=f"https://example.invalid/{country.lower()}/{i}/{n}",
                published_at=datetime.combine(published, time(8 + n % 9, 15), tzinfo=UTC),
                transaction_date=day,
                issuer_name=name,
                issuer_lei=lei,
                pdmr_id=pdmr,
                pdmr_role=role,
                pdmr_role_raw=role.replace("_", " ").upper(),
                notifier_id=f"{pdmr}a" if associated else pdmr,
                notifier_name=holding if associated else None,
                notifier_is_legal_entity=associated,
                closely_associated=associated,
                nature=nature,
                nature_raw=nature.upper(),
                direction=direction,
                instrument_type=instrument,
                instrument_type_raw=instrument.upper(),
                instrument_name=f"{name} {instrument}",
                isin=isin,
                volume=volume,
                price=price,
                currency=currency,
                amount=round(volume * price, 2),
                venue="XSYN" if on_venue else "Outside a trading venue",
                on_venue=on_venue,
                status_raw="Current",
                fetched_at=FETCHED_AT,
                parser="synthetic/1",
            ))

    positions: list[NetShortPosition] = []
    shorted = [0, 3, 5, 6, 9]
    for f, fund in enumerate(FUNDS):
        for k, i in enumerate(shorted):
            if (f + k) % 3 == 2:
                continue
            name, country, *_ = ISSUERS[i]
            pct = round(0.5 + rng.random() * 1.5, 2)
            day = START + timedelta(days=rng.randrange(60))
            while day <= END:
                positions.append(NetShortPosition(
                    record_id=f"short-{f}-{i}-{day.isoformat()}",
                    source=f"{country.lower()}_synthetic_shorts",
                    jurisdiction=country,
                    published_at=datetime.combine(day + timedelta(days=1), time(17, 0), tzinfo=UTC),
                    position_date=day,
                    holder_name=fund,
                    holder_lei=make_lei(f"213800SYNFUND{f:05d}"),
                    issuer_name=name,
                    issuer_lei=issuer_lei(i),
                    isin=make_isin(f"{country}00SYN{i:04d}"),
                    position_pct=pct,
                    fetched_at=FETCHED_AT,
                    parser="synthetic/1",
                ))
                pct = round(max(0.0, pct + rng.choice([-0.2, -0.1, 0.1, 0.1, 0.2])), 2)
                day += timedelta(days=rng.randrange(20, 75))
                if pct < 0.5:
                    break
    return transactions, positions


def build_synthetic_lake(root: Path | str) -> Lake:
    """Write the synthetic dataset (silver plus ECB-style FX rates) into a fresh lake at `root`."""
    from ..reference.fx import SCHEMA

    lake = Lake(root)
    transactions, positions = synthetic_records()
    lake.write_silver(Dataset.INSIDER_TRANSACTIONS, "synthetic", transactions)
    lake.write_silver(Dataset.NET_SHORT_POSITIONS, "synthetic", positions)
    # One SEK rate per month (1 EUR = rate SEK); the gold ASOF join picks the latest one on or before each trade.
    rates = [{"date": date(year, month, 1), "currency": "SEK", "rate": round(SEK_PER_EUR[year] + month / 100, 4)}
             for year in sorted(SEK_PER_EUR) for month in range(1, 13)]
    lake.fx_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rates, schema=SCHEMA), lake.fx_path)
    return lake


# --- Execution accuracy -------------------------------------------------------------------------------------

_MONTH = re.compile(r"^(\d{4}-\d{2})(?:-01)?(?:[T ]00:00:00(?:\.0+)?(?:[+-]00:?00|Z)?)?$")
_MIDNIGHT = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ]00:00:00(?:\.0+)?(?:[+-]00:?00|Z)?$")


def _norm(value: Any) -> Any:
    """Comparable form: floats to cents, dates as text, a month start in any form as YYYY-MM."""
    if isinstance(value, datetime | date):
        value = value.isoformat()
    if isinstance(value, str):
        if month := _MONTH.match(value):
            return month.group(1)
        if day := _MIDNIGHT.match(value):
            return day.group(1)
        return value
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float):
        if isinstance(value, float) and math.isnan(value):
            return None
        rounded = round(float(value), 2)
        return 0.0 if rounded == 0 else rounded
    return str(value)


def _column_vectors(rows: list[list[Any]], width: int) -> list[Counter]:
    return [Counter(_norm(row[c]) for row in rows) for c in range(width)]


def results_match(expected: list[list[Any]], got: list[list[Any]], *, ordered: bool = False) -> bool:
    """Execution accuracy for one question: do the rows agree, up to column order, extra columns and
    rounding to cents? Each expected column is matched to a predicted column with the same values; then the
    projected rows must be equal (as a list when `ordered`, else as a multiset)."""
    if len(expected) != len(got):
        return False
    if not expected:
        return True
    width_e, width_g = len(expected[0]), len(got[0]) if got else 0
    expected_cols, got_cols = _column_vectors(expected, width_e), _column_vectors(got, width_g)
    mapping: list[int] = []
    for col in expected_cols:
        candidates = [g for g in range(width_g) if got_cols[g] == col and g not in mapping]
        if not candidates:
            return False
        mapping.append(candidates[0])
    exp_rows = [tuple(_norm(v) for v in row) for row in expected]
    got_rows = [tuple(_norm(row[g]) for g in mapping) for row in got]
    if ordered:
        return exp_rows == got_rows
    return Counter(exp_rows) == Counter(got_rows)
