import os

def _csv_env(name: str, default: str):
    raw = os.getenv(name, default)
    return [s.strip().lower() for s in raw.split(",") if s.strip()]

# TV
TV_AUDIO = lambda: _csv_env("TV_AUDIO_KEYWORDS", "no audio,no sound,missing audio,audio issue")
TV_VIDEO = lambda: _csv_env("TV_VIDEO_KEYWORDS", "no video,video glitch,black screen,stutter,pixelation")
TV_SUBTITLE = lambda: _csv_env("TV_SUBTITLE_KEYWORDS", "missing subs,no subtitles,bad subtitles,wrong subs,subs out of sync")
TV_OTHER = lambda: _csv_env("TV_OTHER_KEYWORDS", "buffering,playback error,corrupt file")

# Movies
MOV_AUDIO = lambda: _csv_env("MOVIE_AUDIO_KEYWORDS", "no audio,no sound,audio issue")
MOV_VIDEO = lambda: _csv_env("MOVIE_VIDEO_KEYWORDS", "no video,video missing,bad video,broken video,black screen")
MOV_SUBTITLE = lambda: _csv_env("MOVIE_SUBTITLE_KEYWORDS", "missing subs,no subtitles,bad subtitles,wrong subs,subs out of sync")
MOV_OTHER = lambda: _csv_env("MOVIE_OTHER_KEYWORDS", "buffering,playback error,corrupt file")
MOV_WRONG = lambda: _csv_env("MOVIE_WRONG_KEYWORDS", "not the right movie,wrong movie,incorrect movie")