# Twitch → YouTube Shorts clipbot

Runs 3x/day on GitHub Actions (free). Pulls top recent clips from the streamers in `config.yaml`,
turns them vertical (blurred background), and uploads them as Shorts crediting the streamer.

## Setup (one time, ~20 min)

1. **YouTube channel** – create it on your Google account.
2. **Twitch API** – go to dev.twitch.tv/console → Register Application
   (OAuth redirect: `http://localhost`, category: Other). Copy the Client ID and make a Client Secret.
3. **Google Cloud**
   - console.cloud.google.com → new project → enable **YouTube Data API v3**.
   - OAuth consent screen → External → add your email as a test user,
     then set Publishing status to **In production** (otherwise the token expires after 7 days).
   - Credentials → Create OAuth client ID → **Desktop app** → download JSON as `client_secret.json`.
4. **Get the refresh token** (on your computer):
   ```
   pip install google-auth-oauthlib
   python get_token.py
   ```
   Log in with the YouTube channel's account (click "Advanced → continue" on the warning). Copy the 3 values it prints.
5. **GitHub** – make a repo, upload these files (NOT client_secret.json), then
   Settings → Secrets and variables → Actions → add:
   `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`.
6. Edit `config.yaml` with your approved streamers, then Actions → clipbot → **Run workflow** to test.

## Notes
- New API projects upload as **private** until Google audits the app. Request the audit at
  support.google.com/youtube/contact/yt_api_form (free).
- Quota: each upload costs 1,600 of 10,000 daily units → max ~6 uploads/day.
- Keep written permission (DM/email) from each streamer — it's your defense against copyright claims.
