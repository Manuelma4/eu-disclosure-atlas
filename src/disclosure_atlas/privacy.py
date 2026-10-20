"""Pseudonymization of natural persons (GDPR data minimization; see docs/decisions/0002).

Managers (PDMRs) are natural persons by definition, so their names are replaced by a keyed
HMAC that is stable within one issuer. Closely associated persons can be legal entities
(e.g. a family holding company); those keep their name, natural persons do not.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from pathlib import Path

from .normalize import clean

KEY_ENV = "ATLAS_PSEUDONYM_KEY"

_LEGAL_FORM = re.compile(
    r"(?<![\w-])("
    r"ab|ab \(publ\)|aktiebolag|asa|as|a/s|aps|oy|oyj|hf|ehf|"
    r"gmbh|ag|kg|kgaa|se|ug|e\.?\s?v\.?|stiftung|"
    r"s\.?a\.?|s\.?a\.?s\.?u?|s\.?a\.?r\.?l\.?|eurl|sci|sca|scs|sc|sicav|sicaf|"
    r"b\.?v\.?|n\.?v\.?|c\.?v\.?|"
    r"ltd|limited|llc|llp|lp|plc|inc|corp|corporation|company|co\.?|"
    r"s\.?p\.?a\.?|s\.?r\.?l\.?|s\.?a\.?p\.?a\.?|s\.?l\.?|s\.?l\.?u\.?|"
    r"sp\. z o\.o\.|s\.?k\.?a\.?|kft|zrt|nyrt|"
    r"holding|holdings|invest|investment|investments|capital|partners|fund|fonds|foundation|"
    r"fondation|stiftelse|trust|management|ventures|family office|förvaltning|fastigheter"
    r")(?![\w-])",
    re.IGNORECASE,
)


def normalize_name(name: str | None) -> str:
    return (clean(name) or "").casefold()


def is_legal_entity(name: str | None) -> bool:
    """Heuristic: a legal form or corporate keyword means a legal entity. Unknown -> natural person."""
    return bool(_LEGAL_FORM.search(clean(name) or ""))


def load_key(lake_root: Path) -> bytes:
    """Key from $ATLAS_PSEUDONYM_KEY, else a local random key persisted next to the lake."""
    if env := os.environ.get(KEY_ENV):
        return env.encode("utf-8")
    path = lake_root / ".pseudonym_key"
    if not path.exists():
        lake_root.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32), encoding="utf-8")
        path.chmod(0o600)
    return path.read_text(encoding="utf-8").strip().encode("utf-8")


def pseudonym(name: str | None, *, scope: str, key: bytes) -> str:
    """Stable id for a person within a scope (an issuer); different scopes give unlinkable ids."""
    message = f"{scope.casefold()}\x1f{normalize_name(name)}".encode()
    return "p_" + hmac.new(key, message, hashlib.sha256).hexdigest()[:20]
