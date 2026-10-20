"""The read model's per-snapshot gold cache answers exactly like the lake's views (P7 performance)."""

from fastapi.testclient import TestClient
from test_api import lake  # noqa: F401 - shared synthetic lake fixture

from disclosure_atlas.api import create_app
from disclosure_atlas.api.readmodel import ReadModel

PATHS = ["/v1/stats/overview", "/v1/insider-transactions?limit=2", "/v1/issuers", "/v1/stats/flow", "/v1/sources"]


def test_cached_and_uncached_reads_agree(lake, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ATLAS_GOLD_CACHE", "0")
    direct = TestClient(create_app(str(lake.root)))
    expected = {path: direct.get(path).json() for path in PATHS}
    monkeypatch.setenv("ATLAS_GOLD_CACHE", "1")
    cached = TestClient(create_app(str(lake.root)))
    assert {path: cached.get(path).json() for path in PATHS} == expected


def test_cache_is_built_once_per_snapshot(lake):  # noqa: F811
    model = ReadModel(lake, version_ttl=0, cache_gold=True)
    with model.snapshot() as first:
        version = first.version
        count = first.one("SELECT count(*) AS n FROM insider_transactions")["n"]
    cache = model._cache
    with model.snapshot() as again:
        assert again.one("SELECT count(*) AS n FROM insider_transactions")["n"] == count
    assert model._cache is cache and cache[0] == version
    with model.snapshot(as_of=int(version)) as pinned:  # time travel bypasses the cache
        assert pinned.pinned and pinned.one("SELECT count(*) AS n FROM insider_transactions")["n"] == count
