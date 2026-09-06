"""Tests for PR 2 of v3: routing a remediation to the Sonarr/Radarr instance
Seerr actually reports (media.serviceId / serviceId4k) instead of always
hitting instance 0. This is the real production bug: a movie/series that
lives on a non-default instance (e.g. a "strm" Radarr) was never found
because remediarr always queried instance 0's Radarr."""

import asyncio

import pytest

from app.services import radarr as R
from app.services.arr_instances import InstanceNotConfiguredError
from app.webhooks import handlers as H


def test_resolve_instance_prefers_service_id():
    assert H._resolve_instance({"serviceId": 1, "serviceId4k": 2}) == 1


def test_resolve_instance_falls_back_to_service_id4k():
    assert H._resolve_instance({"serviceId4k": 2}) == 2


def test_resolve_instance_defaults_to_zero_when_absent():
    assert H._resolve_instance({}) == 0
    assert H._resolve_instance(None) == 0


def test_resolve_instance_ignores_non_numeric_junk():
    assert H._resolve_instance({"serviceId": "not-a-number"}) == 0


def test_resolve_instance_accepts_numeric_string():
    assert H._resolve_instance({"serviceId": "1"}) == 1


def test_movie_routes_to_reported_instance(monkeypatch):
    # The actual production bug: a movie living only on instance 1 (a "strm"
    # Radarr) must be looked up on instance 1, not instance 0.
    seen = {}

    async def fake_get_movie_by_tmdb(tmdb, instance=0):
        seen["instance"] = instance
        return {"id": 99, "title": "Strm Movie"} if instance == 1 else None

    monkeypatch.setattr(R, "get_movie_by_tmdb", fake_get_movie_by_tmdb)

    async def fake_handle_movie(issue_id, movie, bucket, instance=0):
        seen["handled_instance"] = instance

    monkeypatch.setattr(H, "_handle_movie", fake_handle_movie)

    async def fake_fetch_issue(issue_id):
        return {"media": {"mediaType": "movie", "tmdbId": 555, "serviceId": 1}}

    async def fake_last_human_comment(issue_id):
        return "no audio"

    async def fake_comment(issue_id, text):
        return None

    monkeypatch.setattr(H, "jelly_fetch_issue", fake_fetch_issue)
    monkeypatch.setattr(H, "jelly_last_human_comment", fake_last_human_comment)
    monkeypatch.setattr(H, "jelly_comment", fake_comment)
    monkeypatch.setattr(H, "_under_cooldown", lambda issue_id: False)
    monkeypatch.setattr(H, "_bump_cooldown", lambda issue_id: None)

    payload = {"issue": {"issue_id": 42}, "media": {"mediaType": "movie", "tmdbId": 555, "serviceId": 1}}
    result = asyncio.run(H.handle_jellyseerr(payload))

    assert seen["instance"] == 1
    assert seen["handled_instance"] == 1
    assert result["detail"] == "movie handled: audio"


def test_movie_on_unconfigured_instance_comments_but_stays_open(monkeypatch):
    async def fake_get_movie_by_tmdb(tmdb, instance=0):
        raise InstanceNotConfiguredError(f"Radarr instance {instance} is not configured")

    monkeypatch.setattr(R, "get_movie_by_tmdb", fake_get_movie_by_tmdb)

    comments = []

    async def fake_comment(issue_id, text):
        comments.append(text)

    async def fake_close(issue_id):
        raise AssertionError("must not close an issue for an unconfigured instance")

    async def fake_fetch_issue(issue_id):
        return {"media": {"mediaType": "movie", "tmdbId": 555, "serviceId": 3}}

    async def fake_last_human_comment(issue_id):
        return "no audio"

    monkeypatch.setattr(H, "jelly_fetch_issue", fake_fetch_issue)
    monkeypatch.setattr(H, "jelly_last_human_comment", fake_last_human_comment)
    monkeypatch.setattr(H, "jelly_comment", fake_comment)
    monkeypatch.setattr(H, "jelly_close", fake_close)
    monkeypatch.setattr(H, "_under_cooldown", lambda issue_id: False)
    monkeypatch.setattr(H, "_bump_cooldown", lambda issue_id: None)

    payload = {"issue": {"issue_id": 42}, "media": {"mediaType": "movie", "tmdbId": 555, "serviceId": 3}}
    result = asyncio.run(H.handle_jellyseerr(payload))

    assert "not configured" in result["detail"]
    assert comments and "isn't configured" in comments[0]
