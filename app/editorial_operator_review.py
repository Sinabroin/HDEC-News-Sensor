"""Editorial Operator Review 도메인 (leaf, R4-OPS-10) — 운영자 편집 초안의 durable 저장·게시.

이 leaf만 하는 것:
- 운영자 편집기(templates/editorial_review_console.html)가 보낸 review payload를 **경계 검증**한다:
  product=daily, edition_key ↔ review_snapshot_id(날짜) 일치, selected_items(≤6·안전 URL·안전 요약 HTML),
  운영자 신원(세션에서만 주입 · 클라이언트 값 신뢰 금지).
- 저장 경로를 **서버에서 파생**한다(클라이언트는 경로를 지정할 수 없다). 임시저장=drafts, 게시=승인본.
- durable 저장(git 저장소)을 GitHub Contents API로 낙관적 동시성(base sha/revision)으로 수행한다.
  Vercel 휘발 파일시스템을 authority로 쓰지 않는다.
- 게시는 저장된 **정확한 초안**을 v2 승인본(data/editorial_reviews/<key>.json)으로 확정하고,
  주입된 dispatcher로 **고정된** Daily 발행 워크플로만 트리거하도록 신호한다(워크플로/ref는 이 leaf/
  operator_gateway의 상수 — 클라이언트가 고를 수 없다).

이 leaf가 절대 안 하는 것:
- 브라우저에 GitHub token/OAuth secret/session secret을 노출하지 않는다(환경변수만).
- 임의 저장소/브랜치/경로/워크플로 쓰기 API를 만들지 않는다(전부 서버측 상수·파생).
- Daily 안전 게이트(lead-source·이미지·executive 자격·immutable manifest·at-most-once)를 약화하지 않는다
  (게시는 기존 발행 파이프라인을 그대로 재사용한다 — 이 leaf는 승인본을 durable하게 두고 트리거만 한다).
- 점수/insight/발송/이미지 처리 자체를 하지 않는다.

승인본은 기존 소비자 app/editorial_review.load_review 계약(v2·review_status "approved")과 호환된다.
게시는 editorial_review.choose_daily_articles + editorial_briefings.build_daily_edition_manifest를
그대로 태워 **다른 선택 → 새 edition_id**(내용 주소화·append-only)로 이어진다. 이전 edition_id manifest는
불변이므로 절대 덮어쓰지 않는다.
"""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import datetime
from typing import Callable

from app import (
    config,
    editor_delivery,
    editorial_briefings,
    editorial_review,
    public_urls,
)

# ---------------------------------------------------------------------------
# Bounds and fixed server-side identifiers (never client-controlled)
# ---------------------------------------------------------------------------
DRAFT_DIR = "data/editorial_operator_drafts"
APPROVED_DIR = "data/editorial_reviews"
# Fixed, server-side repository location of the immutable Review snapshots the
# operator editor is opened against (content-addressed, append-only). The client
# never supplies this path, a repository, a branch, or a ref.
SNAPSHOT_DIR = "docs/editorial/review/snapshots"
DISPATCH_REF = "main"
MAX_SELECTED = editorial_review.MAX_REVIEW_ARTICLES
REVIEW_CONTRACT_VERSION = editorial_review.REVIEW_VERSION  # v2 (load_review contract)
CATEGORY_ORDER = editorial_review.CATEGORY_ORDER
_ALLOWED_ORIGINS = frozenset({"ai_collected", "human_link"})
_MAX_TITLE = 500
_MAX_SOURCE = 160
_MAX_SUMMARY = 4000
_MAX_SUMMARY_HTML = 8000
_MAX_CANDIDATE_ID = 128
_MAX_CATEGORY_ANALYSIS_BYTES = 8000
_TIMEOUT_SECONDS = 20
_API_BASE = "https://api.github.com"

