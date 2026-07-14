"""app/delta_classifier.py — 의미 있는 대시보드 변동(delta) 분류 (D7-AK-1).

이 leaf만 소유하는 것:
  · canonical article identity — app.watch_state의 normalize_url/title_fingerprint 재사용.
    단일 키가 아니라 alias 집합(정규화 URL · source+제목지문+게시일 · 안정 article_id)의
    '교집합(하나라도 겹침)'으로 old↔new를 매칭한다. 우선순위 url → std → article_id.
  · change_type 분류 taxonomy + 대표 change_type 1개 + change_reasons(복수) 부여.
  · 발송 인정(meaningful) vs 무시(ignored) 게이트와 change_type 카운트.

절대 하지 않는 것: 네트워크·DB·env·파일·LLM/embedding 호출, 점수/등급 재계산. 모델에
이미 들어 있는 score/tier/section/title/snippet만 읽고, 임계값은 기존 scoring 정책
(INSTANT_THRESHOLD 4.5 / DAILY_THRESHOLD 3.5)을 그대로 재사용한다(새 임계 생성 금지).

입력은 (surface, row-dict) 쌍의 나열이다. 대시보드 HTML/JSON 파싱과 alert-surface
순회는 호출자(scripts/detect_dashboard_alert_delta.py)가 소유하고, 이 파일은 그 결과만
분류한다. rank/top-5 승격은 미지원(단일 전역 랭킹이 없어 임의 rank를 만들지 않음).
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from app.scoring import (
    DAILY_THRESHOLD,
    GRADE_DAILY,
    GRADE_EXCLUDED,
    GRADE_INSTANT,
    GRADE_WEEKLY,
    INSTANT_THRESHOLD,
)
from app.watch_state import normalize_url, title_fingerprint

# ── change_type taxonomy ─────────────────────────────────────────────────────
NEW_ARTICLE = "new_article"
PRIORITY_UPGRADE = "priority_upgrade"
HDEC_RELEVANCE_UPGRADE = "hdec_relevance_upgrade"
MATERIAL_CONTENT_UPDATE = "material_content_update"
RANK_ONLY = "rank_only"  # 미지원(랭킹 부재) — 생성하지 않는다. taxonomy 완결성 위해 상수만 유지.
SURFACE_MOVE = "surface_move"
METADATA_ONLY = "metadata_only"
DUPLICATE_REAPPEARANCE = "duplicate_reappearance"

# 발송 인정 유형 (게이트). 나머지는 전부 무시.
MEANINGFUL_TYPES = frozenset(
    {NEW_ARTICLE, PRIORITY_UPGRADE, HDEC_RELEVANCE_UPGRADE, MATERIAL_CONTENT_UPDATE}
)
# 아티팩트 표시 정렬 순서 (분류 순서가 아니라 최종 노출 순서, §6).
_DISPLAY_ORDER = {
    HDEC_RELEVANCE_UPGRADE: 0,
    PRIORITY_UPGRADE: 1,
    NEW_ARTICLE: 2,
    MATERIAL_CONTENT_UPDATE: 3,
}

_NO_TIER = 9  # hdec_relevance_tier 기본(관련 없음). 낮을수록 관련성 높음.
_EPOCH0 = datetime(1970, 1, 1, tzinfo=timezone.utc)


# ── 결과 자료구조 ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ClassifiedArticle:
    change_type: str
    change_reasons: tuple[str, ...]
    before: dict | None  # 신규 기사면 None (before 값 없음)
    after: dict
    representative: dict  # 아티팩트에 노출할 대표 row (URL 있는·최신 우선)
    surfaces: tuple[str, ...]
    published_epoch: int
    score: float | None
    hdec_direct: bool


@dataclass(frozen=True)
class DeltaClassification:
    meaningful: tuple[ClassifiedArticle, ...]  # 표시정렬된 발송 인정 기사
    candidates: tuple[ClassifiedArticle, ...]  # meaningful + ignored (변동 후보 전체)
    change_type_counts: dict
    meaningful_count: int
    ignored_count: int
    deduplicated_count: int
    duplicate_collapsed_count: int  # new-side dedup으로 접힌 중복 row 수(진단)


# ── 필드 추출 헬퍼 ────────────────────────────────────────────────────────────

def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _parse_score(value: object) -> float | None:
    text = _norm_text(value)
    if not text or text == "-":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _content_fp(text: object) -> str:
    """실질 콘텐츠 지문 — NFKC·HTML unescape·소문자화 후 영숫자/한글만 남긴다.

    공백·구두점·HTML entity·Unicode 표기·시각포맷 차이는 모두 제거돼 동일해진다(§6)."""
    normalized = unicodedata.normalize("NFKC", html.unescape(str(text or "")))
    return re.sub(r"[^0-9a-z가-힣]+", "", normalized.lower())


def _provenance(row: dict) -> dict:
    prov = row.get("provenance")
    return prov if isinstance(prov, dict) else {}


def _tier(row: dict) -> int:
    prov = _provenance(row)
    raw = prov.get("hdec_relevance_tier")
    if raw is None:
        raw = row.get("hdecRelevanceTier")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return _NO_TIER


def _is_hdec_direct(row: dict) -> bool:
    prov = _provenance(row)
    return "hdec_direct" in (
        prov.get("executive_section"),
        prov.get("radar_section"),
        row.get("executive_section"),
    )


def _published_at(row: dict) -> str:
    pub = row.get("published_at")
    if not pub:
        pub = _provenance(row).get("published_at")
    return _norm_text(pub)


def _published_date(row: dict) -> str:
    pub = _published_at(row)
    return pub[:10] if len(pub) >= 10 else pub


def _published_epoch(row: dict) -> int:
    pub = _published_at(row)
    try:
        dt = datetime.fromisoformat(pub)
    except (TypeError, ValueError):
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int((dt - _EPOCH0).total_seconds())


def _row_http_url(row: dict) -> str:
    for field_name in ("external_url", "url", "canonical_url", "original_url"):
        value = _norm_text(row.get(field_name))
        if value.lower().startswith(("http://", "https://")):
            return value
    return ""


def _raw_urls(row: dict) -> set[str]:
    """정규화 전 URL 문자열들 — tracking 변형(utm 등) 재등장 판별용."""
    out: set[str] = set()
    for field_name in ("canonical_url", "external_url", "original_url", "url"):
        value = _norm_text(row.get(field_name))
        if value:
            out.add(value)
    return out


def _signals(row: dict) -> set[str]:
    """이 row의 canonical identity alias 집합. 두 row는 alias가 하나라도 겹치면 동일 기사.

    우선순위(매칭 시): url: → std: → aid:. article_id는 tracking으로 흔들릴 수 있어
    URL/제목·날짜 다음에 둔다(§5). 단 URL이 없고 제목이 바뀌어도 안정 article_id가
    같으면 aid alias로 기존 기사에 매칭된다."""
    sig: set[str] = set()
    for field_name in ("canonical_url", "external_url", "original_url", "url"):
        normalized = normalize_url(str(row.get(field_name) or ""))
        if normalized:
            sig.add("url:" + normalized)
    tfp = title_fingerprint(str(row.get("title") or ""))
    source = _norm_text(row.get("source") or row.get("display_source"))
    date = _published_date(row)
    if tfp and (source or date):
        sig.add(f"std:{source}|{tfp}|{date}")
    article_id = _norm_text(row.get("article_id"))
    if article_id:
        sig.add("aid:" + article_id)
    return sig


def _grade_for(score: float | None) -> str:
    if score is None:
        return ""
    if score >= INSTANT_THRESHOLD:
        return GRADE_INSTANT
    if score >= DAILY_THRESHOLD:
        return GRADE_DAILY
    if score > 0:
        return GRADE_WEEKLY
    return GRADE_EXCLUDED


# ── 기사 그룹(동일 identity의 다중 surface/행 집계) ───────────────────────────

class _Group:
    """같은 canonical identity의 모든 row를 한 기사로 접은 집계 단위."""

    __slots__ = (
        "signals", "url_signals", "norm_urls", "raw_urls", "rows", "surfaces",
        "score", "tier", "hdec_direct", "_title", "_snippet",
        "published_epoch", "_article_id", "_source", "_category",
    )

    def __init__(self) -> None:
        self.signals: set[str] = set()
        self.url_signals: set[str] = set()
        self.norm_urls: set[str] = set()
        self.raw_urls: set[str] = set()
        self.rows: list[dict] = []
        self.surfaces: set[str] = set()
        self.score: float | None = None
        self.tier: int = _NO_TIER
        self.hdec_direct: bool = False
        self._title: str = ""
        self._snippet: str = ""
        self.published_epoch: int = 0
        self._article_id: str = ""
        self._source: str = ""
        self._category: str = ""

    def add(self, surface: str, row: dict, sigs: set[str]) -> None:
        self.rows.append(row)
        self.surfaces.add(surface)
        self.signals |= sigs
        for sig in sigs:
            if sig.startswith("url:"):
                self.url_signals.add(sig)
                self.norm_urls.add(sig[4:])
        self.raw_urls |= _raw_urls(row)

        score = _parse_score(row.get("score"))
        if score is not None and (self.score is None or score > self.score):
            self.score = score
        tier = _tier(row)
        if tier < self.tier:
            self.tier = tier
        if _is_hdec_direct(row):
            self.hdec_direct = True
        if not self._title and _norm_text(row.get("title")):
            self._title = str(row.get("title"))
        if not self._snippet:
            snippet = _norm_text(row.get("snippet") or row.get("whyImportant"))
            if snippet:
                self._snippet = snippet
        epoch = _published_epoch(row)
        if epoch > self.published_epoch:
            self.published_epoch = epoch
        if not self._article_id:
            self._article_id = _norm_text(row.get("article_id"))
        if not self._source:
            self._source = _norm_text(row.get("source") or row.get("display_source"))
        if not self._category:
            self._category = _norm_text(
                row.get("category_label") or row.get("cat") or row.get("tag")
            )

    @property
    def content_key(self) -> tuple[str, str]:
        """실질 콘텐츠 키 — 제목지문 + 요약지문. lens별로 달라지는 radar reason은
        surface 이동을 콘텐츠 변경으로 오인하지 않도록 제외한다(정밀도 우선)."""
        return (title_fingerprint(self._title), _content_fp(self._snippet))

    @property
    def meta_key(self) -> tuple:
        score = round(self.score, 3) if self.score is not None else None
        return (self._source, self._category, self._article_id, score)

    def best_row(self) -> dict:
        """대표 row — http URL 있는 것 우선, 그다음 최신 게시."""
        best = None
        best_key: tuple[int, int] = (-1, -1)
        for row in self.rows:
            key = (1 if _row_http_url(row) else 0, _published_epoch(row))
            if best is None or key > best_key:
                best, best_key = row, key
        return best if best is not None else (self.rows[0] if self.rows else {})


def _build_groups(pairs: Iterable[tuple[str, dict]]) -> tuple[list[_Group], int]:
    """(surface, row) 나열을 canonical identity로 접는다. alias 하나라도 겹치면 병합.

    반환: (groups, collapsed) — collapsed는 기존 그룹에 병합된 중복 row 수."""
    groups: list[_Group] = []
    by_signal: dict[str, _Group] = {}
    collapsed = 0
    for surface, row in pairs:
        sigs = _signals(row)
        target: _Group | None = None
        for sig in sigs:
            existing = by_signal.get(sig)
            if existing is not None:
                target = existing
                break
        if target is None:
            target = _Group()
            groups.append(target)
        else:
            collapsed += 1
        target.add(surface, row, sigs)
        for sig in target.signals:
            by_signal[sig] = target
    return groups, collapsed


def _index_by_signal(groups: list[_Group]) -> dict[str, _Group]:
    index: dict[str, _Group] = {}
    for group in groups:
        for sig in group.signals:
            index.setdefault(sig, group)
    return index


def _match_old(new_group: _Group, old_index: dict[str, _Group]) -> _Group | None:
    """우선순위(url → std → aid)로 old 그룹을 찾는다. 하나라도 겹치면 동일 기사."""
    for prefix in ("url:", "std:", "aid:"):
        for sig in new_group.signals:
            if sig.startswith(prefix):
                found = old_index.get(sig)
                if found is not None:
                    return found
    return None


def _snapshot(group: _Group) -> dict:
    return {
        "score": group.score,
        "grade": _grade_for(group.score),
        "hdec_relevance_tier": group.tier,
        "hdec_direct": group.hdec_direct,
        "surfaces": sorted(group.surfaces),
    }


def _fmt_score(score: float | None) -> str:
    return "없음" if score is None else f"{score:g}"


def _threshold_crossed(old_score: float | None, new_score: float | None) -> float | None:
    """new_score가 기존 threshold(4.5→3.5)를 상향 통과했으면 그 값을 돌려준다(없으면 None)."""
    if new_score is None:
        return None
    old = old_score if old_score is not None else float("-inf")
    for threshold in (INSTANT_THRESHOLD, DAILY_THRESHOLD):
        if old < threshold <= new_score:
            return threshold
    return None


def _make(change_type: str, reasons: list[str], old: _Group | None, new: _Group) -> ClassifiedArticle:
    return ClassifiedArticle(
        change_type=change_type,
        change_reasons=tuple(reasons),
        before=_snapshot(old) if old is not None else None,
        after=_snapshot(new),
        representative=new.best_row(),
        surfaces=tuple(sorted(new.surfaces)),
        published_epoch=new.published_epoch,
        score=new.score,
        hdec_direct=new.hdec_direct,
    )


def _classify_one(new: _Group, old: _Group | None) -> ClassifiedArticle | None:
    """대표 change_type 1개를 정해진 precedence로 부여한다. 완전 동일이면 None(후보 아님)."""
    # identity 자체가 없으면 억지 추정 금지 — fail-closed로 ignored(metadata_only).
    if not new.signals:
        return _make(METADATA_ONLY, ["unstable/absent identity — fail-closed to ignored"], old, new)

    # A. old에 없음 → 신규 기사 (upgrade류 절대 부여 안 함, before 없음).
    if old is None:
        return _make(NEW_ARTICLE, ["new canonical identity (absent in previous dashboard)"], None, new)

    # B. 매칭됨 — 순서대로 판정.
    # ① 현대건설 관련성 상승
    reasons: list[str] = []
    if new.hdec_direct and not old.hdec_direct:
        reasons.append("entered 현대건설 직접 관련 섹션(hdec_direct)")
    if new.tier < old.tier:
        reasons.append(f"hdec relevance tier improved {old.tier}->{new.tier}")
    if reasons:
        return _make(HDEC_RELEVANCE_UPGRADE, reasons, old, new)

    # ② 중요도 상승 — 기존 score threshold 상향 통과
    crossed = _threshold_crossed(old.score, new.score)
    if crossed is not None:
        return _make(
            PRIORITY_UPGRADE,
            [f"score crossed {crossed} threshold ({_fmt_score(old.score)}->{_fmt_score(new.score)})"],
            old, new,
        )

    # ③ 실질 콘텐츠 변경 (제목/요약 정규화 후 상이)
    if old.content_key != new.content_key:
        return _make(MATERIAL_CONTENT_UPDATE, ["title/summary content changed after normalization"], old, new)

    # ④ surface 집합 변경 (콘텐츠·관련성·중요도 불변)
    if old.surfaces != new.surfaces:
        return _make(
            SURFACE_MOVE,
            [f"surface set changed ({','.join(sorted(old.surfaces))}->{','.join(sorted(new.surfaces))})"],
            old, new,
        )

    # ⑤ tracking/id 변형 재등장 vs 순수 메타 vs 완전 동일
    url_shared = bool(old.url_signals & new.url_signals)
    tracking_variant = (old.norm_urls == new.norm_urls and old.raw_urls != new.raw_urls) or not url_shared
    meta_changed = old.meta_key != new.meta_key
    if tracking_variant and not meta_changed:
        return _make(DUPLICATE_REAPPEARANCE, ["reappeared via tracking-url/id variant; no content change"], old, new)
    if meta_changed:
        return _make(METADATA_ONLY, ["metadata changed (source/category/id/score jitter); not executive-meaningful"], old, new)
    return None  # 모든 추적 차원 동일 → 후보 아님


def _sort_key(article: ClassifiedArticle) -> tuple:
    return (
        _DISPLAY_ORDER.get(article.change_type, 9),
        -(article.published_epoch or 0),
        -(article.score if article.score is not None else -1.0),
        -(1 if article.hdec_direct else 0),
    )


def classify_delta(
    old_pairs: Iterable[tuple[str, dict]],
    new_pairs: Iterable[tuple[str, dict]],
) -> DeltaClassification:
    """old/new alert-surface row 나열을 비교해 변동을 change_type으로 분류한다.

    GITHUB_OUTPUT alert_delta와 artifact alert_delta는 모두 이 결과의
    meaningful_count>=1을 단일 경로로 쓴다(--delta-artifact 유무 무관)."""
    old_groups, _ = _build_groups(old_pairs)
    new_groups, collapsed = _build_groups(new_pairs)
    old_index = _index_by_signal(old_groups)

    candidates: list[ClassifiedArticle] = []
    for group in new_groups:
        matched = _match_old(group, old_index)
        classified = _classify_one(group, matched)
        if classified is not None:
            candidates.append(classified)

    counts: dict = {}
    for article in candidates:
        counts[article.change_type] = counts.get(article.change_type, 0) + 1

    meaningful = sorted(
        (c for c in candidates if c.change_type in MEANINGFUL_TYPES),
        key=_sort_key,
    )
    return DeltaClassification(
        meaningful=tuple(meaningful),
        candidates=tuple(candidates),
        change_type_counts=counts,
        meaningful_count=len(meaningful),
        ignored_count=len(candidates) - len(meaningful),
        deduplicated_count=len(candidates),
        duplicate_collapsed_count=collapsed,
    )
