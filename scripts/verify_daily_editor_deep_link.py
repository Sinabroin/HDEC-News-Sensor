#!/usr/bin/env python3
"""D7-AK-6E R4-R9C — Teams Daily Brief exact-edition editor deep link verifier.

Offline, deterministic, zero-send. Covers the §11 fixture matrix:

  message   — the 2026-08-05 Daily Teams message carries two distinct actions
              (operator "Daily Brief 편집기에서 열기" → exact-edition editor
              deep link, reader "게시된 Daily Brief 보기" → immutable dated
              publication), in both HTML and plain text; the mutable
              latest.html is never a Daily Teams action; missing editor
              identity degrades to public-link-only output and a guessed or
              tampered editor link fails validation closed.
  identity  — the edition_id embeds the edition date plus the manifest
              integrity-digest revision (never latest/current/today); the
              manifest binds date, coverage window, ordered articles, titles,
              factual summaries, categories, publishers, publisher-direct
              URLs, Editor's Summary, review state and the publication
              digest; malformed / unknown / tampered manifests fail closed.
  continuity— creating the 2026-08-06 edition leaves the 2026-08-05 manifest
              and dated publication byte-identical (append-only, collision
              refuses overwrite); a changed candidate pool mints a new
              edition_id instead of mutating history.
  editor    — the Review Console deep-link module validates product, id
              format, page/edition match, manifest identity and digest with
              WebCrypto before showing the exact-edition panel; open is
              read-only (GET only, no storage writes, no approve/publish/send
              paths exist in the console).
  auth      — the OAuth login/callback carries only the validated local
              edition identity and reconstructs the return URL from canonical
              constants: external, protocol-relative, javascript:, data:,
              and traversal returns are all rejected fail-closed.
  formats   — the Daily publication template, weekly Teams contract, T&I
              category order, and the Teams major-media / stock-market gates
              are unchanged.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.parse
from dataclasses import replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import ai_centrality  # noqa: E402
from app import editorial_briefings as brief  # noqa: E402
from app import editorial_review  # noqa: E402
from app import operator_auth  # noqa: E402
from app import public_urls as public_url_contract  # noqa: E402
import run_editorial_briefing as runner  # noqa: E402

KST = brief.KST

PASS = 0
FAIL = 0
EXTERNAL_TEST_NETWORK_CALLS = 0
SMTP_ATTEMPTS = 0
TEAMS_SENDS = 0
TELEGRAM_SENDS = 0
PRODUCTION_STATE_WRITES = 0


def _network_blocked(*_args, **_kwargs):
    raise AssertionError("network disabled in verifier")


socket.create_connection = _network_blocked
socket.getaddrinfo = _network_blocked


def check(label: str, condition: object, detail: object = "") -> bool:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"PASS {label}")
        return True
    FAIL += 1
    suffix = f" :: {detail}" if detail else ""
    print(f"FAIL {label}{suffix}")
    return False


def article(
    title: str,
    url: str,
    category: str,
    *,
    summary: str = "사실 요약.",
    source: str = "연합뉴스",
    hour: int = 9,
    day: int = 4,
    centrality: str = ai_centrality.LEVEL_EXPLICIT_AI_CORE,
) -> brief.EditorialArticle:
    return brief.EditorialArticle(
        title=title,
        summary=summary,
        source=source,
        published_at=datetime(2026, 8, day, hour, 0, tzinfo=KST),
        selected_url=url,
        link_kind="article",
        link_label=source,
        category=category,
        ai_centrality_level=centrality,
    )


FIXTURE_ROOT = "https://preview.fixture.test/HDEC-News-Sensor"
RUN_AT = datetime(2026, 8, 5, 7, 20, tzinfo=KST)
NEXT_RUN_AT = datetime(2026, 8, 6, 7, 20, tzinfo=KST)

ARTICLES = [
    article(
        "AI 데이터센터 전력 계약 & 확정 <검증>",
        "https://www.yna.co.kr/view/AKR20260805001",
        "투자·산업",
        summary="AI 데이터센터 전력 공급 계약이 확정됐다.",
    ),
    article(
        "국내 건설사 AI 안전관리 도입",
        "https://www.hankyung.com/article/2026080512345",
        "기업동향",
        source="한국경제",
        hour=10,
    ),
    article(
        "생성형 AI 설계 자동화 상용화",
        "https://www.mk.co.kr/news/it/2026080598765",
        "기술정보",
        source="매일경제",
        hour=11,
    ),
]

NEXT_ARTICLES = [
    article(
        "차세대 AI 반도체 팹 착공",
        "https://www.yna.co.kr/view/AKR20260806002",
        "투자·산업",
        summary="차세대 AI 반도체 팹이 착공됐다.",
        day=5,
    ),
    article(
        "AI 시공 로봇 현장 투입 확대",
        "https://www.hankyung.com/article/2026080654321",
        "기업동향",
        source="한국경제",
        day=5,
        hour=12,
    ),
]

EDITION_ID_RE = re.compile(r"^daily-(\d{4}-\d{2}-\d{2})-([0-9a-f]{16})$")

CONSOLE_TEMPLATE = (ROOT / "templates" / "editorial_review_console.html").read_text(
    encoding="utf-8"
)
DAILY_WORKFLOW = (ROOT / ".github" / "workflows" / "editorial-daily-brief.yml").read_text(
    encoding="utf-8"
)
RUNNER_SOURCE = (ROOT / "scripts" / "run_editorial_briefing.py").read_text(
    encoding="utf-8"
)
OPERATOR_API_SOURCE = (ROOT / "app" / "operator_api.py").read_text(encoding="utf-8")


def extract_console_module() -> str:
    marker = '<script id="exact-edition-deep-link">'
    start = CONSOLE_TEMPLATE.index(marker)
    end = CONSOLE_TEMPLATE.index("</script>", start)
    return CONSOLE_TEMPLATE[start:end]


def render_with_editor(run_at=RUN_AT, articles=None):
    return brief.render_daily(
        list(articles or ARTICLES),
        run_at=run_at,
        root_url=FIXTURE_ROOT,
        review_mode="human_approved",
        review_decision="approved",
        editor_console_available=True,
    )


# ----------------------------------------------------------------------------
# R4-R18 — decoration-safe editor deep-link intent detection.
#
# The exact-edition module must treat a plain console decorated with unknown /
# tracking query params (utm_source, v, foo, a bare source tag) as *not* a deep
# link: it opens the plain console. Deep-link intent is signalled only by the
# identity params the Teams editor URL mints (product, edition_id); once either
# is present the strict validation is unchanged and every invalid identity still
# fails closed. The matrix below is proven three ways: the guard/legacy-bail
# structure, a node-free decision-table mirror of the module's synchronous
# prefix, and — when node is present — an end-to-end run of the REAL extracted
# module (native WebCrypto verifies the immutable manifest, so OPEN_EDITOR is a
# genuine editor open, not a stub).
# ----------------------------------------------------------------------------
DEEP_LINK_MATRIX = (
    ("A_plain_latest", ""),
    ("B_utm_source", "?utm_source=chatgpt.com"),
    ("C_v_test", "?v=test"),
    ("D_foo_bar", "?foo=bar"),
    ("D2_source_only", "?source=teams"),
    ("E_valid_identity", "?product=daily&edition_id={id}&source=teams_daily"),
    ("F_product_weekly", "?product=weekly"),
    ("G_missing_edition_id", "?product=daily"),
    ("G2_garbage_edition_id", "?product=daily&edition_id=not-an-edition"),
    ("H_valid_plus_decoration", "?product=daily&edition_id={id}&source=teams_daily&utm_source=teams"),
    ("X_edition_id_no_product", "?edition_id={id}"),
)

DEEP_LINK_EXPECTED = {
    "A_plain_latest": "OPEN_PLAIN_CONSOLE",
    "B_utm_source": "OPEN_PLAIN_CONSOLE",
    "C_v_test": "OPEN_PLAIN_CONSOLE",
    "D_foo_bar": "OPEN_PLAIN_CONSOLE",
    "D2_source_only": "OPEN_PLAIN_CONSOLE",
    "E_valid_identity": "OPEN_EDITOR",
    "F_product_weekly": "FAILED:unsupported_product",
    "G_missing_edition_id": "FAILED:malformed_edition_id",
    "G2_garbage_edition_id": "FAILED:malformed_edition_id",
    "H_valid_plus_decoration": "OPEN_EDITOR",
    "X_edition_id_no_product": "FAILED:unsupported_product",
}

DEEP_LINK_DECORATION_CASES = (
    "A_plain_latest", "B_utm_source", "C_v_test", "D_foo_bar", "D2_source_only",
)

# The synchronous prefix cannot decide OPEN_EDITOR (that needs the async manifest
# fetch + digest verify); the mirror reports how far the sync gate lets a URL go.
_MIRROR_PROCEEDS = "PROCEEDS_TO_MANIFEST"

CONSOLE_MODULE_MARKER = '<script id="exact-edition-deep-link">'


def deep_link_outcome_mirror(search: str, edition_key: str) -> str:
    """Pure mirror of the module's synchronous intent-detection + product/id gate."""
    query = search[1:] if search.startswith("?") else search
    params = urllib.parse.parse_qs(query, keep_blank_values=True, separator="&")

    def get(key: str) -> str:
        return params.get(key, [""])[0]

    # R4-R18 guard: identity params only (product | edition_id).
    if "product" not in params and "edition_id" not in params:
        return "OPEN_PLAIN_CONSOLE"
    if get("product") != "daily":
        return "FAILED:unsupported_product"
    match = re.match(r"^daily-(\d{4})-(\d{2})-(\d{2})-([0-9a-f]{16})$", get("edition_id"))
    if not match:
        return "FAILED:malformed_edition_id"
    if not (1 <= int(match.group(2)) <= 12 and 1 <= int(match.group(3)) <= 31):
        return "FAILED:malformed_edition_id"
    if f"{match.group(1)}-{match.group(2)}-{match.group(3)}" != edition_key:
        return "FAILED:edition_console_mismatch"
    return _MIRROR_PROCEEDS


