"""뉴스 원문 접근성 분류 — 보안정책 진단용, 기사 중요도와 독립.

이 모듈은 전달받은 URL/응답 메타데이터만 분류한다. 네트워크 호출, 프록시,
리다이렉트 우회, 기사 삭제 또는 레이더 점수 계산은 하지 않는다.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from urllib.parse import urlparse

ACCESS_STATUSES = {
    "unknown", "ok", "corp_blocked", "redirected", "timeout", "error",
}
SOURCE_TYPES = {
    "publisher", "portal", "rss", "api", "search", "licensed_db", "unknown",
}
COLLECTION_METHODS = {
    "rss", "api", "search_result", "portal_result", "manual_report", "unknown",
}
SUGGESTED_POLICIES = {
    "allow_candidate", "review_needed", "keep_blocked", "unknown",
}

_PORTAL_DOMAINS = (
    "news.naver.com", "n.news.naver.com", "v.daum.net", "news.daum.net",
)
_SEARCH_DOMAINS = (
    "news.google.com", "google.com", "bing.com", "search.naver.com",
)
_LICENSED_DOMAINS = ("bigkinds.or.kr",)
_WARNING_BODY_SIGNATURES = (
    "hdec.kr/warning",
    "warning.jpg",
    "유해사이트 차단",
    "접근이 차단되었습니다",
    "사내 보안정책",
    "web filtering",
)
_TRACKING_MARKERS = (
    "doubleclick.", "adservice.", "/click?", "/track?", "utm_redirect",
)
_HARMFUL_CATEGORIES = {"malware", "phishing", "adult", "gambling", "command_and_control"}


def _domain(url: str | None) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def detect_corp_warning_url(url: str) -> bool:
    """HDEC warning 이미지/페이지 URL이면 True.

    URL 문자열만 보는 결정적 판정이라 CI와 오프라인 환경에서도 동작한다.
    응답 본문 signature 판정은 :func:`classify_link_access`가 담당한다.
    """
    value = str(url or "").strip().lower()
    return "hdec.kr/warning" in value or "warning.jpg" in value


def _has_warning_signature(body_sample: str | bytes | None) -> bool:
    if isinstance(body_sample, bytes):
        text = body_sample[:8192].decode("utf-8", errors="replace")
    else:
        text = str(body_sample or "")[:8192]
    lowered = text.lower()
    return any(signature in lowered for signature in _WARNING_BODY_SIGNATURES)


def classify_source_type(url: str | None) -> str:
    """URL 형태와 host로 reader용 source_type을 보수적으로 분류한다."""
    value = str(url or "").strip()
    domain = _domain(value)
    lowered = value.lower()
    if not domain:
        return "unknown"
    if any(domain == item or domain.endswith("." + item) for item in _LICENSED_DOMAINS):
        return "licensed_db"
    if any(domain == item or domain.endswith("." + item) for item in _PORTAL_DOMAINS):
        return "portal"
    if any(domain == item or domain.endswith("." + item) for item in _SEARCH_DOMAINS):
        return "search"
    if re.search(r"(?:^|/)(rss|feed)(?:[/?#]|$)", lowered) or lowered.endswith(
            (".rss", ".xml", ".atom")):
        return "rss"
    if re.search(r"(?:^|/)(api|openapi)(?:[/?#]|$)", lowered):
        return "api"
    if urlparse(value).scheme.lower() in {"http", "https"}:
        return "publisher"
    return "unknown"


def classify_collection_method(article: dict) -> str:
    """수집 provenance에서 collection_method를 파생한다.

    접근 가능 여부와 무관하며 원본 메타데이터가 불명확하면 ``unknown``으로 둔다.
    """
    explicit = str(article.get("collection_method") or "").strip().lower()
    if explicit in COLLECTION_METHODS:
        return explicit

    metadata = article.get("source_metadata_json") or article.get("source_metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    provider = str((metadata or {}).get("provider") or article.get("signal_origin") or "").lower()
    url = str(article.get("url") or "")
    source_type = classify_source_type(url)
    if "rss" in provider or source_type == "rss":
        return "rss"
    if "api" in provider:
        return "api"
    if "search" in provider or "google_news" in provider:
        return "search_result"
    if "portal" in provider or source_type == "portal":
        return "portal_result"
    if any(mark in provider for mark in ("manual", "report")):
        return "manual_report"
    return "unknown"


def classify_link_access(
    original_url,
    final_url=None,
    status_code=None,
    content_type=None,
    body_sample=None,
) -> dict:
    """관측된 URL/응답 정보만으로 링크 접근 상태를 분류한다.

    ``final_url``이나 응답 정보가 없으면 ``unknown``이다. 이 함수가 외부 주소에
    접속하지 않으므로 CI/offline 빌드에서 네트워크가 강제되지 않는다.
    """
    original = str(original_url or "").strip()
    final = str(final_url or "").strip() or original
    domain = _domain(original)
    source_type = classify_source_type(original)

    if detect_corp_warning_url(original) or detect_corp_warning_url(final) or _has_warning_signature(
            body_sample):
        status = "corp_blocked"
        note = "HDEC warning URL 또는 사내 차단 페이지 signature 감지"
    elif isinstance(status_code, str) and status_code.strip().lower() == "timeout":
        status = "timeout"
        note = "원문 응답 시간 초과"
    else:
        try:
            code = int(status_code) if status_code is not None else None
        except (TypeError, ValueError):
            code = None
        if code is not None and code >= 400:
            status = "error"
            note = f"원문 HTTP 오류 ({code})"
        elif original and final and original.rstrip("/") != final.rstrip("/"):
            status = "redirected"
            note = f"원문 요청이 {_domain(final) or '다른 URL'}로 리다이렉트됨"
        elif code is not None and 200 <= code < 400:
            status = "ok"
            ctype = str(content_type or "").split(";", 1)[0].strip()
            note = f"원문 응답 확인 ({code}{', ' + ctype if ctype else ''})"
        elif not original:
            status = "error"
            note = "원문 URL 없음"
        else:
            status = "unknown"
            note = "접근 확인 전 — 기사 중요도와 무관"

    return {
        "link_access_status": status,
        "link_access_note": note,
        "final_url": final,
        "source_domain": domain,
        "source_type": source_type,
    }


def _blocked_reason(article: dict, access: dict) -> str:
    if access["link_access_status"] == "corp_blocked":
        return access["link_access_note"]
    reason = article.get("blocked_reason") or article.get("block_policy_name")
    return str(reason or "")


def _suggest_policy(article: dict, access: dict) -> str:
    explicit = str(article.get("suggested_policy") or "").strip()
    if explicit in SUGGESTED_POLICIES:
        return explicit
    category = str(article.get("url_category") or article.get("security_category") or "").lower()
    original = str(article.get("url") or article.get("original_url") or "")
    if category in _HARMFUL_CATEGORIES or detect_corp_warning_url(original):
        return "keep_blocked"
    if any(marker in original.lower() for marker in _TRACKING_MARKERS):
        return "review_needed"
    if access["link_access_status"] == "corp_blocked":
        if access["source_type"] in {"publisher", "rss", "api", "licensed_db"}:
            return "allow_candidate"
        return "review_needed"
    if access["link_access_status"] in {"redirected", "timeout", "error"}:
        return "review_needed"
    if access["link_access_status"] == "ok" and access["source_type"] == "publisher":
        return "allow_candidate"
    return "unknown"


def _http_nonwarning(value) -> str:
    """http(s)이고 warning URL이 아닐 때만 정규화한 URL을 돌려준다(아니면 "")."""
    text = str(value or "").strip()
    if not re.match(r"^https?://", text, re.I):
        return ""
    if detect_corp_warning_url(text):
        return ""
    host = _domain(text)
    lowered = text.lower()
    if host in {"example.com", "www.example.com", "example.invalid"} or "/mock-" in lowered:
        return ""
    return text


def is_aggregator_url(value) -> bool:
    """news.google.com / 네이버·다음 포털 / 검색 결과 등 '경유(aggregator)' URL이면 True.

    퍼블리셔 원문 직링크(publisher)와 구분한다 — 외부 href는 직링크를 우선하고, 경유 URL은
    직링크 후보가 하나도 없을 때에 한해 fallback으로 쓴다. URL 문자열만 보는 결정적 판정이라
    CI/offline에서도 동작한다(네트워크 없음).
    """
    return classify_source_type(value) in {"portal", "search"}


def _parse_metadata(article: dict) -> dict:
    """source_metadata_json(문자열) 또는 source_metadata(dict)를 dict로 정규화한다."""
    metadata = article.get("source_metadata_json") or article.get("source_metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    return metadata if isinstance(metadata, dict) else {}


def choose_external_article_url(article: dict) -> str:
    """외부 '원문 사이트' 링크로 쓸, 가장 원본에 가깝고 접근 가능한 URL을 고른다.

    우선순위 (D7-AD-X — 퍼블리셔 직링크 우선, news.google.com/포털 경유는 fallback):
      1) ``canonical_url`` / ``original_url`` (퍼블리셔 직링크 필드)
      2) source_metadata의 ``canonical_url`` / ``original_url`` / ``publisher_url``
         (Naver originallink 등 provider가 준 원문 직링크)
      3) ``url`` (수집된 기본 링크) — 퍼블리셔 직링크일 때
      4) source_metadata의 ``source_url`` — 퍼블리셔 직링크일 때
      5) **fallback**: 위 후보가 하나도 없고 ``url``/``source_url``이 news.google.com·
         포털·검색 경유 URL이면 그대로 쓴다(링크를 통째로 없애지 않는다 — 임원이 원문에
         닿을 최소 경로를 유지한다).
      6) ``final_url`` — **진단용 메타**. warning이 아니고 원본과 같은 퍼블리셔 도메인일
         때에 한해 최후 후보로만 쓴다(기본 href로 승격하지 않는다).

    ``hdec.kr/warning`` / ``WARNING.jpg`` warning URL은 어떤 경우에도 외부 href로
    선택하지 않는다. 접근 상태(:func:`classify_link_access`)·점수·분류·최신성 판단과
    독립이며 네트워크 호출은 하지 않는다. 후보가 하나도 없으면 "" (링크 미생성).
    """
    metadata = _parse_metadata(article)
    # 1~4) 퍼블리셔 직링크 우선 — 경유(aggregator/portal/search) URL은 이 패스에서 제외한다.
    for candidate in (
        article.get("canonical_url"),
        article.get("original_url"),
        metadata.get("canonical_url"),
        metadata.get("original_url"),
        metadata.get("publisher_url"),
        article.get("url"),
        metadata.get("source_url"),
    ):
        chosen = _http_nonwarning(candidate)
        if chosen and not is_aggregator_url(chosen):
            return chosen

    # 5) fallback — 퍼블리셔 직링크가 없을 때만 news.google.com/포털/검색 경유 URL을 쓴다.
    for candidate in (article.get("url"), metadata.get("source_url"),
                      article.get("canonical_url"), article.get("original_url")):
        chosen = _http_nonwarning(candidate)
        if chosen:
            return chosen

    # 6) final_url은 리다이렉트/차단 진단 결과다 — 원본과 같은 퍼블리셔이고 warning이 아닐
    # 때만 최후 후보. warning/redirect endpoint를 외부 href로 승격하지 않는다.
    final = _http_nonwarning(article.get("final_url"))
    if final:
        base_domain = _domain(article.get("url") or article.get("original_url"))
        if base_domain and _domain(final) == base_domain:
            return final
    return ""


def build_source_inventory(articles) -> list[dict]:
    """보안팀 검토용 domain 단위 source inventory를 만든다.

    네트워크 조회는 하지 않으며, 기사 row는 변경하거나 삭제하지 않는다. 같은 domain의
    표본 수와 관측 상태를 집계하고 대표 URL/차단 사유/정책 후보를 함께 반환한다.
    """
    groups: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for raw in articles or []:
        article = dict(raw or {})
        original = article.get("original_url") or article.get("url") or ""
        access = classify_link_access(
            original,
            final_url=article.get("final_url"),
            status_code=article.get("status_code"),
            content_type=article.get("content_type"),
            body_sample=article.get("body_sample"),
        )
        groups[access["source_domain"] or "(unknown)"].append((article, access))

    inventory = []
    status_rank = {
        "corp_blocked": 0, "error": 1, "timeout": 2,
        "redirected": 3, "unknown": 4, "ok": 5,
    }
    policy_rank = {
        "keep_blocked": 0, "review_needed": 1, "allow_candidate": 2, "unknown": 3,
    }
    for domain, samples in sorted(groups.items()):
        representative, access = min(
            samples, key=lambda pair: status_rank.get(pair[1]["link_access_status"], 9))
        policies = [_suggest_policy(article, result) for article, result in samples]
        policy = min(policies, key=lambda value: policy_rank.get(value, 9))
        original = representative.get("original_url") or representative.get("url") or ""
        inventory.append({
            "domain": domain,
            "source": representative.get("source") or "출처 미상",
            "original_url": original,
            "final_url": access["final_url"],
            "access_status": access["link_access_status"],
            "blocked_reason": _blocked_reason(representative, access),
            "sample_count": len(samples),
            "source_type": access["source_type"],
            "collection_method": classify_collection_method(representative),
            "suggested_policy": policy,
        })
    return inventory
