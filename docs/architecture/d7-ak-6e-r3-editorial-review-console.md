# D7-AK-6E-R3 Editorial Review Console

At 07:20 KST a no-send workflow builds a Daily candidate bundle and a static
two-pane review console. The left pane contains AI candidates plus a URL-first
article importer. An authenticated reviewer normally pastes only the article
URL. If automatic extraction fails, a collapsed direct-entry fallback exposes
the legacy source, title, summary, category, and image fields. Candidate rating
and editing remain available.

The report ticker taxonomy and default top-to-bottom order are fixed to:

1. 투자·산업
2. 기업동향
3. 기술정보

The right pane contains a live Daily Brief preview with three permanent sector
drop zones in the taxonomy order above. Every selected article remains inside
its sector; there is no category-external hero. A reviewer can drag any left-side
candidate directly into a sector, drag a selected article to an exact position
within a sector, or move it across sectors. Each operation immediately persists
the selected order and human category override to localStorage and returns the
review status to draft. The maximum remains six, and checkbox selection remains
the keyboard-accessible equivalent.

Titles and summaries are editable in place. Summary text supports a safe bold
command; approved rich text is sanitized to `strong` and `br` only before the
production renderer consumes it. Candidate titles and explicit original-article
links use only sanitized HTTP(S) destinations with new-tab isolation. The
only primary top action downloads the standalone **최종 브리핑** HTML. The
category-order, feedback JSONL, and approved-review JSON buttons are no longer
shown. AI recommendation reset is in the secondary menu and requires explicit
confirmation that selections, edits, ratings, and imported articles will be
discarded. This UI cleanup does not alter the Python approved-review or 08:14
AI fallback contracts.

## R3-V7 authenticated URL article import

The static GitHub Pages console never fetches a publisher URL. It calls
`POST /api/editorial/import-article` on the existing FastAPI/Vercel Operator
API with `credentials: include`. The route reuses the current Origin allowlist,
CORS preflight policy, edge/shared-secret authorization, and GitHub OAuth
HttpOnly signed session. In `origin` mode, article import is intentionally not
one of the anonymous low-risk actions, so a valid operator session is required.
The endpoint accepts JSON only, returns transformed fields rather than fetched
HTML, and maps failures to stable client-safe codes without exception details.

`app/editorial_article_import.py` owns the network and extraction boundary:

- HTTP(S) only; userinfo, literal IPs, internal suffixes, and nonstandard ports
  are rejected.
- DNS must return only globally routable addresses. Every redirect is resolved
  and revalidated, with at most three redirects.
- Fetch timeout is at most eight seconds. HTML is streamed to a 2 MB ceiling,
  images to a 5 MB ceiling, and only HTML media types reach the article parser.
- JSON-LD Article/NewsArticle, Open Graph, document headings, semantic
  article/main containers, and scored paragraphs are applied in a deterministic
  priority order. Script, navigation, advertising, share, comment, related, and
  copyright boilerplate is excluded and duplicate paragraphs are removed.
- The full article body and HTML are discarded after processing. Only a
  maximum 1,200-character verification excerpt is returned.

The bounded extractive summarizer scores source sentences by title overlap,
specific low-frequency terms, numeric evidence, length, and position. It keeps
two to four source-supported sentences, targets roughly 250–500 characters, and
does not introduce claims. R3-V6 `analyze_editorial_category` then receives the
extracted title, summary, and source; its complete scores, signals, and reason
are stored on the imported candidate.

On success the browser adds the result to `manualCandidates` as a compatible
`human_link` record with `collection_source_kind=url_import`, selects it, and
appends it to the classified sector. The six-selection limit and canonical URL
duplicate guard run both before and after the request. Later human drag/drop,
editing, deletion, and ordering remain authoritative and are saved immediately
to localStorage. Loading, cancellation, success, duplicate, safe error, retry,
and failure-only manual fallback states are visible.

