"""Tests for the ambiguous-instance case confirmed against real Seerr data:
a title that exists on both a standard and 4K instance reports BOTH
media.serviceId and media.serviceId4k on the same issue, with nothing on the
issue itself saying which version the report concerns. Remediarr asks the
reporter to clarify (via a comment reply) rather than guessing."""

import asyncio

import pytest

from app.webhooks import handlers as H


# --- Pure helpers -------------------------------------------------------

def test_quality_hint_detects_4k():
    assert H._quality_hint_from_text("this is the 4k version") == "4k"
    assert H._quality_hint_from_text("UHD copy is broken") == "4k"


def test_quality_hint_detects_standard():
    assert H._quality_hint_from_text("the 1080p one") == "standard"
    assert H._quality_hint_from_text("standard version") == "standard"


def test_quality_hint_none_when_absent():
    assert H._quality_hint_from_text("wrong movie") is None
    assert H._quality_hint_from_text("") is None
    assert H._quality_hint_from_text(None) is None


def test_resolve_instance_for_action_unambiguous_falls_back():
    # Only one of serviceId/serviceId4k present — no ambiguity, normal resolution.
    instance, needs_clar = H._resolve_instance_for_action({"serviceId": 1}, "wrong movie")
    assert (instance, needs_clar) == (1, False)


def test_resolve_instance_for_action_same_index_not_ambiguous():
    # Both point at the same instance — nothing to disambiguate.
    instance, needs_clar = H._resolve_instance_for_action(
        {"serviceId": 0, "serviceId4k": 0}, "wrong movie"
    )
    assert (instance, needs_clar) == (0, False)


def test_resolve_instance_for_action_ambiguous_no_hint():
    instance, needs_clar = H._resolve_instance_for_action(
        {"serviceId": 0, "serviceId4k": 2}, "Wrong Movie"
    )
    assert needs_clar is True
    assert instance is None


def test_resolve_instance_for_action_ambiguous_with_4k_hint():
    instance, needs_clar = H._resolve_instance_for_action(
        {"serviceId": 0, "serviceId4k": 2}, "the 4k copy is broken"
    )
    assert (instance, needs_clar) == (2, False)


def test_resolve_instance_for_action_ambiguous_with_standard_hint():
    instance, needs_clar = H._resolve_instance_for_action(
        {"serviceId": 0, "serviceId4k": 2}, "standard version"
    )
    assert (instance, needs_clar) == (0, False)


# --- End-to-end through handle_jellyseerr --------------------------------

def _stub_common(monkeypatch, media, last_comment):
    async def fake_fetch_issue(issue_id):
        return {"media": media}

    async def fake_last_human_comment(issue_id):
        return last_comment

    monkeypatch.setattr(H, "jelly_fetch_issue", fake_fetch_issue)
    monkeypatch.setattr(H, "jelly_last_human_comment", fake_last_human_comment)
    monkeypatch.setattr(H, "_under_cooldown", lambda issue_id: False)
    monkeypatch.setattr(H, "_bump_cooldown", lambda issue_id: None)


def test_handle_jellyseerr_asks_for_clarification_when_ambiguous(monkeypatch):
    comments = []

    async def fake_comment(issue_id, text):
        comments.append(text)

    monkeypatch.setattr(H, "jelly_comment", fake_comment)
    media = {"mediaType": "movie", "tmdbId": 555, "serviceId": 0, "serviceId4k": 2, "title": "Motor City"}
    _stub_common(monkeypatch, media, "Wrong Movie")

    payload = {"issue": {"issue_id": 10}, "media": media}
    result = asyncio.run(H.handle_jellyseerr(payload))

    assert "ambiguous" in result["detail"]
    assert comments and "4K" in comments[0] and "Motor City" in comments[0]
    assert 10 in H._PENDING_INSTANCE_CLARIFICATION
    H._PENDING_INSTANCE_CLARIFICATION.clear()


def test_handle_jellyseerr_resumes_after_clarification_reply(monkeypatch):
    # Simulate keyword-scan mode: the reply ("4k") wouldn't match any bucket
    # keyword on its own — must reuse the bucket remembered from the first ask.
    monkeypatch.setattr(H.cfg, "ISSUE_TYPE_AS_BUCKET", False)
    H._PENDING_INSTANCE_CLARIFICATION[11] = {"bucket": "video", "title": "Motor City"}

    seen = {}

    async def fake_get_movie_by_tmdb(tmdb, instance=0):
        seen["instance"] = instance
        return {"id": 1657, "title": "Motor City"}

    async def fake_handle_movie(issue_id, movie, bucket, instance=0):
        seen["handled"] = (bucket, instance)

    monkeypatch.setattr(H.R, "get_movie_by_tmdb", fake_get_movie_by_tmdb)
    monkeypatch.setattr(H, "_handle_movie", fake_handle_movie)

    media = {"mediaType": "movie", "tmdbId": 555, "serviceId": 0, "serviceId4k": 2, "title": "Motor City"}
    _stub_common(monkeypatch, media, "4k")

    payload = {"issue": {"issue_id": 11}, "media": media}
    result = asyncio.run(H.handle_jellyseerr(payload))

    assert seen["instance"] == 2
    assert seen["handled"] == ("video", 2)
    assert result["detail"] == "movie handled: video"
    assert 11 not in H._PENDING_INSTANCE_CLARIFICATION


def test_handle_jellyseerr_unambiguous_single_instance_unaffected(monkeypatch):
    # Regression guard: a normal single-instance (or single-serviceId) report
    # must not be affected by any of this new logic.
    async def fake_get_movie_by_tmdb(tmdb, instance=0):
        return {"id": 42, "title": "Solo Movie"}

    handled = {}

    async def fake_handle_movie(issue_id, movie, bucket, instance=0):
        handled["instance"] = instance

    monkeypatch.setattr(H.R, "get_movie_by_tmdb", fake_get_movie_by_tmdb)
    monkeypatch.setattr(H, "_handle_movie", fake_handle_movie)

    media = {"mediaType": "movie", "tmdbId": 999, "serviceId": 0, "title": "Solo Movie"}
    _stub_common(monkeypatch, media, "wrong movie")

    payload = {"issue": {"issue_id": 12}, "media": media}
    result = asyncio.run(H.handle_jellyseerr(payload))

    assert handled["instance"] == 0
    assert result["detail"] == "movie handled: wrong"
