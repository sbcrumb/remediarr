"""Tests for health.sonarr_ok/radarr_ok checking every configured instance,
not just instance 0 — the gap the maintainer caught after actually running
a multi-instance config: health checks knew nothing about SONARR_URL_1/etc."""

import asyncio

import pytest

from app.services import health as H
from app.services import sonarr as S
from app.services import radarr as R
from app.services import arr_instances as I


def _fake_instances(count, name="sonarr"):
    """count=2 -> {0: ArrInstance(...), 1: ArrInstance(...)}"""
    return {
        i: I.ArrInstance(index=i, base=f"http://{name}-{i}:8989/api/v3", headers={}, timeout=60)
        for i in range(count)
    }


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    # Real retry/delay config would make a failing-instance test take
    # STARTUP_HEALTH_CHECK_RETRIES * STARTUP_HEALTH_CHECK_DELAY seconds.
    from app.config import cfg
    monkeypatch.setattr(cfg, "STARTUP_HEALTH_CHECK_RETRIES", 1)
    monkeypatch.setattr(cfg, "STARTUP_HEALTH_CHECK_DELAY", 0)


def test_single_instance_keeps_plain_label(monkeypatch):
    monkeypatch.setattr(S, "instances", lambda: _fake_instances(1))

    async def fake_ping(url, headers):
        return True, "200"

    monkeypatch.setattr(H, "_ping_json", fake_ping)

    results = asyncio.run(H.sonarr_ok())

    assert set(results.keys()) == {"sonarr"}
    assert results["sonarr"] == (True, "200")


def test_multiple_instances_get_numbered_labels(monkeypatch):
    monkeypatch.setattr(S, "instances", lambda: _fake_instances(3))

    async def fake_ping(url, headers):
        return True, "200"

    monkeypatch.setattr(H, "_ping_json", fake_ping)

    results = asyncio.run(H.sonarr_ok())

    assert set(results.keys()) == {"sonarr", "sonarr_1", "sonarr_2"}


def test_each_instance_checked_against_its_own_url(monkeypatch):
    monkeypatch.setattr(S, "instances", lambda: _fake_instances(2))
    seen_urls = []

    async def fake_ping(url, headers):
        seen_urls.append(url)
        return True, "200"

    monkeypatch.setattr(H, "_ping_json", fake_ping)

    asyncio.run(H.sonarr_ok())

    assert sorted(seen_urls) == [
        "http://sonarr-0:8989/api/v3/system/status",
        "http://sonarr-1:8989/api/v3/system/status",
    ]


def test_one_instance_down_does_not_hide_the_others(monkeypatch):
    monkeypatch.setattr(R, "instances", lambda: _fake_instances(2, name="radarr"))

    async def fake_ping(url, headers):
        if "radarr-1" in url:
            return False, "Connection refused"
        return True, "200"

    monkeypatch.setattr(H, "_ping_json", fake_ping)

    results = asyncio.run(H.radarr_ok())

    assert results["radarr"] == (True, "200")
    ok, detail = results["radarr_1"]
    assert ok is False
    assert "Connection refused" in detail


def test_label_helper():
    assert H._label("sonarr", 0) == "sonarr"
    assert H._label("sonarr", 1) == "sonarr_1"
    assert H._label("radarr", 2) == "radarr_2"
