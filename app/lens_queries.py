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
import re
from pathlib import Path

# app.config를 import하지 않는다 (DB_PATH 캐시 트랩 회피) — 경로를 직접 계산한다.
_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "lens_queries.json"

# 대시보드 모델 lens_policy에 노출하는 필드 (keywords/collect 같은 내부 필드는 제외).
_MODEL_FIELDS = ("label", "query", "supported", "note", "collection")
_COLLECTION_GROUP_PRIORITY = {
    # D7-J: keep Hyundai Group lens query audit visible even when the global live
    # collector cap is reached by earlier high-volume lenses.
    "hyundai_group": -100,
    "value_chain:ai_hyperscaler_infra": -80,
    "value_chain:ai_datacenter_power_cooling": -79,
    "value_chain:ai_chip_supply_chain": -78,
    "value_chain:ai_semiconductor_cluster": -77,
    "value_chain:developer_trust_finance": -76,
}


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


def _extra_collection_groups() -> dict:
    groups = _load().get("collection_groups")
    return groups if isinstance(groups, dict) else {}


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


def hormuz_relevance_policy() -> dict:
    """호르무즈 렌즈 relevance 정책 (direct / geo_anchors / risk_anchors). 없으면 빈 리스트들."""
    spec = _lenses().get("hormuz")
    rel = spec.get("relevance") if isinstance(spec, dict) else None
    out = {"direct": [], "geo_anchors": [], "risk_anchors": []}
    if isinstance(rel, dict):
        for key in out:
            out[key] = [w for w in (rel.get(key) or []) if isinstance(w, str) and w]
    return out


def hormuz_relevant(text: str) -> bool:
    """호르무즈 렌즈 적격성 판정 — D7-AA relevance guard.

    True ⇔ (직접 호르무즈/Strait of Hormuz 언급) OR (geo 앵커 ∧ risk 앵커 동시 등장).
    단순 LNG·중동·유가·해운 같은 단일 키워드만으로는 False(오태깅 차단). 대소문자 무시.
    호출부는 raw 제목을 넘긴다(category_label의 '중동·해외' 같은 섹션어 오주입 회피).
    """
    low = (text or "").lower()
    if not low:
        return False
    pol = hormuz_relevance_policy()
    if any(d.lower() in low for d in pol["direct"]):
        return True
    geo = any(g.lower() in low for g in pol["geo_anchors"])
    risk = any(r.lower() in low for r in pol["risk_anchors"])
    return geo and risk


def national_ai_infra_relevance_policy() -> dict:
    """국가급 AI 인프라 투자 신호 relevance 정책 (D7-AB).

    actor/event/infra 앵커 + boost_combos + negatives. 정책이 없거나 깨지면 빈 구조를
    반환한다(빌더/수집기가 죽지 않게). boost_combos는 [[그룹,...],...] — combo는 그룹들의
    AND, 각 그룹은 동의어들의 OR로 정규화한다.
    """
    rel = _load().get("national_ai_infra_relevance")
    out = {"actor_anchors": [], "event_anchors": [], "infra_anchors": [],
           "boost_combos": [], "negatives": []}
    if not isinstance(rel, dict):
        return out
    for key in ("actor_anchors", "event_anchors", "infra_anchors", "negatives"):
        out[key] = [w for w in (rel.get(key) or []) if isinstance(w, str) and w]
    combos = []
    for combo in rel.get("boost_combos") or []:
        if not isinstance(combo, list):
            continue
        groups = [[t for t in g if isinstance(t, str) and t]
                  for g in combo if isinstance(g, list)]
        groups = [g for g in groups if g]
        if groups:
            combos.append(groups)
    out["boost_combos"] = combos
    return out


