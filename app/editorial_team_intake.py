"""Durable, pending-only team article intake.

The browser supplies an immutable review identity and one URL. The server proves
the snapshot, re-analyzes the URL, derives all editorial fields itself, and writes
one content-addressed pending record through the fixed GitHub Contents client.
No publish, dispatch, approval, or learning activation exists in this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from app import (
    editorial_article_import,
    editorial_briefings,
    editorial_operator_review,
    public_urls,
)

PENDING_SUBMISSION_VERSION = 1
PENDING_SUBMISSION_DIR = "data/editorial_feedback/pending_submissions"
PENDING_SUBMISSION_LIMIT = 50
_IMAGE_DATA_URL_RE = re.compile(
    r"data:image/(?:jpeg|png|webp);base64,[A-Za-z0-9+/]+={0,2}\Z"
)

_ERRORS: dict[str, tuple[int, str]] = {
    "INVALID_PAYLOAD": (400, "검토 요청 형식이 올바르지 않습니다."),
    "AUTH_REQUIRED": (401, "팀원 인증이 필요합니다."),
    "NOT_CONFIGURED": (503, "팀원 검토 요청 저장이 구성되지 않았습니다."),
    "SNAPSHOT_INVALID": (409, "정확한 검토 스냅샷을 확인하지 못했습니다."),
    "ARTICLE_INVALID": (422, "기사를 서버에서 다시 확인하지 못했습니다."),
    "PERSIST_FAILED": (502, "검토 요청 저장에 실패했습니다."),
    "PENDING_INVALID": (409, "저장된 팀원 제안의 무결성을 확인하지 못했습니다."),
    "INTERNAL_ERROR": (500, "검토 요청 처리 중 내부 오류가 발생했습니다."),
}


class TeamIntakeError(RuntimeError):
    def __init__(self, code: str, *, status: int | None = None, message: str = ""):
        default_status, default_message = _ERRORS.get(code, _ERRORS["INTERNAL_ERROR"])
        super().__init__(code)
        self.code = code if code in _ERRORS else "INTERNAL_ERROR"
        self.status = int(default_status if status is None else status)
        self.message = str(message or default_message)

    def response_payload(self) -> dict[str, object]:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


def _identity(payload: Mapping) -> tuple[str, str, str]:
    if not isinstance(payload, Mapping) or set(payload) != {
        "product", "edition_key", "review_snapshot_id", "url"
    }:
        raise TeamIntakeError("INVALID_PAYLOAD")
    if payload.get("product") != "daily":
        raise TeamIntakeError("INVALID_PAYLOAD")
    edition_key = str(payload.get("edition_key") or "")
    snapshot_id = str(payload.get("review_snapshot_id") or "")
    if public_urls.parse_editor_snapshot_id(snapshot_id) != edition_key:
        raise TeamIntakeError("INVALID_PAYLOAD")
    url = str(payload.get("url") or "").strip()
    if not url or len(url) > editorial_article_import.ARTICLE_URL_MAX_LENGTH:
        raise TeamIntakeError("INVALID_PAYLOAD")
    return edition_key, snapshot_id, url


def pending_submission_path(edition_key: str, submission_id: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(edition_key or "")):
        raise TeamIntakeError("INVALID_PAYLOAD")
    if not re.fullmatch(r"submission-[0-9a-f]{64}", str(submission_id or "")):
        raise TeamIntakeError("PENDING_INVALID")
    return f"{PENDING_SUBMISSION_DIR}/{edition_key}/{submission_id}.json"


def _safe_image(value: object) -> str:
    image = str(value or "")
    if not image:
        return ""
    if (
        len(image) > editorial_article_import.IMPORT_IMAGE_MAX_DATA_URL_CHARS
        or not _IMAGE_DATA_URL_RE.fullmatch(image)
    ):
        return ""
    return image


def _record_core(
    *, edition_key: str, snapshot_id: str, article: Mapping[str, object]
) -> dict[str, object]:
    title = str(article.get("title") or "").strip()[:500]
    source = str(article.get("source") or "").strip()[:160]
    summary = str(article.get("summary") or "").strip()[:500]
    category = str(article.get("category") or "").strip()[:80]
    analysis_url = editorial_briefings.manual_publisher_article_url(
        article.get("analysis_url") or article.get("canonical_url")
    )
    publisher_authoritative = bool(article.get("publisher_domain_authoritative"))
    publisher_url = (
        editorial_briefings.manual_publisher_article_url(article.get("publisher_url"))
        if publisher_authoritative
        else ""
    )
    portal_copy = bool(article.get("portal_copy") or article.get("portal_fallback_used"))
    portal_source = str(article.get("portal_source") or "")[:40]
    portal_reason = str(article.get("portal_resolution_reason") or "")[:120]
    if not all((title, source, summary, category, analysis_url)):
        raise TeamIntakeError("ARTICLE_INVALID")
    if publisher_authoritative:
        if not publisher_url or not editorial_article_import.is_publisher_direct_url(
            publisher_url
        ):
            raise TeamIntakeError("ARTICLE_INVALID")
        portal_copy = False
    else:
        if (
            not portal_copy
            or portal_source not in {"daum", "naver"}
            or portal_reason != "portal_copy_fallback"
            or not editorial_article_import.is_allowlisted_portal_copy_url(
                analysis_url, portal_source
            )
        ):
            raise TeamIntakeError("ARTICLE_INVALID")
        publisher_url = ""
    return {
        "schema": "editorial_pending_submission_v1",
        "version": PENDING_SUBMISSION_VERSION,
        "edition_key": edition_key,
        "review_snapshot_id": snapshot_id,
        "analysis_url": analysis_url,
        "publisher_url": publisher_url,
        "publisher_domain_authoritative": publisher_authoritative,
        "source": source,
        "title": title,
        "executive_summary": summary,
        "category": category,
        "image_url": _safe_image(article.get("image_url")),
        "portal_copy": portal_copy,
        "portal_source": portal_source,
        "portal_resolution_reason": portal_reason,
        "status": "pending",
    }


def _submission_id(core: Mapping[str, object]) -> str:
    stable = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "submission-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()


def valid_pending_submission(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    allowed = {
        "schema", "version", "submission_id", "edition_key", "review_snapshot_id",
        "analysis_url", "publisher_url", "publisher_domain_authoritative",
        "source", "title", "executive_summary", "category", "image_url",
        "portal_copy", "portal_source", "portal_resolution_reason",
        "submitted_at", "status",
    }
    if set(value) != allowed:
        return False
    edition_key = str(value.get("edition_key") or "")
    snapshot_id = str(value.get("review_snapshot_id") or "")
    if public_urls.parse_editor_snapshot_id(snapshot_id) != edition_key:
        return False
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00",
        str(value.get("submitted_at") or ""),
    ):
        return False
    try:
        core = _record_core(edition_key=edition_key, snapshot_id=snapshot_id, article={
            "analysis_url": value.get("analysis_url"),
            "publisher_url": value.get("publisher_url"),
            "publisher_domain_authoritative": value.get("publisher_domain_authoritative"),
            "source": value.get("source"),
            "title": value.get("title"),
            "summary": value.get("executive_summary"),
            "category": value.get("category"),
            "image_url": value.get("image_url"),
            "portal_copy": value.get("portal_copy"),
            "portal_source": value.get("portal_source"),
            "portal_resolution_reason": value.get("portal_resolution_reason"),
        })
    except TeamIntakeError:
        return False
    return bool(
        value.get("status") == "pending"
        and value.get("schema") == "editorial_pending_submission_v1"
        and value.get("version") == PENDING_SUBMISSION_VERSION
        and value.get("submission_id") == _submission_id(core)
        and dict(value) == {
            **core,
            "submission_id": value.get("submission_id"),
            "submitted_at": value.get("submitted_at"),
        }
    )


def submit_for_review(
    payload: Mapping,
    *,
    client: editorial_operator_review.GitHubContentsClient | None = None,
    importer: Callable[..., dict] = editorial_article_import.import_article,
    now: datetime | None = None,
) -> dict[str, object]:
    edition_key, snapshot_id, url = _identity(payload)
    client = client or editorial_operator_review.GitHubContentsClient()
    if not client.repo or not client.token:
        raise TeamIntakeError("NOT_CONFIGURED")
    try:
        editorial_operator_review.verify_review_snapshot_authority(
            client, edition_key, snapshot_id
        )
    except editorial_operator_review.OperatorReviewError as exc:
        raise TeamIntakeError("SNAPSHOT_INVALID") from exc
    try:
        imported = importer(url)
    except editorial_article_import.ArticleImportError as exc:
        raise TeamIntakeError("ARTICLE_INVALID", message=exc.message) from exc
    article = imported.get("article") if isinstance(imported, Mapping) else None
    if not isinstance(article, Mapping) or imported.get("ok") is not True:
        raise TeamIntakeError("ARTICLE_INVALID")
    core = _record_core(
        edition_key=edition_key,
        snapshot_id=snapshot_id,
        article=article,
    )
    submission_id = _submission_id(core)
    path = pending_submission_path(edition_key, submission_id)
    current = client.get_file(path)
    if current is not None:
        stored = current.get("json") if isinstance(current, Mapping) else None
        if not valid_pending_submission(stored) or stored.get("submission_id") != submission_id:
            raise TeamIntakeError("PENDING_INVALID")
        return {
            "ok": True,
            "submission_id": submission_id,
            "edition_key": edition_key,
            "status": "pending",
            "unchanged": True,
        }
    submitted_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="seconds"
    )
    record = {**core, "submission_id": submission_id, "submitted_at": submitted_at}
    try:
        client.put_file(
            path,
            content_bytes=(
                json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8"),
            message=f"chore(editorial): add pending team submission {submission_id[-12:]}",
            base_sha=None,
        )
    except editorial_operator_review.OperatorReviewError as exc:
        if exc.code == "STALE_DRAFT":
            concurrent = client.get_file(path)
            stored = concurrent.get("json") if isinstance(concurrent, Mapping) else None
            if valid_pending_submission(stored) and stored.get("submission_id") == submission_id:
                return {
                    "ok": True,
                    "submission_id": submission_id,
                    "edition_key": edition_key,
                    "status": "pending",
                    "unchanged": True,
                }
        raise TeamIntakeError("PERSIST_FAILED") from exc
    return {
        "ok": True,
        "submission_id": submission_id,
        "edition_key": edition_key,
        "status": "pending",
        "unchanged": False,
    }


def load_pending_submissions(
    edition_key: str,
    snapshot_id: str,
    *,
    client: editorial_operator_review.GitHubContentsClient | None = None,
) -> dict[str, object]:
    if public_urls.parse_editor_snapshot_id(snapshot_id) != edition_key:
        raise TeamIntakeError("INVALID_PAYLOAD")
    client = client or editorial_operator_review.GitHubContentsClient()
    if not client.repo or not client.token:
        raise TeamIntakeError("NOT_CONFIGURED")
    try:
        editorial_operator_review.verify_review_snapshot_authority(
            client, edition_key, snapshot_id
        )
        paths = client.list_directory(f"{PENDING_SUBMISSION_DIR}/{edition_key}")
    except editorial_operator_review.OperatorReviewError as exc:
        raise TeamIntakeError("SNAPSHOT_INVALID") from exc
    pattern = re.compile(
        rf"{re.escape(PENDING_SUBMISSION_DIR)}/{re.escape(edition_key)}/"
        r"submission-[0-9a-f]{64}\.json\Z"
    )
    output = []
    for path in paths[: PENDING_SUBMISSION_LIMIT * 2]:
        if not pattern.fullmatch(path):
            continue
        stored = client.get_file(path)
        value = stored.get("json") if isinstance(stored, Mapping) else None
        if (
            not valid_pending_submission(value)
            or value.get("edition_key") != edition_key
            or value.get("review_snapshot_id") != snapshot_id
            or path != pending_submission_path(edition_key, str(value.get("submission_id") or ""))
        ):
            continue
        output.append(dict(value))
        if len(output) >= PENDING_SUBMISSION_LIMIT:
            break
    output.sort(key=lambda item: (str(item["submitted_at"]), str(item["submission_id"])))
    return {
        "ok": True,
        "edition_key": edition_key,
        "review_snapshot_id": snapshot_id,
        "count": len(output),
        "submissions": output,
    }


__all__ = [
    "PENDING_SUBMISSION_DIR",
    "PENDING_SUBMISSION_LIMIT",
    "TeamIntakeError",
    "load_pending_submissions",
    "pending_submission_path",
    "submit_for_review",
    "valid_pending_submission",
]