_ERRORS: dict[str, tuple[int, str]] = {
    "INVALID_PAYLOAD": (400, "요청 형식이 올바르지 않습니다."),
    "UNSUPPORTED_PRODUCT": (400, "이 편집 저장은 Daily Brief 전용입니다."),
    "MALFORMED_EDITION_KEY": (400, "에디션 식별자 형식이 올바르지 않습니다."),
    "MALFORMED_SNAPSHOT_ID": (400, "검토 스냅샷 식별자 형식이 올바르지 않습니다."),
    "EDITION_MISMATCH": (409, "에디션과 검토 스냅샷의 날짜가 일치하지 않습니다."),
    "SNAPSHOT_NOT_FOUND": (409, "검토 스냅샷을 찾을 수 없습니다. 편집기를 다시 열어 주세요."),
    "SNAPSHOT_MANIFEST_MALFORMED": (409, "검토 스냅샷 매니페스트가 유효하지 않습니다."),
    "SNAPSHOT_PRODUCT_MISMATCH": (409, "검토 스냅샷 종류가 올바르지 않습니다."),
    "SNAPSHOT_IDENTITY_MISMATCH": (409, "검토 스냅샷 식별자가 매니페스트와 일치하지 않습니다."),
    "SNAPSHOT_EDITION_MISMATCH": (409, "검토 스냅샷의 에디션이 일치하지 않습니다."),
    "SNAPSHOT_INTEGRITY_MISMATCH": (409, "검토 스냅샷 무결성 검증에 실패했습니다."),
    "EMPTY_SELECTION": (400, "게시하려면 최소 한 건의 기사를 선택해야 합니다."),
    "TOO_MANY_SELECTED": (400, "선택 기사는 최대 6건입니다."),
    "UNSAFE_ARTICLE_URL": (400, "안전하지 않은 기사 URL이 포함되어 있습니다."),
    "STALE_DRAFT": (409, "최신 초안이 아닙니다. 최신 버전을 불러온 뒤 다시 시도해 주세요."),
    "DRAFT_NOT_FOUND": (404, "게시할 저장된 초안을 찾을 수 없습니다."),
    "RECONCILIATION_REQUIRED": (409, "게시 상태가 모호합니다. 최신 상태를 확인해 주세요."),
    "NOT_CONFIGURED": (503, "운영자 저장소 저장이 구성되지 않았습니다."),
    "PERSIST_FAILED": (502, "초안 저장에 실패했습니다."),
    "INTERNAL_ERROR": (500, "내부 오류가 발생했습니다."),
}


class OperatorReviewError(RuntimeError):
    """Typed, browser-safe operator-review error (never leaks secrets/stack)."""

    def __init__(self, code: str, *, message: str = "", status: int | None = None):
        default_status, default_message = _ERRORS.get(code, _ERRORS["INTERNAL_ERROR"])
        super().__init__(code)
        self.code = code if code in _ERRORS else "INTERNAL_ERROR"
        self.status = int(status if status is not None else default_status)
        self.message = str(message or default_message)

    def response_payload(self) -> dict[str, object]:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


# ---------------------------------------------------------------------------
# Identity + payload validation (exact-by-default; fail closed)
# ---------------------------------------------------------------------------
def _text(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _safe_article_url(value: object) -> str:
    """SSRF-safe durable article/image URL.

    http/https only; rejects javascript/data/blob/file, userinfo, CR/LF, ASCII
    controls, the loopback name ``localhost``, and any non-globally-routable
    literal-IP host (loopback/private/link-local/unspecified/multicast/reserved).
    No DNS resolution — a non-literal hostname is left to downstream gates."""
    return editorial_briefings.manual_publisher_article_url(value)


def _bounded_category_analysis(value: object, category: str) -> dict:
    if not isinstance(value, Mapping):
        return {"category": category, "scores": {}, "matched_signals": {}, "reason": ""}
    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"category": category, "scores": {}, "matched_signals": {}, "reason": ""}
    if len(encoded.encode("utf-8")) > _MAX_CATEGORY_ANALYSIS_BYTES:
        return {"category": category, "scores": {}, "matched_signals": {}, "reason": ""}
    scores = value.get("scores")
    matched = value.get("matched_signals")
    return {
        "category": category,
        "scores": scores if isinstance(scores, Mapping) else {},
        "matched_signals": matched if isinstance(matched, Mapping) else {},
        "reason": _text(value.get("reason"), 500),
    }


