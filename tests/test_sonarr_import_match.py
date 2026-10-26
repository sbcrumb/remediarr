"""Tests for CONFIRM_REPLACEMENT_IMPORT — matching Sonarr "On Import" webhooks
to pending issues via (series_id, season, episode) keys, and the close dispatch."""

import asyncio

import pytest

from app.webhooks import handlers
from app.webhooks.handlers import _sonarr_import_keys


def test_single_import_yields_one_key():
    payload = {
        "eventType": "Download",
        "series": {"id": 12, "title": "From"},
        "episodes": [{"seasonNumber": 4, "episodeNumber": 6}],
    }
    assert _sonarr_import_keys(payload) == [(12, 4, 6)]


def test_batch_import_yields_all_keys():
    payload = {
        "eventType": "Download",
        "series": {"id": 7, "title": "Breaking Bad"},
        "episodes": [
            {"seasonNumber": 1, "episodeNumber": 1},
            {"seasonNumber": 1, "episodeNumber": 2},
        ],
    }
    assert _sonarr_import_keys(payload) == [(7, 1, 1), (7, 1, 2)]


def test_non_download_events_yield_nothing():
    # Test button, grabs, renames, health, etc. must not match anything.
    for ev in ("Test", "Grab", "Rename", "Health", ""):
        assert _sonarr_import_keys({"eventType": ev, "series": {"id": 1},
                                    "episodes": [{"seasonNumber": 1, "episodeNumber": 1}]}) == []


def test_any_download_import_matches_fresh_or_upgrade():
    # A new file landing for a pending episode is a plausible fix regardless of why
    # — a fresh import OR an upgrade yields keys (the upgrade path also backstops a
    # missed fresh-import webhook).
    base = {"eventType": "Download", "series": {"id": 12},
            "episodes": [{"seasonNumber": 4, "episodeNumber": 6}]}
    assert _sonarr_import_keys({**base, "isUpgrade": True}) == [(12, 4, 6)]
    assert _sonarr_import_keys({**base, "isUpgrade": False}) == [(12, 4, 6)]
    assert _sonarr_import_keys(base) == [(12, 4, 6)]  # absent (On Import Complete) too


def test_missing_or_malformed_fields_yield_nothing():
    assert _sonarr_import_keys({}) == []
    assert _sonarr_import_keys({"eventType": "Download"}) == []
    # No series id
    assert _sonarr_import_keys({"eventType": "Download", "series": {},
                               "episodes": [{"seasonNumber": 1, "episodeNumber": 1}]}) == []
    # Episode without season/episode numbers is skipped
    assert _sonarr_import_keys({"eventType": "Download", "series": {"id": 3},
                               "episodes": [{"title": "x"}]}) == []


# --- Integration: the close dispatch (network mocked) -----------------------

@pytest.fixture
def mocked_io(monkeypatch):
    """Replace the Seerr/notify IO with recorders and reset the pending map."""
    calls = {"comment": [], "close": [], "notify": []}

    async def fake_comment(issue_id, text):
        calls["comment"].append((issue_id, text))

    async def fake_close(issue_id):
        calls["close"].append(issue_id)
        return True

    async def fake_notify(title, message):
        calls["notify"].append((title, message))

    monkeypatch.setattr(handlers, "jelly_comment", fake_comment)
    monkeypatch.setattr(handlers, "jelly_close", fake_close)
    monkeypatch.setattr(handlers, "notify", fake_notify)
    monkeypatch.setattr(handlers, "CLOSE_ISSUES", True)
    monkeypatch.setattr(handlers, "COMMENT_ON_ACTION", True)
    handlers._PENDING_IMPORTS.clear()
    handlers._PENDING_IMPORT_GROUPS.clear()
    yield calls
    handlers._PENDING_IMPORTS.clear()
    handlers._PENDING_IMPORT_GROUPS.clear()


def test_import_confirms_pending_issue_comments_and_closes(mocked_io):
    handlers._register_pending_import(12, 4, 6, 777, "From")
    payload = {"eventType": "Download", "series": {"id": 12, "title": "From"},
               "episodes": [{"seasonNumber": 4, "episodeNumber": 6}]}

    res = asyncio.run(handlers.handle_sonarr_import(payload))

    assert res["detail"].startswith("import handled")
    assert mocked_io["close"] == [777]                      # closed the right issue
    assert mocked_io["comment"] and mocked_io["comment"][0][0] == 777
    assert "imported" in mocked_io["comment"][0][1].lower()  # the success message
    assert mocked_io["notify"]                               # alert fired
    assert (12, 4, 6) not in handlers._PENDING_IMPORTS       # pending entry popped


