def _parse_tv_keywords() -> dict[str, list[str]]:
    """Parse TV show keyword buckets from config."""
    return {
        "audio": [k.strip().lower() for k in cfg.TV_AUDIO_KEYWORDS.split(",") if k.strip()],
        "video": [k.strip().lower() for k in cfg.TV_VIDEO_KEYWORDS.split(",") if k.strip()],
        "subtitle": [k.strip().lower() for k in cfg.TV_SUBTITLE_KEYWORDS.split(",") if k.strip()],
        "other": [k.strip().lower() for k in cfg.TV_OTHER_KEYWORDS.split(",") if k.strip()],
        "wrong": [k.strip().lower() for k in cfg.TV_WRONG_KEYWORDS.split(",") if k.strip()],
    }