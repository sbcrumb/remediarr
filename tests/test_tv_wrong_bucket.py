"""Tests for the TV 'wrong episode/show' bucket.

TV_WRONG_KEYWORDS has existed in config.py and .env.example since before this
fix, but nothing ever read it - there was no TV_WRONG keyword set and no TV
bucket handler checked for a "wrong" bucket, unlike movies (MOVIE_WRONG_KEYWORDS/
MOV_WRONG), which were fully wired. Setting TV_WRONG_KEYWORDS did nothing.
"""

from app.webhooks.handlers import _bucket_for, TV_WRONG, MOV_WRONG


def test_tv_wrong_keywords_detected():
    assert _bucket_for("this is the wrong episode", "tv") == "wrong"
    assert _bucket_for("wrong show entirely", "tv") == "wrong"
    assert _bucket_for("not the right episode", "tv") == "wrong"


def test_tv_wrong_keywords_detected_for_series_media_type():
    # media_type shows up as "series" in some payload shapes; _bucket_for
    # already treats "tv" and "series" as equivalent for every other bucket.
    assert _bucket_for("incorrect show", "series") == "wrong"


def test_tv_wrong_is_case_insensitive():
    assert _bucket_for("WRONG EPISODE, please fix", "tv") == "wrong"


def test_movie_wrong_still_works_unaffected():
    # Regression check: adding the TV branch must not disturb the existing
    # movie path.
    assert _bucket_for("wrong movie", "movie") == "wrong"
    assert _bucket_for("not the right movie", "movie") == "wrong"


def test_tv_wrong_keywords_not_matched_for_movie_media_type():
    # A TV_WRONG phrase should not accidentally resolve to "wrong" when the
    # media type is movie - the two keyword sets are deliberately separate.
    assert _bucket_for("wrong episode", "movie") is None


def test_movie_wrong_keywords_not_matched_for_tv_media_type():
    assert _bucket_for("wrong movie", "tv") is None


def test_default_keyword_sets_are_nonempty():
    # Sanity check that the module-level sets actually loaded some defaults
    # (would be empty if TV_WRONG_KEYWORDS/MOVIE_WRONG_KEYWORDS env vars were
    # set to "" in the test environment).
    assert TV_WRONG
    assert MOV_WRONG
