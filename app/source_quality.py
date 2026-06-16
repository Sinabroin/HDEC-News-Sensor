"""Source Quality 도메인 — 출처 품질 분류 (P0-C1.6).

공개 RSS(예: Google News)는 언론 보도뿐 아니라 블로그·카페·커뮤니티·재전송성
결과를 섞어 돌려준다. 이 모듈은 (source, title)만 보고 출처 품질을 결정적으로
분류한다 — 임원용 Top 3에 블로그/카페/커뮤니티성 결과가 들어가지 않게 하는
가드레일이다.

경계 원칙 (다른 도메인 책임 침범 금지):
- 순수 함수만 제공한다: DB·네트워크·발송·점수 계산을 하지 않는다.
- 정책 데이터는 data/source_quality_rules.json 한 곳이 단일 소스다.
- 출처 품질은 "사실 보증"이 아니라 랭킹/필터용 신호다 (가짜 신뢰도 생성 금지).

소비처:
- live_collector: excluded 출처를 수집 단계에서 제외 (is_excluded).
- scoring: low/excluded를 즉시 알림 임계 아래로 캡 (SCORE_CAP / cap_for).
- briefing: 시그널 entry에 source_quality 라벨 부착 + Top 3에서 excluded 배제.

분류 결과:
    {"source_quality": trusted|neutral|low|excluded,
     "source_type": news|institution|blog|cafe|community|video|aggregator|unknown,
     "source_quality_reason": "...",
     "source_quality_label": "신뢰 출처|일반 출처|낮은 신뢰도"}
"""

import json
import re

from app import config

_RULES_PATH = config.DATA_DIR / "source_quality_rules.json"

# JSON 로드 실패 시에도 안전하게 동작하도록 최소 기본값을 둔다 (가짜 신뢰도 금지 —
# 알 수 없으면 neutral). 파일이 정상이면 파일 값이 이 기본값을 덮어쓴다.
_DEFAULT_RULES = {
    "excluded_source_patterns": [],
    "excluded_title_patterns": [],
    "downrank_title_patterns": [],
    "low_trust_source_patterns": [],
    "trusted_source_patterns": [],
    "institution_source_patterns": [],
    "source_type_hints": {},
    "quality_label": {
        "trusted": "신뢰 출처", "neutral": "일반 출처",
        "low": "낮은 신뢰도", "excluded": "낮은 신뢰도",
    },
    "score_cap": {"excluded": 1.0, "low": 3.4},
    "operator_note": "출처 품질 필터: 블로그·카페·커뮤니티성 결과는 제외하거나 낮은 우선순위로 처리합니다.",
}

_rules_cache = None


def _load_rules() -> dict:
    global _rules_cache
    if _rules_cache is None:
        merged = dict(_DEFAULT_RULES)
        try:
            data = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                merged.update(data)
        except (OSError, ValueError):
            pass  # 파일 없거나 깨지면 안전 기본값으로 동작 (전부 neutral 경향)
        _rules_cache = merged
    return _rules_cache


# 공개 상수 — 다른 도메인이 import해 단일 소스로 쓴다.
LABEL_BY_QUALITY = _load_rules().get("quality_label") or _DEFAULT_RULES["quality_label"]
SCORE_CAP = _load_rules().get("score_cap") or _DEFAULT_RULES["score_cap"]
OPERATOR_NOTE = _load_rules().get("operator_note") or _DEFAULT_RULES["operator_note"]


def _matches(haystack: str, patterns) -> str | None:
    """patterns 중 haystack에 등장하는 첫 패턴을 돌려준다 (대소문자 무시)."""
    for pat in patterns or []:
        p = (pat or "").strip()
        if p and p.lower() in haystack:
            return p
    return None


def _detect_type(source_low: str, fallback: str = "unknown") -> str:
    hints = _load_rules().get("source_type_hints") or {}
    for stype in ("institution", "blog", "cafe", "video", "community", "aggregator"):
        if _matches(source_low, hints.get(stype)):
            return stype
    return fallback


def _label(quality: str) -> str:
    return LABEL_BY_QUALITY.get(quality, "일반 출처")


