"""Reuse terms of each source, as documented in docs/sources/<source>.md (the notes are the reference).

Every release carries these in its ``datapackage.json`` (Frictionless ``sources`` and ``licenses``) and in the
``sources`` table of the DuckDB file. A source missing here is still released, with a pointer to its note and
``licence: not stated``: add it below when its connector lands.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = "https://github.com/Manuelma4/eu-disclosure-atlas"
DOCS = Path(__file__).resolve().parents[3] / "docs"


@dataclass(frozen=True)
class SourceTerms:
    title: str
    licence: str
    """Short licence name: an SPDX-style id when the source uses a standard licence, otherwise a description."""
    licence_url: str | None
    attribution: str
    commercial_use: bool | None
    """True: allowed; False: excluded by the source's terms; None: the source states no reuse licence."""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


_FI = SourceTerms(
    title="Finansinspektionen (FI), Sweden",
    licence="FI open data terms: free reuse without special permission, attribution required",
    licence_url="https://www.fi.se/en/about-fi/about-fi.se/open-data/",
    attribution="Source: Finansinspektionen (FI) and the release date",
    commercial_use=True,
)


def _no_licence(title: str, attribution: str, url: str | None) -> SourceTerms:
    return SourceTerms(
        title=title,
        licence="not stated: publication required by law, no reuse licence given",
        licence_url=url,
        attribution=attribution,
        commercial_use=None,
    )


SOURCE_TERMS: dict[str, SourceTerms] = {
    "se_fi_pdmr": _FI,
    "se_fi_shorts": _FI,
    "fr_amf_pdmr": SourceTerms(
        title="Autorité des marchés financiers (AMF), France: BDIF managers' transactions",
        licence="AMF BDIF legal notice: reproduction with source and date, commercial use excluded",
        licence_url="https://bdif.amf-france.org/fr/mentions-legales",
        attribution="©Autorité des marchés financiers – droits réservés, with the release date",
        commercial_use=False,
    ),
    "fr_amf_shorts": SourceTerms(
        title="Autorité des marchés financiers (AMF), France: net short positions on data.gouv.fr",
        licence="etalab-2.0",
        licence_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
        attribution="AMF, data.gouv.fr, and the release date",
        commercial_use=True,
    ),
    "de_bafin_shorts": _no_licence(
        "BaFin, Germany: net short positions via the Bundesanzeiger", "Bundesanzeiger", None
    ),
    "es_cnmv_shorts": _no_licence(
        "Comisión Nacional del Mercado de Valores (CNMV), Spain: net short positions",
        "CNMV",
        "https://www.cnmv.es/portal/utilidades/NotaLegal.aspx",
    ),
    "nl_afm_shorts": _no_licence("Autoriteit Financiële Markten (AFM), Netherlands: net short positions", "AFM", None),
    "ie_cbi_shorts": _no_licence(
        "Central Bank of Ireland (CBI): net short positions", "Central Bank of Ireland", None
    ),
}

ECB_FX = SourceTerms(
    title="European Central Bank: euro foreign exchange reference rates (used for amount_eur)",
    licence="ECB copyright notice: free use, the ECB cited as the source, changes stated explicitly",
    licence_url="https://www.ecb.europa.eu/home/disclaimer/html/index.en.html",
    attribution="Source: European Central Bank; amount_eur is derived (converted by the Atlas)",
    commercial_use=True,
)


def terms_for(source: str) -> SourceTerms:
    return SOURCE_TERMS.get(source) or _no_licence(source, source, None)


def source_note_url(source: str) -> str:
    return f"{REPO}/blob/main/docs/sources/{source}.md"


def terms_note(source: str, docs: Path = DOCS) -> str | None:
    """The "Terms" row of the source note, as written there (None outside a repository checkout)."""
    path = docs / "sources" / f"{source}.md"
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if match := re.match(r"\|\s*Terms\s*\|(.*)\|\s*$", line):
            return match.group(1).strip()
    return None
