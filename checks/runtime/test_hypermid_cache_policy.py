import hashlib

from gideon.hypermid.cache import (
    CachedRegion,
    CacheGeneration,
    CacheStore,
    CacheTransition,
)


def _region(data: bytes, ids: tuple[str, ...], mass: int) -> CachedRegion:
    return CachedRegion(hashlib.sha256(data).hexdigest(), data, ids, (), mass)


def _generation(number: int, baseline: bytes, delta: bytes) -> CacheGeneration:
    return CacheGeneration(
        number,
        hashlib.sha256(b"provider").hexdigest(),
        1,
        _region(baseline, ("base-1",), 5),
        _region(delta, ("delta-1",) if delta else (), 2 if delta else 0),
        _region(b"tail", ("tail-1",), 1),
    )


def test_cache_prefix_is_byte_stable_and_cached_decay_requires_boundary() -> None:
    store = CacheStore()
    store.apply(CacheTransition(_generation(1, b"stable-baseline", b"delta-one")))
    first = store.replay_prefix()
    store.apply(CacheTransition(_generation(1, b"stable-baseline", b"delta-two")))
    second = store.replay_prefix()
    assert first is not None and second is not None
    assert first[0] == second[0]
    assert first[1] != second[1]
    refused = store.apply(
        CacheTransition(
            _generation(1, b"stable-baseline", b"delta-two"), "tier_decay", None, True
        )
    )
    assert refused.kind == "pressure_refused"
    outcome = store.apply(
        CacheTransition(
            _generation(2, b"stable-baseline+delta-two", b""),
            "tier_decay",
            "tail_pressure",
            True,
        )
    )
    assert outcome.kind == "applied"
    assert outcome.generation == 2
