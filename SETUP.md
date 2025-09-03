# Remediarr Setup Guide

This guide walks you through setting up Remediarr step-by-step.

## Prerequisites

Before setting up Remediarr, ensure you have:

- **Jellyseerr** running and configured
- **Sonarr** running with TV shows imported
- **Radarr** running with movies imported  
- **Docker** or **Python 3.11+** installed
- API keys for all three services

## Step 1: Get API Keys

### Sonarr API Key
1. Open Sonarr web interface
2. Go to **Settings → General → Security**
3. Copy the **API Key**

### Radarr API Key  
1. Open Radarr web interface
2. Go to **Settings → General → Security**
3. Copy the **API Key**

### Jellyseerr API Key
1. Open Jellyseerr web interface  
2. Go to **Settings → General**
3. Copy the **API Key** (generate one if needed)

## Step 2: Configure Remediarr

### Option A: Docker Compose (Recommended)

Create `docker-compose.yml`:

```yaml
version: '3.8'
services:
  remediarr:
    image: ghcr.io/sbcrumb/remediarr:latest
    container_name: remediarr
    ports:
      - "8189:8189"
    environment:
      # Required Settings
      - SONARR_URL=http://sonarr:8989
      - SONARR_API_KEY=your-sonarr-api-key-here
      - RADARR_URL=http://radarr:7878
      - RADARR_API_KEY=your-radarr-api-key-here
      - JELLYSEERR_URL=http://jellyseerr:5055
      - JELLYSEERR_API_KEY=your-jellyseerr-api-key-here
      
      # Optional: Enable debug logging for initial testing
      - LOG_LEVEL=DEBUG
      
    restart: unless-stopped
```

Start with: `docker-compose up -d`

### Option B: Environment File

1. Download `.env.example` from the repository
2. Copy to `.env`: `cp .env.example .env`
3. Edit `.env` with your settings:

```bash
# Minimum required settings
SONARR_URL=http://localhost:8989
SONARR_API_KEY=your-sonarr-api-key-here
RADARR_URL=http://localhost:7878  
RADARR_API_KEY=your-radarr-api-key-here
JELLYSEERR_URL=http://localhost:5055
JELLYSEERR_API_KEY=your-jellyseerr-api-key-here

# Enable debug for testing
LOG_LEVEL=DEBUG
```

## Step 3: Configure Jellyseerr Webhook

1. Open Jellyseerr → **Settings → Notifications → Webhooks**
2. Click **Add Webhook**
3. Configure:

| Setting | Value |
|---------|--------|
| **Webhook URL** | `http://your-server-ip:8189/webhook/jellyseerr` |
| **Request Method** | `POST` |
| **Notification Types** | ✅ **Issue Reported** only |

4. **JSON Payload** - Copy this exactly:

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

5. Click **Save**

> **Critical**: Only enable "Issue Reported". Other notification types will cause processing loops.

## Step 4: Test the Setup

### 1. Check Remediarr Status
Visit `http://your-server:8189/health/detailed`

You should see all services as "healthy".

### 2. Test with a Real Issue

1. Find a TV episode or movie in Jellyseerr
2. Report an issue with one of these test comments:
   - "no audio in this episode"
   - "video glitch in this movie"  
   - "missing subtitles"

3. Watch the Remediarr logs:
   ```bash
   docker-compose logs -f remediarr
   ```

4. You should see:
   - Webhook received
   - Keywords detected
   - Files deleted from Sonarr/Radarr
   - New download triggered
   - Comment posted in Jellyseerr
   - Issue closed

## Step 5: Customize (Optional)

### Security
Add webhook authentication:
```bash
WEBHOOK_SHARED_SECRET=your-secret-key-here
```

Then add the same secret in Jellyseerr webhook settings.

### Notifications
Enable Gotify notifications:
```bash
GOTIFY_URL=https://gotify.yourdomain.com
GOTIFY_TOKEN=your-gotify-app-token
```

### Custom Keywords
Add your own trigger words:
```bash
TV_AUDIO_KEYWORDS=no audio,no sound,missing audio,silent,muted
MOVIE_WRONG_KEYWORDS=wrong movie,incorrect film,not the right one
```

## Troubleshooting

### Common Issues

**"Service Unhealthy" in health check**
- Verify URLs are correct and services are running
- Check API keys are valid
- Ensure network connectivity between services

**"Missing tvdbId/season/episode" errors**  
- Verify the JSON payload in Jellyseerr matches exactly
- Ensure issues are reported with season/episode info
- Check that TV shows exist in Sonarr with correct TVDB IDs

**Keywords not triggering actions**
- Check spelling in your test comments
- Enable `LOG_LEVEL=DEBUG` to see keyword matching
- Verify custom keywords are comma-separated

**Actions not working**
- Check Sonarr/Radarr permissions for file deletion
- Verify media exists in the *arr applications
- Review logs for API errors

### Debug Mode

Enable detailed logging:
```bash
LOG_LEVEL=DEBUG
```

This shows:
- Webhook payloads received
- Keyword matching process  
- API calls to Sonarr/Radarr
- File operations
- Comment posting

### Getting Help

If you're still having issues:

1. **Check the logs** with `LOG_LEVEL=DEBUG`
2. **Test the health endpoint**: `/health/detailed`  
3. **Verify your configuration** against `.env.example`
4. **Post an issue** on GitHub with logs and config (remove API keys!)

## Next Steps

Once working properly:
- Set `LOG_LEVEL=INFO` to reduce log verbosity
- Configure notifications for monitoring
- Add webhook security for production use
- Customize keywords for your users' language preferences

## Keywords Reference

### TV Shows
- **Audio**: "no audio", "no sound", "missing audio", "audio issue"
- **Video**: "no video", "black screen", "video glitch", "pixelation"  
- **Subtitles**: "no subtitles", "missing subs", "subs out of sync"
- **Other**: "buffering", "corrupt file", "playback error"

### Movies  
- **Audio/Video/Subs**: Same as TV shows
- **Wrong Movie**: "wrong movie", "incorrect movie", "not the right movie"
- **Other**: "buffering", "corrupt file", "playback error"

All keywords are case-insensitive and matched as substrings, so "No Audio!" will match "no audio".
