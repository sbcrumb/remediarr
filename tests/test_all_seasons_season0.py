"""Tests for the 'All Seasons' sentinel (problemSeason=0) in _tv_episode_from_payload."""

import asyncio

import pytest

from app.services import sonarr as S
from app.webhooks import handlers as H


SERIES = {"id": 42, "title": "Example Show"}


def _payload():
    return {
        "issue": {"problemSeason": 0, "problemEpisode": 0},
        "media": {"tvdbId": 12345},
    }


@pytest.fixture(autouse=True)
def _stub_series(monkeypatch):
    async def fake_get_series_by_tvdb(_tvdb_id):
        return SERIES
    monkeypatch.setattr(S, "get_series_by_tvdb", fake_get_series_by_tvdb)


def _stub_episodes(monkeypatch, episodes):
    async def fake_list_episodes(_series_id):
        return episodes
    monkeypatch.setattr(S, "list_episodes", fake_list_episodes)


def test_single_season_with_files_is_auto_targeted(monkeypatch):
    _stub_episodes(monkeypatch, [
        {"seasonNumber": 0, "hasFile": True},   # specials are ignored
        {"seasonNumber": 3, "hasFile": True},
        {"seasonNumber": 4, "hasFile": False},
    ])
    series_id, series, season, episode = asyncio.run(
        H._tv_episode_from_payload(_payload())
    )
    assert (series_id, season, episode) == (42, 3, 0)
    assert series is SERIES


def test_multiple_seasons_with_files_is_refused(monkeypatch):
    _stub_episodes(monkeypatch, [
        {"seasonNumber": 1, "hasFile": True},
        {"seasonNumber": 2, "hasFile": True},
        {"seasonNumber": 3, "hasFile": False},
    ])
    with pytest.raises(H.AllSeasonsAmbiguousError) as exc:
        asyncio.run(H._tv_episode_from_payload(_payload()))
    assert exc.value.season_count == 2
    assert exc.value.title == "Example Show"


def test_no_files_on_disk_raises_ambiguous_with_zero_count(monkeypatch):
    # Zero-count now goes through the same AllSeasonsAmbiguousError path as
    # the >1 case, so the caller comments + closes consistently either way
    # instead of silently ignoring the issue.
    _stub_episodes(monkeypatch, [
        {"seasonNumber": 1, "hasFile": False},
        {"seasonNumber": 2, "hasFile": False},
    ])
    with pytest.raises(H.AllSeasonsAmbiguousError) as exc:
        asyncio.run(H._tv_episode_from_payload(_payload()))
    assert exc.value.season_count == 0
    assert "nothing to remediate" in str(exc.value)


def test_season0_with_specific_episode_is_not_all_seasons_sentinel(monkeypatch):
    # season=0 is also the real "Specials" season number, not just the "All
    # Seasons" UI sentinel. A specific episode number means this is a genuine
    # Specials report and must NOT be routed through the all-seasons logic —
    # even when other seasons exist that would otherwise make it "ambiguous".
    _stub_episodes(monkeypatch, [
        {"seasonNumber": 1, "hasFile": True},
        {"seasonNumber": 2, "hasFile": True},
        {"seasonNumber": 0, "hasFile": True},
    ])
    payload = {
        "issue": {"problemSeason": 0, "problemEpisode": 5},
        "media": {"tvdbId": 12345},
    }
    series_id, series, season, episode = asyncio.run(
        H._tv_episode_from_payload(payload)
    )
    assert (series_id, season, episode) == (42, 0, 5)


def test_season0_specials_only_show_still_treated_as_all_seasons(monkeypatch):
    # No specific episode given (episode=None, not the all-episodes sentinel
    # either) alongside season=0 — still ambiguous-sentinel territory. Season
    # 0 itself is excluded from the "seasons with files" count, so a
    # specials-only show correctly reports "nothing to remediate" rather than
    # silently targeting season 0.
    _stub_episodes(monkeypatch, [
        {"seasonNumber": 0, "hasFile": True},
    ])
    payload = {
        "issue": {"problemSeason": 0},
        "media": {"tvdbId": 12345},
    }
    with pytest.raises(H.AllSeasonsAmbiguousError) as exc:
        asyncio.run(H._tv_episode_from_payload(payload))
    assert exc.value.season_count == 0
