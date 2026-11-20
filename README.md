# Telegram → YouTube streaming uploader (free)

Forward a video to your bot → a GitHub Actions runner streams it from Telegram to YouTube
in RAM (no disk, nothing on your device). Up to 2 GB per file; jobs may run up to 6 h.

## One-time setup

### 1. Telegram
- @BotFather → `/newbot` → save the **bot token**.
- https://my.telegram.org → API development tools → save **api_id** and **api_hash**.
- Get your numeric user id (e.g. via @userinfobot). Only this id may use the bot.

### 2. YouTube / Google
- console.cloud.google.com → new project → enable **YouTube Data API v3**.
- OAuth consent screen: External, add yourself as test user, then **Publish app (In production)**.
  (In "Testing" the refresh token dies after 7 days.) The "unverified app" warning is fine for personal use.
- Credentials → Create OAuth client ID → **Desktop app** → download as `uploader/client_secret.json`.
- On your computer, once: `pip install google-auth-oauthlib && python uploader/get_refresh_token.py`
  → copy the printed **YT_REFRESH_TOKEN**. Delete client_secret.json afterwards.
- Channel must be phone-verified for videos > 15 min.

### 3. GitHub (public repo = free runners)
- Create a **public** repo `tg-yt-uploader`, push this folder to `main`.
- Settings → Secrets and variables → Actions → add secrets:
  `TG_API_ID, TG_API_HASH, TG_BOT_TOKEN, YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN`
- Optional variable `DEFAULT_PRIVACY` = `private` | `unlisted` | `public` (default private).
- Create a fine-grained token (Settings → Developer settings) limited to this repo with
  **Actions: Read and write** → this is `GH_TOKEN` for the Worker.

### 4. Cloudflare Worker
```
cd worker
# edit GH_REPO in wrangler.toml
npx wrangler deploy
npx wrangler secret put WEBHOOK_SECRET     # any random string
npx wrangler secret put ALLOWED_USER_ID    # your numeric Telegram id
npx wrangler secret put GH_TOKEN           # token from step 3
```
Point Telegram at the Worker (use the URL wrangler printed):
```
curl "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \
  -d url="https://tg-yt-webhook.<you>.workers.dev" \
  -d secret_token="<same WEBHOOK_SECRET>"
```

## Use
Send/forward a video to the bot. Caption (optional): first line = title, rest = description,
add `#public`, `#unlisted` or `#private` to override the default.

## Notes
- Repo is public, so Actions logs are public. The script logs only percentages, never titles.
- Resume: on any network/5xx error it asks YouTube for the received offset and re-reads Telegram from there.
- Parallel uploads: every message starts its own job (free plan allows 20 at once).
