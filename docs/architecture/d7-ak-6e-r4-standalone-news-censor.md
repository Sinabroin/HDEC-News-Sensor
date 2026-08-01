# D7-AK-6E R4 — Standalone HDEC News Censor

## Decision

R4 publishes an independently addressable News Censor page without adding a
link, tab, button, or navigation item to any existing Daily, Dashboard,
Editorial, or Pages landing surface. The canonical public target is:

`/news-censor/latest.html`

Each successful live build also preserves a KST dated archive at:

`/news-censor/YYYY-MM-DD.html`

The launch design reference is `NEW_CENSOR.html`, sealed by SHA-256
`c4a1d129a9e8b6d824b961e2042f345cfc2eb405dcbc488a542e5bc6cee14804`.
R4 retains its compact masthead, category filtering, two-column news stream,
summary rail, responsive collapse, and inline article reader. It does not copy
the reference's external font dependency, embedded stale market observations,
or portal-discovery links.

## Ownership and file contract

- `scripts/build_news_censor.py` owns model derivation, publisher-authority
  revalidation, safe rendering, archive naming, and atomic output writes.
- `templates/news_censor.html` owns the standalone HTML, CSS, and browser-only
  filtering/reader interactions. It has no browser fetch, form, persistence,
  sender, or external runtime asset.
- `scripts/verify_news_censor.py` owns offline fixture verification, source
  authority assertions, no-navigation proof, workflow wiring checks, protected
  state hashing, and zero-network/zero-send proof.
- `docs/news-censor/latest.html` is the current generated page.
- `docs/news-censor/YYYY-MM-DD.html` is the immutable edition path for that KST
  day. Repeated successful hourly builds on the same day replace that day's
  edition with the latest verified observation; previous dates are retained.
- `.github/workflows/scheduled-live-refresh.yml` remains the sole static Pages
  publishing owner.

Generated HTML is never hand-edited. A deterministic development preview is
produced only from an explicit artifact:

```bash
TEAMS_AI_NEWS_WATCH=0 TELEGRAM_AUTO_SEND=0 NEWS_MODE=mock \
  python3 scripts/build_executive_brief.py --output-json /tmp/news-censor-demo.json
TEAMS_AI_NEWS_WATCH=0 TELEGRAM_AUTO_SEND=0 \
  python3 scripts/build_news_censor.py \
    --brief-json /tmp/news-censor-demo.json \
    --output-root docs/news-censor \
    --edition-date 2026-08-02
```

The preview is truthfully labelled `DEMO · deterministic fixture` and is not a
production launch result. The scheduled production workflow passes
`--brief-json` and `--require-live`; a mock fallback, failed collection, or
indeterminate artifact therefore cannot replace the last valid public edition.

## Data and authority flow

```text
collector → publisher-direct partition → scoring/briefing
          → HDEC_VALIDATED_EXECUTIVE_BRIEF_V1 JSON
          → report + dashboard + News Censor consumers
          → News Censor model revalidation → static HTML
          → dated archive + latest alias → GitHub Pages
```

The production builder never imports or invokes
`build_brief_via_mock_pipeline`. `--brief-json` is mandatory. The scheduled
workflow performs collection once, writes one atomic
`HDEC_VALIDATED_EXECUTIVE_BRIEF_V1` artifact, and passes that exact path to the
Executive report, operator report, summary dashboard, and News Censor. The
News Censor then calls `publisher_direct.assess_delivery_eligibility` for every
candidate.
Upstream executive relevance is treated as already qualified, while the common
final-authority requirements remain mandatory:

- `publisher_direct=true`;
- non-portal normalized publisher URL;
- title, source, and publisher date/fallback evidence;
- no quarantine state;
- canonical URL deduplication.

Only presentation fields are serialized into the browser JSON island.
Discovery URLs, raw provenance, source metadata, credentials, article bodies,
and production state are not exposed. Article summaries are escaped and all
reader DOM is created with `textContent`. The external origin action uses
`target="_blank"` with `rel="noopener noreferrer"`.

## Standalone page boundary

Existing page files and templates contain no `news-censor` link. R4 deliberately
does not modify:

- `docs/index.html`;
- `docs/daily/*.html`;
- `templates/index.html`;
- `templates/dashboard_preview.html`;
- `templates/editorial_daily.html`;
- `templates/editorial_review_console.html`.

The page embeds the repository-owned HDEC SVG as a data URI and includes its
own responsive CSS. At 680 px and below, the news/rail layout returns to normal
single-column flow, the lead becomes one column, the article grid becomes one
column, and the rail is non-sticky below the feed.

## Collector health and empty-state contract

The artifact carries one explicit status:

- `LIVE_HEALTHY_WITH_ARTICLES`: at least one source response succeeded and at
  least one publisher-direct article passed authority;
- `LIVE_HEALTHY_NO_ELIGIBLE_ARTICLES`: at least one source response succeeded,
  but no publisher-direct article passed authority;