def _normalized_published_at(value: object, *, default: str) -> str:
    """A tz-aware ISO datetime string the Daily reconstruction accepts.

    The consumer (editorial_review._published_at) requires a timezone-aware ISO
    value; an absent/blank/naive value falls back deterministically to the
    edition's coverage-end wall clock so publication never fails on a missing
    timestamp and the stored draft stays clock-free (idempotent)."""
    text = str(value or "").strip()
    if text:
        try:
            parsed = datetime.fromisoformat(text)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None and parsed.tzinfo is not None:
            return text
    return default


def _normalize_selected_item(raw: object, *, default_published_at: str) -> dict:
    if not isinstance(raw, Mapping):
        raise OperatorReviewError("INVALID_PAYLOAD")
    candidate_id = _text(raw.get("candidate_id"), _MAX_CANDIDATE_ID)
    origin = str(raw.get("origin") or "")
    if not candidate_id or origin not in _ALLOWED_ORIGINS:
        raise OperatorReviewError("INVALID_PAYLOAD")
    selected_url = _safe_article_url(raw.get("selected_url"))
    if not selected_url:
        raise OperatorReviewError("UNSAFE_ARTICLE_URL")
    title = _text(raw.get("title"), _MAX_TITLE)
    source = _text(raw.get("source"), _MAX_SOURCE)
    if not title or not source:
        raise OperatorReviewError("INVALID_PAYLOAD")
    # Operator-edited body HTML is re-sanitized SERVER-SIDE: only text, <br>,
    # <strong>, and safe <a href> (http/https) survive; the plain summary is
    # derived from the sanitized HTML so the two never disagree.
    summary_html = editorial_briefings.sanitize_editorial_inline_html(
        _text(raw.get("summary_html"), _MAX_SUMMARY_HTML)
        or _text(raw.get("summary"), _MAX_SUMMARY)
    )
    summary = editorial_briefings.editorial_inline_plain_text(summary_html) or _text(
        raw.get("summary"), _MAX_SUMMARY
    )
    category = editorial_review.normalize_category(raw.get("category"), title, summary)
    image_url = _safe_article_url(raw.get("image_url"))
    item = {
        "candidate_id": candidate_id,
        "origin": origin,
        "title": title,
        "source": source,
        "summary": summary,
        "summary_html": summary_html,
        "selected_url": selected_url,
        "category": category,
        "category_analysis": _bounded_category_analysis(
            raw.get("category_analysis"), category
        ),
        "published_at": _normalized_published_at(
            raw.get("published_at"), default=default_published_at
        ),
        "image_url": image_url,
        "link_kind": "publisher_direct",
        "link_label": _text(raw.get("link_label"), 60) or "원문 보기",
    }
    return item


def _validated_identity(payload: Mapping) -> tuple[str, str]:
    """Return (edition_key, review_snapshot_id) or raise. Edition ↔ snapshot are
    cross-checked: the snapshot embeds its own date and the edition_key MUST equal
    it, so a mismatched or tampered pair fails closed."""
    if not isinstance(payload, Mapping):
        raise OperatorReviewError("INVALID_PAYLOAD")
    if str(payload.get("product") or "") != "daily":
        raise OperatorReviewError("UNSUPPORTED_PRODUCT")
    snapshot_id = str(payload.get("review_snapshot_id") or "")
    snapshot_key = public_urls.parse_editor_snapshot_id(snapshot_id)
    if not snapshot_key:
        raise OperatorReviewError("MALFORMED_SNAPSHOT_ID")
    edition_key = str(payload.get("edition_key") or "")
    if not public_urls.parse_daily_edition_id(f"daily-{edition_key}-0000000000000000"):
        raise OperatorReviewError("MALFORMED_EDITION_KEY")
    if edition_key != snapshot_key:
        raise OperatorReviewError("EDITION_MISMATCH")
    return edition_key, snapshot_id


