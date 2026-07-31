# D7-AK-6E-R3 Editorial Review Console

At 07:20 KST a no-send workflow builds a Daily candidate bundle and a static
two-pane review console. The left pane contains AI candidates plus a human-link
form. A reviewer can paste any selected article URL, enter its source, title,
summary, and category, select it alongside AI candidates, and rate every item.

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
reviewer may restore the AI selection, restore the category order, download
standalone HTML, export feedback JSONL, and export an approved review JSON.

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

The visual language follows the supplied AI 경영 T&I Weekly Brief and the existing
Daily template: navy masthead, a compact executive reading column, fixed category
headings and tickers, image cards, and compact source metadata.

Human-link learning is explainable and bounded. Exported feedback learns source,
category, URL domain, and title-keyword adjustments. A manually supplied and
selected link creates a small capped domain/keyword seed even before the general
minimum sample threshold; it never creates an unlimited source allowlist.

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

## Human-link collection learning boundary

A newly supplied link immediately receives a small, bounded ranking seed for its
domain and meaningful title keywords. Supplemental Google News RSS queries are not
created from a single example. The same domain or keyword must appear in at least
three approved human-link records. The console builder then runs at most six
editorial-only supplemental queries, two results per query and twelve results total,
and merges them through the existing provider deduplication path. This changes the
future candidate pool without changing dashboard workflows or delivery behavior.
