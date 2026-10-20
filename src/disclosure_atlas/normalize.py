"""Source-independent normalization: text, numbers, dates, identifiers and vocabularies.

Vocabulary rules are keyword based and multilingual (EN/FR/DE/ES/IT/NL/SV) so new connectors
can reuse them; a connector with a closed vocabulary should still test its exact values.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from .models import InstrumentType, Nature, Role

_SPACES = re.compile(r"\s+")


def clean(text: str | None) -> str | None:
    """NFKC-normalize, turn non-breaking spaces into spaces, collapse whitespace; '' becomes None."""
    if text is None:
        return None
    text = _SPACES.sub(" ", unicodedata.normalize("NFKC", text)).strip()
    return text or None


def yes(text: str | None) -> bool:
    return (clean(text) or "").casefold() in {"yes", "y", "true", "1", "oui", "ja", "si", "sí", "x"}


def parse_number(text: str | None) -> float | None:
    """Parse '1 234,56', '1,234.56', '61.50' or '2750,0' into a float."""
    text = clean(text)
    if not text:
        return None
    text = text.replace(" ", "").replace("'", "")
    if "," in text and "." in text:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        text = text.replace("." if decimal == "," else ",", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def parse_datetime(text: str | None, fmt: str, tz: str) -> datetime | None:
    """Parse a local timestamp published by an authority and convert it to UTC."""
    text = clean(text)
    if not text:
        return None
    local = datetime.strptime(text, fmt).replace(tzinfo=ZoneInfo(tz))
    return local.astimezone(UTC)


def parse_date(text: str | None, fmt: str) -> date | None:
    text = clean(text)
    return datetime.strptime(text, fmt).date() if text else None


def _alnum_to_digits(code: str) -> str:
    return "".join(str(int(ch, 36)) for ch in code)


def valid_isin(code: str | None) -> str | None:
    """Return the upper-cased ISIN if its ISO 6166 check digit is valid, else None."""
    code = (clean(code) or "").upper().replace(" ", "")
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", code):
        return None
    digits = _alnum_to_digits(code[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            n = n - 9 if n > 9 else n
        total += n
    return code if (10 - total % 10) % 10 == int(code[-1]) else None


def valid_lei(code: str | None) -> str | None:
    """Return the upper-cased LEI if its ISO 17442 (mod 97-10) checksum is valid, else None."""
    code = (clean(code) or "").upper().replace(" ", "")
    if not re.fullmatch(r"[A-Z0-9]{18}[0-9]{2}", code):
        return None
    return code if int(_alnum_to_digits(code)) % 97 == 1 else None


def record_id(source: str, *parts: object) -> str:
    """Deterministic record id: the same source row always gets the same id across fetches."""
    payload = "\x1f".join([source, *("" if p is None else str(p) for p in parts)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# --- Roles -------------------------------------------------------------------------------

_DEPUTY = re.compile(
    r"\b(deputy|vice|adjoint|d[ée]l[ée]gu[ée]|stellvertret\w*|suppleant|vice-|vicepr[ée]sident\w*)\b", re.I
)
_HONORARY = re.compile(r"\b(d.honneur|honorary|honoraire)\b", re.I)
_OTHER_MEMBER = re.compile(r"\bother member of the company|\bautre membre\b", re.I)
_CEO = re.compile(
    r"\b(ceo|chief executive|managing director\w*|verkst[äa]llande direkt[öo]r|vd|directeur g[ée]n[ée]ral|"
    r"pdg|pr[ée]sident[- ]directeur|vorstandsvorsitz\w*|consejero delegado|amministratore delegato|"
    r"directrice g[ée]n[ée]rale|pr[ée]sidente?[- ]directrice|pr[ée]sidente? du directoire|g[ée]rante?s?|dg|"
    r"general manager)\b",
    re.I,
)
_CFO = re.compile(
    r"\b(cfo|chief financial|finance director|directeur financier|directrice financi[èe]re|"
    r"finanzvorstand|director financiero|direttore finanziario|ekonomichef|finanschef)\b",
    re.I,
)
_CHAIR = re.compile(
    r"\b(chair\w*|pr[ée]sident\w* du conseil|aufsichtsratsvorsitz\w*|presidente del consiglio|"
    r"presidente del consejo|ordf[öo]rande|voorzitter|pr[ée]sidente?)\b",
    re.I,
)
_BOARD = re.compile(
    r"\b(board|administrat(eur|rice)|membre du conseil|aufsichtsrat\w*|verwaltungsrat\w*|consejer[oa]|"
    r"consigliere|styrelse\w*|commissaris|supervisory|"
    r"adm(in|n|i)i?strat(eur|rice)s?|membres? d[eu] conseil|conseil ?d.administration|conseil de surv\w+|"
    r"conseil de g[ée]rance|membres? du ca)\b",
    re.I,
)
_EXECUTIVE = re.compile(
    r"\b(executive|officer|director|directeur|directrice|dirigeant\w*|vorstand\w*|management|"
    r"dirigente|ledande befattningshavare|directie|"
    r"comit[ée] ex[ée]cutif|comit[ée] de direction|directoire|comex|codir|secr[ée]taire g[ée]n[ée]ral\w*|"
    r"evp|svp|vp|coo|cto|cio|caio|drh|dga|head of|chief of staff)\b",
    re.I,
)
_ROLE_PRIORITY = [Role.CEO, Role.CFO, Role.CHAIR, Role.EXECUTIVE, Role.BOARD_MEMBER, Role.OTHER]
# The MAR template category contains a comma that must not split it into two roles.
_MAR_BODY = re.compile(r"administrative,\s+management", re.I)


def _role_of(part: str) -> Role:
    if _DEPUTY.search(part):
        return Role.BOARD_MEMBER if _BOARD.search(part) else Role.EXECUTIVE
    if _OTHER_MEMBER.search(part) or _HONORARY.search(part):
        return Role.OTHER
    for role, pattern in ((Role.CEO, _CEO), (Role.CFO, _CFO), (Role.CHAIR, _CHAIR), (Role.BOARD_MEMBER, _BOARD),
                          (Role.EXECUTIVE, _EXECUTIVE)):
        if pattern.search(part):
            return role
    return Role.OTHER


def normalize_role(raw: str | None, separators: str = r",|;|\n") -> Role:
    """Map a (possibly multi-role) position string to the most senior normalized role."""
    raw = _MAR_BODY.sub("administrative management", raw or "")
    parts = [p for p in (clean(x) for x in re.split(separators, raw)) if p]
    if not parts:
        return Role.UNKNOWN
    roles = {_role_of(p) for p in parts}
    return next(r for r in _ROLE_PRIORITY if r in roles)


# --- Nature of transaction ---------------------------------------------------------------

def _words(*alternatives: str) -> re.Pattern[str]:
    return re.compile(r"\b(" + "|".join(alternatives) + r")\b", re.I)


_INCREASE = _words("increase", "received", r"re[çc]u\w*", "augmentation", "erhalten", "zugang", r"recibid\w*",
                   r"ricevut\w*", "acquisition", r"r[ée]ception")
_DECREASE = _words("decrease", "given", r"donn[ée]\w*", "diminution", "abgang", "ceduto", r"donation [àa]")
_NEUTRAL = {Nature.LENDING, Nature.PLEDGE}
_NATURE_RULES: list[tuple[Nature, re.Pattern[str], int]] = [
    (Nature.INTERNAL_TRANSFER, re.compile(r"\binternal transaction|transfert interne", re.I), 0),
    (Nature.LENDING, _words("loan", "lending", r"pr[êe]t", "leihe", r"pr[ée]stamo", "prestito", r"l[åa]n"), 0),
    (Nature.PLEDGE, _words(r"pledg\w*", "nantissement", r"verpf[äa]ndung", r"pignoraci[óo]n", "pegno",
                           r"pants[äa]ttning"), 0),
    (Nature.GIFT, _words("gift", r"donations?", "schenkung", r"donaci[óo]n", r"g[åa]va", r"pr[ée]sent d.usage"), 0),
    (Nature.INHERITANCE, _words("inheritance", "succession", "erbschaft", "herencia", "arv"), 1),
    (Nature.EXERCISE, _words("exercise", "exercice", r"aus[üu]bung", "ejercicio", "esercizio", r"l[öo]sen"), 0),
    (Nature.CONVERSION, _words("conversion", "umwandlung", "konvertering"), 0),
    # A contribution in kind ("apport") hands the shares over, unless the text says they were received.
    (Nature.EXCHANGE, _words("apport", "contribution in kind"), -1),
    (Nature.EXCHANGE, _words("exchange", r"[ée]change", "tausch", "byte"), 0),
    (Nature.MERGER, _words("merger", "fusion", "verschmelzung"), 0),
    (Nature.GRANT, _words(r"actions? gratuites?", r"actions? de performance", r"free shares?",
                          r"performance shares?", "livraison"), 1),
    (Nature.SUBSCRIPTION, _words("subscription", "souscription", "zeichnung", r"suscripci[óo]n", "sottoscrizione",
                                 "teckning", "soucription"), 1),
    (Nature.GRANT, _words("allotment", r"grant\w*", "attribution", "zuteilung", r"asignaci[óo]n", "assegnazione",
                          "tilldelning"), 1),
    (Nature.BUY, _words("acquisition", "purchase", "buy", "achat", "acquisto", "erwerb", "kauf", "compra",
                        r"k[öo]p"), 1),
    (Nature.SELL, _words("disposal", "sale", "sell", "cession", "vente", r"ver[äa]u[ßs]erung", "verkauf", "venta",
                         "vendita", "avyttring", r"f[öo]rs[äa]ljning"), -1),
]


_WARRANT_NAME = re.compile(r"\bbons? de souscription\b", re.I)


def normalize_nature(raw: str | None) -> tuple[Nature, int]:
    """Return (nature, direction) where direction is +1 (holding increases), -1 or 0 (neutral/unknown)."""
    # "Acquisition de bons de souscription" buys warrants: the instrument name is not a subscription.
    text = _WARRANT_NAME.sub("BSA", clean(raw) or "")
    for nature, pattern, default_direction in _NATURE_RULES:
        if pattern.search(text):
            direction = default_direction
            if nature in _NEUTRAL:
                direction = 0
            elif nature is Nature.INTERNAL_TRANSFER:
                inner, _ = normalize_nature(pattern.sub("", text))
                direction = {Nature.BUY: 1, Nature.SELL: -1}.get(inner, 0)
            elif _INCREASE.search(text):
                direction = 1
            elif _DECREASE.search(text):
                direction = -1
            return nature, direction
    return Nature.OTHER, 0


# --- Instrument type ---------------------------------------------------------------------

_INSTRUMENT_RULES: list[tuple[InstrumentType, re.Pattern[str]]] = [
    # Units of employee savings funds and insurance contracts invested in the issuer's shares.
    (InstrumentType.OTHER, _words(r"fcpe?", "fie", "fonds", r"unit[ée]s de compte", "actionnariat")),
    (InstrumentType.CONVERTIBLE, _words("convertible", "wandelanleihe", r"obligations? convertibles?",
                                        r"oranes?", r"ornanes?", r"oc[ée]anes?", r"ocas?",
                                        r"obligations? remboursables?")),
    (InstrumentType.WARRANT, _words(r"warrant\w*", r"bons? de souscription", r"optionsschein\w*",
                                    r"teckningsoption\w*")),
    (InstrumentType.SUBSCRIPTION_RIGHT, _words("subscription right", "unit right", r"droits? pr[ée]f[ée]rentiels?",
                                               r"bezugsrecht\w*", r"tecknings?r[äa]tt\w*", r"unitr[äa]tt\w*")),
    (InstrumentType.OPTION, _words(r"option\w*", "call", "put", r"stock options?")),
    (InstrumentType.SWAP, _words("swap", "cfd", "contract for difference")),
    (InstrumentType.BOND, _words("bond", "obligation", "anleihe", "bono", r"obbligazion\w*", r"titres? de cr[ée]ance")),
    (InstrumentType.SHARE, _words("share", "shares", "action", "actions", r"aktie\w*", r"acci[óo]n\w*", r"azion\w*",
                                  r"aandel\w*", "bta", "btu", "interim share", r"depositary receipts?", "adrs?",
                                  "ads")),
]


def normalize_instrument(raw: str | None) -> InstrumentType:
    text = clean(raw) or ""
    for instrument, pattern in _INSTRUMENT_RULES:
        if pattern.search(text):
            return instrument
    return InstrumentType.OTHER
