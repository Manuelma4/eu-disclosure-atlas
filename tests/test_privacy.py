import stat

import pytest

from disclosure_atlas.privacy import is_legal_entity, load_key, pseudonym


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Example Family Holding AB", True),
        ("Example Robotics AB (publ)", True),
        ("Exemple Industries S.A.", True),
        ("Beispiel Verwaltungs GmbH", True),
        ("Voorbeeld Beheer B.V.", True),
        ("Esempio Capital S.r.l.", True),
        ("Example Invest Oy", True),
        ("Anna Example", False),
        ("Erik Exempelsson", False),
        ("Marie-Claire Exemple", False),
        ("", False),
    ],
)
def test_is_legal_entity(name, expected):
    assert is_legal_entity(name) is expected


def test_pseudonym_is_stable_scoped_and_keyed():
    a = pseudonym("Anna Example", scope="LEI1", key=b"k1")
    assert a.startswith("p_") and len(a) == 22
    assert pseudonym("anna\xa0EXAMPLE ", scope="LEI1", key=b"k1") == a
    assert pseudonym("Anna Example", scope="LEI2", key=b"k1") != a
    assert pseudonym("Anna Example", scope="LEI1", key=b"k2") != a
    assert "anna" not in a.casefold()


def test_load_key_persists_private_file(tmp_path):
    key = load_key(tmp_path)
    assert key == load_key(tmp_path)
    mode = stat.S_IMODE((tmp_path / ".pseudonym_key").stat().st_mode)
    assert mode == 0o600


def test_load_key_prefers_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_PSEUDONYM_KEY", "from-env")
    assert load_key(tmp_path) == b"from-env"
    assert not (tmp_path / ".pseudonym_key").exists()
