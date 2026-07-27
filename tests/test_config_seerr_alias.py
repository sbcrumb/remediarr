"""SEERR_* is the canonical name; JELLYSEERR_* must keep working for existing
setups (Jellyseerr/Seerr/Overseerr-based apps all speak the same API)."""

import os

import pytest

from app.config import Settings, env_alias

BASE_ENV = {
    "SONARR_URL": "http://sonarr:8989",
    "SONARR_API_KEY": "x",
    "RADARR_URL": "http://radarr:7878",
    "RADARR_API_KEY": "x",
}


def _settings(monkeypatch, **extra):
    # conftest.py sets SEERR_URL/SEERR_API_KEY globally so importing app.config
    # doesn't fail during collection; clear those so each test controls exactly
    # which var(s) are present.
    monkeypatch.delenv("SEERR_URL", raising=False)
    monkeypatch.delenv("SEERR_API_KEY", raising=False)
    monkeypatch.delenv("JELLYSEERR_URL", raising=False)
    monkeypatch.delenv("JELLYSEERR_API_KEY", raising=False)
    for k, v in {**BASE_ENV, **extra}.items():
        monkeypatch.setenv(k, v)
    return Settings()


def test_seerr_name_works(monkeypatch):
    s = _settings(monkeypatch, SEERR_URL="http://seerr:5055", SEERR_API_KEY="key1")
    assert s.SEERR_URL == "http://seerr:5055"
    assert s.SEERR_API_KEY == "key1"


def test_legacy_jellyseerr_name_still_works(monkeypatch):
    s = _settings(monkeypatch, JELLYSEERR_URL="http://jellyseerr:5055", JELLYSEERR_API_KEY="key2")
    assert s.SEERR_URL == "http://jellyseerr:5055"
    assert s.SEERR_API_KEY == "key2"


def test_seerr_name_takes_precedence_when_both_set(monkeypatch):
    s = _settings(
        monkeypatch,
        SEERR_URL="http://new:5055",
        SEERR_API_KEY="newkey",
        JELLYSEERR_URL="http://old:5055",
        JELLYSEERR_API_KEY="oldkey",
    )
    assert s.SEERR_URL == "http://new:5055"
    assert s.SEERR_API_KEY == "newkey"


def test_env_alias_helper_prefers_new_name(monkeypatch):
    monkeypatch.setenv("SEERR_BOT_COMMENT_PREFIX", "[New]")
    monkeypatch.setenv("JELLYSEERR_BOT_COMMENT_PREFIX", "[Old]")
    assert env_alias("SEERR_BOT_COMMENT_PREFIX", "JELLYSEERR_BOT_COMMENT_PREFIX") == "[New]"


def test_env_alias_helper_falls_back_to_old_name(monkeypatch):
    monkeypatch.delenv("SEERR_BOT_COMMENT_PREFIX", raising=False)
    monkeypatch.setenv("JELLYSEERR_BOT_COMMENT_PREFIX", "[Old]")
    assert env_alias("SEERR_BOT_COMMENT_PREFIX", "JELLYSEERR_BOT_COMMENT_PREFIX") == "[Old]"


def test_env_alias_helper_default(monkeypatch):
    monkeypatch.delenv("SEERR_BOT_COMMENT_PREFIX", raising=False)
    monkeypatch.delenv("JELLYSEERR_BOT_COMMENT_PREFIX", raising=False)
    assert env_alias("SEERR_BOT_COMMENT_PREFIX", "JELLYSEERR_BOT_COMMENT_PREFIX", "[Remediarr]") == "[Remediarr]"