The import API URL is public configuration, never a credential. The console
builder accepts `--article-import-api-url` or `ARTICLE_IMPORT_API_URL`, validates
the exact HTTPS endpoint path, and embeds an empty value by default. With no
configuration, import controls are disabled with an explanatory message while
candidate editing, drag/drop, rating, and final briefing download continue.

### Publisher-direct portal resolution

Portal and intermediary URLs are discovery inputs, never final article
authorities. Known Daum, Naver, Google News, MSN/Yahoo redistribution, search,
and URL-shortener hosts fail the publisher-direct test. For a Daum-like input,
the server first fetches the bounded portal page, then checks a publisher
redirect, canonical metadata, JSON-LD article URLs, original/syndication
metadata, and an unambiguous outbound original-article link. The chosen
publisher candidate is passed through the complete DNS/SSRF policy again and
its HTML is fetched separately for title, source, date, body, summary, category,
and image extraction.

The response and imported candidate retain:

- `input_url`, `discovery_url`, and `discovery_source`
- `publisher_url`, `publisher_domain`, and `publisher_direct`
- `portal_source`, `portal_resolution_reason`, and `portal_fallback_used`

`canonical_url`, `selected_url`, both original-article links, and the standalone
brief always use the validated publisher URL. `source` comes from the publisher
page rather than the portal. The browser also denies known portal/search/
shortener URLs when rendering pre-existing candidate links. A portal page with
no single safe publisher article URL returns `PORTAL_ORIGINAL_NOT_FOUND`; an
unsafe target or redirect remains a safety error. Neither condition adds a
candidate, and the direct-entry fallback is then available. There is no
portal-as-publisher fallback.

## R3-V6 deterministic category analysis

`analyze_editorial_category` scores title, summary, and source signals for
투자·산업, 기업동향, and 기술정보. Title matches have the strongest weight,
summary matches have a smaller weight, source matches are supplemental, and an
existing suggested category contributes only a weak prior. The fixed taxonomy
order resolves non-empty ties; a completely signal-free item defaults to
기술정보. Candidate JSON stores the score map, matched signals, and a readable
reason. A category explicitly chosen for a human link or changed by a reviewer
remains authoritative downstream.

## R3-V6 local preview images

Live console builds pass normalized articles through the existing
`materialize_preview_images` safety boundary in a child of `/tmp`. Only bytes
that pass its download, MIME, magic-byte, and quality checks are copied into:

- `docs/editorial/review/<edition>/assets/images/`
- `docs/editorial/review/latest/assets/images/`

Candidate image URLs are relative `assets/images/...` paths, so the console
never renders a remote image URL. Fixture builds make no image request and use a
deterministic local SVG. Every image is lazy-decoded and has an explicit
load-error fallback. Image materialization counters are retained in both
manifests. When the unchanged Daily publisher consumes the dated candidate
bundle, the review contract rebases that same local asset to
`../review/<edition>/assets/images/...`; the production Daily template therefore
does not need to change or fetch the original remote image.

For R3-V7 imports, representative image candidates follow JSON-LD, Open Graph,
Twitter, `image_src`, and article-body order. The server applies the same public
destination, redirect, MIME, magic-byte, Pillow decode, and editorial quality
checks used by preview image materialization. SVG, mismatched MIME/magic,
oversized, invalid, logo-like, and default images are rejected. Accepted
JPEG/PNG/WebP input is decoded, resized within 1280×720, and re-encoded as a
quality-bounded JPEG data URL (about 250 KB binary / 350 KB data URL maximum).
The browser validator accepts only existing local assets or bounded JPEG/PNG/
WebP data URLs. This keeps an imported image inside localStorage and standalone
HTML without inserting a remote image source; failures use the existing visual
fallback.

The visual language follows the supplied AI 경영 T&I Weekly Brief and the existing
Daily template: navy masthead, a compact executive reading column, fixed category
headings and tickers, image cards, and compact source metadata.

