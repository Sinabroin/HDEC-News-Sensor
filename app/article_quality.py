"""Article Quality 도메인 — 임원 관련성 품질 게이트 (P0-C1.11).

공개 RSS(예: Google News)는 AI 편중 쿼리에서 주식 테마/급등/증권 리서치성 기사를
다수 끌어온다. 이 모듈은 (source, title)만 보고 결정적으로:
  1) stock-hype(테마주·머니무브·목표가·증권가 리서치 등) 여부를 판정하고,
  2) 현대건설 직접 AI-계약/제재 기사를 판정한다.

경계 원칙 (source_quality·radar와 동일한 '순수 파생' 원칙):
- 순수 함수만 제공한다 — DB·네트워크·발송·점수 계산을 하지 않는다.
- 정책 키워드는 data/article_quality_rules.json 한 곳이 단일 소스다.
- 다른 app 도메인을 import하지 않는다 (leaf 모듈; scoring/radar/briefing이 소비한다).
- stock-hype/관련성은 "사실 보증"이 아니라 랭킹/필터 가드레일이다 (가짜 신뢰도 금지).

오탐 방지 설계:
- 제목·출처만 본다 (500자 snippet은 보지 않는다 — '수혜/급등'이 누적돼 정상 기사가
  잘못 강등되는 것을 막는다).
- bare '주가'는 '발주가/수주가'(정상 건설 기사)에 substring으로 걸리므로 쓰지 않는다.
- strong 지표는 단독으로, weak 지표는 2개 이상일 때만 stock-hype로 본다 — 광범위
  산업 기사가 시장 용어 1개 때문에 강등되지 않게 한다 (신뢰 매체 보호).
- 현대건설 직접 언급(제목)이 있으면 stock-hype 강등에서 제외한다 — 현대건설 기사는
  Phase 4 보호 로직이 따로 다룬다.

소비처:
- scoring: stock_hype면 STOCKHYPE_SCORE_CAP로 캡 + 제외 등급; hdec_ai_contract/
  hdec_enforcement면 등급 floor(최소 '추적 필요').
- radar: stock_hype면 레이더 섹션에서 제외(OTHER); hdec_ai_contract→ai /
  hdec_enforcement→risk_regulation.
"""

import json

from app import config

_RULES_PATH = config.DATA_DIR / "article_quality_rules.json"

# JSON 로드 실패 시에도 안전하게 동작하도록 최소 기본값 (가짜 강등 금지 — 비면 비활성).
_DEFAULT_RULES = {
    "stockhype_score_cap": 2.4,
    "stockhype_strong_title_patterns": [],
    "stockhype_weak_title_patterns": [],
    "equity_research_source_patterns": [],
    "hdec_names": ["현대건설"],
    "ai_tokens": ["ai", "인공지능"],
    "hdec_contract_patterns": [],
    "hdec_enforcement_patterns": [],
    "hdec_enforcement_severe_patterns": [],
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
            pass  # 파일 없거나 깨지면 안전 기본값으로 동작 (게이트 비활성 경향)
        _rules_cache = merged
    return _rules_cache


# 공개 상수 — 다른 도메인이 import해 단일 소스로 쓴다.
STOCKHYPE_SCORE_CAP = float(_load_rules().get("stockhype_score_cap") or 2.4)


def _first(haystack: str, patterns) -> str | None:
    for pat in patterns or []:
        p = (pat or "").strip().lower()
        if p and p in haystack:
            return pat
    return None


def _all_hits(haystack: str, patterns) -> list[str]:
    return [pat for pat in patterns or []
            if (pat or "").strip() and (pat or "").strip().lower() in haystack]


def is_hdec_direct(title: str) -> bool:
    """제목이 현대건설을 직접 다루는지 (현대건설이 기사 주체)."""
    rules = _load_rules()
    t = (title or "").lower()
    return _first(t, rules.get("hdec_names")) is not None


def assess(source: str = "", title: str = "", snippet: str = "") -> dict:
    """(source, title)을 임원 품질 신호로 분류한다 (순수·결정적).

    snippet은 받지만 stock-hype 판정에는 쓰지 않는다 (제목·출처 위주 — 오탐 방지).
    반환:
        stock_hype: bool — 주식 테마/급등/증권 리서치성 (현대건설 직접 제외)
        stock_hype_reason: str
        hdec_direct: bool — 제목에 현대건설 직접 언급
        hdec_ai_contract: bool — 현대건설 + AI + 계약/하도급/협력사/상생펀드 등
        hdec_enforcement: bool — 현대건설 + 벌점/제재/영업정지/입찰제한 등
        enforcement_severe: bool — 영업정지·입찰제한·중대재해 등 고심각 제재
    """
    rules = _load_rules()
    title_low = (title or "").lower()
    src_low = (source or "").lower()

    hdec_direct = _first(title_low, rules.get("hdec_names")) is not None

    # ---- stock-hype / equity-research ----
    strong = _first(title_low, rules.get("stockhype_strong_title_patterns"))
    weak_hits = _all_hits(title_low, rules.get("stockhype_weak_title_patterns"))
    equity_src = _first(src_low, rules.get("equity_research_source_patterns"))
    hype_hit = bool(strong) or len(weak_hits) >= 2 or bool(equity_src)
    # 현대건설 직접 기사는 stock-hype 강등에서 제외 — Phase 4 보호가 따로 다룬다.
    stock_hype = bool(hype_hit and not hdec_direct)

    if stock_hype:
        if equity_src:
            reason = f"증권 리서치성 출처('{equity_src}') — 임원 핵심 섹션에서 강등"
        elif strong:
            reason = f"주식 테마/급등성 제목('{strong}') — 임원 핵심 섹션에서 강등"
        else:
            reason = (f"주식 시장 표현 다수({'·'.join(weak_hits[:3])}) — "
                      "임원 핵심 섹션에서 강등")
    else:
        reason = ""

    # ---- 현대건설 직접 AI-계약 / 제재 ----
    hdec_ai_contract = False
    hdec_enforcement = False
    enforcement_severe = False
    if hdec_direct:
        has_ai = _first(title_low, rules.get("ai_tokens")) is not None
        has_contract = _first(title_low, rules.get("hdec_contract_patterns")) is not None
        # 핵심이 AI 계약검토/컴플라이언스면 ai로 본다 (불공정도 AI 차단 맥락이면 포함).
        hdec_ai_contract = bool(has_ai and has_contract)
        enf = _first(title_low, rules.get("hdec_enforcement_patterns"))
        hdec_enforcement = enf is not None
        if hdec_enforcement:
            enforcement_severe = _first(
                title_low, rules.get("hdec_enforcement_severe_patterns")) is not None

    return {
        "stock_hype": stock_hype,
        "stock_hype_reason": reason,
        "hdec_direct": hdec_direct,
        "hdec_ai_contract": hdec_ai_contract,
        "hdec_enforcement": hdec_enforcement,
        "enforcement_severe": enforcement_severe,
    }
