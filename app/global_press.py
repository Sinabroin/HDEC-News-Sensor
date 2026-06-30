"""Global Press 도메인 (leaf) — 해외 언론 출처 판별 + 영어 locale 수집 그룹 단일 소스 (D7-AD-N).

국내 뉴스(Google News 한국 locale)와 분리해 '해외 언론' 신호를 별도 그룹/렌즈로 둔다.
data/global_press_sources.json을 읽어 (1) 라이브 수집기용 영어 locale 쿼리 그룹,
(2) 표시 레이어(briefing/대시보드)용 해외 매체 출처 판별을 제공한다.

경계(이 파일만 한다 / 절대 안 한다):
- 한다: 정책 JSON 로드, is_foreign_press(출처 판별), collection_query_group(수집 그룹 dict).
  순수 함수, 네트워크 0건.
- 안 한다: DB 접근, 점수/insight, 발송, 네트워크 호출, 기사 본문 저장/번역, app.config import.

가짜 데이터 금지: collect 쿼리는 NEWS_MODE=live 공개 Google News(en-US) 경로에서만 쓰이고,
mock은 data/mock_articles.json만 읽어 해외 출처가 없으므로 해외 섹션은 항상 빈 상태가 된다.
수집 실패 시 빈 결과를 돌려주고 가짜 해외 기사를 만들지 않는다. 원문 제목은 보존한다.
"""

import json
import re
from pathlib import Path

# app.config를 import하지 않는다 (DB_PATH 캐시 트랩 회피) — 경로를 직접 계산한다.
_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "global_press_sources.json"

DEFAULT_LABEL = "해외 언론"

_HANGUL_RE = re.compile(r"[가-힣]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _load() -> dict:
    """정책 JSON을 읽는다. 파일이 없거나 깨지면 빈 정책을 반환한다(호출자가 죽지 않게)."""
    try:
        data = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def source_label() -> str:
    """해외 기사에 붙일 출처 그룹 라벨('해외 언론')."""
    return _load().get("foreign_source_label") or DEFAULT_LABEL


def _foreign_outlets() -> list[str]:
    outlets = _load().get("foreign_outlets")
    if not isinstance(outlets, list):
        return []
    return [o for o in outlets if isinstance(o, str) and o.strip()]


def _looks_foreign_title(title) -> bool:
    """제목이 비한국어(라틴 문자 있고 한글 없음)이면 True — 해외 매체 판별의 1차 신호.

    국내 수집은 Google News 한국 locale(hl=ko)이라 한글 제목, 해외 수집은 en-US라 영문 제목이
    돌아온다. 출처명 레지스트리를 일일이 열거하는 것보다 제목 언어가 훨씬 robust한 분리 신호다
    (해외 매체의 long-tail 출처명을 다 등록할 수 없으므로). 숫자/기호만 있는 제목은 제외한다.
    """
    text = str(title or "")
    return bool(_LATIN_RE.search(text)) and not _HANGUL_RE.search(text)


def _source_in_registry(source) -> bool:
    """출처명이 알려진 해외 매체 레지스트리에 있으면 True (대소문자 무시, 단어 경계 부분 일치).

    단어 경계를 요구해 짧은 매체명(BBC/CNN 등)이 다른 단어 속에 우연히 박혀 오탐하는 것을 막는다.
    """
    text = " ".join(str(source or "").split()).lower()
    if not text:
        return False
    for outlet in _foreign_outlets():
        needle = outlet.lower()
        idx = text.find(needle)
        while idx != -1:
            before = text[idx - 1] if idx > 0 else " "
            after_pos = idx + len(needle)
            after = text[after_pos] if after_pos < len(text) else " "
            if not before.isalnum() and not after.isalnum():
                return True
            idx = text.find(needle, idx + 1)
    return False


def is_foreign_press(source, title: str = "") -> bool:
    """해외 언론(해외 매체) 기사인지 판별한다.

    1차(robust): 제목이 비한국어(영문 등)이면 해외로 본다 — 국내 수집(hl=ko)은 한글 제목,
    해외 수집(en-US)은 영문 제목이라 언어가 가장 신뢰도 높은 분리 신호다.
    2차(보조): 출처명이 알려진 해외 매체 레지스트리에 있으면 해외로 본다(제목이 모호할 때).
    둘 다 아니면 보수적으로 False — 국내 출처를 잘못 '해외'로 태깅하지 않는다(가짜 분류 금지).
    """
    if _looks_foreign_title(title):
        return True
    return _source_in_registry(source)


def collection_query_group() -> dict | None:
    """라이브 수집기가 머지할 영어 locale 수집 그룹 dict (enabled일 때만).

    {name, label, queries, locale{hl,gl,ceid}, max_per_query, max_total}. 비활성/빈 쿼리면
    None — 수집기는 None을 무시한다(가짜 그룹을 만들지 않음). locale은 en-US(해외 매체)다.
    """
    group = _load().get("collection_group")
    if not isinstance(group, dict) or not group.get("enabled"):
        return None
    queries = [q for q in (group.get("queries") or [])
               if isinstance(q, str) and q.strip()]
    if not queries:
        return None
    locale = group.get("locale") if isinstance(group.get("locale"), dict) else {}
    return {
        "name": group.get("name") or "global_press",
        "label": group.get("label") or source_label(),
        "queries": queries,
        "locale": {k: locale.get(k) for k in ("hl", "gl", "ceid") if locale.get(k)},
        "max_per_query": int(group.get("max_per_query") or 2),
        "max_total": int(group.get("max_total") or 12),
    }