def normalize_operator_review(
    payload: Mapping,
    *,
    operator_login: str,
    review_status: str,
) -> dict:
    """Validate/normalize into a durable, identity-bound review record.

    Operator identity comes ONLY from the caller (the verified session), never
    from the browser payload. Content is deterministic (no timestamps) so an
    identical save reproduces an identical revision (idempotent)."""
    edition_key, snapshot_id = _validated_identity(payload)
    login = str(operator_login or "").strip().lower()
    if not login:
        raise OperatorReviewError("INVALID_PAYLOAD", status=401)
    raw_items = payload.get("selected_items")
    if not isinstance(raw_items, list):
        raise OperatorReviewError("INVALID_PAYLOAD")
    if len(raw_items) > MAX_SELECTED:
        raise OperatorReviewError("TOO_MANY_SELECTED")
    default_published_at = f"{edition_key}T06:40:00+09:00"
    items = [
        _normalize_selected_item(item, default_published_at=default_published_at)
        for item in raw_items
    ]
    ids = [item["candidate_id"] for item in items]
    if len(ids) != len(set(ids)):
        raise OperatorReviewError("INVALID_PAYLOAD")
    record = {
        "version": REVIEW_CONTRACT_VERSION,
        "product": "daily",
        "edition_type": "daily",
        "edition_key": edition_key,
        "review_snapshot_id": snapshot_id,
        "review_status": review_status,
        "operator_login": login,
        "selected_items": items,
    }
    return record


# ---------------------------------------------------------------------------
# Server-derived storage paths (client never supplies a path)
# ---------------------------------------------------------------------------
def draft_storage_path(edition_key: str, snapshot_id: str) -> str:
    # The snapshot id is regex-validated (review-YYYY-MM-DD-<16hex>) and its
    # embedded date MUST equal edition_key, so neither component can carry path
    # traversal or a foreign edition. The path is fully server-derived.
    snapshot_key = public_urls.parse_editor_snapshot_id(snapshot_id)
    if not snapshot_key:
        raise OperatorReviewError("MALFORMED_SNAPSHOT_ID")
    if not public_urls.parse_daily_edition_id(f"daily-{edition_key}-0000000000000000"):
        raise OperatorReviewError("MALFORMED_EDITION_KEY")
    if edition_key != snapshot_key:
        raise OperatorReviewError("EDITION_MISMATCH")
    return f"{DRAFT_DIR}/{edition_key}/{snapshot_id}.json"


def approved_review_path(edition_key: str) -> str:
    if not public_urls.parse_daily_edition_id(f"daily-{edition_key}-0000000000000000"):
        raise OperatorReviewError("MALFORMED_EDITION_KEY")
    return f"{APPROVED_DIR}/{edition_key}.json"


def review_snapshot_manifest_path(snapshot_id: str) -> str:
    """Server-derived repository path of an immutable Review snapshot manifest.

    ``snapshot_id`` is regex-validated (``review-YYYY-MM-DD-<16hex>``, ``\\Z``-
    anchored) so it carries no traversal or control character; the repository,
    branch, and this path are entirely server-side (never client-supplied)."""
    if not public_urls.parse_editor_snapshot_id(snapshot_id):
        raise OperatorReviewError("MALFORMED_SNAPSHOT_ID")
    return f"{SNAPSHOT_DIR}/{snapshot_id}/manifest.json"


