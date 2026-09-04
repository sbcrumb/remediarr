"""Tests for BLOCKLIST_ON_REPLACE: sonarr.blocklist_current_releases and
radarr.blocklist_current_release. These call real Sonarr/Radarr history +
mark-as-failed endpoints in production; here _history_for_* and
mark_history_failed are monkeypatched so the tests exercise only the
target-selection logic (the part that was actually buggy)."""

import asyncio

import pytest

from app.services import sonarr as S
from app.services import radarr as R
from app.services import arr_history as H


def _grabbed(hid, episode_id, download_id, date):
    return {"id": hid, "eventType": "grabbed", "episodeId": episode_id,
             "downloadId": download_id, "date": date}


def _imported(episode_id, download_id, date):
    return {"eventType": "downloadFolderImported", "episodeId": episode_id,
             "downloadId": download_id, "date": date}


def _grabbed_movie(hid, download_id, date):
    return {"id": hid, "eventType": "grabbed", "downloadId": download_id, "date": date}


def _imported_movie(download_id, date):
    return {"eventType": "downloadFolderImported", "downloadId": download_id, "date": date}


@pytest.fixture
def blocked(monkeypatch):
    """Records (arr_name, history_id) for every mark_history_failed call and
    always reports success, without making a real HTTP call."""
    calls = []

    async def fake_mark(client, api_base, headers, history_id, arr_name):
        calls.append((arr_name, history_id))
        return True

    monkeypatch.setattr(H, "mark_history_failed", fake_mark)
    return calls


def test_sonarr_multi_episode_no_import_history_blocklists_both(monkeypatch, blocked):
    # Bug: previously the "no import event" fallback took only the FIRST
    # matching grab across the whole batch, then broke out of the loop
    # entirely — leaving other episodes' releases un-blocklisted.
    events = [
        _grabbed(101, episode_id=1, download_id="dl-a", date="2026-01-02T00:00:00Z"),
        _grabbed(102, episode_id=2, download_id="dl-b", date="2026-01-01T00:00:00Z"),
    ]

    async def fake_history(series_id):
        return events

    monkeypatch.setattr(S, "_history_for_series", fake_history)

    blocked_count = asyncio.run(S.blocklist_current_releases(series_id=1, episode_ids=[1, 2]))
    assert blocked_count == 2
    assert set(blocked) == {("Sonarr", 101), ("Sonarr", 102)}


def test_sonarr_partial_import_coverage_still_blocklists_both(monkeypatch, blocked):
    # Bug: one episode having an import record made `imported_dls` non-empty,
    # which switched the matching mode for the WHOLE batch and silently
    # dropped the episode that had no import record of its own.
    events = [
        _imported(episode_id=1, download_id="dl-a", date="2026-01-02T00:00:00Z"),
        _grabbed(201, episode_id=1, download_id="dl-a", date="2026-01-01T00:00:00Z"),
        # Episode 2 has no import event at all — only ever grabbed.
        _grabbed(202, episode_id=2, download_id="dl-b", date="2026-01-01T00:00:00Z"),
    ]

    async def fake_history(series_id):
        return events

    monkeypatch.setattr(S, "_history_for_series", fake_history)

    blocked_count = asyncio.run(S.blocklist_current_releases(series_id=1, episode_ids=[1, 2]))
    assert blocked_count == 2
    assert set(blocked) == {("Sonarr", 201), ("Sonarr", 202)}


def test_sonarr_season_pack_shared_download_id_blocklisted_once(monkeypatch, blocked):
    events = [
        _imported(episode_id=1, download_id="dl-pack", date="2026-01-02T00:00:01Z"),
        _imported(episode_id=2, download_id="dl-pack", date="2026-01-02T00:00:02Z"),
        _grabbed(301, episode_id=1, download_id="dl-pack", date="2026-01-01T00:00:00Z"),
    ]

    async def fake_history(series_id):
        return events

    monkeypatch.setattr(S, "_history_for_series", fake_history)

    blocked_count = asyncio.run(S.blocklist_current_releases(series_id=1, episode_ids=[1, 2]))
    assert blocked_count == 1
    assert blocked == [("Sonarr", 301)]


def test_sonarr_no_matching_history_blocklists_nothing(monkeypatch, blocked):
    async def fake_history(series_id):
        return [_grabbed(401, episode_id=99, download_id="dl-x", date="2026-01-01T00:00:00Z")]

    monkeypatch.setattr(S, "_history_for_series", fake_history)

    blocked_count = asyncio.run(S.blocklist_current_releases(series_id=1, episode_ids=[1]))
    assert blocked_count == 0
    assert blocked == []


def test_radarr_normal_import_blocklists_it(monkeypatch, blocked):
    events = [
        _imported_movie(download_id="dl-a", date="2026-01-02T00:00:00Z"),
        _grabbed_movie(501, download_id="dl-a", date="2026-01-01T00:00:00Z"),
    ]

    async def fake_history(movie_id):
        return events

    monkeypatch.setattr(R, "_history_for_movie", fake_history)

    blocked_count = asyncio.run(R.blocklist_current_release(movie_id=1))
    assert blocked_count == 1
    assert blocked == [("Radarr", 501)]


def test_radarr_newest_import_missing_download_id_gives_up(monkeypatch, blocked):
    # Bug: the old code kept walking PAST an import with no downloadId (e.g. a
    # manual import) to an OLDER import that did have one — blocklisting an
    # already-superseded release instead of doing nothing. Correct behavior is
    # to give up rather than risk blocklisting the wrong release.
    events = [
        _imported_movie(download_id="", date="2026-01-03T00:00:00Z"),   # newest, no id
        _imported_movie(download_id="dl-old", date="2026-01-02T00:00:00Z"),  # stale
        _grabbed_movie(601, download_id="dl-old", date="2026-01-01T00:00:00Z"),
    ]

    async def fake_history(movie_id):
        return events

    monkeypatch.setattr(R, "_history_for_movie", fake_history)

    blocked_count = asyncio.run(R.blocklist_current_release(movie_id=1))
    assert blocked_count == 0
    assert blocked == []


def test_radarr_no_import_falls_back_to_newest_grab(monkeypatch, blocked):
    events = [
        _grabbed_movie(701, download_id="dl-a", date="2026-01-01T00:00:00Z"),
    ]

    async def fake_history(movie_id):
        return events

    monkeypatch.setattr(R, "_history_for_movie", fake_history)

    blocked_count = asyncio.run(R.blocklist_current_release(movie_id=1))
    assert blocked_count == 1
    assert blocked == [("Radarr", 701)]