Human-link learning is explainable and bounded. Feedback records under the
preserved review contract learn source, category, URL domain, and title-keyword
adjustments. A manually supplied and selected link creates a small capped
domain/keyword seed even before the general minimum sample threshold; it never
creates an unlimited source allowlist.

At the 08:14 Daily publish step:

1. a valid version-2 approved review preserves the exact selected order, edited
   titles, sanitized bold summaries, category tickers, and human-supplied links;
2. without approval, the first six AI/profile candidates use the default
   투자·산업 → 기업동향 → 기술정보 order;
3. if the candidate bundle is absent or malformed, the existing live collection
   and automatic selection path runs unchanged.

The browser stores draft work in localStorage. The console itself writes no
production state and sends no message. The scheduled workflow publishes only the
candidate console under `docs/editorial/review`.

## Existing collector review and R3-V8 boundary

The current automatic collector is already **direct-aware**, but not yet
strictly publisher-direct:

- `live_collector.py` discovers through Google News RSS, attempts a bounded
  Google decoder or HTTP redirect/canonical recovery, and writes a recovered
  publisher URL to `source_metadata.source_url`. The original aggregator URL is
  intentionally retained in the raw row.
- `naver_news_provider.py` prefers Naver API `originallink`; when absent it uses
  the Naver `link`, which can remain a portal URL.
- `news_access.choose_article_link` prioritizes canonical/original/publisher
  fields and truthfully labels unresolved Google/portal URLs as fallbacks.
- `editorial_briefings.resolve_publisher_article_url` additionally checks RSS
  source/original/content fields and bounded redirect, canonical, Open Graph,
  and outbound candidates. Direct articles receive a source-quality advantage
  in selection.
- However, unresolved aggregators are still eligible, deduplication can use
  their selected portal URL, and Daily/Teams renderers ultimately use
  `EditorialArticle.selected_url`. Thus the production path does not yet
  guarantee publisher-canonical authority for every displayed or sent link.

Changing those persisted collection and delivery semantics in this R3-V7 PR
would cross the console/import scope and could conflict with the separate PR #13
operations recovery. No production collector, delivery workflow, Daily
template, or production state is changed here.

The next publisher-direct collector migration should affect, at minimum,
`app/live_collector.py`, `app/naver_news_provider.py`, `app/news_access.py`,
`app/editorial_briefings.py`, provider/source configuration, and their offline
verifiers. The staged plan is:

1. add a versioned publisher RSS/newsroom/official-institution source registry
   and collect those sources before discovery providers;
2. preserve input/discovery provenance separately while requiring one validated
   publisher canonical URL before executive-surface eligibility;
3. quarantine unresolved portal rows instead of rendering or sending them;
4. deduplicate and cluster on normalized publisher canonical URL, then migrate
   legacy source identities with audit counters and reversible shadow reports;
5. require Daily HTML, dashboard, Teams, and Telegram link audits to report zero
   portal/search/shortener authorities before activation.

The R3-V8 verifier should use only mock resolver/opener fixtures for direct RSS,
official newsrooms, Daum/Naver/Google discovery success and failure, ambiguous
outbound links, unsafe redirects, canonical deduplication, and zero-portal final
HTML/message payloads. Rollout should compare shadow candidate counts and
publisher diversity before removing the current labeled fallback behavior.

## Human-link collection learning boundary

A newly supplied link immediately receives a small, bounded ranking seed for its
domain and meaningful title keywords. Supplemental Google News RSS queries are not
created from a single example. The same domain or keyword must appear in at least
three approved human-link records. The console builder then runs at most six
editorial-only supplemental queries, two results per query and twelve results total,
and merges them through the existing provider deduplication path. This changes the
future candidate pool without changing dashboard workflows or delivery behavior.

`NEXT=R3_V8_PUBLISHER_DIRECT_COLLECTOR`
