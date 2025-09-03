# Remediarr

[![Build & Publish to GHCR](https://github.com/sbcrumb/remediarr/actions/workflows/ghcr-dev.yml/badge.svg)](https://github.com/sbcrumb/remediarr/actions/workflows/ghcr-dev.yml)
[![Container image](https://img.shields.io/badge/GHCR-ghcr.io%2Fsbcrumb%2Fremediarr-blue)](https://github.com/sbcrumb/remediarr/pkgs/container/remediarr)

**Automated issue resolution for Jellyseerr via Sonarr & Radarr webhooks**

> ⚠️ **Work in Progress**: Remediarr is under active development. Configuration options, API endpoints, and behavior may change between versions. Please check the changelog and update your configuration when upgrading. Feedback and bug reports are welcome!

Remediarr is a lightweight webhook service that automatically fixes common media issues reported through Jellyseerr. When users report problems like "no audio" or "wrong movie", Remediarr detects the keywords, deletes problematic files, triggers new downloads, and closes the issue—all without manual intervention.

## Features

- **🎬 Movie Automation**: Handles audio, video, subtitle issues, and wrong movie downloads
- **📺 TV Show Automation**: Manages episode-specific problems with season/episode detection  
- **🤖 Smart Keyword Detection**: Recognizes issue types from user comments
- **💬 User Coaching**: Suggests correct keywords when users don't use recognizable terms
- **🔄 Loop Prevention**: Avoids processing its own comments and resolved issues
- **📱 Notifications**: Optional Gotify and Apprise integration
- **🔐 Security**: HMAC signature verification and custom header authentication
- **⚡ Performance**: Built with FastAPI for speed and reliability

## How It Works

1. **User reports issue** in Jellyseerr: "no audio in S02E05"
2. **Jellyseerr sends webhook** to Remediarr with issue details
3. **Remediarr processes** the comment, detects "audio" keyword
4. **Finds the episode** in Sonarr using TVDB ID and season/episode
5. **Deletes bad file** and triggers new download in Sonarr
6. **Comments on issue**: "S02E05: replaced file; new download grabbed"
7. **Closes the issue** automatically

## Quick Start

### Docker Compose (Recommended)

```yaml
version: '3.8'
services:
  remediarr:
    image: ghcr.io/sbcrumb/remediarr:latest
    container_name: remediarr
    ports:
      - "8189:8189"
    environment:
      # Required - Your service URLs and API keys
      - SONARR_URL=http://sonarr:8989
      - SONARR_API_KEY=your-sonarr-api-key
      - RADARR_URL=http://radarr:7878  
      - RADARR_API_KEY=your-radarr-api-key
      - JELLYSEERR_URL=http://jellyseerr:5055
      - JELLYSEERR_API_KEY=your-jellyseerr-api-key
      
      # Optional - Notifications
      - GOTIFY_URL=https://gotify.example.com
      - GOTIFY_TOKEN=your-gotify-token
      
      # Optional - Security
      - WEBHOOK_SHARED_SECRET=your-shared-secret
      
    restart: unless-stopped
```

### Manual Docker

```bash
docker run -d \
  --name remediarr \
  -p 8189:8189 \
  -e SONARR_URL=http://sonarr:8989 \
  -e SONARR_API_KEY=your-api-key \
  -e RADARR_URL=http://radarr:7878 \
  -e RADARR_API_KEY=your-api-key \
  -e JELLYSEERR_URL=http://jellyseerr:5055 \
  -e JELLYSEERR_API_KEY=your-api-key \
  ghcr.io/sbcrumb/remediarr:latest
```

## Jellyseerr Configuration

Configure webhooks in **Jellyseerr → Settings → Notifications → Webhooks**:

### Webhook Settings
- **Webhook URL**: `http://your-server:8189/webhook/jellyseerr`
- **Request Method**: `POST`
- **Notification Types**: Check **only** "Issue Reported" (other types will cause loops)

### JSON Payload
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
    "problemSeason": "{{affected_season}}",
    "problemEpisode": "{{affected_episode}}"
  },
  "comment": {
    "comment_message": "{{comment_message}}"
  }
}
```

> **Important**: Only enable "Issue Reported" notifications to prevent processing loops

## Configuration

Remediarr is configured entirely through environment variables. See the [complete configuration guide](.env.example) for all options.

### Required Settings

| Variable | Description | Example |
|----------|-------------|---------|
| `SONARR_URL` | Sonarr base URL | `http://sonarr:8989` |
| `SONARR_API_KEY` | Sonarr API key | `abc123...` |
| `RADARR_URL` | Radarr base URL | `http://radarr:7878` |
| `RADARR_API_KEY` | Radarr API key | `def456...` |
| `JELLYSEERR_URL` | Jellyseerr base URL | `http://jellyseerr:5055` |
| `JELLYSEERR_API_KEY` | Jellyseerr API key | `ghi789...` |

### Keyword Customization

You can customize which keywords trigger each action type:

```bash
# TV Show Keywords
TV_AUDIO_KEYWORDS="no audio,no sound,missing audio,audio issue,wrong language"
TV_VIDEO_KEYWORDS="no video,video glitch,black screen,stutter,pixelation"  
TV_SUBTITLE_KEYWORDS="missing subs,no subtitles,bad subtitles,wrong subs"
TV_OTHER_KEYWORDS="buffering,playback error,corrupt file"

# Movie Keywords  
MOVIE_AUDIO_KEYWORDS="no audio,no sound,audio issue,wrong language"
MOVIE_VIDEO_KEYWORDS="no video,video missing,bad video,black screen"
MOVIE_SUBTITLE_KEYWORDS="missing subs,no subtitles,bad subtitles"
MOVIE_OTHER_KEYWORDS="buffering,playback error,corrupt file"
MOVIE_WRONG_KEYWORDS="wrong movie,incorrect movie,not the right movie"
```

### Security Options

```bash
# HMAC signature verification (recommended)
WEBHOOK_SHARED_SECRET="your-secret-key"

# Or custom header authentication
WEBHOOK_HEADER_NAME="X-Custom-Auth"
WEBHOOK_HEADER_VALUE="your-auth-token"
```

## Supported Issue Types

### TV Shows
- **Audio Issues**: "no audio", "missing audio", "wrong language" → Deletes episode file, triggers re-download
- **Video Issues**: "no video", "black screen", "pixelation" → Deletes episode file, triggers re-download  
- **Subtitle Issues**: "no subtitles", "subs out of sync" → Deletes episode file, triggers re-download
- **Other Issues**: "buffering", "corrupt file" → Deletes episode file, triggers re-download

### Movies
- **Audio/Video/Subtitle Issues**: Same behavior as TV shows
- **Wrong Movie**: "wrong movie", "incorrect movie" → Deletes all movie files, triggers new search
- **Other Issues**: "buffering", "corrupt file" → Deletes movie files, triggers new search

## User Coaching

When users don't include recognizable keywords, Remediarr posts helpful suggestions:

**User comment**: "this doesn't work"  
**Remediarr response**: "Tip for other issues: Include keywords like 'buffering', 'corrupt file', 'playback error' for automatic fixes."

## Notifications

### Gotify
```bash
GOTIFY_URL=https://gotify.example.com
GOTIFY_TOKEN=AbCdEf123456
GOTIFY_PRIORITY=5
```

### Apprise (Discord, Slack, Telegram, etc.)
```bash
APPRISE_URLS="discord://webhook_id/webhook_token,slack://hook_url"
```

## API Endpoints

- `GET /` - Basic status and version info
- `GET /health` - Simple health check  
- `GET /health/detailed` - Health check including external services
- `POST /webhook/jellyseerr` - Main webhook endpoint
- `GET /docs` - Interactive API documentation

## Troubleshooting

### Common Issues

**"Missing tvdbId/season/episode" error**
- Ensure your Jellyseerr webhook payload includes all the template variables
- Check that the issue was reported with season/episode information

**Issues not auto-closing**
- Some Jellyseerr versions don't support the close API endpoint
- Disable with `JELLYSEERR_CLOSE_ISSUES=false` if needed
- Remediarr will still comment when actions are taken

**Webhook loops**  
- Only enable "Issue Reported" in Jellyseerr webhook settings
- Don't enable "Issue Comment" or other event types

**Files not found in Sonarr/Radarr**
- Verify the content exists in your *arr apps
- Check that TVDB/TMDB IDs match between Jellyseerr and your *arr apps

### Debug Mode
```bash
LOG_LEVEL=DEBUG
```

This enables detailed logging of webhook processing, keyword matching, and API calls.

## Development

### Local Development
```bash
git clone https://github.com/sbcrumb/remediarr.git
cd remediarr
cp .env.example .env
# Edit .env with your settings
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8189
```

### Docker Development
```bash
docker build -t remediarr:dev .
docker run --rm -p 8189:8189 --env-file .env remediarr:dev
```

## Container Images

- **Latest stable**: `ghcr.io/sbcrumb/remediarr:latest`
- **Version tagged**: `ghcr.io/sbcrumb/remediarr:v1.0.0`  
- **Development**: `ghcr.io/sbcrumb/remediarr:dev`

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature-name`
3. Make your changes
4. Add tests if applicable
5. Commit: `git commit -m 'Add feature'`
6. Push: `git push origin feature-name`  
7. Open a Pull Request

Please update `.env.example` if you add new configuration options.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/sbcrumb/remediarr/issues)
- **Discussions**: [GitHub Discussions](https://github.com/sbcrumb/remediarr/discussions)
- **Documentation**: Check the `.env.example` file for all configuration options

## Donations

If Remediarr saves you time managing media issues:

- **GitHub Sponsors**: [sponsor sbcrumb](https://github.com/sponsors/sbcrumb)
- **Bitcoin**: `bc1qjc200yg9mc08uskmeka8zrjddp8lw2j6d8q0kn`

Thank you for supporting open source development!