def _anchor_hit(low: str, anchor: str) -> bool:
    """앵커 1개가 (이미 소문자화된) 텍스트에 등장하는가.

    짧은 ASCII 영문 앵커(SK·EPC·LG 등)는 단어 경계를 요구해 'risk'/'task'/'desk' 같은 영문
    단어 내부의 부분문자열 오탐을 막는다(예: 'risk'의 'sk'가 actor 'SK'로 오인되지 않게).
    한글·혼합 앵커('삼성'·'AI 데이터센터')는 부분문자열로 본다.
    """
    a = (anchor or "").lower()
    if not a:
        return False
    if a.isascii() and a.isalpha():
        return re.search(r"(?<![a-z])" + re.escape(a) + r"(?![a-z])", low) is not None
    return a in low


def national_ai_infra_relevant(text: str) -> bool:
    """국가급 AI 인프라 투자 신호 적격성 판정 — D7-AB relevance gate.

    True ⇔ actor 앵커 ∧ event 앵커 ∧ infra 앵커 동시 등장 (그리고 negative 가드 미해당).
    호르무즈 가드와 동일 철학 — 단순 '정부'·'AI'·'데이터센터' 단일 키워드 OR로는 통과하지
    않는다. 특정 인물명·'1000조' 단독으로도 통과하지 않는다(actor/event/infra 3축 구조 필수).
    호출부는 raw 제목을 넘긴다(섹션 라벨 오주입 회피). 대소문자 무시.
    """
    low = (text or "").lower()
    if not low:
        return False
    pol = national_ai_infra_relevance_policy()
    if any(_anchor_hit(low, neg) for neg in pol["negatives"]):
        return False
    actor = any(_anchor_hit(low, a) for a in pol["actor_anchors"])
    event = any(_anchor_hit(low, e) for e in pol["event_anchors"])
    infra = any(_anchor_hit(low, i) for i in pol["infra_anchors"])
    return actor and event and infra


def national_ai_infra_boost(text: str) -> bool:
    """Executive Read 후보 승격 판정 — D7-AB ranking boost.

    True ⇔ national_ai_infra_relevant(text) ∧ boost_combos 중 하나 충족. 각 combo는 그룹들의
    AND이고 각 그룹은 동의어들의 OR다(예: 'AI 데이터센터' ∧ ('전력 인프라'|'전력망'|…)).
    boost는 단독 통과 조건이 아니라 relevant 위에 얹는 승격 신호다 — relevant=False면 항상 False.
    """
    if not national_ai_infra_relevant(text):
        return False
    low = (text or "").lower()
    for combo in national_ai_infra_relevance_policy()["boost_combos"]:
        if all(any(_anchor_hit(low, term) for term in group) for group in combo):
            return True
    return False


def collection_query_groups() -> list:
    """수집기용 렌즈 쿼리 그룹 — collect 쿼리가 있는 렌즈만 (collector-supported).

    [{name: 'lens:<id>', label, queries: [...]}] 형태. live_collector가 토픽 프로파일과
    동일한 패턴으로 기존 쿼리 그룹에 합친다(중복 쿼리는 수집기가 dedup). 미연동 렌즈는
    collect가 비어 있어 제외된다 — 가짜 수집을 만들지 않는다.
    """
    groups = []
    lens_items = list(enumerate(_lenses().items()))
    lens_items.sort(key=lambda item: (_COLLECTION_GROUP_PRIORITY.get(item[1][0], 0),
                                      item[0]))
    for _idx, (key, spec) in lens_items:
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
    extra_items = list(enumerate(_extra_collection_groups().items()))

    def _extra_priority(item):
        idx, (key, spec) = item
        name = spec.get("name") if isinstance(spec, dict) else None
        return (_COLLECTION_GROUP_PRIORITY.get(name or f"value_chain:{key}", 0), idx)

    extra_items.sort(key=_extra_priority)
    for _idx, (key, spec) in extra_items:
        if not isinstance(spec, dict) or spec.get("enabled") is False:
            continue
        queries = [q for q in (spec.get("collect") or []) if isinstance(q, str) and q.strip()]
        if not queries:
            continue
        name = spec.get("name") or f"value_chain:{key}"
        groups.append({
            "name": name,
            "label": spec.get("label") or key,
            "queries": queries,
        })
    return groups
