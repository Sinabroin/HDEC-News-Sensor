# D7-AK-6E-R3 Editorial Review Console

At 07:20 KST a no-send workflow builds a Daily candidate bundle and a static
two-pane review console. The left pane contains AI candidates plus a human-link
form. A reviewer can paste any selected article URL, enter its source, title,
summary, and category, select it alongside AI candidates, and rate every item.

The report ticker taxonomy and default top-to-bottom order are fixed to:

1. 투자·산업
2. 기업동향
3. 기술정보

The right pane contains a live Daily Brief preview. Titles and summaries are
editable in place. Summary text supports a safe bold command; approved rich text
is sanitized to `strong` and `br` only before the production renderer consumes
it. The reviewer may drag articles into another order, restore the AI selection,
restore the category order, download standalone HTML, export feedback JSONL,
and export an approved review JSON.

The visual language follows the supplied AI 경영 T&I Weekly Brief and the existing
Daily template: navy masthead, 680px executive reading column, headline hero,
Editor's Summary, category tickers, image cards, and compact source metadata.

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