def test_import_for_unknown_episode_is_noop(mocked_io):
    handlers._register_pending_import(12, 4, 6, 777, "From")  # pending for E06
    payload = {"eventType": "Download", "series": {"id": 12, "title": "From"},
               "episodes": [{"seasonNumber": 4, "episodeNumber": 7}]}  # E07 imports

    res = asyncio.run(handlers.handle_sonarr_import(payload))

    assert res["detail"].startswith("ignored")
    assert mocked_io["close"] == []                           # nothing closed
    assert mocked_io["comment"] == []
    assert (12, 4, 6) in handlers._PENDING_IMPORTS            # E06 still pending


def test_duplicate_import_webhook_closes_once(mocked_io):
    # On Import + On Import Complete can both fire for the same episode; the
    # second must be a no-op (pending entry already popped).
    handlers._register_pending_import(12, 4, 6, 777, "From")
    payload = {"eventType": "Download", "series": {"id": 12, "title": "From"},
               "episodes": [{"seasonNumber": 4, "episodeNumber": 6}]}

    asyncio.run(handlers.handle_sonarr_import(payload))
    asyncio.run(handlers.handle_sonarr_import(payload))  # duplicate

    assert mocked_io["close"] == [777]                       # closed exactly once


def test_handle_tv_confirm_mode_registers_and_does_not_close(mocked_io, monkeypatch):
    # The first half of the flow: in confirm mode a delete-bucket remediation
    # deletes + searches, posts the interim comment, registers the pending
    # entry, and must NOT close yet.
    async def fake_delete(series_id, episode_ids, instance=0):
        return len(episode_ids)

    async def fake_search(episode_ids, instance=0):
        return None

    monkeypatch.setattr(handlers.S, "delete_episodefiles", fake_delete)
    monkeypatch.setattr(handlers.S, "trigger_episode_search", fake_search)
    monkeypatch.setattr(handlers.cfg, "CONFIRM_REPLACEMENT_IMPORT", True)

    series = {"id": 12, "title": "From", "tvdbId": 111}
    asyncio.run(handlers._handle_tv(777, series, 4, 6, [555], "audio"))

    assert (12, 4, 6) in handlers._PENDING_IMPORTS           # registered as pending
    assert handlers._PENDING_IMPORTS[(12, 4, 6)]["issue_ids"] == {777}
    assert mocked_io["comment"] and "re-download" in mocked_io["comment"][0][1].lower()
    assert mocked_io["close"] == []                          # NOT closed at search time


# --- Default (flag OFF) parity + bucket gating ------------------------------

@pytest.fixture
def mocked_tv(mocked_io, monkeypatch):
    """mocked_io plus stubbed Sonarr delete/search; records delete calls."""
    deletes = []

    async def fake_delete(series_id, episode_ids, instance=0):
        deletes.append((series_id, tuple(episode_ids)))
        return len(episode_ids)

    async def fake_search(episode_ids, instance=0):
        return None

    monkeypatch.setattr(handlers.S, "delete_episodefiles", fake_delete)
    monkeypatch.setattr(handlers.S, "trigger_episode_search", fake_search)
    mocked_io["deletes"] = deletes
    return mocked_io


def test_handle_tv_flag_off_closes_at_search_time(mocked_tv, monkeypatch):
    # The maintainer's #1 constraint: with CONFIRM_REPLACEMENT_IMPORT off, behavior
    # is unchanged — comment success + close now, register nothing.
    monkeypatch.setattr(handlers.cfg, "CONFIRM_REPLACEMENT_IMPORT", False)
    series = {"id": 12, "title": "From", "tvdbId": 111}

    asyncio.run(handlers._handle_tv(777, series, 4, 6, [555], "audio"))

    assert mocked_tv["close"] == [777]                       # closed at search time
    assert mocked_tv["comment"] and "re-download" not in mocked_tv["comment"][0][1].lower()
    assert any("fixed" in m.lower() for _, m in mocked_tv["notify"])
    assert handlers._PENDING_IMPORTS == {}                   # nothing armed