def verify_review_snapshot_authority(
    client: "GitHubContentsClient", edition_key: str, snapshot_id: str
) -> dict:
    """Prove ``review_snapshot_id`` resolves to a REAL immutable Review snapshot.

    Regex shape and date equality are NOT sufficient authority: the durable draft
    and approved review bind an exact snapshot identity, so the server must prove
    that identity names a snapshot that actually exists and is intact BEFORE any
    durable write or workflow dispatch.

    The server derives the fixed manifest path itself and reads it from the fixed
    repository authority (the client supplies no repository, branch, path, ref, or
    manifest URL). The manifest is then validated under the EXISTING immutable
    snapshot integrity contract (:func:`app.editor_delivery.validate_snapshot_manifest`),
    which recomputes the content-addressed sha256 digest that binds the candidate
    bundle and console HTML identities and re-derives the ``<16hex>`` id suffix —
    so a syntactically valid but nonexistent or tampered id fails closed. Precise
    identity mismatches (product / snapshot id / edition) are reported before the
    integrity contract so each failure class carries its own typed code.

    No second snapshot identity scheme is introduced; this reuses the production
    canonicalization. Returns the validated manifest on success."""
    manifest_path = review_snapshot_manifest_path(snapshot_id)
    fetched = client.get_file(manifest_path)
    if fetched is None:
        # No manifest at the server-derived path → the snapshot does not exist.
        raise OperatorReviewError("SNAPSHOT_NOT_FOUND")
    manifest = fetched.get("json") if isinstance(fetched, Mapping) else None
    if not isinstance(manifest, Mapping):
        raise OperatorReviewError("SNAPSHOT_MANIFEST_MALFORMED")
    manifest = dict(manifest)
    if str(manifest.get("product") or "") != "editor_review_snapshot":
        raise OperatorReviewError("SNAPSHOT_PRODUCT_MISMATCH")
    if str(manifest.get("review_snapshot_id") or "") != snapshot_id:
        raise OperatorReviewError("SNAPSHOT_IDENTITY_MISMATCH")
    if str(manifest.get("edition_key") or "") != edition_key:
        raise OperatorReviewError("SNAPSHOT_EDITION_MISMATCH")
    try:
        validated = editor_delivery.validate_snapshot_manifest(manifest)
    except editor_delivery.EditorDeliveryError as exc:
        # Integrity/resource-digest/asset/generation-evidence failure — this is
        # where a mutated candidate-bundle or console-HTML identity is caught,
        # because both are folded into the content-addressed integrity digest.
        raise OperatorReviewError("SNAPSHOT_INTEGRITY_MISMATCH") from exc
    if (
        validated.get("review_snapshot_id") != snapshot_id
        or validated.get("edition_key") != edition_key
    ):
        raise OperatorReviewError("SNAPSHOT_IDENTITY_MISMATCH")
    return validated