def classify(source: str, title: str = "") -> dict:
    """(source, title)을 출처 품질로 분류한다 (순수·결정적).

    우선순위:
      1) source가 excluded 패턴(블로그/카페/커뮤니티/티스토리/유튜브 등) → excluded
      2) source가 공공/기관 패턴 → trusted(institution)
      3) source가 신뢰 매체 패턴 → trusted(news)
      4) source가 재전송/홍보성 패턴 → low(aggregator)
      5) (이하 neutral 출처에 한해 제목 검사)
         제목에 블로그/카페/브이로그 → excluded
         제목에 후기/추천/광고/협찬/할인 등 소비자성 → low
      6) 그 외 → neutral

    신뢰 매체/기관은 제목 패턴을 무시한다 — '네이버 블로그 규제' 같은 정상 보도가
    제목 단어 때문에 잘못 강등되지 않게 한다.
    """
    rules = _load_rules()
    src = (source or "").strip()
    src_low = src.lower()
    title_low = (title or "").lower()

    # 1) 출처 자체가 블로그/카페/커뮤니티성 → 제외 (가장 강한 신호이므로 최우선)
    hit = _matches(src_low, rules.get("excluded_source_patterns"))
    if hit:
        stype = _detect_type(src_low, "blog")
        return {
            "source_quality": "excluded",
            "source_type": stype,
            "source_quality_reason": f"블로그/카페/커뮤니티성 출처('{hit}') — 임원용 신호에서 제외",
            "source_quality_label": _label("excluded"),
        }

    # 2) 공공/기관 출처 → 신뢰
    hit = _matches(src_low, rules.get("institution_source_patterns"))
    if hit:
        return {
            "source_quality": "trusted",
            "source_type": "institution",
            "source_quality_reason": f"공공/기관 출처('{hit}')",
            "source_quality_label": _label("trusted"),
        }

    # 3) 신뢰 매체 → 신뢰 (제목 패턴 무시)
    hit = _matches(src_low, rules.get("trusted_source_patterns"))
    if hit:
        return {
            "source_quality": "trusted",
            "source_type": "news",
            "source_quality_reason": f"신뢰 매체('{hit}')",
            "source_quality_label": _label("trusted"),
        }

    # 4) 재전송/홍보성 출처 → 낮은 신뢰도
    hit = _matches(src_low, rules.get("low_trust_source_patterns"))
    if hit:
        return {
            "source_quality": "low",
            "source_type": _detect_type(src_low, "aggregator"),
            "source_quality_reason": f"재전송/홍보성 출처('{hit}') — 우선순위 하향",
            "source_quality_label": _label("low"),
        }

    # 5) neutral 출처에 한해 제목 패턴 검사
    hit = _matches(title_low, rules.get("excluded_title_patterns"))
    if hit:
        return {
            "source_quality": "excluded",
            "source_type": _detect_type(src_low, "blog"),
            "source_quality_reason": f"제목이 블로그/카페성('{hit}') — 임원용 신호에서 제외",
            "source_quality_label": _label("excluded"),
        }
    hit = _matches(title_low, rules.get("downrank_title_patterns"))
    if hit:
        return {
            "source_quality": "low",
            "source_type": _detect_type(src_low, "unknown"),
            "source_quality_reason": f"제목이 소비자/후기/광고성('{hit.strip()}') — 우선순위 하향",
            "source_quality_label": _label("low"),
        }

    # 6) 알 수 없는 출처는 중립 (자동 차단하지 않는다)
    return {
        "source_quality": "neutral",
        "source_type": _detect_type(src_low, "unknown"),
        "source_quality_reason": "식별된 제외/신뢰 패턴 없음 — 중립 처리",
        "source_quality_label": _label("neutral"),
    }


# 집계/재전송 호스트 표시 정규화 (P0-C1.11) — raw 호스트가 신뢰 매체처럼 보이지 않게.
_AGGREGATOR_DISPLAY = _load_rules().get("aggregator_display") or {}
# 일반 호스트형 출처(점 포함, 한글/공백 없음) 판별 — 알 수 없는 집계 호스트는 '원문 경유'로.
_HOST_RE = re.compile(r"^https?://|^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}(?:/|$)")
VIA_GENERIC_LABEL = "원문 경유"


def normalize_display_source(source: str) -> str:
    """임원 표시용 출처 라벨 — 집계/재전송 호스트는 'Daum 경유' 등으로 정규화한다.

    공개 RSS가 출처를 v.daum.net 같은 집계 호스트로 돌려줄 때, 이를 신뢰 매체처럼
    노출하지 않는다. 정상 매체명(연합뉴스 등)은 그대로 둔다 (정규화 대상 아님).
    원문 URL/내부 source 필드는 호출자가 그대로 보존한다 — 이 함수는 표시 라벨만 만든다.
    """
    src = (source or "").strip()
    if not src:
        return src
    low = src.lower()
    # 명시된 집계 호스트는 긴 키 우선으로 라벨 매핑 (v.daum.net이 daum.net보다 먼저).
    for host in sorted(_AGGREGATOR_DISPLAY, key=len, reverse=True):
        if host and host.lower() in low:
            return _AGGREGATOR_DISPLAY[host]
    # 매핑에 없지만 명백한 호스트형 출처(점 포함, 한글/공백 없음)면 중립적 '원문 경유'.
    if " " not in src and not any("가" <= ch <= "힣" for ch in src) and _HOST_RE.match(low):
        return VIA_GENERIC_LABEL
    return src


def is_excluded(source: str, title: str = "") -> bool:
    """수집 단계 제외 판정 — live_collector가 excluded 출처를 버릴 때 쓴다."""
    return classify(source, title)["source_quality"] == "excluded"


def cap_for(quality: str) -> float | None:
    """해당 품질의 점수 상한. 상한이 없으면(trusted/neutral) None."""
    cap = SCORE_CAP.get(quality)
    return float(cap) if cap is not None else None
