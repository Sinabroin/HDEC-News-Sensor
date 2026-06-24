"""Lens Queries 도메인 (leaf) — 중앙 렌즈 쿼리 정책의 단일 소스 로더 (P0-D7-E).

요약 대시보드(scripts/build_static_dashboard.py)와 라이브 수집기(app/live_collector.py)가
같은 정책(data/lens_queries.json)을 공유하도록 한다 — "렌즈 우선(lens-first)" 수집과
렌즈 태깅, 빈 상태 설명이 한곳에서 정의된 쿼리/키워드/연동 상태에서 나오게 만든다.

경계(이 파일만 한다 / 절대 안 한다):
- 한다: data/lens_queries.json을 읽어 (1) 대시보드 모델용 lens_policy, (2) 빌더 태깅용
  키워드→렌즈 매핑, (3) 수집기용 렌즈 쿼리 그룹을 파생한다. 순수 함수, 네트워크 0건.
- 안 한다: DB 접근, 점수/insight, 발송, 네트워크, app.config import(빌더가 bootstrap 전에
  import해도 안전하도록 경로를 자체 계산한다 — config는 import 시 DB_PATH를 캐시한다).

가짜 데이터 금지: collect 쿼리는 NEWS_MODE=live 공개 RSS 경로에서만 쓰이고, mock은
data/mock_articles.json만 읽는다. 미연동 렌즈(supported=false)는 가짜 결과를 만들지 않는다.
"""

import json
from pathlib import Path

# app.config를 import하지 않는다 (DB_PATH 캐시 트랩 회피) — 경로를 직접 계산한다.
_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "lens_queries.json"

# 대시보드 모델 lens_policy에 노출하는 필드 (keywords/collect 같은 내부 필드는 제외).
_MODEL_FIELDS = ("label", "query", "supported", "note", "collection")


def _load() -> dict:
    """정책 JSON을 읽는다. 파일이 없거나 깨지면 빈 정책을 반환한다(빌더가 죽지 않게)."""
    try:
        data = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _lenses() -> dict:
    lenses = _load().get("lenses")
    return lenses if isinstance(lenses, dict) else {}


def policy_for_model() -> dict:
    """대시보드 preview-model에 주입할 lens_policy dict.

    각 렌즈 → {label, query, supported, note, collection}. collection(연동 상태)을 포함해
    모델이 '어떤 수집 정책의 렌즈인지'를 정직하게 담는다. 정의 순서를 보존한다.
    """
    out = {}
    for key, spec in _lenses().items():
        if not isinstance(spec, dict):
            continue
        entry = {f: spec.get(f) for f in _MODEL_FIELDS}
        entry["label"] = entry.get("label") or key
        entry["query"] = entry.get("query") or ""
        entry["supported"] = bool(entry.get("supported"))
        entry["note"] = entry.get("note") or ""
        entry["collection"] = entry.get("collection") or (
            "query" if entry["supported"] else "unconfigured")
        out[key] = entry
    return out


def keyword_lens_pairs() -> list:
    """빌더 태깅용 (keywords_tuple, lens_key) 목록 — keywords가 있는 렌즈만.

    수집된 기사 제목/카테고리에서 이 키워드가 매칭되면 해당 렌즈로 태깅한다. 부분문자열
    오탐은 정책 JSON 단계에서 차단한다(예: '시행령' 회피 위해 '시행사'만 등록).
    """
    pairs = []
    for key, spec in _lenses().items():
        if not isinstance(spec, dict):
            continue
        words = [w for w in (spec.get("keywords") or []) if isinstance(w, str) and w]
        if words:
            pairs.append((tuple(words), key))
    return pairs


def collection_query_groups() -> list:
    """수집기용 렌즈 쿼리 그룹 — collect 쿼리가 있는 렌즈만 (collector-supported).

    [{name: 'lens:<id>', label, queries: [...]}] 형태. live_collector가 토픽 프로파일과
    동일한 패턴으로 기존 쿼리 그룹에 합친다(중복 쿼리는 수집기가 dedup). 미연동 렌즈는
    collect가 비어 있어 제외된다 — 가짜 수집을 만들지 않는다.
    """
    groups = []
    for key, spec in _lenses().items():
        if not isinstance(spec, dict) or not spec.get("supported"):
            continue
        queries = [q for q in (spec.get("collect") or []) if isinstance(q, str) and q.strip()]
        if not queries:
            continue
        groups.append({
            "name": f"lens:{key}",
            "label": spec.get("label") or key,
            "queries": queries,
        })
    return groups