def content_revision(record: Mapping) -> str:
    """sha256 over canonical content JSON (identity of the stored record)."""
    core = {key: value for key, value in record.items() if key != "revision"}
    return hashlib.sha256(
        json.dumps(
            core, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Durable persistence via GitHub Contents API (injectable for offline tests)
# ---------------------------------------------------------------------------
class GitHubContentsClient:
    """Minimal, fixed-repository, fixed-branch GitHub Contents client.

    Repository (config.OPERATOR_REPO) and branch (DISPATCH_REF=main) are fixed
    server-side; the caller supplies only a server-derived path. Token comes from
    config (env) and is never returned/logged. Update conflicts (409/422) fail
    closed as STALE_DRAFT — no blind retry."""

    def __init__(self, *, repo: str = "", token: str = "", branch: str = DISPATCH_REF):
        self.repo = repo or config.OPERATOR_REPO
        self.token = token or config.GH_OPERATOR_TOKEN
        self.branch = branch

    def _request(self, path: str, *, method: str = "GET", data: bytes | None = None):
        url = f"{_API_BASE}/repos/{self.repo}/contents/{path}"
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        return urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS)

    def get_file(self, path: str) -> dict | None:
        url_path = f"{path}?ref={self.branch}"
        try:
            with self._request(url_path) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise OperatorReviewError("PERSIST_FAILED") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise OperatorReviewError("PERSIST_FAILED") from exc
        try:
            content = base64.b64decode(payload.get("content", "")).decode("utf-8")
            parsed = json.loads(content)
        except (ValueError, TypeError):
            parsed = None
        return {"sha": payload.get("sha") or "", "json": parsed}

    def put_file(
        self, path: str, *, content_bytes: bytes, message: str, base_sha: str | None
    ) -> dict:
        body: dict[str, object] = {
            "message": message,
            "content": base64.b64encode(content_bytes).decode("ascii"),
            "branch": self.branch,
        }
        if base_sha:
            body["sha"] = base_sha
        data = json.dumps(body).encode("utf-8")
        try:
            with self._request(path, method="PUT", data=data) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (409, 422):
                raise OperatorReviewError("STALE_DRAFT") from exc
            raise OperatorReviewError("PERSIST_FAILED") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise OperatorReviewError("PERSIST_FAILED") from exc
        return {"sha": ((payload.get("content") or {}).get("sha")) or ""}


def _serialize(record: Mapping, revision: str) -> bytes:
    stored = {**record, "revision": revision}
    return (json.dumps(stored, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def save_draft(
    payload: Mapping,
    *,
    operator_login: str,
    client: GitHubContentsClient | None = None,
) -> dict:
    """Persist an operator DRAFT durably (git), with optimistic concurrency.

    A DRAFT is never auto-published; only publish_daily writes the approved
    review that the Daily pipeline consumes."""
    client = client or GitHubContentsClient()
    if not client.repo or not client.token:
        raise OperatorReviewError("NOT_CONFIGURED")
    record = normalize_operator_review(
        payload, operator_login=operator_login, review_status="draft"
    )
    # Prove the bound review snapshot is a REAL committed immutable snapshot in
    # the repository authority before any durable write (a valid-shaped but
    # nonexistent/tampered id fails closed here, performing no write).
    verify_review_snapshot_authority(
        client, record["edition_key"], record["review_snapshot_id"]
    )
    revision = content_revision(record)
    path = draft_storage_path(record["edition_key"], record["review_snapshot_id"])
    base_revision = str(payload.get("base_revision") or "")
    current = client.get_file(path)
    if current is None:
        if base_revision:
            # The client believed a draft existed; none does → fail closed.
            raise OperatorReviewError("STALE_DRAFT")
        base_sha = None
    else:
        current_json = current.get("json") if isinstance(current.get("json"), Mapping) else {}
        current_revision = str(current_json.get("revision") or content_revision(current_json))
        if revision == current_revision:
            return {
                "ok": True,
                "revision": revision,
                "path": path,
                "unchanged": True,
                "edition_key": record["edition_key"],
                "review_snapshot_id": record["review_snapshot_id"],
                "selected_count": len(record["selected_items"]),
            }
        if base_revision != current_revision:
            raise OperatorReviewError("STALE_DRAFT")
        base_sha = current.get("sha") or None
    client.put_file(
        path,
        content_bytes=_serialize(record, revision),
        message=f"chore(editorial): save operator draft {record['edition_key']}",
        base_sha=base_sha,
    )
    return {
        "ok": True,
        "revision": revision,
        "path": path,
        "unchanged": False,
        "edition_key": record["edition_key"],
        "review_snapshot_id": record["review_snapshot_id"],
        "selected_count": len(record["selected_items"]),
    }


def publish_daily(
    payload: Mapping,
    *,
    operator_login: str,
    client: GitHubContentsClient | None = None,
    dispatcher: Callable[[], dict] | None = None,
) -> dict:
    """Confirm the EXACT persisted draft as the approved Daily review and request
    the fixed Daily publication workflow.

    Fail-closed properties:
    - the approved review is built from the PERSISTED draft (exact draft
      authority), not from a divergent posted selection;
    - a stale ``base_revision`` (≠ the persisted draft) fails STALE_DRAFT;
    - optimistic concurrency on the approved review: a ``base_approved_revision``
      that no longer matches the current approved review fails
      RECONCILIATION_REQUIRED (ambiguous publish);
    - re-publishing the identical draft is idempotent (already_published),
      performing no second dispatch;
    - the dispatcher is a no-argument injected callable → the browser can never
      choose the workflow, ref, or repository."""
    client = client or GitHubContentsClient()
    if not client.repo or not client.token:
        raise OperatorReviewError("NOT_CONFIGURED")
    edition_key, snapshot_id = _validated_identity(payload)
    login = str(operator_login or "").strip().lower()
    if not login:
        raise OperatorReviewError("INVALID_PAYLOAD", status=401)
    base_revision = str(payload.get("base_revision") or "")
    if not base_revision:
        raise OperatorReviewError("STALE_DRAFT")

    # Prove the bound review snapshot is a REAL committed immutable snapshot
    # before reading the draft, writing the approved review, or dispatching.
    verify_review_snapshot_authority(client, edition_key, snapshot_id)

    draft_path = draft_storage_path(edition_key, snapshot_id)
    draft = client.get_file(draft_path)
    if draft is None or not isinstance(draft.get("json"), Mapping):
        raise OperatorReviewError("DRAFT_NOT_FOUND")
    draft_json = draft["json"]
    draft_revision = str(draft_json.get("revision") or content_revision(draft_json))
    if base_revision != draft_revision:
        raise OperatorReviewError("STALE_DRAFT")

    selected = draft_json.get("selected_items")
    if not isinstance(selected, list) or not selected:
        raise OperatorReviewError("EMPTY_SELECTION")
    if len(selected) > MAX_SELECTED:
        raise OperatorReviewError("TOO_MANY_SELECTED")

    approved = {
        "version": REVIEW_CONTRACT_VERSION,
        "product": "daily",
        "edition_type": "daily",
        "edition_key": edition_key,
        "review_snapshot_id": snapshot_id,
        "review_status": "approved",
        "operator_login": login,
        "source_draft_revision": draft_revision,
        "selected_items": selected,
    }
    approved_revision = content_revision(approved)
    approved_path = approved_review_path(edition_key)
    base_approved_revision = str(payload.get("base_approved_revision") or "")
    current = client.get_file(approved_path)
    if current is None:
        if base_approved_revision:
            raise OperatorReviewError("RECONCILIATION_REQUIRED")
        base_sha = None
    else:
        current_json = current.get("json") if isinstance(current.get("json"), Mapping) else {}
        current_revision = str(
            current_json.get("revision") or content_revision(current_json)
        )
        if current_revision == approved_revision:
            # Exactly this draft is already the approved review → idempotent.
            return {
                "ok": True,
                "already_published": True,
                "approved_revision": approved_revision,
                "source_draft_revision": draft_revision,
                "edition_key": edition_key,
                "approved_review_path": approved_path,
                "dispatched": False,
            }
        if base_approved_revision != current_revision:
            # Someone else changed the approved review since the operator loaded
            # it → ambiguous; do not overwrite, do not dispatch.
            raise OperatorReviewError("RECONCILIATION_REQUIRED")
        base_sha = current.get("sha") or None

    client.put_file(
        approved_path,
        content_bytes=_serialize(approved, approved_revision),
        message=f"chore(editorial): approve operator Daily review {edition_key}",
        base_sha=base_sha,
    )
    dispatch_result: dict = {"dispatched": False}
    if dispatcher is not None:
        dispatch_result = {"dispatched": True, "dispatch": dispatcher()}
    return {
        "ok": True,
        "already_published": False,
        "approved_revision": approved_revision,
        "source_draft_revision": draft_revision,
        "edition_key": edition_key,
        "review_snapshot_id": snapshot_id,
        "approved_review_path": approved_path,
        **dispatch_result,
    }


# ---------------------------------------------------------------------------
# Deterministic supersession preview (pure; reuses the real mint)
# ---------------------------------------------------------------------------
def superseding_edition_preview(
    *,
    base_manifest: Mapping,
    approved_review: Mapping,
    bundle: Mapping,
    run_at,
    root_url: str = public_urls.PUBLIC_ROOT,
) -> dict:
    """Compute the edition identity an operator-approved review WOULD mint.

    Reuses the exact production mint (choose_daily_articles →
    build_daily_edition_manifest) so a changed selection yields a new edition_id,
    while the base edition manifest is only read (never mutated)."""
    articles, review_mode = editorial_review.choose_daily_articles(
        bundle,
        approved_review,
        limit=max(
            editorial_briefings.DAILY_MAX_ARTICLES,
            len(bundle.get("candidates") or []),
        ),
    )
    coverage = editorial_briefings.daily_coverage(run_at)
    dated_url, latest_url = editorial_briefings.public_urls(
        root_url, "daily", str(base_manifest.get("edition_key") or "")
    )
    manifest = editorial_briefings.build_daily_edition_manifest(
        edition_key=str(base_manifest.get("edition_key") or ""),
        coverage=coverage,
        articles=articles,
        html_sha256=hashlib.sha256(review_mode.encode("utf-8")).hexdigest(),
        dated_url=dated_url,
        latest_url=latest_url,
        run_at=run_at,
        review_mode="human_approved",
        review_decision="approved",
    )
    base_id = str(base_manifest.get("edition_id") or "")
    return {
        "base_edition_id": base_id,
        "superseding_edition_id": manifest["edition_id"],
        "edition_id_changed": manifest["edition_id"] != base_id,
        "article_count": manifest["article_count"],
        "review_mode": review_mode,
    }
