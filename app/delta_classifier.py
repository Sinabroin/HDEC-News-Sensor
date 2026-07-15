"""app/delta_classifier.py — 의미 있는 대시보드 변동(delta) 분류 (D7-AK-1/2).

이 leaf만 소유하는 것:
  · canonical article identity — app.watch_state의 normalize_url/title_fingerprint 재사용.
    단일 키가 아니라 alias 집합(정규화 URL · source+제목지문+게시일 · 안정 article_id)의
    '교집합(하나라도 겹침)'으로 old↔new를 매칭한다. 우선순위 url → std → article_id.
  · change_type 분류 taxonomy + 대표 change_type 1개 + change_reasons(복수) 부여.
  · 시간당 알림 자격(hourly eligibility) 판정 + suppression 사유/카운트.

두 계층은 분리한다 (D7-AK-2 §2). ① change classification — '무슨 변화인가'.
② hourly alert eligibility — '그 변화를 지금 임원에게 보낼 가치가 있는가'. 저가치·stale
이라는 이유로 change_type을 바꾸거나 지우지 않는다. 분류는 그대로 두고 정책 결과
(hourly_eligible + hourly_suppression_reasons)를 덧붙이며, 분류된 meaningful 전체
(pre_policy_meaningful)와 실제 발송 후보(meaningful = hourly eligible)를 모두 보존한다.

절대 하지 않는 것: 네트워크·DB·env·파일·LLM/embedding 호출, 점수/등급 재계산. 모델에
이미 들어 있는 score/tier/section/title/snippet/published_at만 읽고, 임계값은 기존 정책
(scoring INSTANT 4.5 / DAILY 3.5, news_recency IMMEDIATE_MAX_AGE_HOURS 72)을 그대로
재사용한다(새 임계 생성 금지). 신선도 기준시각은 호출자가 주입한다 — 이 파일은 벽시계를
직접 읽지 않는다(fixture가 실행 날짜에 따라 흔들리지 않게: D7-AJ-1 재발 방지).

입력은 (surface, row-dict) 쌍의 나열이다. 대시보드 HTML/JSON 파싱과 alert-surface
순회는 호출자(scripts/detect_dashboard_alert_delta.py)가 소유하고, 이 파일은 그 결과만
분류한다. rank/top-5 승격은 미지원(단일 전역 랭킹이 없어 임의 rank를 만들지 않음).
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Iterable

from app.news_recency import hours_since_published, passes_immediate_recency
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

# 분류상 '의미 있는' 변동 유형. 나머지는 전부 무시(ignored).
# 주의: 이건 분류 계층이다. 여기 속해도 시간당 알림 자격(hourly eligibility)은 별개로 본다.
MEANINGFUL_TYPES = frozenset(
    {NEW_ARTICLE, PRIORITY_UPGRADE, HDEC_RELEVANCE_UPGRADE, MATERIAL_CONTENT_UPDATE}
)

# ── hourly alert eligibility (D7-AK-2) ───────────────────────────────────────
# suppression 사유 — 아티팩트/진단에 그대로 노출된다.
BELOW_DAILY_SCORE_THRESHOLD = "below_daily_score_threshold"
NOT_HDEC_DIRECT = "not_hdec_direct"
OUTSIDE_IMMEDIATE_RECENCY_WINDOW = "outside_immediate_recency_window"
PUBLISHED_TIME_UNAVAILABLE = "published_time_unavailable"

# suppression 집계 버킷 — 기사 1건은 정확히 하나의 버킷에만 센다(카운트 합이 어긋나지 않게).
# 우선순위: 저가치 → stale → 시각불명. 가치를 먼저 보므로 suppressed_stale은 '가치는 있으나
# 너무 오래된' 건수를 뜻한다 — 놓친 신호를 감시하는 운영 KPI로 그게 더 쓸모 있다.
SUPPRESSED_LOW_VALUE = "low_value"
SUPPRESSED_STALE = "stale"
SUPPRESSED_UNKNOWN_TIME = "unknown_time"

# 아티팩트 표시 정렬 (§7) — 분류 precedence와 다르다.
# ①② 상승 신호 → ③ 현대건설 직접 → ④ 점수 → ⑤⑥ 신규→내용갱신 → ⑦ 최신순.
_UPGRADE_ORDER = {HDEC_RELEVANCE_UPGRADE: 0, PRIORITY_UPGRADE: 1}
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
    # ── 정책 계층 (분류와 분리). 분류가 meaningful인 기사에만 판정한다.
    hourly_eligible: bool = False
    hourly_suppression_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeltaClassification:
    # 실제 발송 대상 — 분류상 meaningful ∧ hourly eligible, 표시정렬됨.
    meaningful: tuple[ClassifiedArticle, ...]
    # 정책 적용 전 분류상 meaningful 전체(진단용 — 무엇을 왜 걸렀는지 남긴다).
    pre_policy_meaningful: tuple[ClassifiedArticle, ...]
    candidates: tuple[ClassifiedArticle, ...]  # 변동 후보 전체(meaningful + ignored)
    change_type_counts: dict
    meaningful_count: int  # == hourly_eligible_count (발송 대상 수)
    pre_policy_meaningful_count: int
    ignored_count: int  # 분류에서 무시된 수(정책 suppression과 별개)
    deduplicated_count: int
    duplicate_collapsed_count: int  # new-side dedup으로 접힌 중복 row 수(진단)
    suppressed_low_value_count: int = 0
    suppressed_stale_count: int = 0
    suppressed_unknown_time_count: int = 0

    @property
    def hourly_eligible_count(self) -> int:
        """meaningful_count의 명시적 별칭 — 발송 대상 수는 단일 값 하나뿐이다."""
        return self.meaningful_count


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
        "published_epoch", "published_at", "_article_id", "_source", "_category",
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
        self.published_at: str = ""  # 신선도 게이트용 raw 문자열('시각 불명'과 '오래됨' 구분)
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
        pub_raw = _published_at(row)
        if epoch > self.published_epoch:
            self.published_epoch = epoch
            self.published_at = pub_raw
        if not self.published_at and pub_raw:
            self.published_at = pub_raw
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


# ── 정책 계층: 시간당 알림 자격 (D7-AK-2) ────────────────────────────────────

def _hourly_eligibility(
    group: _Group, news_mode: str, ref_dt: datetime | None
) -> tuple[bool, list[str], str]:
    """분류상 meaningful인 변동이 '지금 임원에게 보낼' 자격이 있는지 판정한다.

    자격 = 가치(현대건설 직접 ∨ score >= DAILY_THRESHOLD) ∧ 신선도(즉시확인 recency 창).
    새 임계값을 만들지 않고 기존 scoring / news_recency 정책을 그대로 재사용한다.
    반환: (eligible, suppression 사유 전부, 집계 버킷 1개)."""
    reasons: list[str] = []
    bucket = ""

    # ① 가치 게이트 — score 누락/파싱 실패는 0으로 취급(fail-closed).
    score = group.score if group.score is not None else 0.0
    if not (group.hdec_direct or score >= DAILY_THRESHOLD):
        reasons += [BELOW_DAILY_SCORE_THRESHOLD, NOT_HDEC_DIRECT]
        bucket = SUPPRESSED_LOW_VALUE

    # ② 신선도 게이트 — live에서만 창을 적용하고 시각 불명이면 닫는다(news_recency 계약).
    if not passes_immediate_recency(group.published_at, news_mode, ref_dt):
        if hours_since_published(group.published_at, ref_dt) is None:
            reasons.append(PUBLISHED_TIME_UNAVAILABLE)
            bucket = bucket or SUPPRESSED_UNKNOWN_TIME
        else:
            reasons.append(OUTSIDE_IMMEDIATE_RECENCY_WINDOW)
            bucket = bucket or SUPPRESSED_STALE

    return (not reasons), reasons, bucket


def _sort_key(article: ClassifiedArticle) -> tuple:
    """표시 정렬(§7) — 상승 신호 → 현대건설 직접 → 점수 → 신규/내용갱신 → 최신순.

    최신순은 마지막 tiebreaker다. 이전에는 published가 점수보다 앞서 있어 top-5가 사실상
    '가장 최근에 게시된 것들'이 됐고 고점수·현대건설 직접 신호가 밀려났다(D7-AK-2A)."""
    return (
        _UPGRADE_ORDER.get(article.change_type, 2),
        -(1 if article.hdec_direct else 0),
        -(article.score if article.score is not None else -1.0),
        _DISPLAY_ORDER.get(article.change_type, 9),
        -(article.published_epoch or 0),
    )


def classify_delta(
    old_pairs: Iterable[tuple[str, dict]],
    new_pairs: Iterable[tuple[str, dict]],
    *,
    news_mode: str = "mock",
    reference_dt: datetime | None = None,
) -> DeltaClassification:
    """old/new alert-surface row를 비교해 ① change_type을 분류하고 ② 시간당 알림 자격을 판정한다.

    두 계층은 섞지 않는다. 저가치/stale이라는 이유로 change_type을 바꾸지 않고, 분류 결과에
    hourly_eligible + hourly_suppression_reasons를 덧붙일 뿐이다. 분류상 meaningful 전체는
    pre_policy_meaningful로 보존한다.

    GITHUB_OUTPUT alert_delta와 artifact alert_delta는 모두 이 결과의
    meaningful_count(= hourly_eligible_count) >= 1을 단일 경로로 쓴다(--delta-artifact 유무 무관).

    news_mode/reference_dt는 호출자가 주입한다 — 이 파일은 벽시계를 읽지 않는다."""
    old_groups, _ = _build_groups(old_pairs)
    new_groups, collapsed = _build_groups(new_pairs)
    old_index = _index_by_signal(old_groups)

    candidates: list[ClassifiedArticle] = []
    suppressed = {SUPPRESSED_LOW_VALUE: 0, SUPPRESSED_STALE: 0, SUPPRESSED_UNKNOWN_TIME: 0}
    for group in new_groups:
        matched = _match_old(group, old_index)
        classified = _classify_one(group, matched)
        if classified is None:
            continue
        # 정책 계층은 분류상 meaningful인 것에만 적용한다 — 이미 무시된 변동은 정책이
        # 다시 셀 필요가 없다(ignored와 suppressed 카운트가 겹치지 않게).
        if classified.change_type in MEANINGFUL_TYPES:
            eligible, reasons, bucket = _hourly_eligibility(group, news_mode, reference_dt)
            classified = replace(
                classified,
                hourly_eligible=eligible,
                hourly_suppression_reasons=tuple(reasons),
            )
            if bucket:
                suppressed[bucket] += 1
        candidates.append(classified)

    counts: dict = {}
    for article in candidates:
        counts[article.change_type] = counts.get(article.change_type, 0) + 1

    pre_policy = sorted(
        (c for c in candidates if c.change_type in MEANINGFUL_TYPES), key=_sort_key
    )
    eligible_articles = [c for c in pre_policy if c.hourly_eligible]
    return DeltaClassification(
        meaningful=tuple(eligible_articles),
        pre_policy_meaningful=tuple(pre_policy),
        candidates=tuple(candidates),
        change_type_counts=counts,
        meaningful_count=len(eligible_articles),
        pre_policy_meaningful_count=len(pre_policy),
        ignored_count=len(candidates) - len(pre_policy),
        deduplicated_count=len(candidates),
        duplicate_collapsed_count=collapsed,
        suppressed_low_value_count=suppressed[SUPPRESSED_LOW_VALUE],
        suppressed_stale_count=suppressed[SUPPRESSED_STALE],
        suppressed_unknown_time_count=suppressed[SUPPRESSED_UNKNOWN_TIME],
    )