def test_handle_tv_other_bucket_does_not_register(mocked_tv, monkeypatch):
    # 'other' = search-only (no file replaced) -> close at search time even in
    # confirm mode; nothing to await an import for.
    monkeypatch.setattr(handlers.cfg, "CONFIRM_REPLACEMENT_IMPORT", True)
    series = {"id": 12, "title": "From", "tvdbId": 111}

    asyncio.run(handlers._handle_tv(777, series, 4, 6, [555], "other"))

    assert (12, 4, 6) not in handlers._PENDING_IMPORTS       # not armed
    assert mocked_tv["close"] == [777]                       # closed at search time
    assert mocked_tv["deletes"] == []                        # 'other' never deletes


# --- M3/L11: several issues on one episode, in-flight dedup -----------------

def test_two_issues_same_episode_skip_redelete_and_both_close(mocked_tv, monkeypatch):
    monkeypatch.setattr(handlers.cfg, "CONFIRM_REPLACEMENT_IMPORT", True)
    series = {"id": 12, "title": "From", "tvdbId": 111}

    asyncio.run(handlers._handle_tv(777, series, 4, 6, [555], "audio"))   # first issue
    asyncio.run(handlers._handle_tv(888, series, 4, 6, [555], "audio"))   # second, same ep

    # The in-flight download is reused: delete/search ran once, both issues armed.
    assert len(mocked_tv["deletes"]) == 1
    assert handlers._PENDING_IMPORTS[(12, 4, 6)]["issue_ids"] == {777, 888}

    payload = {"eventType": "Download", "series": {"id": 12, "title": "From"},
               "episodes": [{"seasonNumber": 4, "episodeNumber": 6}]}
    asyncio.run(handlers.handle_sonarr_import(payload))

    assert sorted(mocked_tv["close"]) == [777, 888]          # both close on the one import
    assert (12, 4, 6) not in handlers._PENDING_IMPORTS


# --- M1: a Seerr error must not drop the issue or abort sibling keys --------

def test_comment_failure_rearms_and_continues_batch(mocked_io, monkeypatch):
    # E06 will raise on comment; E07 must still finalize, and E06 stays pending.
    async def flaky_comment(issue_id, text):
        if issue_id == 606:
            raise RuntimeError("Seerr 500")
        mocked_io["comment"].append((issue_id, text))

    monkeypatch.setattr(handlers, "jelly_comment", flaky_comment)
    handlers._register_pending_import(12, 4, 6, 606, "From")
    handlers._register_pending_import(12, 4, 7, 707, "From")
    payload = {"eventType": "Download", "series": {"id": 12, "title": "From"},
               "episodes": [{"seasonNumber": 4, "episodeNumber": 6},
                            {"seasonNumber": 4, "episodeNumber": 7}]}

    asyncio.run(handlers.handle_sonarr_import(payload))

    assert mocked_io["close"] == [707]                       # sibling key still finalized
    assert (12, 4, 7) not in handlers._PENDING_IMPORTS       # E07 done
    assert handlers._PENDING_IMPORTS[(12, 4, 6)]["issue_ids"] == {606}  # E06 re-armed, not lost


# --- L8: COMMENT_ON_ACTION / CLOSE_ISSUES false branches --------------------

def test_close_issues_false_pops_but_does_not_close(mocked_io, monkeypatch):
    monkeypatch.setattr(handlers, "CLOSE_ISSUES", False)
    handlers._register_pending_import(12, 4, 6, 777, "From")
    payload = {"eventType": "Download", "series": {"id": 12, "title": "From"},
               "episodes": [{"seasonNumber": 4, "episodeNumber": 6}]}

    asyncio.run(handlers.handle_sonarr_import(payload))

    assert mocked_io["close"] == []                          # never closed
    assert mocked_io["comment"] and mocked_io["notify"]      # still commented + alerted
    assert (12, 4, 6) not in handlers._PENDING_IMPORTS       # still consumed


