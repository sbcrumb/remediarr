"""Tests for ISSUE_TYPE_AS_BUCKET — deriving the bucket from the Seerr issue TYPE."""

from app.webhooks.handlers import _bucket_from_issue_type


def test_body_string_wins():
    assert _bucket_from_issue_type({"issue_type": "Audio"}) == "audio"
    assert _bucket_from_issue_type({"issue_type": "video"}) == "video"
    assert _bucket_from_issue_type({"issue_type": "SUBTITLE"}) == "subtitle"
    assert _bucket_from_issue_type({"issue_type": "other"}) == "other"


def test_int_fallback():
    assert _bucket_from_issue_type({"issueType": 1}) == "video"
    assert _bucket_from_issue_type({"issueType": 2}) == "audio"
    assert _bucket_from_issue_type({"issueType": 3}) == "subtitle"
    assert _bucket_from_issue_type({"issueType": 4}) == "other"


def test_body_string_preferred_over_int():
    assert _bucket_from_issue_type({"issue_type": "audio", "issueType": 1}) == "audio"


def test_invalid_and_empty():
    assert _bucket_from_issue_type({}) is None
    assert _bucket_from_issue_type({"issue_type": "wrong"}) is None
    assert _bucket_from_issue_type({"issue_type": ""}) is None
    assert _bucket_from_issue_type({"issueType": 99}) is None
    assert _bucket_from_issue_type({"issueType": None}) is None


def test_string_is_normalized_and_bool_int_is_ignored():
    # Surrounding whitespace / casing are tolerated on the string path...
    assert _bucket_from_issue_type({"issue_type": "  Audio  "}) == "audio"
    # ...and a bool must not be treated as its int value (True == 1 in Python).
    assert _bucket_from_issue_type({"issueType": True}) is None
    assert _bucket_from_issue_type({"issueType": False}) is None