- `LIVE_COLLECTION_FAILED`: live collection itself failed;
- `LIVE_FALLBACK_REJECTED`: a live attempt produced a mock fallback, which is
  never production input.

Health also carries request count, source count, successful-source count, raw
candidate count, publisher-direct eligible count, quarantine count, and final
portal URL count. Mode is never inferred from an environment variable.

A healthy zero publishes a LIVE page with
`현재 조건을 충족한 신규 기사가 없습니다`. It never restores the fixture,
never creates a Teams candidate, and never changes delivery state. Failed,
fallback, inconsistent, or indeterminate input exits nonzero before `latest`
or the dated edition is written.

## Production rails and image contract

- Market values come only from the artifact's repository market snapshot.
  Each item exposes provider, as-of, and delayed/proxy/stale/unavailable state;
  missing values render `N/A`.
- Weather comes from the artifact's `weather_snapshot`, which is collected once
  by `weather_risk` during artifact creation. Representative region, forecast
  timestamp, source, and collection timestamp remain visible; missing data is
  explicitly unavailable.
- Safety/incident rows come only from brief risk clusters whose supporting
  articles retain publisher-direct title/source/date/URL evidence. Otherwise
  the rail is unavailable.
- Article images use the existing editorial image resolver, byte validator, and
  quality gate. Accepted bytes are materialized under
  `news-censor/assets/images`; remote image hotlinks never enter HTML or the
  browser model. A deterministic embedded SVG is used only where no valid local
  image was materialized.
- The seven primary categories are fixed. Secondary filters are derived from
  actual topic/category/radar/decision tags and only nonzero filters render.

## Publishing and fail-closed behavior

The existing scheduled live-refresh job performs the following order:

1. run the News Censor offline verifier with all sender defaults closed;
2. collect once into a validated live brief JSON artifact, including collector
   health, market data, and weather risk;
3. run `build_news_censor.py --brief-json ... --require-live --json` as the
   artifact gate, then pass the same artifact to all static consumers;
4. stage `docs/news-censor` with the existing static output set;
5. commit and push only on `main` and only outside forced dry-run mode.

Any collector failure/fallback, inconsistent health, template failure,
unresolved marker, or write failure exits nonzero before `live_ok=true`. A
healthy zero is the sole zero-article success. The publish and notification
steps are unreachable for failures. The builder does not mutate sender state,
editorial state, or a repository database.

## Verification

The focused offline gate is:

```bash
TEAMS_AI_NEWS_WATCH=0 TELEGRAM_AUTO_SEND=0 \
  python3 scripts/verify_news_censor.py
```

It proves:

- explicit deterministic fixture artifact builds DEMO with zero attempted
  network or sender calls;
- production input has no implicit mock call;
- healthy live with articles and healthy live zero both build correctly;
- failed and fallback artifacts return nonzero without overwriting a valid live
  output;
- byte-identical latest and dated outputs from one render;
- `--require-live` rejection before any output write;
- fixed categories, nonzero dynamic tag filters, market/weather/safety rails,
  local-or-fallback images, responsive structure, keyboard reader open/close,
  and focus restoration;
- unique publisher-direct canonical URLs and zero portal URLs;
- no discovery, credential, browser-fetch, persistence, or mutation surface;
- no navigation addition to existing pages;
- scheduled workflow verification, live gate, and publish scope;
- byte-identical protected pages and production state before/after verification.

The R3 stack and existing static/report regression verifiers remain required.

## Sender and rollout boundary

R4 does not send Teams or Telegram messages. During implementation, CI, merge,
and deployment verification:

- `TEAMS_AI_NEWS_WATCH=0`
- `TELEGRAM_AUTO_SEND=0`

The existing Teams workflow remains the sole production owner for article
delivery. After deployment and public-page verification, the separately
authorized canary must use `production_canary=true` and `canary_cap=1` while the
scheduled watch is still disabled. Only SMTP-accepted success or a verified
no-eligible-live-article result permits the later repository-variable state:

- `TEAMS_AI_NEWS_MAX_ARTICLES=1`
- `TEAMS_AI_NEWS_WATCH=1`
- `TELEGRAM_AUTO_SEND=0`

No other transport, recipient, cap, or schedule change is part of R4.

## Rollback

Before merge, close the feature PR and delete only its feature branch. After
merge, revert the R4 feature commit; do not reset `main` or rewrite generated
history. If a production build is invalid, set `TEAMS_AI_NEWS_WATCH=0`, keep
`TELEGRAM_AUTO_SEND=0`, disable the scheduled publisher if necessary, and
revert the R4 commit. Existing Daily, Dashboard, Editorial, and sender-state
artifacts remain independent and must not be deleted.

`NEXT=R4_DRAFT_PR_CI_AND_PUBLIC_DEPLOYMENT`