def test_comment_off_still_closes_and_pops(mocked_io, monkeypatch):
    monkeypatch.setattr(handlers, "COMMENT_ON_ACTION", False)
    handlers._register_pending_import(12, 4, 6, 777, "From")
    payload = {"eventType": "Download", "series": {"id": 12, "title": "From"},
               "episodes": [{"seasonNumber": 4, "episodeNumber": 6}]}

    asyncio.run(handlers.handle_sonarr_import(payload))

    assert mocked_io["close"] == [777]                       # closed
    assert mocked_io["comment"] == []                        # but silent
    assert (12, 4, 6) not in handlers._PENDING_IMPORTS


# --- Season/multi-episode confirm-import grouping ---------------------------
# A season or multi-episode fix waits on several (series_id, season, episode)
# keys for one issue — _PENDING_IMPORT_GROUPS — and must only comment/close
# once ALL of them have imported, not once per episode.

def test_register_pending_import_group_tracks_all_keys_and_returns_fresh(mocked_io):
    fresh = handlers._register_pending_import_group(12, 4, [6, 7], 777, "From", "S04 (E06, E07)")

    assert fresh == [6, 7]
    assert handlers._PENDING_IMPORTS[(12, 4, 6)]["issue_ids"] == {777}
    assert handlers._PENDING_IMPORTS[(12, 4, 7)]["issue_ids"] == {777}
    assert handlers._PENDING_IMPORT_GROUPS[777]["remaining"] == {(12, 4, 6), (12, 4, 7)}


def test_register_pending_import_group_merges_already_pending_episode(mocked_io):
    handlers._register_pending_import(12, 4, 6, 606, "From")  # E06 already in flight

    fresh = handlers._register_pending_import_group(12, 4, [6, 7], 777, "From", "S04 (E06, E07)")

    assert fresh == [7]                                        # only E07 is new
    assert handlers._PENDING_IMPORTS[(12, 4, 6)]["issue_ids"] == {606, 777}
    assert handlers._PENDING_IMPORTS[(12, 4, 7)]["issue_ids"] == {777}


def test_grouped_issue_does_not_finalize_until_every_episode_imports(mocked_io):
    handlers._register_pending_import_group(12, 4, [6, 7], 777, "From", "S04 (E06, E07)")

    e6 = {"eventType": "Download", "series": {"id": 12, "title": "From"},
          "episodes": [{"seasonNumber": 4, "episodeNumber": 6}]}
    asyncio.run(handlers.handle_sonarr_import(e6))

    assert mocked_io["close"] == []                            # not yet — E07 still out
    assert mocked_io["comment"] == []
    assert 777 in handlers._PENDING_IMPORT_GROUPS
    assert handlers._PENDING_IMPORT_GROUPS[777]["remaining"] == {(12, 4, 7)}

    e7 = {"eventType": "Download", "series": {"id": 12, "title": "From"},
          "episodes": [{"seasonNumber": 4, "episodeNumber": 7}]}
    asyncio.run(handlers.handle_sonarr_import(e7))

    assert mocked_io["close"] == [777]                         # now it finalizes, once
    assert len(mocked_io["comment"]) == 1
    assert 777 not in handlers._PENDING_IMPORT_GROUPS


def test_grouped_issue_finalizes_in_one_batch_import_event(mocked_io):
    # A season-pack import often lands as one Sonarr webhook listing every
    # episode at once rather than one call per episode.
    handlers._register_pending_import_group(12, 4, [6, 7], 777, "From", "S04 (E06, E07)")
    payload = {"eventType": "Download", "series": {"id": 12, "title": "From"},
               "episodes": [{"seasonNumber": 4, "episodeNumber": 6},
                            {"seasonNumber": 4, "episodeNumber": 7}]}

    asyncio.run(handlers.handle_sonarr_import(payload))

    assert mocked_io["close"] == [777]
    assert len(mocked_io["comment"]) == 1


