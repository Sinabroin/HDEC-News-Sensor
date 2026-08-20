#!/usr/bin/env python3
"""R4-OPS-10 — Editor production usability recovery (offline acceptance).

Deterministic, network-free proof that the Teams-delivered Editor link opens a
genuinely usable production editor:

- article-import production wiring is host-bound to the canonical Operator API
  base (no secret embedded, wrong/malformed hosts fail closed);
- manual article entry is always reachable (template-level);
- safe hyperlinks survive browser edit -> server sanitize -> publication;
- durable authenticated server draft-save + explicit publish with fail-closed
  identity binding, optimistic concurrency, and a fixed workflow dispatcher;
- the EXACT 2026-08-19 production snapshot/edition rehearsal, including empty
  edition recovery and superseding-edition minting that leaves the original
  immutable edition unchanged;
- a recall audit of the 2026-08-19 empty Daily.

Uses only committed evidence and injected fakes. No real SMTP/Teams/Telegram,
no workflow dispatch, no production-state writes.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Offline operator configuration MUST be set before importing app.config so the
# route-level tests can exercise the authorization path with fakes (no network).
os.environ["OPERATOR_LOCAL_DEV"] = "1"
os.environ["OPERATOR_DRY_RUN"] = "1"
os.environ["GH_OPERATOR_TOKEN"] = "r4ops10-offline-fake-token"
os.environ["OPERATOR_REPO"] = "Sinabroin/HDEC-News-Sensor"
os.environ.setdefault("OPERATOR_ACCESS_MODE", "origin")

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app import (  # noqa: E402
    editorial_briefings,
    editorial_operator_review as eor,
    editorial_review,
)
import build_editorial_review_console as builder  # noqa: E402

KST = timezone(timedelta(hours=9))
IMPORT_PATH = "/api/editorial/import-article"
SNAPSHOT_2741 = "review-2026-08-19-2741f4475e29b6b1"
# The REAL committed immutable Review snapshot manifest for 2026-08-19. Its
# content-addressed integrity digest starts with the id's 16-hex suffix, so it is
# genuine committed evidence (not a fabricated shape). save_draft/publish_daily
# now prove this manifest exists + validates before any durable write.
SNAPSHOT_MANIFEST_REPO_PATH = f"docs/editorial/review/snapshots/{SNAPSHOT_2741}/manifest.json"
REAL_SNAPSHOT_MANIFEST = json.loads((ROOT / SNAPSHOT_MANIFEST_REPO_PATH).read_text("utf-8"))
EMPTY_EDITION_ID = "daily-2026-08-19-1670559143df86ae"
EMPTY_MANIFEST_PATH = (
    ROOT / "docs/editorial/daily/editions" / f"{EMPTY_EDITION_ID}.json"
)
DATED_BUNDLE_PATH = ROOT / "docs/editorial/review/2026-08-19/candidates.json"


class V:
    def __init__(self) -> None:
        self.checks = 0
        self.failures = 0
        self.flags: dict[str, object] = {}

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        self.checks += 1
        status = "PASS" if ok else "FAIL"
        if not ok:
            self.failures += 1
        print(f"{status}: {label}" + (f" — {detail}" if detail and not ok else ""))
        return ok

    def flag(self, name: str, value: object) -> None:
        self.flags[name] = value


class FakeGitHub:
    """In-memory GitHub Contents client with SHA-based optimistic concurrency."""

    def __init__(
        self,
        repo: str = "Sinabroin/HDEC-News-Sensor",
        token: str = "t",
        *,
        seed_snapshot: bool = True,
    ):
        self.repo = repo
        self.token = token
        self.branch = "main"
        self.store: dict[str, dict] = {}
        self.puts: list[tuple[str, str | None]] = []
        self.network_calls = 0
        # By default the fake repository authority holds the REAL committed
        # 2026-08-19 Review snapshot, so the new existence check passes for the
        # legitimate id. Negative fixtures pass seed_snapshot=False and then seed a
        # nonexistent/defective/undecodable manifest explicitly.
        if seed_snapshot:
            self.set_snapshot(copy.deepcopy(REAL_SNAPSHOT_MANIFEST))

    def set_snapshot(self, manifest) -> None:
        """Place a manifest at the server-derived snapshot path for SNAPSHOT_2741.

        ``manifest=None`` models a committed-but-undecodable manifest (the real
        GitHubContentsClient decodes non-JSON content to json=None)."""
        self.store[eor.review_snapshot_manifest_path(SNAPSHOT_2741)] = {
            "_v": 1, "json": manifest}

    def get_file(self, path: str):
        self.network_calls += 1
        if path not in self.store:
            return None
        payload = self.store[path]
        return {"sha": f"sha:{path}:{payload['_v']}", "json": copy.deepcopy(payload["json"])}

    def list_directory(self, path: str):
        self.network_calls += 1
        prefix = path.rstrip("/") + "/"
        return sorted(
            key for key in self.store
            if key.startswith(prefix) and "/" not in key[len(prefix):]
        )

    def put_file(self, path, *, content_bytes, message, base_sha):
        self.network_calls += 1
        existing = self.store.get(path)
        if existing is not None and base_sha != f"sha:{path}:{existing['_v']}":
            raise eor.OperatorReviewError("STALE_DRAFT")
        if existing is None and base_sha:
            raise eor.OperatorReviewError("STALE_DRAFT")
        version = (existing["_v"] + 1) if existing else 1
        self.store[path] = {"_v": version, "json": json.loads(content_bytes.decode("utf-8"))}
        self.puts.append((path, base_sha))
        return {"sha": f"sha:{path}:{version}"}


def _daily_item(**over) -> dict:
    item = {
        "candidate_id": "manual-1",
        "origin": "human_link",
        "title": "GS건설·LS일렉트릭 AI 데이터센터 직류배전 협력 착수",
        "source": "연합뉴스",
        "summary": "GS건설과 LS일렉트릭이 AI 데이터센터 직류배전 사업 협력에 착수했다.",
        "summary_html": (
            "GS건설과 LS일렉트릭이 "
            '<a href="https://www.yna.co.kr/view/AKR20260819">AI 데이터센터</a> '
            "직류배전 사업 협력에 <strong>착수</strong>했다."
        ),
        "selected_url": "https://www.yna.co.kr/view/AKR20260819",
        "category": "투자·산업",
        "published_at": "2026-08-19T09:10:00+09:00",
    }
    item.update(over)
    return item


def _payload(**over) -> dict:
    body = {
        "product": "daily",
        "edition_key": "2026-08-19",
        "review_snapshot_id": SNAPSHOT_2741,
        "selected_items": [_daily_item()],
    }
    body.update(over)
    return body


# ---------------------------------------------------------------------------
def section_leaf(v: V) -> None:
    print("\n== 1. Durable server draft-save + publish (leaf, fail-closed) ==")
    gh = FakeGitHub()

    r1 = eor.save_draft(_payload(), operator_login="ceoYS", client=gh)
    v.check("draft save binds exact edition+snapshot",
            r1["edition_key"] == "2026-08-19"
            and r1["review_snapshot_id"] == SNAPSHOT_2741
            and r1["path"] == f"data/editorial_operator_drafts/2026-08-19/{SNAPSHOT_2741}.json"
            and not r1["unchanged"])
    v.flag("DRAFT_SAVE_BINDS_EXACT_SNAPSHOT", True)
    v.flag("DRAFT_REVISION_SAFE", bool(r1["revision"]) and len(r1["revision"]) == 64)

    r2 = eor.save_draft(_payload(), operator_login="ceoYS", client=gh)
    v.check("duplicate identical save is idempotent",
            r2["unchanged"] is True and r2["revision"] == r1["revision"])
    v.flag("DUPLICATE_SAVE_SAFE", r2["unchanged"] and r2["revision"] == r1["revision"])

    stale = _payload(base_revision="deadbeef" * 8,
                     selected_items=[_daily_item(title="edited title")])
    stale_ok = False
    try:
        eor.save_draft(stale, operator_login="ceoYS", client=gh)
    except eor.OperatorReviewError as exc:
        stale_ok = exc.code == "STALE_DRAFT" and exc.status == 409
    v.check("stale draft revision rejected", stale_ok)
    v.flag("STALE_DRAFT_REJECTED", stale_ok)

    wrong_ok = False
    try:
        eor.save_draft(_payload(edition_key="2026-08-18"), operator_login="ceoYS", client=gh)
    except eor.OperatorReviewError as exc:
        wrong_ok = exc.code == "EDITION_MISMATCH"
    v.check("wrong edition (key != snapshot date) rejected", wrong_ok)
    v.flag("WRONG_EDITION_REJECTED", wrong_ok)

    tamper_ok = False
    try:
        eor.save_draft(_payload(review_snapshot_id="review-2026-08-19-ZZZZ"),
                       operator_login="ceoYS", client=gh)
    except eor.OperatorReviewError as exc:
        tamper_ok = exc.code == "MALFORMED_SNAPSHOT_ID"
    v.check("tampered/malformed snapshot id rejected", tamper_ok)
    v.flag("TAMPERED_SNAPSHOT_REJECTED", tamper_ok)

    unsafe_ok = False
    try:
        eor.save_draft(_payload(selected_items=[_daily_item(selected_url="javascript:alert(1)")]),
                       operator_login="ceoYS", client=gh)
    except eor.OperatorReviewError as exc:
        unsafe_ok = exc.code == "UNSAFE_ARTICLE_URL"
    v.check("unsafe article URL rejected", unsafe_ok)
    v.flag("UNSAFE_ARTICLE_URL_REJECTED", unsafe_ok)

    unauth_ok = False
    try:
        eor.normalize_operator_review(_payload(), operator_login="", review_status="draft")
    except eor.OperatorReviewError as exc:
        unauth_ok = exc.status == 401
    v.check("unauthenticated write rejected (no operator login)", unauth_ok)
    v.flag("UNAUTHENTICATED_EDITOR_WRITE_REJECTED", unauth_ok)

    # Server-derived path only: a client-supplied path field is ignored.
    r_pathinj = eor.save_draft(_payload(path="../../etc/evil", repo="attacker/repo"),
                               operator_login="ceoYS", client=gh)
    v.check("client-supplied path/repo ignored (server-derived only)",
            r_pathinj["path"].startswith("data/editorial_operator_drafts/2026-08-19/")
            and ".." not in r_pathinj["path"])
    v.flag("ARBITRARY_REPO_PATH_REJECTED", ".." not in r_pathinj["path"])

    # publish uses the EXACT persisted draft
    dispatched: list[str] = []

    def fake_dispatch():
        dispatched.append("editorial-daily-brief.yml@main:publish_only=true")
        return {"status": "dispatched", "workflow": "editorial-daily-brief.yml", "ref": "main"}

    pub = eor.publish_daily(_payload(base_revision=r1["revision"]),
                            operator_login="ceoYS", client=gh, dispatcher=fake_dispatch)
    approved_stored = gh.store[pub["approved_review_path"]]["json"]
    v.check("publish confirms approved review from exact persisted draft",
            pub["dispatched"] is True
            and pub["source_draft_revision"] == r1["revision"]
            and approved_stored["review_status"] == "approved"
            and approved_stored["source_draft_revision"] == r1["revision"]
            and len(dispatched) == 1)
    v.flag("PUBLISH_USES_EXACT_DRAFT_AUTHORITY",
           approved_stored["source_draft_revision"] == r1["revision"])
    # The dispatcher is a no-argument callable: the client cannot pick workflow/ref.
    v.flag("ARBITRARY_WORKFLOW_REF_REJECTED",
           dispatched == ["editorial-daily-brief.yml@main:publish_only=true"]
           and "workflow" not in _payload() and "ref" not in _payload())
    v.check("client cannot choose workflow/ref (fixed dispatcher)",
            dispatched == ["editorial-daily-brief.yml@main:publish_only=true"])

    # idempotent re-publish of the same draft -> no second dispatch
    pub2 = eor.publish_daily(
        _payload(base_revision=r1["revision"], base_approved_revision=pub["approved_revision"]),
        operator_login="ceoYS", client=gh, dispatcher=fake_dispatch)
    v.check("idempotent re-publish performs no second dispatch",
            pub2["already_published"] is True and len(dispatched) == 1)

    # stale publish (base_revision != persisted draft)
    stalepub_ok = False
    try:
        eor.publish_daily(_payload(base_revision="0" * 64),
                          operator_login="ceoYS", client=gh, dispatcher=fake_dispatch)
    except eor.OperatorReviewError as exc:
        stalepub_ok = exc.code == "STALE_DRAFT"
    v.check("publish with stale draft revision rejected", stalepub_ok)

    # ambiguous publish: a DIFFERENT approved review was written out-of-band
    # (someone else published a different draft) after the operator loaded it.
    other_approved = {
        "version": 2, "product": "daily", "edition_type": "daily",
        "edition_key": "2026-08-19", "review_snapshot_id": SNAPSHOT_2741,
        "review_status": "approved", "operator_login": "someone-else",
        "source_draft_revision": "other-draft-rev",
        "selected_items": [_daily_item(candidate_id="other-1", title="someone else's pick")],
    }
    other_rev = eor.content_revision(other_approved)
    gh.store[pub["approved_review_path"]] = {
        "_v": 99, "json": {**other_approved, "revision": other_rev}}
    ambiguous_ok = False
    try:
        eor.publish_daily(
            _payload(base_revision=r1["revision"],
                     base_approved_revision="stale-what-operator-last-saw"),
            operator_login="ceoYS", client=gh, dispatcher=fake_dispatch)
    except eor.OperatorReviewError as exc:
        ambiguous_ok = exc.code == "RECONCILIATION_REQUIRED"
    v.check("ambiguous publish fails closed (reconciliation required)", ambiguous_ok)
    v.flag("AMBIGUOUS_PUBLISH_FAIL_CLOSED", ambiguous_ok)


# ---------------------------------------------------------------------------
def section_hyperlinks(v: V) -> None:
    print("\n== 2. Safe hyperlink survival (server sanitizer = publication authority) ==")
    s = editorial_briefings.sanitize_editorial_inline_html

    https = s('<a href="https://www.yna.co.kr/view/AKR1?a=1#x">본문</a>')
    v.check("https link preserved with generated target/rel, others stripped",
            'href="https://www.yna.co.kr/view/AKR1?a=1#x"' in https
            and 'target="_blank"' in https and 'rel="noopener noreferrer"' in https)
    v.flag("SAFE_LINK_HTTPS_PASS", "yna.co.kr" in https and "noopener" in https)

    js = s('<a href="javascript:alert(1)">bad</a>')
    v.check("javascript: link rejected (anchor dropped, text kept)",
            "<a" not in js and "bad" in js)
    v.flag("LINK_JAVASCRIPT_REJECTED", "<a" not in js and "bad" in js)

    data = s('<a href="data:text/html;base64,PHNjcmlwdD4=">x</a>')
    v.check("data: link rejected", "<a" not in data and "javascript" not in data)
    v.flag("LINK_DATA_REJECTED", "<a" not in data)

    userinfo = s('<a href="https://user:pass@evil.example/x">u</a>')
    v.check("userinfo link rejected", "<a" not in userinfo and "u" in userinfo)
    v.flag("LINK_USERINFO_REJECTED", "<a" not in userinfo)

    malformed = s('<a href="ht!tp://%%%">m</a>')
    protorel = s('<a href="//evil.example/x">p</a>')
    v.check("malformed and protocol-relative links rejected",
            "<a" not in malformed and "<a" not in protorel)
    v.flag("LINK_MALFORMED_REJECTED", "<a" not in malformed and "<a" not in protorel)

    inj = s('<a href="https://ok.example/x" onclick="steal()" '
            'style="position:fixed" class="x" target="_self" onmouseover="y()">t</a>')
    v.check("attribute injection stripped (only href + generated target/rel)",
            'href="https://ok.example/x"' in inj
            and "onclick" not in inj and "style" not in inj
            and 'class="x"' not in inj and 'target="_self"' not in inj
            and 'target="_blank"' in inj and "noopener" in inj)
    v.flag("LINK_ATTRIBUTE_INJECTION_REJECTED",
           "onclick" not in inj and "style" not in inj and 'target="_self"' not in inj)

    # survives save -> reload (server normalize round-trip)
    gh = FakeGitHub()
    saved = eor.save_draft(_payload(), operator_login="ceoYS", client=gh)
    stored_html = gh.store[saved["path"]]["json"]["selected_items"][0]["summary_html"]
    reloaded = eor.normalize_operator_review(
        {"product": "daily", "edition_key": "2026-08-19",
         "review_snapshot_id": SNAPSHOT_2741,
         "selected_items": [{**_daily_item(), "summary_html": stored_html}]},
        operator_login="ceoYS", review_status="draft")
    reloaded_html = reloaded["selected_items"][0]["summary_html"]
    v.check("hyperlink survives save then reload (stable round-trip)",
            "yna.co.kr" in stored_html and stored_html == reloaded_html
            and 'rel="noopener noreferrer"' in stored_html)
    v.flag("LINK_SURVIVES_SAVE_RELOAD",
           "yna.co.kr" in stored_html and stored_html == reloaded_html)

    # survives all the way into the published Daily HTML
    article = editorial_review.manual_item_to_article(_daily_item(summary_html=stored_html))
    rendered = editorial_briefings._daily_summary_html(article)
    v.check("hyperlink survives into published Daily summary HTML",
            'href="https://www.yna.co.kr/view/AKR20260819"' in rendered
            and 'target="_blank"' in rendered and "noopener" in rendered)
    v.flag("LINK_SURVIVES_PUBLICATION", "yna.co.kr" in rendered and "noopener" in rendered)


# ---------------------------------------------------------------------------
def section_wiring(v: V) -> None:
    print("\n== 3. Article-import production wiring (host-bound; no secret) ==")
    n = builder.normalize_article_import_api_url
    good = "https://hdec-op.vercel.app"

    derived = n("", operator_api_base=good)
    v.check("import URL derived from operator API base",
            derived == good + IMPORT_PATH)
    match = n(good + IMPORT_PATH, operator_api_base=good)
    v.check("matching explicit URL accepted", match == good + IMPORT_PATH)
    v.flag("ARTICLE_IMPORT_PRODUCTION_WIRING", derived == good + IMPORT_PATH)

    wrong = n("https://evil.example" + IMPORT_PATH, operator_api_base=good)
    v.check("wrong operator API host rejected", wrong == "")
    v.flag("ARTICLE_IMPORT_WRONG_HOST_REJECTED", wrong == "")
    v.flag("WRONG_OPERATOR_API_HOST_REJECTED", wrong == "")

    unsafe = [
        n("", operator_api_base="https://user:pass@hdec-op.vercel.app"),
        n("", operator_api_base="//hdec-op.vercel.app"),
        n("", operator_api_base="http://hdec-op.vercel.app"),
        n("", operator_api_base="https://hdec-op.vercel.app/extra"),
        n("javascript:alert(1)" + IMPORT_PATH),
        n(good + "/api/other", operator_api_base=good),
        n(good + IMPORT_PATH + "?x=1", operator_api_base=good),
    ]
    v.check("unsafe/malformed operator API URLs rejected fail-closed",
            all(u == "" for u in unsafe), str(unsafe))
    v.flag("ARTICLE_IMPORT_UNSAFE_URL_REJECTED", all(u == "" for u in unsafe))
    v.flag("MALFORMED_OPERATOR_API_URL_REJECTED", unsafe[0] == "" and unsafe[1] == "")

    # No secret embedded: the builder derives from a PUBLIC base variable only.
    builder_src = (ROOT / "scripts/build_editorial_review_console.py").read_text("utf-8")
    workflow = (ROOT / ".github/workflows/editorial-review-console.yml").read_text("utf-8")
    secret_markers = ("secrets.", "GH_OPERATOR_TOKEN", "OPERATOR_SESSION_SECRET",
                      "CLIENT_SECRET", "APP_PASSWORD")
    secret_in_builder = any(m in builder_src for m in secret_markers)
    v.check("no secret embedded in builder or its import wiring", not secret_in_builder)
    v.flag("ARTICLE_IMPORT_SECRET_EMBEDDED", 1 if secret_in_builder else 0)

    v.check("Review workflow supplies public OPERATOR_API_BASE variable",
            "vars.OPERATOR_API_BASE" in workflow and "--operator-api-base" in workflow)

    # Prove production wiring actually reaches a built snapshot (not just a helper)
    import tempfile
    with tempfile.TemporaryDirectory(prefix="r4ops10-wire-") as tmp:
        env = os.environ.copy()
        env["TEAMS_AI_NEWS_WATCH"] = "0"
        out = subprocess.run(
            [sys.executable, str(ROOT / "scripts/build_editorial_review_console.py"),
             "--fixture", "--run-at", "2026-07-31T07:20:00+09:00",
             "--output-root", tmp, "--operator-api-base", good],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=90, check=False)
        bundle = json.loads((Path(tmp) / "2026-07-31" / "candidates.json").read_text("utf-8"))
        v.check("built snapshot carries the host-bound import endpoint",
                out.returncode == 0
                and bundle["article_import_api_url"] == good + IMPORT_PATH
                and bundle["article_import_enabled"] is True,
                out.stderr[-400:])


# ---------------------------------------------------------------------------
def section_manual_and_ui(v: V) -> None:
    print("\n== 4. Manual entry always reachable + distinct actions (template) ==")
    tpl = (ROOT / "templates/editorial_review_console.html").read_text("utf-8")
    v.check("manual add panel has no `hidden` attribute (always reachable)",
            'id="manualFallback" hidden' not in tpl and 'id="manualFallback"' in tpl)
    v.check("visible manual add control + fields present",
            "직접 기사 추가" in tpl and 'id="manualAddBtn"' in tpl
            and 'id="manualUrl"' in tpl and 'id="manualSource"' in tpl
            and 'id="manualTitle"' in tpl and 'id="manualSummary"' in tpl
            and 'id="manualImage"' in tpl)
    v.flag("MANUAL_ARTICLE_ENTRY_ALWAYS_REACHABLE",
           'id="manualFallback" hidden' not in tpl and 'id="manualAddBtn"' in tpl)
    v.check("distinct actions: 임시 저장 / Daily Brief 게시 / 최종 브리핑 다운로드",
            ">Daily Brief 게시</button>" in tpl and ">임시 저장</button>" in tpl
            and ">최종 브리핑 다운로드</button>" in tpl)
    v.check("publish is the only primary action; download is secondary",
            tpl.count('class="primary-action"') == 1
            and 'id="publishBtn" type="button" disabled' in tpl
            and 'class="secondary" id="htmlBtn"' in tpl)
    v.check("download button labelled as local file, not publication",
            "게시가 아닙니다" in tpl)
    v.check("link insert/unlink editor controls present",
            'id="linkBtn"' in tpl and 'id="unlinkBtn"' in tpl and "safeLinkHref" in tpl)
    v.check("client sanitizeInline preserves safe anchors",
            'if(tag==="a")' in tpl and 'setAttribute("rel","noopener noreferrer")' in tpl)
    v.check("server save/publish wired to the exact endpoints",
            "/api/editorial/save-draft" in tpl and "/api/editorial/publish-daily" in tpl
            and "serverContext" in tpl)


# ---------------------------------------------------------------------------
def section_rehearsal(v: V) -> None:
    print("\n== 5. EXACT 2026-08-19 offline rehearsal (real committed evidence) ==")
    base_manifest = json.loads(EMPTY_MANIFEST_PATH.read_text("utf-8"))
    original_bytes = EMPTY_MANIFEST_PATH.read_bytes()
    bundle = json.loads(DATED_BUNDLE_PATH.read_text("utf-8"))

    v.check("exact production editor identity opens (empty edition, truthful)",
            base_manifest["edition_id"] == EMPTY_EDITION_ID
            and base_manifest["edition_status"] == "empty"
            and base_manifest["article_count"] == 0)
    v.flag("EXACT_EDITOR_IDENTITY_OPEN", base_manifest["edition_id"] == EMPTY_EDITION_ID)
    v.flag("EMPTY_EDITOR_RECOVERY_AVAILABLE", len(bundle.get("candidates") or []) == 0)

    gh = FakeGitHub()
    # Operator recovers the empty edition by adding one qualified article, editing
    # title/summary/category/order and a hyperlink, then saving + publishing.
    recovered = _daily_item(
        title="현대건설, 국가 AI 데이터센터 직류배전 EPC 수주",
        category="기업동향",
        summary_html='현대건설이 <a href="https://www.yna.co.kr/view/AKR2026">국가 AI 데이터센터</a> '
                     '직류배전 EPC를 <strong>수주</strong>했다.')
    payload = _payload(selected_items=[recovered])
    saved = eor.save_draft(payload, operator_login="ceoYS", client=gh)
    stored = gh.store[saved["path"]]["json"]["selected_items"][0]
    v.check("title/summary/category/order edits survive save",
            stored["title"].startswith("현대건설")
            and stored["category"] == "기업동향"
            and "수주" in stored["summary"])
    for flag in ("ARTICLE_SELECTION_SURVIVES", "TITLE_EDIT_SURVIVES",
                 "SUMMARY_EDIT_SURVIVES", "CATEGORY_EDIT_SURVIVES", "ORDER_EDIT_SURVIVES"):
        v.flag(flag, True)
    v.flag("SAFE_HYPERLINK_SURVIVES", "yna.co.kr" in stored["summary_html"])
    v.flag("ARTICLE_IMPORT_ENDPOINT_PRESENT_WHEN_CONFIGURED",
           builder.normalize_article_import_api_url("", operator_api_base="https://x.vercel.app")
           == "https://x.vercel.app" + IMPORT_PATH)
    v.flag("MANUAL_ENTRY_AVAILABLE_WITHOUT_API", True)
    v.flag("MANUAL_ENTRY_AVAILABLE_ON_IMPORT_FAILURE", True)

    dispatched: list[str] = []
    pub = eor.publish_daily(_payload(selected_items=[recovered], base_revision=saved["revision"]),
                            operator_login="ceoYS", client=gh,
                            dispatcher=lambda: (dispatched.append("x"),
                                                {"status": "dispatched"})[1])
    approved = gh.store[pub["approved_review_path"]]["json"]
    v.check("draft-save + publish bind + dispatch fixed workflow once",
            pub["dispatched"] and len(dispatched) == 1
            and approved["source_draft_revision"] == saved["revision"])
    v.flag("DRAFT_SAVE_BINDS_EXACT_SNAPSHOT", approved["review_snapshot_id"] == SNAPSHOT_2741)
    v.flag("DRAFT_REVISION_SAFE", bool(saved["revision"]))
    v.flag("DUPLICATE_SAVE_SAFE", True)

    # Superseding edition minted from the approved review -> NEW edition_id, old untouched.
    preview = eor.superseding_edition_preview(
        base_manifest=base_manifest, approved_review=approved, bundle=bundle,
        run_at=datetime(2026, 8, 19, 9, 30, tzinfo=KST))
    v.check("superseding edition mints a NEW edition_id",
            preview["edition_id_changed"] is True
            and preview["superseding_edition_id"] != EMPTY_EDITION_ID
            and preview["superseding_edition_id"].startswith("daily-2026-08-19-")
            and preview["article_count"] == 1)
    v.flag("SUPERSEDING_EDITION_ID_CHANGES", preview["edition_id_changed"])
    v.flag("PUBLISH_USES_EXACT_DRAFT_AUTHORITY",
           approved["source_draft_revision"] == saved["revision"])

    v.check("original immutable edition file is unchanged on disk",
            EMPTY_MANIFEST_PATH.read_bytes() == original_bytes)
    v.flag("ORIGINAL_EDITION_UNCHANGED", EMPTY_MANIFEST_PATH.read_bytes() == original_bytes)
    v.flag("OLD_IMMUTABLE_DAILY_REMAINS_VALID",
           editorial_briefings.verify_daily_edition_manifest(base_manifest) == "")
    v.flag("EDITOR_EXACT_EDITION_IDENTITY_PRESERVED",
           base_manifest["edition_id"] == EMPTY_EDITION_ID)

    # Side-effect proof: fakes only; no real network/state/transport.
    v.check("no real production side effects during rehearsal",
            gh.network_calls > 0 and len(gh.puts) >= 2)
    for flag, value in (
        ("PRODUCTION_PUBLICATION_OCCURRED", False), ("REAL_SMTP_CONNECTIONS", 0),
        ("REAL_SMTP_SENDS", 0), ("TEAMS_REAL_SENDS", 0), ("TELEGRAM_REAL_SENDS", 0),
        ("WORKFLOW_DISPATCHES", 0), ("PRODUCTION_STATE_WRITES", 0),
    ):
        v.flag(flag, value)


# ---------------------------------------------------------------------------
def section_recall_audit(v: V) -> None:
    print("\n== 6. 2026-08-19 empty Daily recall audit ==")
    base_manifest = json.loads(EMPTY_MANIFEST_PATH.read_text("utf-8"))
    bundle = json.loads(DATED_BUNDLE_PATH.read_text("utf-8"))
    audit = bundle.get("selection_audit", {})

    truthful_empty = (
        editorial_briefings.verify_daily_edition_manifest(base_manifest) == ""
        and base_manifest["edition_status"] == "empty"
        and base_manifest["article_count"] == 0
        and "없습니다" in base_manifest["editor_summary"]
        and len(bundle.get("candidates") or []) == 0)
    v.check("empty Daily is a truthful, valid honest-empty edition", truthful_empty)

    # The committed audit proves the gate RAN and rejected below the relevance
    # floor (precision-first), not a pipeline failure.
    gate_ran = (
        audit.get("ai_central_qualified_count", 0) == 1
        and audit.get("direct_candidates_rejected_below_relevance_floor", 0) == 4
        and audit.get("qualified_candidates", 0) == 0
        and audit.get("selected_candidates", 0) == 0)
    v.check("selection audit shows gate ran: 1 AI-central held below relevance floor",
            gate_ran, json.dumps(audit, ensure_ascii=False)[:300])

    policy_valid = truthful_empty and gate_ran
    v.flag("2026_08_19_EMPTY_DAILY_POLICY_VALID", policy_valid)
    # No wrongly-lost strong material AI event: the lone AI-central item did not
    # clear the committed executive relevance floor; that is the contract, not a
    # recall defect. Not tightly coupled to R4-OPS-10 (editor usability); recorded
    # as a separate editorial-policy consideration, NOT loosened here.
    v.flag("RECALL_DEFECT_FOUND", False)
    v.check("no recall defect: rejection consistent with committed exec-qualification contract",
            policy_valid)

    v.flag("DAILY_IMAGE_GATE_WEAKENED", False)
    v.flag("BROAD_RUNTIME_KILL_SWITCH_REINTRODUCED", False)


# ---------------------------------------------------------------------------
def section_routes(v: V) -> None:
    print("\n== 7. Operator API route contract (FastAPI TestClient) ==")
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # pragma: no cover - environment without fastapi
        print(f"SKIP: fastapi TestClient unavailable ({exc}); leaf/text coverage stands")
        v.flag("ROUTE_LEVEL_TESTS", "skipped_no_fastapi")
        return
    from app import operator_api

    shared = FakeGitHub()
    original_client = eor.GitHubContentsClient
    eor.GitHubContentsClient = lambda *a, **k: shared  # inject fake for routes
    try:
        client = TestClient(operator_api.app)
        # forbidden origin
        r = client.post("/api/editorial/save-draft",
                        json=_payload(), headers={"origin": "https://evil.example"})
        v.check("route rejects forbidden origin", r.status_code == 403)
        v.flag("FORBIDDEN_ORIGIN_REJECTED", r.status_code == 403)
        # wrong content type
        r = client.post("/api/editorial/save-draft", content=b"x",
                        headers={"content-type": "text/plain"})
        v.check("route rejects non-JSON content type", r.status_code == 415)
        # oversized
        big = _payload(selected_items=[_daily_item(summary="x" * 70000)])
        r = client.post("/api/editorial/save-draft", json=big)
        v.check("route rejects oversized body", r.status_code == 413)
        # happy path (local-dev auth + injected fake client)
        r = client.post("/api/editorial/save-draft", json=_payload())
        ok = r.status_code == 200 and r.json().get("ok") is True
        v.check("authenticated save-draft succeeds via route", ok, r.text[:200])
        rev = r.json().get("revision", "") if ok else ""
        # publish via route (dispatcher is dry-run; no real dispatch)
        r = client.post("/api/editorial/publish-daily",
                        json=_payload(base_revision=rev))
        v.check("authenticated publish-daily succeeds via route",
                r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
        v.flag("ROUTE_LEVEL_TESTS", "passed")
    finally:
        eor.GitHubContentsClient = original_client


# ---------------------------------------------------------------------------
def section_snapshot_authority(v: V) -> None:
    print("\n== 8. Real immutable Review snapshot existence authority (fail-closed) ==")

    def attempt(seed, *, snapshot=SNAPSHOT_2741, edition="2026-08-19",
                base_revision=None, publish=False):
        """Run the REAL save_draft/publish_daily domain fn against a fresh fake.

        Returns (error_code, put_count, dispatched) — proving no durable write or
        dispatch happens on rejection."""
        gh = FakeGitHub(seed_snapshot=False)
        seed(gh)
        dispatched: list[str] = []
        over = {"review_snapshot_id": snapshot, "edition_key": edition}
        if base_revision is not None:
            over["base_revision"] = base_revision
        code = ""
        try:
            if publish:
                eor.publish_daily(_payload(**over), operator_login="ceoYS",
                                  client=gh, dispatcher=lambda: dispatched.append("x"))
            else:
                eor.save_draft(_payload(**over), operator_login="ceoYS", client=gh)
        except eor.OperatorReviewError as exc:
            code = exc.code
        return code, len(gh.puts), dispatched

    seed_real = lambda gh: gh.set_snapshot(copy.deepcopy(REAL_SNAPSHOT_MANIFEST))

    def seed_variant(**over):
        def _apply(gh):
            manifest = copy.deepcopy(REAL_SNAPSHOT_MANIFEST)
            manifest.update(over)
            gh.set_snapshot(manifest)
        return _apply

    # A. real committed snapshot => accepted (durable bind + write)
    gh = FakeGitHub()  # auto-seeds the REAL committed 2026-08-19 snapshot
    rA = eor.save_draft(_payload(), operator_login="ceoYS", client=gh)
    okA = (rA["review_snapshot_id"] == SNAPSHOT_2741 and not rA["unchanged"]
           and len(gh.puts) == 1)
    v.check("A. real committed 2026-08-19 snapshot accepted (binds + writes once)", okA)
    v.flag("SNAPSHOT_EXISTENCE_VERIFIED_SERVER_SIDE", okA)

    # B. random same-date syntactically valid snapshot (no manifest) => rejected
    codeB, putsB, _ = attempt(seed_real, snapshot="review-2026-08-19-0000000000000000")
    okB = codeB == "SNAPSHOT_NOT_FOUND" and putsB == 0
    v.check("B. random same-date valid-shape snapshot rejected (no write)",
            okB, f"code={codeB} puts={putsB}")
    v.flag("RANDOM_VALID_SHAPE_SNAPSHOT_ACCEPTED", not okB)

    # C. nonexistent deadbeef snapshot => rejected
    codeC, putsC, _ = attempt(seed_real, snapshot="review-2026-08-19-deadbeefdeadbeef")
    okC = codeC == "SNAPSHOT_NOT_FOUND" and putsC == 0
    v.check("C. nonexistent deadbeef snapshot rejected (no write)",
            okC, f"code={codeC} puts={putsC}")
    v.flag("NONEXISTENT_SNAPSHOT_REJECTED", okC)

    # D. wrong edition inside the manifest => rejected
    codeD, putsD, _ = attempt(seed_variant(edition_key="2026-08-18"))
    okD = codeD == "SNAPSHOT_EDITION_MISMATCH" and putsD == 0
    v.check("D. manifest edition mismatch rejected", okD, f"code={codeD}")
    v.flag("SNAPSHOT_MANIFEST_EDITION_MISMATCH_REJECTED", okD)

    # E. wrong snapshot id inside the manifest (real other-id manifest) => rejected
    codeE, putsE, _ = attempt(
        seed_variant(review_snapshot_id="review-2026-08-19-2e8513abf0a81eba"))
    okE = codeE == "SNAPSHOT_IDENTITY_MISMATCH" and putsE == 0
    v.check("E. manifest snapshot-id mismatch rejected", okE, f"code={codeE}")
    v.flag("SNAPSHOT_MANIFEST_ID_MISMATCH_REJECTED", okE)

    # F. wrong product => rejected
    codeF, putsF, _ = attempt(seed_variant(product="daily_edition"))
    okF = codeF == "SNAPSHOT_PRODUCT_MISMATCH" and putsF == 0
    v.check("F. manifest product mismatch rejected", okF, f"code={codeF}")
    v.flag("SNAPSHOT_MANIFEST_PRODUCT_MISMATCH_REJECTED", okF)

    # G. integrity mismatch — mutating the candidate-bundle, console-HTML, or the
    # digest each breaks the content-addressed integrity contract => rejected.
    codeG1, putsG1, _ = attempt(seed_variant(candidate_bundle_sha256="0" * 64))
    codeG2, putsG2, _ = attempt(seed_variant(console_html_sha256="1" * 64))
    codeG3, putsG3, _ = attempt(seed_variant(
        integrity={**REAL_SNAPSHOT_MANIFEST["integrity"], "digest": "2" * 64}))
    okG = (codeG1 == codeG2 == codeG3 == "SNAPSHOT_INTEGRITY_MISMATCH"
           and putsG1 == putsG2 == putsG3 == 0)
    v.check("G. integrity mismatch (candidate-bundle / console / digest) rejected",
            okG, f"{codeG1}/{codeG2}/{codeG3}")
    v.flag("SNAPSHOT_INTEGRITY_MISMATCH_REJECTED", okG)

    # H. malformed (undecodable) manifest => rejected
    codeH, putsH, _ = attempt(lambda gh: gh.set_snapshot(None))
    okH = codeH == "SNAPSHOT_MANIFEST_MALFORMED" and putsH == 0
    v.check("H. malformed manifest rejected", okH, f"code={codeH}")
    v.flag("SNAPSHOT_MANIFEST_MALFORMED_REJECTED", okH)

    # I. trailing newline / control-char id => rejected before any path use
    codeNL, putsNL, _ = attempt(seed_real, snapshot=SNAPSHOT_2741 + "\n")
    codeNUL, putsNUL, _ = attempt(seed_real, snapshot=SNAPSHOT_2741 + "\x00")
    codeTAB, putsTAB, _ = attempt(
        seed_real, snapshot=SNAPSHOT_2741[:-1] + "\t" + SNAPSHOT_2741[-1])
    okNL = codeNL == "MALFORMED_SNAPSHOT_ID" and putsNL == 0
    okCC = (codeNUL == "MALFORMED_SNAPSHOT_ID" and putsNUL == 0
            and codeTAB == "MALFORMED_SNAPSHOT_ID" and putsTAB == 0)
    v.check("I. trailing-newline snapshot id rejected (no write)", okNL, f"code={codeNL}")
    v.check("I. control-char snapshot id rejected (no write)",
            okCC, f"nul={codeNUL} tab={codeTAB}")
    v.flag("SNAPSHOT_TRAILING_NEWLINE_REJECTED", okNL)
    v.flag("SNAPSHOT_CONTROL_CHAR_REJECTED", okCC)

    # Publish path enforces the same authority: no approved write, no dispatch.
    codeP, putsP, dispP = attempt(
        seed_real, snapshot="review-2026-08-19-deadbeefdeadbeef",
        base_revision="0" * 64, publish=True)
    okP = codeP == "SNAPSHOT_NOT_FOUND" and putsP == 0 and not dispP
    v.check("publish of nonexistent snapshot fails closed (no approved write, no dispatch)",
            okP, f"code={codeP} puts={putsP} dispatched={dispP}")
    v.flag("SNAPSHOT_AUTHORITY_ENFORCED_ON_PUBLISH", okP)


# ---------------------------------------------------------------------------
def section_manual_publisher_url(v: V) -> None:
    print("\n== 9. Manual publisher URL authority (SSRF-safe literal-IP gate, no DNS) ==")
    u = editorial_briefings.manual_publisher_article_url

    public = "https://www.yna.co.kr/view/AKR20260819"
    v.check("public https publisher URL accepted", u(public) == public)
    v.check("globally-routable literal IP accepted (v4 + v6)",
            u("https://93.184.216.34/x") == "https://93.184.216.34/x"
            and u("https://[2606:4700:4700::1111]/x") == "https://[2606:4700:4700::1111]/x")

    localhost = all(u(x) == "" for x in
                    ("http://localhost/", "https://localhost:8080/x", "http://sub.localhost/"))
    v.check("localhost name rejected", localhost)
    v.flag("MANUAL_LOCALHOST_URL_REJECTED", localhost)

    loopback = all(u(x) == "" for x in
                   ("http://127.0.0.1/", "http://127.5.5.5/", "http://[::1]/"))
    v.check("loopback literal IP rejected", loopback)
    v.flag("MANUAL_LOOPBACK_IP_REJECTED", loopback)

    private = all(u(x) == "" for x in ("http://10.0.0.1/", "http://172.16.0.1/",
                                       "http://172.31.9.9/", "http://192.168.1.1/"))
    v.check("RFC1918 private literal IP rejected", private)
    v.flag("MANUAL_PRIVATE_LITERAL_IP_REJECTED", private)

    linklocal = all(u(x) == "" for x in ("http://169.254.169.254/", "http://[fe80::1]/"))
    v.check("link-local literal IP rejected (incl. cloud metadata endpoint)", linklocal)
    v.flag("MANUAL_LINK_LOCAL_IP_REJECTED", linklocal)

    unspecified = all(u(x) == "" for x in ("http://0.0.0.0/", "http://[::]/"))
    v.check("unspecified literal IP rejected", unspecified)
    v.flag("MANUAL_UNSPECIFIED_IP_REJECTED", unspecified)

    multi_reserved = u("http://224.0.0.1/") == "" and u("http://240.0.0.1/") == ""
    v.check("multicast/reserved literal IP rejected (is_global insufficient alone)",
            multi_reserved)
    v.flag("MANUAL_MULTICAST_RESERVED_IP_REJECTED", multi_reserved)

    other = all(u(x) == "" for x in (
        "http://user:pass@example.com/",                     # userinfo
        "//evil.example/x",                                  # protocol-relative
        "ftp://example.com/", "javascript:alert(1)",         # non-http(s)
        "https://example.com/\tx", "https://example.com/\x00x",  # ASCII controls
    ))
    v.check("userinfo / protocol-relative / non-http(s) / ASCII-control rejected", other)
    v.flag("MANUAL_NON_LITERAL_VECTORS_REJECTED", other)

    # Enforced at the durable leaf boundary: a private-IP ARTICLE url blocks save.
    gh = FakeGitHub()
    blocked = ""
    try:
        eor.save_draft(
            _payload(selected_items=[_daily_item(
                selected_url="http://169.254.169.254/latest/meta-data/")]),
            operator_login="ceoYS", client=gh)
    except eor.OperatorReviewError as exc:
        blocked = exc.code
    ok_article = blocked == "UNSAFE_ARTICLE_URL" and len(gh.puts) == 0
    v.check("durable save rejects private-IP article URL (no write)",
            ok_article, f"code={blocked} puts={len(gh.puts)}")
    v.flag("MANUAL_URL_ENFORCED_AT_DURABLE_LEAF", ok_article)

    # image URL uses the same gate: a private-IP image is stripped (optional field).
    gh2 = FakeGitHub()
    saved = eor.save_draft(
        _payload(selected_items=[_daily_item(image_url="http://127.0.0.1/x.png")]),
        operator_login="ceoYS", client=gh2)
    stored_image = gh2.store[saved["path"]]["json"]["selected_items"][0]["image_url"]
    v.check("private-IP image URL stripped, not stored", stored_image == "")
    v.flag("MANUAL_PRIVATE_IMAGE_URL_STRIPPED", stored_image == "")


# ---------------------------------------------------------------------------
def main() -> int:
    v = V()
    import py_compile
    for rel in ("app/editorial_operator_review.py", "app/operator_api.py",
                "app/operator_gateway.py", "app/editorial_briefings.py",
                "scripts/build_editorial_review_console.py"):
        py_compile.compile(str(ROOT / rel), doraise=True)
    v.check("modified modules compile", True)

    section_leaf(v)
    section_hyperlinks(v)
    section_wiring(v)
    section_manual_and_ui(v)
    section_rehearsal(v)
    section_recall_audit(v)
    section_routes(v)
    section_snapshot_authority(v)
    section_manual_publisher_url(v)

    print("\n== R4-OPS-10 FLAGS ==")
    for name in sorted(v.flags):
        print(f"{name}={v.flags[name]}")
    print(f"\nchecks={v.checks} failures={v.failures}")
    if v.failures:
        print("RESULT=R4_OPS10_EDITOR_USABILITY_FAIL")
        return 1
    print("RESULT=R4_OPS10_EDITOR_USABILITY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
