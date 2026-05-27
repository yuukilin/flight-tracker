# Flight Bot Worker

Cloudflare Worker for interactive Telegram commands.

## Deploy

```bash
cd worker
npm install -g wrangler          # if not installed
wrangler login                    # browser auth

# Create KV namespace (one-time)
wrangler kv namespace create STATE
# Copy the id into wrangler.toml

# Set secrets
wrangler secret put TELEGRAM_BOT_TOKEN
wrangler secret put GITHUB_TOKEN
wrangler secret put GITHUB_OWNER
wrangler secret put GITHUB_REPO
wrangler secret put AUTHORIZED_CHAT_ID

# Deploy
wrangler deploy

# Set Telegram webhook
curl "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=<WORKER_URL>"
```