def test_handle_tv_season_confirm_mode_registers_group_and_does_not_close(mocked_io, monkeypatch):
    async def fake_episode_numbers(series_id, season, instance=0):
        return [1, 2]

    async def fake_all_ids(series_id, season, instance=0):
        return [101, 102]

    async def fake_delete_all(series_id, season, instance=0):
        return 2

    async def fake_season_search(series_id, season, instance=0):
        return None

    monkeypatch.setattr(handlers.S, "get_episode_numbers_with_files_for_season", fake_episode_numbers)
    monkeypatch.setattr(handlers.S, "get_all_episode_ids_for_season", fake_all_ids)
    monkeypatch.setattr(handlers.S, "delete_all_episodefiles_for_season", fake_delete_all)
    monkeypatch.setattr(handlers.S, "trigger_season_search", fake_season_search)
    monkeypatch.setattr(handlers.cfg, "CONFIRM_REPLACEMENT_IMPORT", True)

    series = {"id": 12, "title": "From", "tvdbId": 111}
    asyncio.run(handlers._handle_tv_season(777, series, 4, "audio"))

    assert handlers._PENDING_IMPORT_GROUPS[777]["remaining"] == {(12, 4, 1), (12, 4, 2)}
    assert mocked_io["comment"] and "re-download" in mocked_io["comment"][0][1].lower()
    assert mocked_io["close"] == []                            # NOT closed at search time


def test_handle_tv_season_flag_off_closes_at_search_time(mocked_io, monkeypatch):
    async def fake_all_ids(series_id, season, instance=0):
        return [101, 102]

    async def fake_delete_all(series_id, season, instance=0):
        return 2

    async def fake_season_search(series_id, season, instance=0):
        return None

    monkeypatch.setattr(handlers.S, "get_all_episode_ids_for_season", fake_all_ids)
    monkeypatch.setattr(handlers.S, "delete_all_episodefiles_for_season", fake_delete_all)
    monkeypatch.setattr(handlers.S, "trigger_season_search", fake_season_search)
    monkeypatch.setattr(handlers.cfg, "CONFIRM_REPLACEMENT_IMPORT", False)

    series = {"id": 12, "title": "From", "tvdbId": 111}
    asyncio.run(handlers._handle_tv_season(777, series, 4, "audio"))

    assert mocked_io["close"] == [777]                         # unchanged default behavior
    assert handlers._PENDING_IMPORT_GROUPS == {}


def test_handle_tv_season_confirm_mode_with_no_files_falls_back_to_immediate_close(mocked_io, monkeypatch):
    # Nothing to wait on (e.g. the season has no files at all) — close now,
    # same as flag-off, instead of registering an empty/unfinishable group.
    async def fake_episode_numbers(series_id, season, instance=0):
        return []

    async def fake_all_ids(series_id, season, instance=0):
        return []

    async def fake_delete_all(series_id, season, instance=0):
        return 0

    async def fake_season_search(series_id, season, instance=0):
        return None

    monkeypatch.setattr(handlers.S, "get_episode_numbers_with_files_for_season", fake_episode_numbers)
    monkeypatch.setattr(handlers.S, "get_all_episode_ids_for_season", fake_all_ids)
    monkeypatch.setattr(handlers.S, "delete_all_episodefiles_for_season", fake_delete_all)
    monkeypatch.setattr(handlers.S, "trigger_season_search", fake_season_search)
    monkeypatch.setattr(handlers.cfg, "CONFIRM_REPLACEMENT_IMPORT", True)

    series = {"id": 12, "title": "From", "tvdbId": 111}
    asyncio.run(handlers._handle_tv_season(777, series, 4, "audio"))

    assert mocked_io["close"] == [777]
    assert handlers._PENDING_IMPORT_GROUPS == {}


def test_handle_tv_specific_episodes_confirm_mode_registers_group(mocked_io, monkeypatch):
    async def fake_episode_ids_for(series_id, season, episode, instance=0):
        return [100 + episode]

    async def fake_delete(series_id, episode_ids, instance=0):
        return len(episode_ids)

    async def fake_search(episode_ids, instance=0):
        return None

    monkeypatch.setattr(handlers.S, "episode_ids_for", fake_episode_ids_for)
    monkeypatch.setattr(handlers.S, "delete_episodefiles", fake_delete)
    monkeypatch.setattr(handlers.S, "trigger_episode_search", fake_search)
    monkeypatch.setattr(handlers.cfg, "CONFIRM_REPLACEMENT_IMPORT", True)

    series = {"id": 12, "title": "From", "tvdbId": 111}
    asyncio.run(handlers._handle_tv_specific_episodes(777, series, 4, [6, 7], "video"))

    assert handlers._PENDING_IMPORT_GROUPS[777]["remaining"] == {(12, 4, 6), (12, 4, 7)}
    assert mocked_io["close"] == []
    assert mocked_io["comment"] and "re-download" in mocked_io["comment"][0][1].lower()
