---
name: hdec-p0a-verify
description: Use when verifying HDEC Executive Radar P0-A mock mode. Checks mock-only flow, forbidden fields, X API absence, no external calls, and notification safety.
---

# HDEC P0-A Verify

Verify the project in this order:

1. Confirm `APP_MODE=mock` is the default.
2. Confirm P0-A works without internet, API keys, RSS, Naver, OpenAI, Teams, or X.
3. Run or document these checks:
   - `curl -X POST http://localhost:8000/api/sense/run`
   - `curl http://localhost:8000/api/articles`
   - `grep -R "raw_payload" . --exclude-dir=.git`
   - `grep -RiE "api\.x\.com|twitter|x bearer token" . --exclude-dir=.git`
   - `grep -E "body|content|full_text|article_body" app/schema.sql`
4. Verify:
   - no raw_payload
   - no X API code
   - no body/content/full_text/article_body DB fields
   - Send button required before notification log sent status
   - OPENAI_API_KEY absent still passes
   - mock mode does not call external services
5. Report PASS/FAIL clearly.
