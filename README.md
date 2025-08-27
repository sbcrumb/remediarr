# Remediarr

**Remediarr** is a lightweight webhook service that listens to **Jellyseerr** issue webhooks and automatically remediates common problems.

- **TV issues (Audio/Video/Subtitles):** delete the bad episode and trigger a re-download.
- **Movie issues (Audio/Video/Subtitles):** mark the last bad grab as failed, delete the bad file(s), and trigger a new search.
- **Wrong Movie:** optionally only re-search if the title has a digital release (configurable).
- **Coaching mode:** if the report lacks **keywords**, Remediarr posts a helpful comment instead of acting.
- **Gotify notifications:** optional status pings.

All user-facing comments and keyword lists are **customizable via `.env`**.

---

## Quick Start

1. **Clone repo & prepare env**
   ```bash
   git clone https://github.com/<your-username>/remediarr.git
   cd remediarr
   cp .env.example .env
   # Edit .env to add your URLs/API keys and preferred keywords/messages

2. Build & run (local)
docker compose -f docker-compose.example.yml up -d --build

3. Configure Jellyseerr → Notifications → Webhooks

URL: http://<your-host>:8189/webhook/jellyseerr

Method: POST

Payload: use the JSON template below (make sure season/episode are included when available)

(Optional) Secret/Header: If you set a shared secret or custom header in Jellyseerr, also set the same in .env.

{
  "event": "{{event}}",
  "subject": "{{subject}}",
  "message": "{{message}}",

  "media": {
    "media_type": "{{media_type}}",
    "tmdbId": "{{media_tmdbid}}",
    "tvdbId": "{{media_tvdbid}}",

    "seasonNumber": "{{season_number}}",
    "episodeNumber": "{{episode_number}}"
  },

  "issue": {
    "issue_id": "{{issue_id}}",
    "issue_type": "{{issue_type}}",
    "issue_status": "{{issue_status}}",

    "affected_season": "{{affected_season}}",
    "affected_episode": "{{affected_episode}}",

    "season": "{{season}}",
    "episode": "{{episode}}"
  },

  "comment": {
    "comment_message": "{{comment_message}}"
  }
}

Notes

If seasonNumber / episodeNumber aren’t provided by Jellyseerr, Remediarr tries to infer SxxExx from text or fetch the issue details to extract them.

The bot also reads free text like S01E02 if you include it in your message.

Keywords (defaults)

Keywords drive auto-fix behavior and are configurable in .env.

TV Audio: no audio, no sound, missing audio, audio issue

TV Video: no video, video glitch, black screen, stutter, pixelation

TV Subtitles: no subtitles, bad subtitles, subs out of sync, wrong subs

Movie Wrong Movie: wrong movie, not the right movie, incorrect movie

Other: buffering, playback error, corrupt file

TV “Other” triggers search only (no delete) by default.

APP_HOST=0.0.0.0
APP_PORT=8189
LOG_LEVEL=INFO

WEBHOOK_SHARED_SECRET=
WEBHOOK_HEADER_NAME=X-Jellyseerr-Token
WEBHOOK_HEADER_VALUE=


SONARR_URL=http://sonarr:8989
SONARR_API_KEY=

RADARR_URL=http://radarr:7878
RADARR_API_KEY=

JELLYSEERR_URL=http://jellyseerr:5055
JELLYSEERR_API_KEY=
JELLYSEERR_COACH_REPORTERS=true
JELLYSEERR_COMMENT_ON_ACTION=true
JELLYSEERR_CLOSE_ISSUES=false

TMDB_API_KEY=
SEARCH_ONLY_IF_DIGITAL_RELEASE=true


GOTIFY_URL=
GOTIFY_TOKEN=
GOTIFY_PRIORITY=5

TV_AUDIO_KEYWORDS=no audio,no sound,missing audio,audio issue
TV_VIDEO_KEYWORDS=no video,video glitch,black screen,stutter,pixelation
TV_SUBTITLE_KEYWORDS=missing subs,no subtitles,bad subtitles,wrong subs,subs out of sync
TV_OTHER_KEYWORDS=buffering,playback error,corrupt file

MOVIE_AUDIO_KEYWORDS=no audio,no sound,audio issue
MOVIE_VIDEO_KEYWORDS=no video,video missing,bad video,broken video,black screen
MOVIE_SUBTITLE_KEYWORDS=no subtitles,bad subtitles,subs out of sync
MOVIE_OTHER_KEYWORDS=buffering,playback error,corrupt file
MOVIE_WRONG_KEYWORDS=wrong movie,not the right movie,incorrect movie

# Coaching (uses {keywords})
MSG_COACH_TV_AUDIO=[Remediarr] Tip: include one of these keywords to auto-fix TV audio (delete episode file + re-download): {keywords}.
MSG_COACH_TV_VIDEO=[Remediarr] Tip: include one of these keywords to auto-fix TV video: {keywords}.
MSG_COACH_TV_SUBTITLE=[Remediarr] Tip: include one of these keywords to auto-fix TV subtitles: {keywords}.
MSG_COACH_TV_OTHER=[Remediarr] Tip: include one of these keywords to trigger automation for TV other: {keywords}.
MSG_COACH_MOV_AUDIO=[Remediarr] Tip: include one of these keywords to auto-handle movie audio: {keywords}.
MSG_COACH_MOV_VIDEO=[Remediarr] Tip: include one of these keywords to auto-handle movie video: {keywords}.
MSG_COACH_MOV_SUBTITLE=[Remediarr] Tip: include one of these keywords to auto-handle movie subtitles: {keywords}.
MSG_COACH_MOV_OTHER=[Remediarr] Tip: include one of these keywords to auto-handle movie other: {keywords}.

# TV action (uses {title} {season} {episode})
MSG_TV_EP_REPLACED=[Remediarr] {title} S{season:02d}E{episode:02d} – deleted file and re-download started.
MSG_TV_EP_SEARCH_ONLY=[Remediarr] {title} S{season:02d}E{episode:02d} – re-download started.
MSG_TV_OTHER_SEARCH_ONLY=[Remediarr] {title} S{season:02d}E{episode:02d} – search triggered (no delete).

# Movie action (uses {title} {deleted})
MSG_MOV_GENERIC_HANDLED=[Remediarr] {title}: blocklisted last grab, deleted {deleted} file(s), search started.
MSG_MOV_WRONG_HANDLED=[Remediarr] Wrong movie: {title}. Blocklisted last grab, deleted {deleted} file(s), search started.
MSG_MOV_WRONG_NO_RELEASE=[Remediarr] Wrong movie: {title}. Blocklisted last grab, deleted {deleted} file(s). Not searching (not digitally released).

# Auto-close failure
MSG_AUTOCLOSE_FAIL=[Remediarr] Action completed but I couldn’t auto-close this issue. Please close it once you verify it’s fixed.


docker build -t remediarr:local .


docker run --rm -p 8189:8189 --env-file .env remediarr:local

docker compose -f docker-compose.example.yml up -d --build

Webhook Endpoint

POST /webhook/jellyseerr

Expects the JSON payload above.

If you set a shared secret or custom header in Jellyseerr, mirror it in .env.

Troubleshooting

Got 400 “Missing tvdbId/season/episode”
Make sure your payload includes tvdbId, seasonNumber, and episodeNumber where possible, or include SxxExx in the text.

Issue not auto-closed
Some Jellyseerr builds reject auto-close endpoints. Remediarr leaves a comment if it can’t close the issue. You can disable attempts with JELLYSEERR_CLOSE_ISSUES=false.

Contributing

Fork → create a feature branch → commit → push → open PR.

Keep PRs focused and small where possible.

Add/update .env.example if you introduce new settings.


License

This project is licensed under the MIT License.

The repo includes a LICENSE file with the MIT text.

To set it to you: edit the first lines to include your name and the year, e.g.:

MIT License

Copyright (c) 2025 SBCrumb


Donations

If Remediarr saves you time, consider fueling more tinkering:

GitHub Sponsors: add your link here → https://github.com/sponsors/<your-username>

Ko-fi: https://ko-fi.com/<your-handle>

Buy Me a Coffee: https://www.buymeacoffee.com/<your-handle>

Bitcoin (BTC) Address: bc1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
(replace with your address)

Thank you! 🚀

