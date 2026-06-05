"""Auth on the webhook endpoints — the header check is the only auth on
/webhook/sonarr, so prove it actually gates the route."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import cfg
from app.webhooks import handlers
from app.webhooks.router import router

# A Test-event payload: keys -> [] so the handler no-ops without any network call.
TEST_EVENT = {"eventType": "Test"}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_pending():
    handlers._PENDING_IMPORTS.clear()
    yield
    handlers._PENDING_IMPORTS.clear()


@pytest.mark.parametrize("path", ["/webhook/sonarr", "/webhook/jellyseerr"])
def test_wrong_header_is_rejected(client, monkeypatch, path):
    monkeypatch.setattr(cfg, "WEBHOOK_HEADER_NAME", "Authorization")
    monkeypatch.setattr(cfg, "WEBHOOK_HEADER_VALUE", "s3cret")
    r = client.post(path, json=TEST_EVENT, headers={"Authorization": "wrong"})
    assert r.status_code == 401


@pytest.mark.parametrize("path", ["/webhook/sonarr", "/webhook/jellyseerr"])
def test_missing_header_is_rejected(client, monkeypatch, path):
    monkeypatch.setattr(cfg, "WEBHOOK_HEADER_NAME", "Authorization")
    monkeypatch.setattr(cfg, "WEBHOOK_HEADER_VALUE", "s3cret")
    r = client.post(path, json=TEST_EVENT)
    assert r.status_code == 401


def test_correct_header_is_accepted(client, monkeypatch):
    monkeypatch.setattr(cfg, "WEBHOOK_HEADER_NAME", "Authorization")
    monkeypatch.setattr(cfg, "WEBHOOK_HEADER_VALUE", "s3cret")
    r = client.post("/webhook/sonarr", json=TEST_EVENT, headers={"Authorization": "s3cret"})
    assert r.status_code == 200
    assert r.json()["detail"].startswith("ignored")  # Test event -> no-op


def test_no_header_configured_is_open(client, monkeypatch):
    # When the operator hasn't set the header pair, the check short-circuits open.
    monkeypatch.setattr(cfg, "WEBHOOK_HEADER_NAME", None)
    monkeypatch.setattr(cfg, "WEBHOOK_HEADER_VALUE", None)
    r = client.post("/webhook/sonarr", json=TEST_EVENT)
    assert r.status_code == 200