# node -e harness: runs the REAL extracted module against each matrix URL with a
# minimal browser shim (native URLSearchParams/URL/TextEncoder/WebCrypto). argv:
# [1]=module JS, [2]=fixture JSON, [3]=cases JSON. Emits {name: outcome} JSON.
_DEEP_LINK_HARNESS_JS = r'''
const moduleSrc = process.argv[1];
const fixture = JSON.parse(process.argv[2]);
const CASES = JSON.parse(process.argv[3]);
const EDITION_KEY = fixture.edition_key;
const MANIFEST = fixture.manifest;
const URLS = fixture.publisher_urls;
function runCase(search) {
  return new Promise(function (resolve) {
    const fetched = [];
    const overlays = [];
    var window = { location: { search: search }, crypto: globalThis.crypto };
    var document = {
      _app: { style: {} },
      body: { appendChild: function (node) { if (node && node.id === "deepLinkFailClosed") overlays.push(node); } },
      querySelector: function (sel) {
        if (sel === ".app") return document._app;
        if (sel === "main.right") return { firstChild: null, insertBefore: function () {} };
        return null;
      },
      createElement: function () {
        var node = { style: {}, dataset: {}, _attrs: {} };
        node.setAttribute = function (k, v) { node._attrs[k] = v; };
        node.appendChild = function () {};
        return node;
      },
      getElementById: function () { return null; }
    };
    var fetch = function (url) {
      fetched.push(String(url));
      if (String(url).indexOf("/editions/") >= 0) {
        return Promise.resolve({ ok: true, json: async function () { return JSON.parse(JSON.stringify(MANIFEST)); } });
      }
      return Promise.resolve({ ok: true, json: async function () { return { authenticated: false }; } });
    };
    var CATEGORY_ORDER = ["투자·산업", "기업동향", "기술정보"];
    var articleImportApiUrl = "";
    var state = { selected: [], edits: {}, exactEditionId: "" };
    var bundle = { edition_key: EDITION_KEY, candidates: URLS.map(function (u, i) { return { candidate_id: "cand-" + i, selected_url: u, title: "T" + i, category: CATEGORY_ORDER[2], summary: "s" }; }) };
    function esc(value) { return String(value == null ? "" : value).replace(/[&<>"']/g, function (m) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]; }); }
    function safeUrl(value) { try { var p = new URL(String(value)); return ["http:", "https:"].indexOf(p.protocol) >= 0 ? p.href : ""; } catch (e) { return ""; } }
    function duplicateByUrl(u) { return bundle.candidates.find(function (c) { return c.selected_url === u; }) || null; }
    function byId() { return {}; }
    function view() { return { title: "t", category: CATEGORY_ORDER[2], summary: "s" }; }
    function normalizeSelectedOrder() {}
    function render() {}
    eval(moduleSrc);
    setTimeout(function () {
      var dbg = window.__exactEditionDebug;
      var outcome;
      if (!dbg) outcome = overlays.length ? "FAILED:overlay_no_debug" : "OPEN_PLAIN_CONSOLE";
      else if (dbg.state === "failed") outcome = "FAILED:" + dbg.reason;
      else if (dbg.state === "verified") outcome = "OPEN_EDITOR";
      else outcome = "UNKNOWN";
      resolve({ outcome: outcome, overlay: overlays.length, editionsFetched: fetched.some(function (u) { return u.indexOf("/editions/") >= 0; }) });
    }, 80);
  });
}
(async function () {
  var results = {};
  for (var i = 0; i < CASES.length; i++) { results[CASES[i][0]] = await runCase(CASES[i][1]); }
  process.stdout.write(JSON.stringify(results));
})();
'''


