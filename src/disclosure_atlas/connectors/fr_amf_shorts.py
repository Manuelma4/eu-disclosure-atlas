"""France - Autorité des marchés financiers (AMF) public net short positions.

Source notes and quirks: docs/sources/fr_amf_shorts.md. In short:
- One open-data CSV on data.gouv.fr with every position published since 1 November 2012; its file name
  changes with every refresh, so the current URL is read from the data.gouv.fr dataset API.
- ';'-separated, quoted; holder LEI included; publication start and end dates per position.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import date, datetime

import pytz

from ._shorts import Row, ShortsConnector, parse_pct, to_date
from .base import RawDocument, register

PARIS = pytz.timezone("Europe/Paris")
DATASET_API = "https://www.data.gouv.fr/api/1/datasets/62738e33f1be79935d3e5553/"


def decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252")


@register
class FranceAMFShorts(ShortsConnector):
    name = "fr_amf_shorts"
    jurisdiction = "FR"
    authority = "Autorité des marchés financiers (AMF)"
    homepage = "https://www.data.gouv.fr/datasets/historique-des-positions-courtes-nettes-sur-actions-rendues-publiques-depuis-le-1er-novembre-2012"

    def fetch(self, start: date, end: date) -> Iterator[RawDocument]:
        dataset = self.http.get(DATASET_API).json()
        resource = next(r for r in dataset["resources"] if (r.get("format") or "").lower() == "csv")
        response = self.http.get(resource["url"])
        yield self.document(str(response.url), response, {"resource": resource["id"]}, start, end, ".csv",
                            last_modified=resource.get("last_modified"))

    def rows(self, doc: RawDocument) -> Iterator[Row]:
        for row in csv.DictReader(io.StringIO(decode(doc.content)), delimiter=";"):
            pct, below = parse_pct(row.get("Ratio"))
            published = to_date(row.get("Date de debut de publication position"))
            yield Row(
                holder=row.get("Detenteur de la position courte nette"),
                holder_lei=row.get("Legal Entity Identifier detenteur"),
                issuer=row.get("Emetteur / issuer"),
                isin=row.get("code ISIN"),
                pct=pct,
                below_threshold=below,
                position_date=to_date(row.get("Date de debut position")),
                published_at=published_at(published),
            )


def published_at(day: date | None) -> datetime | None:
    """The AMF gives the publication day only; stored as midnight Paris time."""
    return PARIS.localize(datetime(day.year, day.month, day.day)) if day else None
