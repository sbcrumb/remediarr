[![Build & Publish to GHCR](https://github.com/sbcrumb/remediarr/actions/workflows/ghcr-main.yml/badge.svg)](https://github.com/sbcrumb/remediarr/actions/workflows/ghcr-main.yml)
[![Container image](https://img.shields.io/badge/GHCR-ghcr.io%2Fsbcrumb%2Fremediarr-blue)](https://github.com/sbcrumb/remediarr/pkgs/container/remediarr)

# Remediarr

Remediarr is a lightweight webhook service that listens to **Jellyseerr issue webhooks** and automatically remediates common problems.

✨ **What it does**
- **TV Issues (Audio / Video / Subtitles):** Delete the bad episode file and trigger a re-download.
- **Movie Issues (Audio / Video / Subtitles):** Mark the last bad grab as failed, delete bad file(s), and trigger a new search.
- **Wrong Movie Reports:** Blocklist + delete the last bad grab, with optional “only search if digitally released” logic.
- **Coaching Mode:** If a user doesn’t include recognizable keywords, Remediarr leaves a helpful comment explaining what to do.
- **Gotify Notifications:** Optional push messages when an action is taken.
- **Customization:** All keywords, comments, and behaviors are configurable in `.env`.

##Settings Note
Please set the webhook fire settings just to Issue Reported for now. If you select Comment resolved etc it will create a loop condition.
Working on correcting it.
<img width="913" height="690" alt="image" src="https://github.com/user-attachments/assets/5a3058c1-e26b-44dd-a7dc-9a32c5b94049" />


## Quick Start

### 1. Clone & prepare
```bash
git clone https://github.com/<your-username>/remediarr.git
cd remediarr
cp .env.example .env
# Edit .env with your API keys, URLs, and preferred keywords/messages
```

### 2. Build & run locally
```bash
docker build -t remediarr:local .
docker run --rm -p 8189:8189 --env-file .env remediarr:local
```

### 3. Or use Docker Compose
```bash
docker compose -f docker-compose.example.yml up -d --build
```

---

## Jellyseerr Setup

In **Jellyseerr → Settings → Notifications → Webhooks**:

- **URL:**
  ```
  http://<your-server>:8189/webhook/jellyseerr
  ```

- **Method:** `POST`

- **Payload:** use this JSON template (make sure season/episode are included when available)
  ```json
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
  ```

- **Secret/Header (optional):**  
  If you configure a shared secret or custom header in Jellyseerr, mirror it in `.env`.

---

## Environment Variables

See `.env.example` for all available options. Highlights:

- **Web server**
  ```
  APP_HOST=0.0.0.0
  APP_PORT=8189
  LOG_LEVEL=INFO
  ```

- **Sonarr & Radarr**
  ```
  SONARR_URL=http://sonarr:8989
  SONARR_API_KEY=your-sonarr-api-key

  RADARR_URL=http://radarr:7878
  RADARR_API_KEY=your-radarr-api-key
  ```

- **Jellyseerr**
  ```
  JELLYSEERR_URL=http://jellyseerr:5055
  JELLYSEERR_API_KEY=your-jellyseerr-api-key
  JELLYSEERR_COACH_REPORTERS=true
  JELLYSEERR_COMMENT_ON_ACTION=true
  JELLYSEERR_CLOSE_ISSUES=false
  ```

- **TMDB (for digital release checks)**
  ```
  TMDB_API_KEY=your-tmdb-api-key
  SEARCH_ONLY_IF_DIGITAL_RELEASE=true
  ```

- **Gotify notifications (optional)**
  ```
  GOTIFY_URL=https://gotify.example.com
  GOTIFY_TOKEN=your-gotify-token
  GOTIFY_PRIORITY=5
  ```

- **Keyword defaults**
  ```
  TV_AUDIO_KEYWORDS=no audio,no sound,missing audio,audio issue
  TV_VIDEO_KEYWORDS=no video,video glitch,black screen,stutter,pixelation
  TV_SUBTITLE_KEYWORDS=missing subs,no subtitles,bad subtitles,wrong subs,subs out of sync
  TV_OTHER_KEYWORDS=buffering,playback error,corrupt file

  MOVIE_AUDIO_KEYWORDS=no audio,no sound,audio issue
  MOVIE_VIDEO_KEYWORDS=no video,video missing,bad video,broken video,black screen
  MOVIE_SUBTITLE_KEYWORDS=no subtitles,bad subtitles,subs out of sync
  MOVIE_OTHER_KEYWORDS=buffering,playback error,corrupt file
  MOVIE_WRONG_KEYWORDS=wrong movie,not the right movie,incorrect movie
  ```

- **Comment templates**
  ```
  MSG_COACH_TV_AUDIO=[Remediarr] Tip: include one of these keywords to auto-fix TV audio: {keywords}.
  MSG_COACH_TV_VIDEO=[Remediarr] Tip: include one of these keywords to auto-fix TV video: {keywords}.
  MSG_COACH_TV_SUBTITLE=[Remediarr] Tip: include one of these keywords to auto-fix TV subtitles: {keywords}.
  MSG_COACH_TV_OTHER=[Remediarr] Tip: include one of these keywords for TV other: {keywords}.
  MSG_COACH_MOV_AUDIO=[Remediarr] Tip: include one of these keywords to auto-handle movie audio: {keywords}.
  MSG_COACH_MOV_VIDEO=[Remediarr] Tip: include one of these keywords to auto-handle movie video: {keywords}.
  MSG_COACH_MOV_SUBTITLE=[Remediarr] Tip: include one of these keywords to auto-handle movie subtitles: {keywords}.
  MSG_COACH_MOV_OTHER=[Remediarr] Tip: include one of these keywords to auto-handle movie other: {keywords}.

  MSG_TV_EP_REPLACED=[Remediarr] {title} S{season:02d}E{episode:02d} – deleted file and re-download started.
  MSG_TV_EP_SEARCH_ONLY=[Remediarr] {title} S{season:02d}E{episode:02d} – re-download started.
  MSG_TV_OTHER_SEARCH_ONLY=[Remediarr] {title} S{season:02d}E{episode:02d} – search triggered (no delete).

  MSG_MOV_GENERIC_HANDLED=[Remediarr] {title}: blocklisted last grab, deleted {deleted} file(s), search started.
  MSG_MOV_WRONG_HANDLED=[Remediarr] Wrong movie: {title}. Blocklisted last grab, deleted {deleted} file(s), search started.
  MSG_MOV_WRONG_NO_RELEASE=[Remediarr] Wrong movie: {title}. Blocklisted last grab, deleted {deleted} file(s). Not searching (not digitally released).

  MSG_AUTOCLOSE_FAIL=[Remediarr] Action completed but I couldn’t auto-close this issue. Please close it once you verify it’s fixed.
  ```

---

## Troubleshooting

- **Got 400 “Missing tvdbId/season/episode”**  
  Make sure your payload includes `tvdbId`, `seasonNumber`, and `episodeNumber` where possible, or include `SxxExx` in the text.

- **Issue not auto-closed**  
  Some Jellyseerr builds reject auto-close endpoints. Remediarr leaves a comment if it can’t close the issue. You can disable attempts with:  
  ```
  JELLYSEERR_CLOSE_ISSUES=false
  ```

---

## Contributing

1. Fork the repo  
2. Create a feature branch  
3. Commit your changes  
4. Push  
5. Open a PR  

Please keep PRs small and focused.  
If you add new settings, update `.env.example`.

---

## License

This project is licensed under the MIT License.

```
MIT License

Copyright (c) 2025 SBCrumb

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## Donations

If Remediarr saves you time, consider fueling more tinkering:

- GitHub Sponsors: https://github.com/sponsors/sbcrumb?preview=true  
- Ko-fi: Coming Soon
- Buy Me a Coffee: Coming Soon
- Bitcoin (BTC): `bc1qjc200yg9mc08uskmeka8zrjddp8lw2j6d8q0kn`  

Thank you! 🚀


### Dev / Testing builds
- Stable: `ghcr.io/sbcrumb/remediarr:latest`
- Versioned: `ghcr.io/sbcrumb/remediarr:v0.1.7`
- **Dev/testing**: `ghcr.io/sbcrumb/remediarr:dev` (updated on each push to the `dev` branch)