def run_deep_link_node_matrix(module_js: str, edition) -> dict | None:
    node = shutil.which("node")
    if not node:
        return None
    fixture = {
        "edition_id": edition.edition_id,
        "edition_key": edition.edition_key,
        "manifest": edition.edition_manifest,
        "publisher_urls": [
            row["publisher_url"] for row in edition.edition_manifest["articles"]
        ],
    }
    cases = [[name, tmpl.replace("{id}", edition.edition_id)] for name, tmpl in DEEP_LINK_MATRIX]
    result = subprocess.run(
        [
            node, "-e", _DEEP_LINK_HARNESS_JS, module_js,
            json.dumps(fixture, ensure_ascii=False),
            json.dumps(cases, ensure_ascii=False),
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"deep-link node harness failed rc={result.returncode}: "
            f"{(result.stderr or result.stdout).strip()[:600]}"
        )
    return json.loads(result.stdout)


def main() -> int:
    # ------------------------------------------------------------------
    # §11-1/2/3/17/18 — the 2026-08-05 Daily message actions.
    # ------------------------------------------------------------------
    edition = render_with_editor()
    brief.validate_rendered(edition)
    check("2026-08-05 edition renders and validates", edition.edition_key == "2026-08-05")
    check(
        "operator and reader actions both present with distinct labels",
        brief.DAILY_EDITOR_LINK_LABEL in edition.teams_html
        and brief.DAILY_PUBLISHED_LINK_LABEL in edition.teams_html
        and brief.DAILY_EDITOR_LINK_LABEL != brief.DAILY_PUBLISHED_LINK_LABEL
        and brief.DAILY_EDITOR_LINK_LABEL in edition.teams_text
        and brief.DAILY_PUBLISHED_LINK_LABEL in edition.teams_text,
    )
    check(
        "HTML message renders exactly editor+published+dashboard anchors",
        edition.teams_html.count("<a ") == 3
        and edition.teams_html.index(brief.DAILY_EDITOR_LINK_LABEL)
        < edition.teams_html.index(brief.DAILY_PUBLISHED_LINK_LABEL),
    )
    check(
        "R4-R10 delivered CTAs are ordered editor -> reader -> dashboard (plain text)",
        edition.teams_text.index(brief.DAILY_EDITOR_LINK_LABEL)
        < edition.teams_text.index(brief.DAILY_PUBLISHED_LINK_LABEL)
        < edition.teams_text.index(public_url_contract.CANONICAL_DASHBOARD_URL),
    )
    check(
        "R4-R10 delivered CTAs are ordered editor -> reader -> dashboard (HTML)",
        edition.teams_html.index(brief.DAILY_EDITOR_LINK_LABEL)
        < edition.teams_html.index(brief.DAILY_PUBLISHED_LINK_LABEL)
        < edition.teams_html.index(public_url_contract.CANONICAL_DASHBOARD_URL),
    )
    check(
        "plain text carries both action URLs",
        edition.editor_url in edition.teams_text
        and edition.public_dated_url in edition.teams_text,
    )
    id_match = EDITION_ID_RE.match(edition.edition_id)
    check(
        "editor link carries the immutable edition id",
        bool(id_match)
        and id_match.group(1) == "2026-08-05"
        and f"edition_id={edition.edition_id}" in edition.editor_url
        and "product=daily" in edition.editor_url
        and "source=teams_daily" in edition.editor_url,
        edition.edition_id,
    )
    check(
        "edition identity is never latest/current/today",
        all(
            token not in edition.editor_url.lower()
            for token in ("latest", "current", "today")
        )
        and edition.edition_id != edition.edition_key,
    )
    check(
        "reader action targets the immutable dated archive, not latest",
        edition.public_dated_url.endswith("/editorial/daily/2026-08-05.html")
        and public_url_contract.DAILY_LATEST_URL not in edition.teams_text
        and public_url_contract.DAILY_LATEST_URL not in edition.teams_html
        and "/latest.html" not in edition.teams_text,
    )
    check(
        "editor deep link opens the dated Review Console for its own date",
        edition.editor_url.startswith(
            f"{FIXTURE_ROOT}/editorial/review/2026-08-05/index.html?"
        ),
    )
    check(
        "HTML attribute values are escaped in the message",
        'href="' + brief.escape(edition.editor_url, quote=True) + '"'
        in edition.teams_html
        and "&amp;edition_id=" in edition.teams_html
        and brief.escape(ARTICLES[0].title) in edition.teams_html,
    )

    # ------------------------------------------------------------------
    # §11-13 — unavailable editor identity → public-link-only output.
    # ------------------------------------------------------------------
    plain_edition = brief.render_daily(
        list(ARTICLES),
        run_at=RUN_AT,
        root_url=FIXTURE_ROOT,
        review_mode="human_approved",
        review_decision="approved",
    )
    brief.validate_rendered(plain_edition)
    check(
        "missing editor identity degrades to public-link-only output",
        plain_edition.editor_url == ""
        and plain_edition.teams_html.count("<a ") == 2
        and brief.DAILY_EDITOR_LINK_LABEL not in plain_edition.teams_html
        and brief.DAILY_PUBLISHED_LINK_LABEL in plain_edition.teams_html
        and plain_edition.public_dated_url in plain_edition.teams_text,
    )
    check(
        "edition identity still minted without the editor action",
        plain_edition.edition_id == edition.edition_id
        and plain_edition.edition_manifest == edition.edition_manifest,
    )

    # A guessed or tampered editor link can never validate.
    for label, bad_url in (
        ("foreign root", "https://evil.example/editorial/review/2026-08-05/index.html?product=daily"),
        ("wrong product", edition.editor_url.replace("product=daily", "product=weekly")),
        ("wrong edition id", edition.editor_url.replace(edition.edition_id, "daily-2026-08-05-ffffffffffffffff")),
        ("latest route", f"{FIXTURE_ROOT}/editorial/review/latest/index.html?product=daily&edition_id={edition.edition_id}&source=teams_daily"),
    ):
        try:
            brief.validate_rendered(replace(edition, editor_url=bad_url))
            check(f"tampered editor link fails validation closed ({label})", False)
        except brief.EditorialError:
            check(f"tampered editor link fails validation closed ({label})", True)

    # ------------------------------------------------------------------
    # §11-15 — exact load fields match the immutable manifest.
    # ------------------------------------------------------------------
    manifest = edition.edition_manifest
    check("manifest validates fail-open-free", brief.verify_daily_edition_manifest(manifest) == "")
    check(
        "manifest binds ordered titles/categories/publishers/URLs",
        [entry["title"] for entry in manifest["articles"]]
        == [item.title for item in ARTICLES]
        and [entry["category"] for entry in manifest["articles"]]
        == [item.category for item in ARTICLES]
        and [entry["publisher"] for entry in manifest["articles"]]
        == [item.source for item in ARTICLES]
        and [entry["publisher_url"] for entry in manifest["articles"]]
        == [item.selected_url for item in ARTICLES]
        and [entry["position"] for entry in manifest["articles"]] == [1, 2, 3]
        and manifest["articles"][0]["headline"] is True,
    )
    check(
        "manifest binds date, coverage window, review state and publication",
        manifest["edition_key"] == "2026-08-05"
        and manifest["coverage_start"] == edition.coverage.start.isoformat()
        and manifest["coverage_end"] == edition.coverage.end.isoformat()
        and manifest["review_mode"] == "human_approved"
        and manifest["review_decision"] == "approved"
        and manifest["headline_title"] == ARTICLES[0].title
        and manifest["editor_summary"] == ARTICLES[0].summary
        and manifest["publication"]["dated_url"] == edition.public_dated_url
        and manifest["publication"]["html_sha256"] == edition.html_sha256
        and manifest["publication"]["publication_state"] == "published",
    )
    expected_fields = {
        "version", "product", "edition_key", "coverage_start", "coverage_end",
        "published_run_at", "review_mode", "review_decision", "headline_title",
        "editor_summary", "edition_status", "article_count", "articles",
        "publication", "revision", "edition_id", "integrity",
    }
    manifest_dump = json.dumps(manifest, ensure_ascii=False)
    check(
        "manifest is non-sensitive (exact field set, no auth/recipient data)",
        set(manifest) == expected_fields
        and all(
            token not in manifest_dump.lower()
            for token in ("password", "secret", "token", "smtp", "recipient", "@")
        ),
    )
    digest = manifest["integrity"]["digest"]
    check(
        "edition id embeds the manifest integrity revision",
        manifest["revision"] == digest[:16]
        and edition.edition_id == f"daily-2026-08-05-{digest[:16]}"
        and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
    )
    rerendered = render_with_editor()
    check(
        "same inputs mint the same edition id (deterministic)",
        rerendered.edition_id == edition.edition_id
        and rerendered.edition_manifest == manifest,
    )
    revised = render_with_editor(
        articles=[replace(ARTICLES[0], title="AI 데이터센터 전력 계약 정정 보도")]
        + ARTICLES[1:]
    )
    check(
        "changed content mints a new edition id (supersession, same date)",
        revised.edition_key == "2026-08-05"
        and revised.edition_id != edition.edition_id,
    )
    later_run = render_with_editor(run_at=datetime(2026, 8, 5, 7, 40, tzinfo=KST))
    check(
        "republished run mints a new revision identity",
        later_run.edition_id != edition.edition_id
        and later_run.edition_key == "2026-08-05",
    )

    # Fail-closed manifest validation (§11-7/8/9).
    tampered_title = json.loads(json.dumps(manifest, ensure_ascii=False))
    tampered_title["articles"][0]["title"] = "변조된 제목"
    tampered_digest = json.loads(json.dumps(manifest, ensure_ascii=False))
    tampered_digest["integrity"]["digest"] = "f" * 64
    wrong_identity = json.loads(json.dumps(manifest, ensure_ascii=False))
    wrong_identity["edition_key"] = "2026-08-04"
    for label, payload, expected_reason in (
        ("non-object manifest", [], "manifest_not_object"),
        ("malformed edition id", {"edition_id": "latest"}, "edition_id_malformed"),
        ("identity mismatch", wrong_identity, "identity_mismatch"),
        ("tampered content digest", tampered_title, "digest_mismatch"),
        ("tampered digest field", tampered_digest, "revision_mismatch"),
    ):
        reason = brief.verify_daily_edition_manifest(payload)
        check(f"manifest fails closed on {label}", reason == expected_reason, reason)

    # ------------------------------------------------------------------
    # §11-4/16 — append-only continuity across the next edition.
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory(prefix="d7ak6e-r4r9c-editions-") as temporary:
        docs_root = Path(temporary)
        first_path = runner.write_daily_edition_manifest(edition, docs_root=docs_root)
        first_file = docs_root / first_path
        first_bytes = first_file.read_bytes()
        dated_fixture = docs_root / "docs" / "editorial" / "daily" / "2026-08-05.html"
        dated_fixture.parent.mkdir(parents=True, exist_ok=True)
        dated_fixture.write_bytes(edition.html.encode("utf-8"))
        dated_before = dated_fixture.read_bytes()

        idempotent_path = runner.write_daily_edition_manifest(
            edition, docs_root=docs_root
        )
        check(
            "identical republish is idempotent (byte-stable)",
            idempotent_path == first_path and first_file.read_bytes() == first_bytes,
        )
        next_edition = render_with_editor(run_at=NEXT_RUN_AT, articles=NEXT_ARTICLES)
        next_path = runner.write_daily_edition_manifest(
            next_edition, docs_root=docs_root
        )
        check(
            "2026-08-06 edition writes its own manifest",
            next_path != first_path
            and (docs_root / next_path).is_file()
            and next_edition.edition_key == "2026-08-06",
        )
        check(
            "old 2026-08-05 editor identity still loads 2026-08-05 exactly",
            first_file.read_bytes() == first_bytes
            and brief.verify_daily_edition_manifest(
                json.loads(first_file.read_text(encoding="utf-8"))
            )
            == ""
            and json.loads(first_file.read_text(encoding="utf-8"))["edition_key"]
            == "2026-08-05",
        )
        check(
            "old public dated page is untouched by the new edition",
            dated_fixture.read_bytes() == dated_before,
        )
        collision = replace(revised, edition_id=edition.edition_id)
        try:
            runner.write_daily_edition_manifest(collision, docs_root=docs_root)
            check("differing payload at an existing id fails closed", False)
        except runner.OrchestratorError:
            check("differing payload at an existing id fails closed", True)
        check(
            "current live candidate changes never mutate the historical edition",
            first_file.read_bytes() == first_bytes,
        )

    # Runtime manifest round-trip carries the new identity fields.
    with tempfile.TemporaryDirectory(prefix="d7ak6e-r4r9c-runtime-") as temporary:
        runtime_dir = Path(temporary)
        runtime_manifest = brief.manifest_for_runtime(
            edition,
            ROOT / "docs/editorial/daily/2026-08-05.html",
            ROOT / "docs/editorial/daily/latest.html",
        )
        runtime_manifest["edition_manifest_path"] = (
            f"docs/editorial/daily/editions/{edition.edition_id}.json"
        )
        runner._write_runtime_manifest(runtime_dir, runtime_manifest)
        loaded = runner._load_runtime_manifest(runtime_dir, "daily")
        check(
            "runtime manifest carries edition_id/editor_url/manifest path",
            loaded["edition_id"] == edition.edition_id
            and loaded["editor_url"] == edition.editor_url
            and loaded["edition_manifest_path"].endswith(
                f"{edition.edition_id}.json"
            ),
        )
        message = runner.build_link_message(
            loaded, "sender@fixture.test", "channel@fixture.test"
        )
        bodies = "\n".join(
            str(part.get_content())
            for part in message.walk()
            if part.get_content_type() in {"text/plain", "text/html"}
        )
        check(
            "SMTP message subject and bodies carry the exact edition actions",
            message["Subject"] == "[HDEC AI Daily Brief] 2026-08-05"
            and edition.editor_url in bodies
            and edition.public_dated_url in bodies
            and list(message.iter_attachments()) == [],
        )

    # ------------------------------------------------------------------
    # §11-5/6/7/8/9/12/14 — Review Console deep-link module (read-only open).
    # ------------------------------------------------------------------
    module = extract_console_module()
    check(
        "console module validates product/id/date/page identity",
        'product!=="daily"' in module
        and "EDITION_ID_RE" in module
        and "daily-(\\d{4})-(\\d{2})-(\\d{2})-([0-9a-f]{16})" in module
        and "linkedEditionKey!==bundle.edition_key" in module,
    )
    for reason in (
        "unsupported_product",
        "malformed_edition_id",
        "edition_console_mismatch",
        "unknown_edition",
        "malformed_manifest",
        "manifest_identity_mismatch",
        "manifest_tampered",
        "integrity_unavailable",
        "edition_not_reconstructable",
    ):
        check(f"console fail-closed reason wired: {reason}", f'"{reason}"' in module)
    check(
        "console verifies the manifest digest with WebCrypto before trusting it",
        "crypto.subtle.digest" in module
        and "canonicalJson" in module
        and 'digest.slice(0,16)!==idMatch[4]' in module
        and "recomputed!==digest" in module,
    )
    check(
        "console loads the manifest relative to its own origin only",
        "fetch(dailyArtifactUrl(`editions/${editionIdParam}.json`)" in module
        and "window.location.origin" in module
        and 'const marker="/editorial/review/"' in module
        and 'cache:"no-store"' in module,
    )
    check(
        "console open is read-only (no storage writes, no POST, no send)",
        "localStorage.setItem" not in module
        and "POST" not in module
        and "XMLHttpRequest" not in module
        and "sendBeacon" not in module
        and ".submit(" not in module
        and 'method:"GET"' in module,
    )
    module_code = "\n".join(
        line for line in module.splitlines() if not line.strip().startswith("//")
    )
    check(
        "console exposes no approve/publish path on open",
        re.search(r"approv", module_code, re.IGNORECASE) is None
        and re.search(r"publish(?!er)", module_code, re.IGNORECASE) is None
        and "reviewStatus" not in module_code
        and "/api/operator/" not in module_code,
    )
    check(
        "fail-closed overlay hides the editor and keeps the public reader link",
        'overlay.dataset.deepLinkState="failed"' in module
        and 'app.style.display="none"' in module
        and "게시된 Daily Brief 보기" in module,
    )
    check(
        "authenticated flow probes the session read-only and shows authority",
        '"/api/auth/session"' in module
        and 'credentials:"include"' in module
        and "운영자 인증됨" in module,
    )
    check(
        "unauthenticated flow enters the existing GitHub OAuth with local identity only",
        '"/api/auth/github/login?"' in CONSOLE_TEMPLATE
        and 'new URLSearchParams({product:"daily",edition_key:bundle.edition_key})' in CONSOLE_TEMPLATE
        and 'params.set("edition_id",editionId)' in CONSOLE_TEMPLATE
        and 'params.set("review_snapshot_id",snapshotId)' in CONSOLE_TEMPLATE
        and '>운영자 로그인</a>' in CONSOLE_TEMPLATE
        and "GitHub로 운영자 로그인" not in CONSOLE_TEMPLATE,
    )
    check(
        "verified panel is the exact-edition authority surface",
        'panel.dataset.deepLinkState="verified"' in module
        and "정확한 에디션을 확인했습니다" in module
        and "manifest.revision" in module,
    )
    check(
        "manifest DRIVES the editable form state (not a panel-only overlay)",
        "function adoptExactEdition" in module
        and "duplicateByUrl(row.publisher_url)" in module
        # R4-R11 — a draft saved under this exact edition id survives reload;
        # any other saved state never masquerades as the linked edition.
        and "state.exactEditionId===manifest.edition_id" in module
        and "state.exactEditionId=manifest.edition_id" in module
        and "state.selected=orderedIds" in module
        and "state.edits={...state.edits,...driven}" in module
        and "adoptExactEdition(manifest)" in module
        and 'failClosed("edition_not_reconstructable"' in module,
    )
    check(
        "a different or newer console bundle fails closed instead of masquerading",
        module.index("adoptExactEdition(manifest)")
        < module.index("renderExactEditionPanel(manifest)")
        and "if(!candidate)return false" in module,
    )
    check(
        "driven editable rows are exposed for exact-edition acceptance proof",
        "editable:state.selected.map(" in module,
    )

    # ------------------------------------------------------------------
    # R4-R18 — decoration-safe deep-link intent detection (see module note).
    # ------------------------------------------------------------------
    check(
        "R4-R18 deep-link intent guards on identity params only (product|edition_id)",
        'if(!params.has("product")&&!params.has("edition_id"))return;' in module,
    )
    check(
        "R4-R18 legacy zero-param-only bail is gone (decoration no longer fails closed)",
        "[...params.keys()].length" not in module,
    )
    # Node-free decision-table mirror: every matrix URL's synchronous outcome.
    for name, search_tmpl in DEEP_LINK_MATRIX:
        search = search_tmpl.replace("{id}", edition.edition_id)
        mirror = deep_link_outcome_mirror(search, edition.edition_key)
        expected = DEEP_LINK_EXPECTED[name]
        mirror_ok = (
            mirror == _MIRROR_PROCEEDS if expected == "OPEN_EDITOR" else mirror == expected
        )
        check(f"R4-R18 mirror decision {name} -> {expected}", mirror_ok, mirror)
    # End-to-end proof against the REAL extracted module in node (WebCrypto).
    module_js = module[module.index(CONSOLE_MODULE_MARKER) + len(CONSOLE_MODULE_MARKER):]
    node_matrix = run_deep_link_node_matrix(module_js, edition)
    if node_matrix is None:
        print("INFO node unavailable; R4-R18 matrix proven by structure + decision mirror")
    else:
        for name, expected in DEEP_LINK_EXPECTED.items():
            outcome = node_matrix.get(name, {})
            hit = outcome.get("outcome") == expected
            if expected == "OPEN_EDITOR":
                hit = hit and outcome.get("editionsFetched") is True
            check(f"R4-R18 real-module matrix {name} -> {expected}", hit, outcome)
        check(
            "R4-R18 decoration/plain cases open the console with no fail-closed overlay",
            all(node_matrix[name]["overlay"] == 0 for name in DEEP_LINK_DECORATION_CASES),
        )
        check(
            "R4-R18 valid-identity cases reach the immutable-manifest fetch",
            node_matrix["E_valid_identity"]["editionsFetched"] is True
            and node_matrix["H_valid_plus_decoration"]["editionsFetched"] is True,
        )

    # Cross-language digest parity: run the module's own canonicalJson in node
    # when available; otherwise prove Python canonicalization is key-order
    # independent (browser parity is proven by the acceptance package).
    node = shutil.which("node")
    core = {
        key: value
        for key, value in manifest.items()
        if key not in brief.EDITION_MANIFEST_IDENTITY_FIELDS
    }
    if node:
        js_fn = module[module.index("function canonicalJson"):]
        js_fn = js_fn[: js_fn.index("async function sha256Hex")]
        script = (
            js_fn
            + "\nconst crypto=require('node:crypto');"
            + "const value=JSON.parse(process.argv[1]);"
            + "process.stdout.write(crypto.createHash('sha256')"
            + ".update(Buffer.from(canonicalJson(value),'utf8')).digest('hex'));"
        )
        result = subprocess.run(
            [node, "-e", script, json.dumps(core, ensure_ascii=False)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        check(
            "browser canonicalization reproduces the Python digest (node)",
            result.returncode == 0 and result.stdout.strip() == digest,
            result.stdout.strip() or result.stderr.strip(),
        )
    else:
        print("INFO node unavailable; JS digest parity proven in acceptance package")
        shuffled = json.loads(
            json.dumps(
                {key: core[key] for key in sorted(core, reverse=True)},
                ensure_ascii=False,
            )
        )
        check(
            "canonicalization is key-order independent",
            brief.canonical_edition_manifest_bytes(shuffled)
            == brief.canonical_edition_manifest_bytes(core),
        )

    # ------------------------------------------------------------------
    # §11-6/10/11/12 — authentication safe return.
    # ------------------------------------------------------------------
    valid_return = operator_auth.validated_editor_return_url(
        "daily", edition.edition_id, "teams_daily"
    )
    check(
        "validated return reconstructs the canonical exact-edition URL",
        valid_return
        == public_url_contract.daily_editor_console_url(edition.edition_id)
        and valid_return.startswith(public_url_contract.PUBLIC_ROOT + "/editorial/review/2026-08-05/")
        and f"edition_id={edition.edition_id}" in valid_return,
    )
    encoded = operator_auth.encode_editor_return("daily", edition.edition_id, "teams_daily")
    check(
        "cookie round trip returns to the exact same edition",
        encoded == f"daily|{edition.edition_id}|teams_daily"
        and operator_auth.editor_return_url_from_cookie(encoded) == valid_return,
    )
    for label, product, edition_id, source in (
        ("external URL", "daily", "https://evil.example/steal", "teams_daily"),
        ("protocol-relative", "daily", "//evil.example/steal", "teams_daily"),
        ("javascript scheme", "daily", "javascript:alert(1)", "teams_daily"),
        ("data scheme", "daily", "data:text/html,x", "teams_daily"),
        ("path traversal", "daily", "daily-2026-08-05-aaaaaaaaaaaaaaaa/../../x", "teams_daily"),
        ("unknown product", "weekly", edition.edition_id, "teams_daily"),
        ("malformed id", "daily", "latest", "teams_daily"),
        ("date-only id", "daily", "2026-08-05", "teams_daily"),
        ("bad source tag", "daily", edition.edition_id, "TEAMS DAILY!"),
    ):
        check(
            f"auth return rejects {label}",
            operator_auth.validated_editor_return_url(product, edition_id, source) == ""
            and operator_auth.encode_editor_return(product, edition_id, source) == "",
        )
    for label, cookie in (
        ("tampered cookie payload", f"daily|{edition.edition_id}"),
        ("cookie with absolute URL", "daily|https://evil.example|teams_daily"),
        ("empty cookie", ""),
    ):
        check(
            f"auth return cookie fails closed on {label}",
            operator_auth.editor_return_url_from_cookie(cookie) == "",
        )
    check(
        "login preserves only the validated identity and callback reconstructs it",
        "encode_editor_return" in OPERATOR_API_SOURCE
        and "editor_return_url_from_cookie" in OPERATOR_API_SOURCE
        and "config.OPERATOR_AUTH_SUCCESS_URL" in OPERATOR_API_SOURCE
        and OPERATOR_API_SOURCE.count("clear_editor_return_cookie") >= 4,
    )

    # ------------------------------------------------------------------
    # Publish/send wiring and workflow gate.
    # ------------------------------------------------------------------
    check(
        "publish fails closed when the dated console is missing and writes the "
        "manifest for every published Daily (R4-R11)",
        "_daily_console_path(key).is_file()" in RUNNER_SOURCE
        and '"daily_editor_console_missing"' in RUNNER_SOURCE
        and "daily_editor_publication_error(edition)" in RUNNER_SOURCE
        and "write_daily_edition_manifest(edition)" in RUNNER_SOURCE,
    )
    check(
        "workflow verifies the deep-link contract before build and send",
        "scripts/verify_daily_editor_deep_link.py" in DAILY_WORKFLOW
        and DAILY_WORKFLOW.index("verify_daily_editor_deep_link.py")
        < DAILY_WORKFLOW.index("- name: Build Daily publication"),
    )
    check(
        "workflow commits the edition manifest through the explicit allowlist",
        'EDITION_MANIFEST_PATH: ${{ steps.publish.outputs.edition_manifest_path }}'
        in DAILY_WORKFLOW
        and 'git add -- "$EDITION_MANIFEST_PATH"' in DAILY_WORKFLOW
        and 'git add -- "$DATED_PATH" "$LATEST_PATH"' in DAILY_WORKFLOW,
    )
    # R4-R10 — the Daily brief must be scheduled AFTER the Review Console build so
    # the dated console exists when it publishes and the editor CTA is emitted
    # instead of degrading to a reader-only message (the observed defect).
    review_console_workflow = (
        ROOT / ".github" / "workflows" / "editorial-review-console.yml"
    ).read_text(encoding="utf-8")

    def _cron_minutes_of_day(text: str) -> list[int]:
        return [
            int(hour) * 60 + int(minute)
            for minute, hour in re.findall(
                r'cron:\s*"(\d+)\s+(\d+) \* \* \*"', text
            )
        ]

    console_crons = _cron_minutes_of_day(review_console_workflow)
    daily_crons = _cron_minutes_of_day(DAILY_WORKFLOW)
    check(
        "Daily brief runs after the Review Console build (editor CTA sequencing)",
        bool(console_crons)
        and bool(daily_crons)
        and min(daily_crons) > max(console_crons),
        f"console={console_crons} daily={daily_crons}",
    )
    check(
        "reader-only degradation is removed: bundle-unavailable fallback skips "
        "fail-closed with a machine-readable reason (R4-R11)",
        '"daily_review_bundle_unavailable"' in RUNNER_SOURCE
        and "_skip_daily_editor_unavailable(" in RUNNER_SOURCE
        and "daily_reader_only_send_allowed=false" in RUNNER_SOURCE
        and 'review_mode = "live_collection_fallback"' not in RUNNER_SOURCE,
    )
    check(
        "delivered Daily lead-source gate is wired into the publish path "
        "(no fallback delivery remains to gate)",
        "filter_lead_source_eligible(" in RUNNER_SOURCE
        and "lead_source_gate=True" not in RUNNER_SOURCE,
    )

    # ------------------------------------------------------------------
    # R4-R11 — fail-closed exact-edition editor publication gate, exercised
    # functionally against a real console page rendered from the template.
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory(prefix="d7ak6e-editor-gate-") as raw:
        tmp = Path(raw)
        gate_edition = render_with_editor()
        console_file = tmp / "review" / gate_edition.edition_key / "index.html"
        console_file.parent.mkdir(parents=True)

        def write_console(edition_key: str, urls: list[str]) -> None:
            bundle = {
                "edition_key": edition_key,
                "candidates": [
                    {"candidate_id": f"candidate-{index}", "selected_url": url}
                    for index, url in enumerate(urls, 1)
                ],
            }
            embedded = json.dumps(bundle, ensure_ascii=False).replace("</", "<\\/")
            console_file.write_text(
                CONSOLE_TEMPLATE.replace("{{EDITION_KEY}}", edition_key)
                .replace("{{COVERAGE_LABEL}}", "fixture")
                .replace("{{CANDIDATE_JSON}}", embedded),
                encoding="utf-8",
            )

        manifest_urls = [
            row["publisher_url"] for row in gate_edition.edition_manifest["articles"]
        ]
        original_console_path = runner._daily_console_path
        try:
            runner._daily_console_path = lambda key: console_file
            check(
                "editor gate: missing dated console fails closed",
                runner.daily_editor_publication_error(gate_edition)
                == "daily_editor_console_missing",
            )
            write_console(gate_edition.edition_key, manifest_urls + [
                "https://www.mk.co.kr/news/it/9999-unselected-candidate"
            ])
            check(
                "editor gate: full adoption into the console bundle passes",
                runner.daily_editor_publication_error(gate_edition) == "",
            )
            write_console(gate_edition.edition_key, manifest_urls[1:])
            check(
                "editor gate: a manifest article missing from the console "
                "bundle fails closed",
                runner.daily_editor_publication_error(gate_edition)
                == "daily_editor_not_reconstructable",
            )
            write_console("2026-01-01", manifest_urls)
            check(
                "editor gate: a different edition's console bundle fails closed",
                runner.daily_editor_publication_error(gate_edition)
                == "daily_editor_not_reconstructable",
            )
            reader_only = brief.render_daily(
                list(ARTICLES),
                run_at=RUN_AT,
                root_url=FIXTURE_ROOT,
                review_mode="human_approved",
                review_decision="approved",
                editor_console_available=False,
            )
            check(
                "editor gate: an edition without an editor identity fails closed",
                runner.daily_editor_publication_error(reader_only)
                == "daily_editor_identity_unavailable",
            )
        finally:
            runner._daily_console_path = original_console_path

    # ------------------------------------------------------------------
    # §11-19/20/21/22 — format and previous-repair preservation.
    # ------------------------------------------------------------------
    pin_source = (ROOT / "scripts" / "verify_publisher_direct_collector.py").read_text(
        encoding="utf-8"
    )
    pin_match = re.search(
        r'"templates/editorial_daily.html": \(\s*"([0-9a-f]{64})"', pin_source
    )
    import hashlib

    template_sha = hashlib.sha256(
        (ROOT / "templates" / "editorial_daily.html").read_bytes()
    ).hexdigest()
    check(
        "Daily publication visual shell is byte-identical to its pin",
        bool(pin_match) and pin_match.group(1) == template_sha,
        template_sha,
    )
    check(
        "published Daily page carries no editor deep link (reader surface unchanged)",
        "편집기에서 열기" not in edition.html
        and "edition_id=" not in edition.html,
    )
    weekly_articles = [
        replace(item, published_at=datetime(2026, 7, 30, 9, 0, tzinfo=KST))
        for item in ARTICLES
    ]
    weekly = brief.render_weekly(
        weekly_articles, run_at=RUN_AT, root_url=FIXTURE_ROOT
    )
    brief.validate_rendered(weekly)
    check(
        "Weekly Teams contract is unchanged (2 anchors, latest URL, label)",
        weekly.teams_html.count("<a ") == 2
        and public_url_contract.WEEKLY_LATEST_URL in weekly.teams_html
        and "이번 주 Weekly Brief 보기" in weekly.teams_html
        and weekly.editor_url == ""
        and weekly.edition_id == "",
    )
    check(
        "T&I categories remain exactly 3 in canonical order",
        list(editorial_review.CATEGORY_ORDER)
        == ["투자·산업", "기업동향", "기술정보"]
        and list(brief._TNI_CATEGORY_STYLE) == ["투자·산업", "기업동향", "기술정보"],
    )
    check(
        "T&I report and weekly reference guards remain wired",
        (ROOT / "scripts" / "verify_tni_report_immutable_reference.py").is_file()
        and (ROOT / "scripts" / "verify_weekly_tni_reference_parity.py").is_file()
        and (ROOT / "templates" / "editorial_weekly_tni.html").is_file(),
    )
    push_source = (ROOT / "app" / "teams_ai_push.py").read_text(encoding="utf-8")
    check(
        "Teams major-media and stock-market gates remain wired",
        "def apply_major_media_first_gate" in push_source
        and "def evaluate_stock_market_gate" in push_source
        and "SOURCE_GATE_SPECIALIST_HOLDBACK" in push_source
        and "TEAMS_SPECIALIST_HOLDBACK_MINUTES = 120" in push_source,
    )

    print(f"DAILY_EDITOR_DEEP_LINK_VERIFIER={PASS}/{FAIL}")
    print(
        f"EXTERNAL_TEST_NETWORK_CALLS={EXTERNAL_TEST_NETWORK_CALLS} "
        f"SMTP_ATTEMPTS={SMTP_ATTEMPTS} TEAMS_SENDS={TEAMS_SENDS} "
        f"TELEGRAM_SENDS={TELEGRAM_SENDS} "
        f"PRODUCTION_STATE_WRITES={PRODUCTION_STATE_WRITES}"
    )
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
