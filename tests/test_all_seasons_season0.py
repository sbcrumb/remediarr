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


def test_no_files_on_disk_is_skipped(monkeypatch):
    _stub_episodes(monkeypatch, [
        {"seasonNumber": 1, "hasFile": False},
        {"seasonNumber": 2, "hasFile": False},
    ])
    with pytest.raises(ValueError) as exc:
        asyncio.run(H._tv_episode_from_payload(_payload()))
    assert not isinstance(exc.value, H.AllSeasonsAmbiguousError)
    assert "nothing to remediate" in str(exc.value)
