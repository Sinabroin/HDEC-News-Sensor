# D7-AG-2 — Operator API deployment and Hormuz fixed-risk activation

## Implemented contract

- Deployment entrypoint: `app.operator_api:app` (`pyproject.toml`)
- Public browser POST targets:
  - `/api/operator/collect`
  - `/api/operator/send`
  - `/api/operator/send-teams`
- Server workflows:
  - collect → `scheduled-live-refresh.yml`
  - Telegram → `telegram-notify.yml` with `approve_send=true`
  - Teams → `email-alert.yml` with `approve_send_email=true`, `send_to_teams=true`
- PIN verification and GitHub credentials stay server-side.
- The dashboard never POSTs directly to GitHub, Telegram, Teams, or Naver.
- Hormuz is always present in the main risk surface. A real matching article is shown when
  collected; otherwise the card says `금일 신규 기사 없음 / 감시 유지`.

## Production activation

The repository is ready for Vercel's FastAPI Python runtime. Authentication to both Vercel
and GitHub is required for the following external-state changes.

```bash
# 1. Authenticate and deploy the minimal FastAPI entrypoint.
npx vercel@latest login
npx vercel@latest --prod

# 2. Add production server secrets in Vercel. Never use `--value` in shared shell history.
npx vercel@latest env add GH_OPERATOR_TOKEN production
npx vercel@latest env add OPERATOR_SHARED_SECRET production
npx vercel@latest env add OPERATOR_REPO production
npx vercel@latest env add OPERATOR_ALLOWED_ORIGINS production

# Values:
# OPERATOR_REPO=Sinabroin/HDEC-News-Sensor
# OPERATOR_ALLOWED_ORIGINS=https://sinabroin.github.io
# GH_OPERATOR_TOKEN=fine-grained PAT or GitHub App token scoped to this repository,
#                   with Actions write permission only as required for dispatch.
```

Redeploy after adding environment variables, then verify the public health endpoint:

```bash
curl -fsS https://YOUR-OPERATOR-HOST/api/operator/health
# Expected: {"status":"ok","operator_api_enabled":true}
```

Inject only the public HTTPS base URL into the GitHub repository variable and rebuild:

```bash
gh auth login
gh variable set OPERATOR_API_BASE \
  -R Sinabroin/HDEC-News-Sensor \
  --body "https://YOUR-OPERATOR-HOST"
gh workflow run scheduled-live-refresh.yml \
  -R Sinabroin/HDEC-News-Sensor \
  --ref main
```

After Pages publishes the regenerated dashboard, run the read-only public smoke:

```bash
python3 scripts/smoke_public_operator_hormuz.py
```

The smoke performs GET requests only. It does not submit a PIN, dispatch a workflow, or send
Telegram/Teams messages.
