"""Tests for app.services.arr_instances: multi-instance Sonarr/Radarr config
parsing. Instance 0 is always the plain <PREFIX>_URL/<PREFIX>_API_KEY vars
(matches today's single-instance behavior); SONARR_URL_1, SONARR_URL_2, ...
are additional, optional instances, matching Seerr's own 0-based
media.serviceId ordering."""

import pytest

from app.services import arr_instances as I
from app.services import sonarr as S
from app.services import radarr as R


def _clear(monkeypatch, prefix, indices):
    for i in indices:
        suffix = "" if i == 0 else f"_{i}"
        monkeypatch.delenv(f"{prefix}_URL{suffix}", raising=False)
        monkeypatch.delenv(f"{prefix}_API_KEY{suffix}", raising=False)
        monkeypatch.delenv(f"{prefix}_HTTP_TIMEOUT{suffix}", raising=False)


def test_instance_0_only_matches_existing_single_instance_behavior(monkeypatch):
    _clear(monkeypatch, "SONARR", range(4))
    monkeypatch.setenv("SONARR_URL", "http://sonarr:8989")
    monkeypatch.setenv("SONARR_API_KEY", "key0")

    instances = I.load_instances("SONARR")

    assert set(instances.keys()) == {0}
    assert instances[0].base == "http://sonarr:8989/api/v3"
    assert instances[0].headers == {"X-Api-Key": "key0"}
    assert instances[0].timeout == 60


def test_additional_instances_parsed_in_order(monkeypatch):
    _clear(monkeypatch, "SONARR", range(4))
    monkeypatch.setenv("SONARR_URL", "http://sonarr:8989")
    monkeypatch.setenv("SONARR_API_KEY", "key0")
    monkeypatch.setenv("SONARR_URL_1", "http://sonarr-strm:8989")
    monkeypatch.setenv("SONARR_API_KEY_1", "key1")

    instances = I.load_instances("SONARR")

    assert set(instances.keys()) == {0, 1}
    assert instances[1].base == "http://sonarr-strm:8989/api/v3"
    assert instances[1].headers == {"X-Api-Key": "key1"}


def test_trailing_slash_stripped(monkeypatch):
    _clear(monkeypatch, "RADARR", range(2))
    monkeypatch.setenv("RADARR_URL", "http://radarr:7878/")
    monkeypatch.setenv("RADARR_API_KEY", "key0")

    instances = I.load_instances("RADARR")

    assert instances[0].base == "http://radarr:7878/api/v3"


def test_gap_in_numbering_stops_at_first_missing(monkeypatch):
    # SONARR_URL_2 set without SONARR_URL_1 — reading stops at instance 1
    # (missing), instance 2 is never reached. Documented behavior, not a bug:
    # gaps aren't supported, matching Seerr's own contiguous ordering.
    _clear(monkeypatch, "SONARR", range(4))
    monkeypatch.setenv("SONARR_URL", "http://sonarr:8989")
    monkeypatch.setenv("SONARR_API_KEY", "key0")
    monkeypatch.setenv("SONARR_URL_2", "http://sonarr-orphan:8989")
    monkeypatch.setenv("SONARR_API_KEY_2", "key2")

    instances = I.load_instances("SONARR")

    assert set(instances.keys()) == {0}


def test_additional_instance_timeout_falls_back_to_instance_0s(monkeypatch):
    _clear(monkeypatch, "SONARR", range(2))
    monkeypatch.setenv("SONARR_URL", "http://sonarr:8989")
    monkeypatch.setenv("SONARR_API_KEY", "key0")
    monkeypatch.setenv("SONARR_HTTP_TIMEOUT", "30")
    monkeypatch.setenv("SONARR_URL_1", "http://sonarr-strm:8989")
    monkeypatch.setenv("SONARR_API_KEY_1", "key1")

    instances = I.load_instances("SONARR")

    assert instances[1].timeout == 30


def test_additional_instance_can_override_its_own_timeout(monkeypatch):
    _clear(monkeypatch, "SONARR", range(2))
    monkeypatch.setenv("SONARR_URL", "http://sonarr:8989")
    monkeypatch.setenv("SONARR_API_KEY", "key0")
    monkeypatch.setenv("SONARR_URL_1", "http://sonarr-strm:8989")
    monkeypatch.setenv("SONARR_API_KEY_1", "key1")
    monkeypatch.setenv("SONARR_HTTP_TIMEOUT_1", "120")

    instances = I.load_instances("SONARR")

    assert instances[1].timeout == 120


def test_no_api_key_yields_no_headers(monkeypatch):
    _clear(monkeypatch, "RADARR", range(2))
    monkeypatch.setenv("RADARR_URL", "http://radarr:7878")
    monkeypatch.delenv("RADARR_API_KEY", raising=False)

    instances = I.load_instances("RADARR")

    assert instances[0].headers == {}


def test_get_instance_missing_index_returns_none(monkeypatch):
    _clear(monkeypatch, "SONARR", range(2))
    monkeypatch.setenv("SONARR_URL", "http://sonarr:8989")
    monkeypatch.setenv("SONARR_API_KEY", "key0")

    instances = I.load_instances("SONARR")

    assert I.get_instance(instances, 1) is None
    assert I.get_instance(instances, 0) is instances[0]


def test_sonarr_unconfigured_instance_fails_loud_not_silent():
    # conftest.py only sets SONARR_URL/SONARR_API_KEY (instance 0) — no
    # SONARR_URL_1. Requesting instance 1 must raise clearly, not silently
    # fall back to instance 0 (that would be the exact bug this feature
    # exists to fix, just relocated).
    with pytest.raises(ValueError, match="Sonarr instance 1 is not configured"):
        S._instance(1)


def test_radarr_unconfigured_instance_fails_loud_not_silent():
    with pytest.raises(ValueError, match="Radarr instance 1 is not configured"):
        R._instance(1)


def test_instance_0_always_resolves():
    assert S._instance(0).base.endswith("/api/v3")
    assert R._instance(0).base.endswith("/api/v3")
