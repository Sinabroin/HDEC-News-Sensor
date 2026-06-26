#!/usr/bin/env python3
"""D6-A/B + D7-B — Executive summary dashboard static export (real article data).

`/dashboard-preview` is served from `templates/dashboard_preview.html` (demo preview).
This builder reuses that checked-in template *shell* but injects the REAL daily-brief
article data into the preview-model island so `docs/daily/dashboard-latest.html` shows
the same collected articles as `docs/daily/latest.html` — both derive from the one
shared brief object (`build_executive_brief.build_brief_via_mock_pipeline`).

Honesty contract is preserved:
- This file itself reads no secrets, opens no socket directly, and touches no env. The
  shared brief pipeline owns collection — mock mode uses a temp DB + mock articles, and
  `NEWS_MODE=live` (workflow) collects public RSS, exactly like `build_static_report.py`.
- Only the news/AI article rows + the featured hero become real. The market snapshot block
  stays demo (지연/대용/보고/미연동) EXCEPT the per-period history of supported headline items
  (USD/KRW·JPY/KRW·WTI·Brent·Copper·US 5Y/10Y), which by default is a **deterministic demo
  fixture** (distinct 1주/1개월/3개월/1년 windows) and, only with `--market-mode live`, is
  replaced by **public delayed quotes** fetched by the `app/market_history` leaf (the leaf
  owns the network; this builder reads no process env and imports no network module). US 2Y
  / KR 10Y have no free source → stay 히스토리 미연동(non-clickable). AIS / early-signal /
  theme blocks stay demo and keep their labels.
- In live mode the news section is labelled '자동 수집 기사' (real); in mock it stays
  '데모 데이터'. Market preview stays demo in both. The full daily report remains
  `docs/daily/latest.html` and is never replaced by this export.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
for _p in (ROOT, SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from build_executive_brief import build_brief_via_mock_pipeline  # noqa: E402
# 중앙 렌즈 쿼리 정책(단일 소스) — leaf는 app.config/DB/네트워크를 건드리지 않아 bootstrap
# 전에 import해도 안전하다(config의 DB_PATH 캐시 트랩 회피). 정책=대시보드+수집기 공유.
from app import ai_value_chain, lens_queries  # noqa: E402
# 시장 기간 히스토리 provider(leaf) — 네트워크는 이 leaf만 소유한다(빌더는 소켓/urllib/env를
# 직접 들이지 않는다). mock(기본)은 결정적 데모 픽스처, --market-mode live일 때만 공개 시세 실측.
from app import market_history  # noqa: E402
# 사이트 워치리스트(leaf, P0-D7-M) — 사내 제공 현장/프로젝트명을 제목에서 매칭해 scope/business
# 렌즈를 단다. 공개 빌드(비공개 워치리스트 경로 미설정)는 공개 샘플만 보고 비공개 목록을 노출하지
# 않는다 — 제목이 이미 언급한 프로젝트만 태깅하며, 매칭 없는 항목은 모델/HTML에 들어가지 않는다.
from app import site_watchlist  # noqa: E402

SOURCE_TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
DEFAULT_OUTPUT = "docs/daily/dashboard-latest.html"
EXPORT_TITLE = "HDEC Executive Radar — 요약 대시보드"
EXPORT_MARKER = "dashboard-export:summary"
_KST = timezone(timedelta(hours=9))

# 템플릿 nav의 data-filter와 1:1 — 생성된 행의 lens 키는 반드시 이 집합 안에 있어야 한다
# (유효하지 않은 키는 어떤 nav 필터에도 걸리지 않아 죽은 태그가 된다).
VALID_LENS = {
    "now", "new", "ai", "civil_infrastructure", "building_housing", "plant",
    "new_energy", "development_business", "global_business", "safety_quality",
    "hyundai_group", "competitor_contractors", "trust_companies", "developers",
    "oil_energy", "hormuz", "domestic_site", "overseas_site", "overseas_branch",
    "overseas_subsidiary",
}
# 상태 렌즈(노출 상태일 뿐 콘텐츠 분류가 아님) — 대시보드 제외/콘텐츠 판정 시 구분한다.
_STATE_LENS = {"now", "new"}
# 주거/건축 보강 키워드(D7-Q) — '아파트' 등 주거형은 공유 키워드 풀(lens_queries)에 없어
# building_housing 태그가 누락된다. 라이브 수집 쿼리는 그대로 두고, 대시보드 표시 단계에서만
# 보수적으로 보강한다(가짜가 아니라 제목의 실제 주거 키워드 기반 · 부분문자열 오탐 방지로
# 명확한 주거형만 둔다 — 예: '단지'는 산업단지 오탐이 있어 '주거단지/주택단지'로 한정).
_RESIDENTIAL_LENS_TERMS = ("아파트", "오피스텔", "주상복합", "주거단지", "주택단지")

NEWS_ROW_CAP = 20
LENS_BANK_CAP = 10
AI_BANK_CAP = 8
# D7-U: AI 뱅크 내에서 하이퍼스케일러/AI 칩 밸류체인 신호용으로 예약하는 슬롯 수. 국내 건설
# tier-2 신호가 US 빅테크 커버리지를 밀어내지 못하게 한다(가용 시에만 채움 · 가짜 생성 없음).
AI_HYPER_RESERVE = 3
NOW_BANK_CAP = 6

# lens 키 → 한국어 라벨 (featured 칩용). nav 라벨과 일치.
_LENS_LABEL = {
    "now": "즉시 확인", "new": "신규 이슈", "ai": "AI 신호",
    "civil_infrastructure": "토목", "building_housing": "건축주택", "plant": "플랜트",
    "new_energy": "New Energy", "development_business": "개발사업", "global_business": "글로벌",
    "safety_quality": "안전·품질", "hyundai_group": "현대 그룹사",
    "competitor_contractors": "경쟁 시공사", "trust_companies": "신탁사",
    "developers": "시행사·디벨로퍼", "oil_energy": "유가·에너지", "hormuz": "호르무즈",
    "domestic_site": "국내현장", "overseas_site": "해외현장", "overseas_branch": "해외지사",
    "overseas_subsidiary": "해외법인",
}

# 머신 category → 기본 렌즈 (briefing이 분류한 결정 카테고리)
_CATEGORY_LENS = {
    "dc_power": ["ai", "new_energy", "plant"],
    "mideast_overseas": ["global_business", "overseas_site", "plant"],
    "safety": ["safety_quality"],
    "hdec": [],
}
# radar_section → 렌즈
_SECTION_LENS = {
    "ai": ["ai"],
    "business_overseas": ["global_business", "overseas_site"],
    "risk_regulation": ["safety_quality"],
    "competitor_supply": ["competitor_contractors"],
    "macro": ["oil_energy"],
    "hdec_direct": [],
}
# category_label/제목 키워드 → 추가 렌즈 (보수적 보강 — 매핑 불완전 시 fallback).
# 단일 소스는 data/lens_queries.json(app.lens_queries)이며, 아래는 정책 로드 실패 시의
# fallback이다 — 둘 다 동일 계약(부분문자열 오탐 금지)을 따른다.
# 함정: 부분문자열 오탐 금지 — '시행'은 '시행령/시행규칙'에 걸리므로 '시행사'만 쓴다.
# 그룹사/신탁/시행 렌즈는 수집된 기사 본문/제목에서만 매칭한다(없으면 0건=정직 빈 상태).
_KEYWORD_LENS_FALLBACK = [
    (("토목", "철도", "광역철도", "도로", "교량", "터널", "SOC"), "civil_infrastructure"),
    (("건축", "주택", "정비", "분양", "공사비", "재건축", "재개발"), "building_housing"),
    (("플랜트", "LNG", "원전", "EPC", "정유", "석유화학", "발전소", "SMR"), "plant"),
    (("SMR", "수소", "전력망", "데이터센터", "신재생", "태양광", "풍력", "전력 인프라"), "new_energy"),
    (("중동", "해외", "글로벌", "사우디", "UAE", "카타르", "네옴", "체코", "유럽", "수출"), "global_business"),
    (("유가", "국제유가", "원유", "WTI", "브렌트", "두바이유", "LNG", "연료", "정제유",
      "정제마진", "가스", "호르무즈", "에너지"), "oil_energy"),
    (("안전", "중대재해", "특별감독", "규제", "벌점", "산업안전"), "safety_quality"),
    (("경쟁", "수주 경쟁"), "competitor_contractors"),
    # 범현대 그룹사 — 현대건설(E&C)·현대엔지니어링은 제외(HDEC 직접 렌즈 오염 방지).
    (("현대차", "현대자동차", "현대모비스", "현대글로비스", "현대제철", "현대중공업",
      "HD현대", "현대로템", "현대오토에버", "현대위아", "현대트랜시스", "현대건설기계"),
     "hyundai_group"),
    (("신탁", "리츠", "REITs", "책임준공", "토지신탁", "자산신탁"), "trust_companies"),
    (("시행사", "디벨로퍼", "부동산개발", "PFV"), "developers"),
]
# 중앙 정책(data/lens_queries.json)에서 키워드→렌즈 매핑을 로드 — 대시보드 태깅과
# 라이브 수집 쿼리가 같은 소스를 공유한다. 로드 실패 시에만 위 fallback을 쓴다.
_KEYWORD_LENS = lens_queries.keyword_lens_pairs() or _KEYWORD_LENS_FALLBACK

# opportunity_or_risk → (cat 라벨, catColor, tag class)
_KIND = {
    "기회": ("기회", "#3C7A4E", "green"),
    "리스크": ("리스크", "#B85049", "red"),
    "관찰": ("관찰", "#3F6FA8", "sky"),
}
# score_band → (tag 라벨, tag class)
_BAND_TAG = {
    "즉시 확인": ("즉시", "red"),
    "검토 필요": ("검토 필요", "amber"),
    "추적 필요": ("참고", "sky"),
}
# score_components 색 팔레트 (featured 신호 지수)
_COMP_PALETTE = ["#3F6FA8", "#3C7A4E", "#9AAAC1", "#9C7232", "#3F6FA8", "#3C7A4E"]


def _is_http(url) -> bool:
    return bool(url) and str(url).startswith(("http://", "https://"))


def _key(sig) -> str:
    if not sig:
        return ""
    return str(sig.get("article_id") or sig.get("url") or sig.get("title") or "")


def _fmt_time(iso) -> str:
    """published_at → KST 'MM-DD HH:MM' (결정적 절대시각, now() 의존 없음)."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return str(iso)[:16]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_KST).strftime("%m-%d %H:%M")


def _fmt_kst_full(iso) -> str:
    """ISO datetime → KST 'YYYY-MM-DD HH:mm' (헤더 생성 시각·최신 기사 시각용).

    brief.generated_at는 이미 KST(+09:00)지만, UTC 등 다른 tz가 와도 KST로 환산한다.
    파싱 실패 시 빈 문자열 — 가짜 날짜를 만들지 않는다.
    """
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M")


def _latest_article_kst(rows) -> str:
    """표시 행들의 실제 published_at 중 가장 최신을 KST 'YYYY-MM-DD HH:mm'로.

    rows는 _row_from_signal 산출물(provenance.published_at 보유). 값이 없으면 빈 문자열
    (가짜 최신 시각 생성 금지). 헤더의 '최신 기사' 보조 표기에 쓰인다.
    """
    best = None
    for row in rows or []:
        pub = (row.get("provenance") or {}).get("published_at") if isinstance(row, dict) else None
        if not pub:
            continue
        try:
            dt = datetime.fromisoformat(str(pub))
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if best is None or dt > best:
            best = dt
    return best.astimezone(_KST).strftime("%Y-%m-%d %H:%M") if best else ""


def _score_value(sig) -> float:
    try:
        return float(sig.get("final_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _published_rank(sig) -> float:
    raw = sig.get("published_at")
    if not raw:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _is_strict_immediate(sig) -> bool:
    return sig.get("alert_grade") == "즉시 알림 후보" or sig.get("score_band") == "즉시 확인"


def _is_hdec_direct(sig) -> bool:
    return sig.get("executive_section") == "hdec_direct" or sig.get("radar_section") == "hdec_direct"


def _is_risk_signal(sig) -> bool:
    return (
        sig.get("opportunity_or_risk") == "리스크"
        or sig.get("radar_section") == "risk_regulation"
        or sig.get("category") == "safety"
    )


def _signal_rank_key(sig, immediate_basis: str = "") -> tuple:
    """Score-first ordering for visible rows and lens banks.

    Tie-breaks keep actual instant alerts, HDEC direct, risk/safety, and newer rows ahead
    without letting a lower-scored featured/reference row jump the queue.
    """
    basis_rank = 1 if immediate_basis == "strict_instant" or _is_strict_immediate(sig) else 0
    return (
        -_score_value(sig),
        -basis_rank,
        -int(_is_hdec_direct(sig)),
        -int(_is_risk_signal(sig)),
        -_published_rank(sig),
        sig.get("title") or "",
    )


# ── 출처 표시 보강 (D7-C) ──────────────────────────────────────────────────
# live RSS가 출처를 'v.daum.net' 같은 집계 호스트로 주거나 비워서 briefing이 일반
# '원문 경유'로 정규화하면, 임원 화면에 매체를 알 수 없게 된다. 그럴 때만 URL 도메인에서
# 매체명을 끌어오고, 도메인도 매핑에 없으면 등록 도메인 자체를 보여준다(일반 라벨보다 구체적).
# 가짜 매체명을 만들지 않는다 — 도메인은 사실이고, 식별된 매체명/집계 라벨은 그대로 둔다.
_VIA_GENERIC = "원문 경유"
_DOMAIN_RE = re.compile(r"^https?://([^/]+)", re.I)
_DOMAIN_PUBLISHER = {
    "yna.co.kr": "연합뉴스", "yonhapnews": "연합뉴스", "yonhapnewstv": "연합뉴스TV",
    "chosunbiz.com": "조선비즈", "biz.chosun.com": "조선비즈", "chosun.com": "조선일보",
    "joongang.co.kr": "중앙일보", "joins.com": "중앙일보", "donga.com": "동아일보",
    "hani.co.kr": "한겨레", "khan.co.kr": "경향신문", "hankyung.com": "한국경제",
    "mk.co.kr": "매일경제", "sedaily.com": "서울경제", "edaily.co.kr": "이데일리",
    "mt.co.kr": "머니투데이", "fnnews.com": "파이낸셜뉴스", "etnews.com": "전자신문",
    "zdnet.co.kr": "ZDNet Korea", "dt.co.kr": "디지털타임스", "newsis.com": "뉴시스",
    "news1.kr": "뉴스1", "asiae.co.kr": "아시아경제", "heraldcorp.com": "헤럴드경제",
    "seoul.co.kr": "서울신문", "kmib.co.kr": "국민일보", "munhwa.com": "문화일보",
    "hankookilbo.com": "한국일보", "segye.com": "세계일보", "kookje.co.kr": "국제신문",
    "imaeil.com": "매일신문", "businesspost.co.kr": "비즈니스포스트", "newspim.com": "뉴스핌",
    "ajunews.com": "아주경제", "electimes.com": "전기신문", "ekn.kr": "에너지경제",
    "cnews.co.kr": "건설경제", "kookbang.dema.mil.kr": "국방일보", "yna.kr": "연합뉴스",
}


def _domain_of(url) -> str:
    """URL에서 등록 도메인(www. 제거, 포트 제거)을 추출 — urllib 미사용(소스 스캔 계약)."""
    if not _is_http(url):
        return ""
    m = _DOMAIN_RE.match(str(url))
    if not m:
        return ""
    host = m.group(1).lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def _publisher_from_url(url) -> str:
    host = _domain_of(url)
    if not host:
        return ""
    for key, name in _DOMAIN_PUBLISHER.items():
        if key in host:
            return name
    return host  # 미식별 도메인은 도메인 자체 노출 (일반 '원문 경유'보다 구체적)


def _better_source(sig) -> str:
    """임원 표시용 출처 — 일반 '원문 경유'를 피하고 도메인 유래 매체명으로 보강 (D7-C).

    식별된 매체명(연합뉴스 등)이나 집계 라벨('Daum 경유' 등)은 그대로 둔다. 일반 '원문 경유'
    (미식별 호스트)일 때만 URL 도메인에서 매체명/도메인을 끌어온다. 가짜 매체명을 만들지 않는다.
    """
    disp = (sig.get("display_source") or "").strip()
    if disp and disp != _VIA_GENERIC:
        return disp
    pub = _publisher_from_url(sig.get("url"))
    if pub:
        return pub
    raw = (sig.get("source") or "").strip()
    if raw and raw != _VIA_GENERIC:
        return raw
    return disp or raw or "출처 미상"


# ── 근접 중복 제거 (D7-C) ──────────────────────────────────────────────────
_NEAR_DUP_THRESHOLD = 0.7


def _norm_tokens(title) -> set:
    return set(re.findall(r"[가-힣A-Za-z0-9]+", str(title or "").lower()))


def _title_overlap(a, b) -> float:
    """두 제목의 토큰 겹침 비율(작은 쪽 기준) — 0~1."""
    ta, tb = _norm_tokens(a), _norm_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _drop_near_dups(signals) -> list:
    """제목 토큰 겹침이 임계 이상인 근접 중복 제거(먼저 온 신호=상위 우선순위 유지).

    호출 측이 점수/중요도 순으로 정렬해 넘기면 더 중요한 기사가 살아남는다. 정확 중복은
    _dedup_signals(_key)가 이미 처리하므로, 여기서는 URL/제목이 미세하게 다른 재전송본을 본다.
    """
    kept = []
    for s in signals or []:
        t = s.get("title") or ""
        if any(_title_overlap(t, k.get("title") or "") >= _NEAR_DUP_THRESHOLD for k in kept):
            continue
        kept.append(s)
    return kept


# ── 약한 현대건설-인접 오탐 가드 (D7-C) ─────────────────────────────────────
# 'HD현대·현대차' 그룹사와 '현대건설기계'(HD현대 굴착기 자회사)는 사명만 비슷할 뿐
# 현대건설(E&C)이 아니다. 제목에 현대건설 E&C 직접 사업이 함께 적시되지 않으면 HDEC 직접
# hero 후보에서 제외한다(요약 대시보드의 대표 신호 보호). 함정: '현대건설기계'는 부분문자열로
# '현대건설'을 포함하므로, 진짜 현대건설 판정은 negative lookahead로 '기계'를 배제해야 한다.
_HDEC_FP_TOKENS = ("HD현대", "HD 현대", "현대건설기계", "HD건설기계", "HD 건설기계",
                   "현대차", "현대자동차", "현대글로비스", "현대모비스", "현대중공업",
                   "HD한국조선", "현대백화점", "현대해상", "현대카드", "현대오토에버",
                   "현대트랜시스", "현대로템", "현대제철", "현대위아", "현대두산")
# 진짜 현대건설(E&C) 직접 언급 — '현대건설기계'는 제외(부정형 lookahead).
_HDEC_REAL_RE = re.compile(r"현대건설(?!기계)|현대 건설|현대엔지니어링|현대ENG|힐스테이트")


def _is_weak_hdec_fp(sig) -> bool:
    """현대건설과 무관한 'HD현대·현대차·현대건설기계' 류 사명 오탐인가 (featured hero 제외용).

    FP 사명 토큰이 있어도, 같은 제목에 현대건설(E&C) 직접 언급이 함께 있으면 진짜 신호로 본다
    (혼합 기사). '현대건설기계'만 있는 경우는 lookahead가 '현대건설' 매칭을 막아 오탐으로 판정.
    """
    title = sig.get("title") or ""
    if not any(tok in title for tok in _HDEC_FP_TOKENS):
        return False
    return not _HDEC_REAL_RE.search(title)


# 해외 마커(글로벌/해외현장 렌즈 정밀화용, D7-G Part C) — 명시적 해외 지시어가 없는 행은
# 섹션/카테고리 분류만으로 '글로벌'에 섞이지 않게 한다(국내 일반 기업뉴스 오염 방지).
_OVERSEAS_MARKERS = ("중동", "해외", "사우디", "UAE", "카타르", "네옴", "오만", "이라크",
                     "두바이", "아부다비", "체코", "유럽", "폴란드", "인도네시아", "베트남",
                     "글로벌", "수출", "팀코리아", "싱가포", "美", "미국", "일본", "인도",
                     "국제유가", "달러", "EPC", "OpenAI", "오픈AI", "Anthropic", "앤트로픽",
                     "Claude", "클로드", "Google", "구글", "Microsoft", "마이크로소프트",
                     "Amazon", "AWS", "Meta", "메타", "Oracle", "오라클", "xAI",
                     "Broadcom", "브로드컴", "NVIDIA", "엔비디아", "TSMC")


def _has_overseas_marker(text: str) -> bool:
    return any(m in (text or "") for m in _OVERSEAS_MARKERS)


_HYUNDAI_GROUP_TERMS = (
    "현대엔지니어링", "현대ENG", "현대로템", "현대제철", "HD현대", "HD 현대",
    "HD현대일렉트릭", "현대일렉트릭", "현대글로비스", "현대모비스", "현대차그룹",
    "현대자동차그룹", "현대차", "현대자동차",
)
_HYUNDAI_GROUP_CONTEXT = (
    "건설", "인프라", "플랜트", "EPC", "수주", "철도", "도시철도", "고속철",
    "철강", "철근", "H형강", "전력", "전력기기", "변압기", "데이터센터", "물류",
    "투자", "에너지", "원전", "SMR", "공급망", "설비", "특허", "기술", "O&M",
)


def _is_hyundai_group_relevant(text: str) -> bool:
    """현대 그룹사 렌즈는 사명과 건설·인프라 맥락이 같이 있을 때만 매칭한다."""
    blob = text or ""
    return any(t in blob for t in _HYUNDAI_GROUP_TERMS) and any(
        c in blob for c in _HYUNDAI_GROUP_CONTEXT
    )


# 해외지사 렌즈(D7-L) — 해외 영업·지역본부·현지 사무소·해외 네트워크 등 해외 '조직/거점'
# 신호만 분류한다. 단순 해외 프로젝트/수주(해외현장·글로벌 렌즈 소관)는 조직/사무소/지사
# 마커가 없으면 분류하지 않는다. raw 제목만 본다(injected category_label로 새지 않게).
# STRONG 토큰은 그 자체로 해외 조직 신호라 해외 마커가 불필요하고, GENERIC 토큰(지사/지점/
# 사무소)은 부분문자열 오탐(복지사/은행 지점/회계 사무소)을 막기 위해 해외 마커가 함께일 때만.
_OVERSEAS_ORG_STRONG = (
    "해외지사", "해외 지사", "중동지역본부", "지역본부", "해외영업", "해외 영업",
    "해외 네트워크", "해외사업본부", "해외사업 조직", "현지 사무소", "현지사무소",
    "해외 사무소", "해외건설협회", "현지지사",
    "overseas office", "regional office", "country office", "local office", "branch office",
)
_OVERSEAS_ORG_GENERIC = ("지사", "지점", "사무소", "주재원")


def _is_overseas_branch_relevant(title: str) -> bool:
    """해외 조직/거점(지사·지역본부·해외영업·현지 사무소) 신호인가 — raw 제목 기준."""
    t = title or ""
    if any(w in t for w in _OVERSEAS_ORG_STRONG):
        return True
    return any(w in t for w in _OVERSEAS_ORG_GENERIC) and _has_overseas_marker(t)


# 해외법인 렌즈(D7-L) — 실제 해외 법인·현지법인·현지 자회사 등 '법인격' 신호만 분류한다.
# generic 해외 프로젝트/수주(EPC/수주 단어만)로는 분류하지 않는다 — 법인/자회사 마커가
# 있어야 한다. raw 제목만 본다. 부분문자열 오탐(법인세/법인카드)을 피해 '해외법인/현지법인'
# 복합어 또는 '자회사/법인 설립'(+해외 마커)만 인정한다(bare '법인'은 쓰지 않는다).
_OVERSEAS_ENTITY_STRONG = (
    "해외법인", "해외 법인", "현지법인", "현지 법인", "해외 자회사", "현지 자회사",
    "해외법인장", "현지법인장", "법인장", "현지화 법인",
    "overseas subsidiary", "local entity", "local corporation", "local subsidiary",
)
_OVERSEAS_ENTITY_GENERIC = ("자회사", "법인 설립", "법인설립", "subsidiary")


def _is_overseas_subsidiary_relevant(title: str) -> bool:
    """실제 해외 법인격(해외법인·현지법인·현지 자회사·법인장) 신호인가 — raw 제목 기준."""
    t = title or ""
    if any(w in t for w in _OVERSEAS_ENTITY_STRONG):
        return True
    return any(w in t for w in _OVERSEAS_ENTITY_GENERIC) and _has_overseas_marker(t)


def _lens_for(sig) -> list:
    keys = set(_CATEGORY_LENS.get(sig.get("category"), []))
    keys.update(_SECTION_LENS.get(sig.get("radar_section"), []))
    raw_title = sig.get("title") or ""
    raw_source = sig.get("source") or ""
    raw_snippet = sig.get("snippet") or ""
    value_chain = ai_value_chain.classify_ai_value_chain(raw_title, raw_source, raw_snippet)
    keys.update(ai_value_chain.recommended_lenses(raw_title, raw_source, raw_snippet))
    text = f"{raw_title} {sig.get('category_label') or ''}"
    for words, lens in _KEYWORD_LENS:
        if any(w in text for w in words):
            keys.add(lens)
    if _is_hyundai_group_relevant(text):
        keys.add("hyundai_group")
    else:
        keys.discard("hyundai_group")
    # 해외 마커가 없으면 글로벌/해외현장 렌즈에서 제외 — 국내 기업뉴스가 섹션 분류만으로
    # '글로벌'에 섞이지 않게 한다(가짜가 아니라 정밀화 · 다른 렌즈 태그는 유지).
    # 함정: category_label은 섹션 설명("중동·해외 수주 환경")이라 항상 해외어를 포함한다 →
    # 기사 고유성 판정은 raw 제목만 본다(섹션 라벨로 도메스틱 기사가 글로벌에 새지 않게).
    if not _has_overseas_marker(raw_title):
        keys.discard("global_business")
        keys.discard("overseas_site")
    if value_chain.get("ai_value_chain_layer") == ai_value_chain.LAYER_GENERIC_AI:
        keys.discard("ai")
        keys.discard("global_business")
        keys.discard("overseas_site")
    # 해외지사/해외법인 렌즈(D7-L) — 조직/법인격 마커가 raw 제목에 있을 때만 분류한다. broad
    # 키워드(EPC/수주/법인)로 들어온 generic 해외 프로젝트 오탐을 제거하고, 실제 해외 조직/
    # 법인 신호는 키워드 매핑 누락과 무관하게 정확히 태깅한다(hyundai_group과 동일 패턴).
    if _is_overseas_branch_relevant(raw_title):
        keys.add("overseas_branch")
    else:
        keys.discard("overseas_branch")
    if _is_overseas_subsidiary_relevant(raw_title):
        keys.add("overseas_subsidiary")
    else:
        keys.discard("overseas_subsidiary")
    # AI 렌즈(D7-L) — 섹션/카테고리(injected)로 'ai'가 붙어도 raw 제목에 직접 AI/데이터센터
    # 인프라 근거가 없으면 제외한다(안전/규제 기사의 category 오염 차단). 근거가 있을 때만 유지.
    if ("ai" in keys
            and not _has_ai_evidence(raw_title, raw_source, raw_snippet)
            and value_chain.get("ai_value_chain_layer") != ai_value_chain.LAYER_CUSTOM_CHIP):
        keys.discard("ai")
    # 사이트 워치리스트(D7-M) — raw 제목이 워치리스트 프로젝트명/별칭을 직접 언급하면 그 항목의
    # scope(국내/해외현장·해외지사·해외법인)와 business 렌즈(토목/건축주택/플랜트/New Energy)를
    # 더한다. 운영자 워치리스트가 권위 있는 분류라 위의 generic 게이트 discard 이후에 적용한다.
    for match in site_watchlist.classify_site_lenses(raw_title):
        scope = match.get("scope")
        biz = match.get("business_lens")
        if scope in VALID_LENS:
            keys.add(scope)
        if biz and biz in VALID_LENS:
            keys.add(biz)
    # 주거/건축 보수적 폴백(D7-Q) — 다른 정밀 콘텐츠 렌즈가 하나도 없을 때만, 제목의 주거형
    # 키워드('아파트' 등 공유 풀에 없는 항목)로 building_housing을 보강한다(task 폴백 규칙:
    # "정밀 렌즈가 없으면 가장 보수적인 건설 렌즈"). 이미 plant/safety 등 정밀 렌즈가 있으면
    # 보강하지 않는다 — 예: "아파트·원전…건설株"는 원전→plant라 폴백 비적용(과태깅·중복 방지).
    # 이 폴백이 "…아파트에 'AI 주차로봇' 도입"처럼 렌즈가 비는 주거 기사를 건져 빈 렌즈를 막는다.
    if not (keys - _STATE_LENS) and any(w in raw_title for w in _RESIDENTIAL_LENS_TERMS):
        keys.add("building_housing")
    # 건설-AI 보강(D7-Q) — 제목에 직접 AI 근거가 있고 콘텐츠 렌즈가 이미 있으면(건설/주거 등)
    # 'ai'를 더한다. 순수 비건설 AI 노이즈는 콘텐츠 렌즈가 없어 태깅되지 않는다(과태깅 방지).
    if (keys - _STATE_LENS) and _has_ai_evidence(raw_title, raw_source, raw_snippet):
        keys.add("ai")
    if sig.get("alert_grade") == "즉시 알림 후보" or sig.get("score_band") == "즉시 확인":
        keys.add("now")
    return sorted(keys & VALID_LENS)


def _has_dashboard_lens(sig) -> bool:
    """대시보드 행으로 노출할 유효 콘텐츠 렌즈가 있는가(D7-Q · 단일 제외 판정점).

    의미 기반 매핑(_lens_for) 후에도 비면 임원/건설 관련성이 없는 기사(소비재 신상품 등)로
    보고 대시보드 행에서 제외한다 — 빈 렌즈 행을 가짜로 라벨링하지 않기 위함. 'new'(top_new_
    issues 분류)는 정당한 상태 태그라 호출부에서 별도로 보존한다(여기선 콘텐츠 렌즈만 본다).
    """
    return bool(_lens_for(sig))


def _row_from_signal(sig, extra_lens=()) -> dict:
    kind = sig.get("opportunity_or_risk") or "관찰"
    cat_label, cat_color, _kind_cls = _KIND.get(kind, ("관찰", "#3F6FA8", "sky"))
    band = sig.get("score_band") or sig.get("alert_grade") or "추적 필요"
    tag, tag_class = _BAND_TAG.get(band, ("참고", "sky"))
    score = sig.get("final_score")
    score_str = "-" if score is None else f"{float(score):.1f}"
    if kind == "리스크":
        score_label, score_color = "리스크", "#B85049"
    elif band == "즉시 확인":
        score_label, score_color = "즉시", "#B85049"
    elif score is not None and float(score) >= 3.5:
        score_label, score_color = "중요", "#5E6E8C"
    else:
        score_label, score_color = "관찰", "#5E6E8C"
    spread = sig.get("spread") or {}
    related = (f"관련 {spread.get('related_count')}건"
               if spread.get("related_count") else "단독 신호")
    sources = (f"출처 {spread.get('source_count')}곳"
               if spread.get("source_count") else "출처 1곳")
    value_chain = ai_value_chain.classify_ai_value_chain(
        sig.get("title") or "", sig.get("source") or "", sig.get("snippet") or "")
    lens = sorted(set(_lens_for(sig)) | ({l for l in extra_lens} & VALID_LENS))
    provenance = {
        "source": "executive_brief",
        "article_id": sig.get("article_id") or "",
        "source_type": sig.get("source_type") or "",
        "radar_section": sig.get("radar_section") or "",
        "executive_section": sig.get("executive_section") or "",
        "category": sig.get("category") or "",
        "published_at": sig.get("published_at") or "",
        "source_quality": sig.get("source_quality") or "",
        "is_ai_value_chain": value_chain["is_ai_value_chain"],
        "ai_value_chain_layer": value_chain["ai_value_chain_layer"],
        "hdec_relevance_tier": value_chain["hdec_relevance_tier"],
        "ai_value_chain_reason": value_chain["reason"],
    }
    # 사이트 워치리스트 매칭 provenance(D7-M/D7-N) — 제목이 워치리스트 프로젝트명을 실제로
    # 언급한 행에만 출처를 단다. 전체 비공개 목록은 모델에 넣지 않는다(매칭된 항목만). 한 제목이
    # 여러 항목과 매칭되면 matches 리스트로 모두 담고, 첫 매칭을 display용 primary로 둔다(트리
    # 노드의 alert_marker '!'를 이 row→node 연결로 켠다 — D7-N site_watch_tree).
    site_matches = site_watchlist.classify_site_lenses(sig.get("title") or "")
    if site_matches:
        m = site_matches[0]
        provenance["site_watch_match"] = True
        provenance["site_watch_id"] = m.get("id") or ""
        provenance["site_watch_label"] = m.get("label") or m.get("name") or ""
        provenance["site_watch_scope"] = m.get("scope") or ""
        provenance["site_watch_business_lens"] = m.get("business_lens") or ""
        provenance["site_watch_org_unit"] = m.get("org_unit") or ""
        if sig.get("published_at"):
            provenance["site_watch_latest_published_at"] = sig.get("published_at")
        provenance["site_watch_matches"] = [
            {"id": x.get("id") or "", "label": x.get("label") or x.get("name") or "",
             "scope": x.get("scope") or "", "business_lens": x.get("business_lens") or "",
             "org_unit": x.get("org_unit") or ""}
            for x in site_matches
        ]
    return {
        "tag": tag, "tagClass": tag_class,
        "title": sig.get("title") or "",
        "source": _better_source(sig),
        "time": _fmt_time(sig.get("published_at")),
        "related": related, "sources": sources,
        "cat": cat_label, "catColor": cat_color,
        "score": score_str, "scoreLabel": score_label, "scoreColor": score_color,
        "lens": lens,
        "url": sig.get("url") if _is_http(sig.get("url")) else "",
        "provenance": provenance,
    }


# ── AI 신호 실행가능성 분류 (D7-D) ──────────────────────────────────────────
# 임원 의사결정용 AI 신호 카테고리. 일반 '테마 %'가 아니라 실무 렌즈로 분류한다.
# 첫 매칭 우선(대부분 데이터센터·전력). raw 제목/category_label만 본다(가짜 값 생성 금지).
_AI_CATEGORIES = [
    ("chip_supply", "AI 칩·반도체 공급망",
     ("AI 칩", "AI칩", "반도체", "HBM", "파운드리", "Broadcom", "브로드컴",
      "NVIDIA", "엔비디아", "TSMC", "마이크론", "Micron", "SK하이닉스", "삼성전자")),
    ("dc_power", "AI·데이터센터·전력 인프라",
     ("데이터센터", "전력", "송배전", "송전", "변전", "계통", "전력망", "그리드",
      "SMR", "원전", "LNG", "발전", "EPC", "냉각", "전력 인프라")),
    ("field_auto", "현장 생산성·자동화",
     ("로봇", "스마트건설", "자동화", "BIM", "모듈러", "공정", "생산성", "무인", "드론", "OSC")),
    ("safety_ai", "안전·품질 리스크",
     ("안전", "품질", "점검", "모니터링", "사고", "예측", "감리", "검측", "중대재해")),
    ("market_ai", "경쟁사·발주처 AI 움직임",
     ("경쟁", "발주처", "발주", "조달", "입찰", "삼성", "GS건설", "대우", "DL", "포스코")),
]
_AI_DEFAULT = ("internal", "내부 적용 후보")
# 카테고리별 한 줄 의미(왜 보는가) — 신호가 없을 때도 카테고리 자체는 노출하기 위함.
_AI_CATEGORY_DESC = {
    "chip_supply": "AI 칩·HBM·파운드리 → 반도체 클러스터·전력·하이테크 EPC 연계",
    "dc_power": "데이터센터 전력수요 → 원전·SMR·LNG·전력망·EPC 수주 기회",
    "field_auto": "현장 로봇·스마트건설 → 공정·원가·안전 자동화 적용",
    "safety_ai": "AI 점검·모니터링·사고 예측 → 안전·품질 리스크 저감",
    "market_ai": "경쟁사 도입·발주처 조달 수요 → 수주 경쟁·기술 격차",
    "internal": "현대건설 내부 적용 가능성 — 담당 라인 배정 검토",
}


# AI 직접 근거 게이트 (D7-L) — 렌즈/뱅크의 'ai' 태그는 raw 제목(+출처)의 직접 AI/데이터센터
# 인프라 근거에서만 나온다. injected category_label 단독으로는 분류하지 않는다 — 안전/규제
# 기사가 category 오염으로 AI에 새거나, 제목엔 없고 주입 토픽에만 '데이터센터'가 있는 곁다리
# 기사가 AI로 분류되는 것을 막는다. 가짜 값 생성 없음(있는 토큰만 본다).
_AI_TOKEN_RE = re.compile(r"(?<![A-Za-z가-힣])AI(?![A-Za-z가-힣])")  # 대문자 standalone 'AI'
_AI_DIRECT_TERMS = (
    "인공지능", "스마트건설", "스마트 건설", "디지털트윈", "디지털 트윈", "BIM",
    "건설로봇", "건설 로봇", "시공로봇", "현장 로봇", "자율시공", "건설 자동화",
    "머신러닝", "딥러닝", "생성형",
)
_AI_DC_TERMS = ("데이터센터", "데이터 센터", "data center", "datacenter")
# 데이터센터는 곁다리 단독 언급이 아니라 전력/냉각/건설·발주 인프라 맥락이 함께 있어야 AI 신호.
_AI_DC_INFRA = (
    "전력", "송전", "송배전", "변전", "계통", "전력망", "그리드", "냉각", "쿨링",
    "epc", "건설", "시공", "수주", "발주", "공사", "신축", "투자", "smr", "원전",
    "발전", "인프라", "부지",
)


def _has_ai_evidence(title: str, source: str = "", snippet: str = "") -> bool:
    """raw 제목(+출처)에 직접 AI/데이터센터 인프라 근거가 있는가 (injected label 미사용)."""
    vc = ai_value_chain.classify_ai_value_chain(title or "", source or "", snippet or "")
    if ai_value_chain.is_executive_ai_candidate(vc):
        return True
    raw = f"{title or ''} {source or ''} {snippet or ''}"
    if _AI_TOKEN_RE.search(raw):
        return True
    if any(t in raw for t in _AI_DIRECT_TERMS):
        return True
    low = raw.lower()
    return any(t in low for t in _AI_DC_TERMS) and any(t in low for t in _AI_DC_INFRA)


def _ai_category(sig) -> tuple:
    layer = sig.get("ai_value_chain_layer") or ai_value_chain.classify_ai_value_chain(
        sig.get("title") or "", sig.get("source") or "", sig.get("snippet") or ""
    )["ai_value_chain_layer"]
    if layer in {
            ai_value_chain.LAYER_CUSTOM_CHIP,
            ai_value_chain.LAYER_SEMI_SUPPLY,
            ai_value_chain.LAYER_SEMI_CLUSTER,
    }:
        return "chip_supply", "AI 칩·반도체 공급망"
    if layer in {
            ai_value_chain.LAYER_DC_POWER,
            ai_value_chain.LAYER_DC_COOLING,
            ai_value_chain.LAYER_DC_CONSTRUCTION,
    }:
        return "dc_power", "AI·데이터센터·전력 인프라"
    if layer == ai_value_chain.LAYER_SMART_CONSTRUCTION:
        return "field_auto", "현장 생산성·자동화"
    text = f"{sig.get('title') or ''} {sig.get('category_label') or ''}"
    for key, label, words in _AI_CATEGORIES:
        if any(w in text for w in words):
            return key, label
    return _AI_DEFAULT


# AI 신호 → 임원 액션 유형 (5종). 일반 문구가 아니라 의사결정 액션으로 고정 매핑한다.
_AI_ACTION = {
    "chip_supply": "전략 모니터링",
    "dc_power": "수주기획 검토",
    "field_auto": "현장 PoC 후보",
    "safety_ai": "안전품질 검토",
    "market_ai": "모니터링",
    "internal": "기술검토",
}


def _ai_relevance(sig) -> tuple:
    """현대건설 관련성 (유형, 문구) — 저장된 분류 필드에서만 파생(생성 라벨/가짜 사실 금지).

    유형은 direct/indirect/competitor/market 4종. '직접(direct)'은 executive_section==
    hdec_direct(직접 분류)에만 쓴다 — hdec_bucket(전략 implication)이 있다고 직접이라
    과장하지 않는다(예: MS·아마존 SMR은 직접 아님 = 간접 연계 indirect).
    """
    cat = sig.get("category_label") or "건설산업 AI"
    if sig.get("executive_section") == "hdec_direct":
        return "direct", "현대건설 직접 연관 — " + cat
    if sig.get("is_competitor"):
        return "competitor", "경쟁사 동향 — 수주 경쟁·기술 격차 점검"
    if sig.get("radar_section") == "business_overseas" \
            or sig.get("executive_section") == "business_overseas":
        return "market", "해외 발주환경 — 수주 기회 연계 점검"
    if sig.get("hdec_bucket"):
        return "indirect", "현대건설 사업 연계 — " + cat + " (간접 영향)"
    return "market", cat + " — 수주·사업 기회 관련성 점검"


def _ai_enrich(row: dict, sig: dict) -> dict:
    """AI 행에 임원 실행 정보 부착(왜 중요/현대건설 관련성·유형/예상 액션·유형/카테고리).

    예상 액션은 카테고리에서 5종 액션 유형(수주기획 검토/기술검토/현장 PoC 후보/안전품질
    검토/모니터링)으로 고정 매핑한다 — 경쟁사 관련성은 모니터링으로 덮어쓴다. raw 파생만.
    """
    cat_key, cat_label = _ai_category(sig)
    why = (sig.get("one_line_reason") or sig.get("implication")
           or sig.get("category_label") or "AI·데이터센터 연계 신호").strip()
    rel_type, rel_text = _ai_relevance(sig)
    action = _AI_ACTION.get(cat_key, "모니터링")
    if rel_type == "competitor":
        action = "모니터링"                       # 경쟁사 동향은 수주 경쟁 모니터링이 우선
    if cat_key == "internal":
        action = f"{action} · 내부 적용 담당 배정 필요"
    value_chain = ai_value_chain.classify_ai_value_chain(
        sig.get("title") or "", sig.get("source") or "", sig.get("snippet") or "")
    row["aiCategory"] = cat_key
    row["aiCategoryLabel"] = cat_label
    row["aiValueChainLayer"] = value_chain["ai_value_chain_layer"]
    row["hdecRelevanceTier"] = value_chain["hdec_relevance_tier"]
    row["aiValueChainReason"] = value_chain["reason"]
    row["why"] = why
    row["relevance"] = rel_text
    row["relevanceType"] = rel_type
    row["action"] = action
    return row


# ── 신선도 라벨 + 글로벌 '왜 중요' (D7-G Part B/C) ───────────────────────────
# 신선도: 표시 행의 실제 published_at를 brief 기준일과 비교해 오늘/최근 7일/최근 30일로 라벨한다.
# 오래된 기사를 '오늘'로 위장하지 않는다(가짜 날짜 생성 금지) — 31일 초과는 라벨을 생략한다.
def _ref_date(brief: dict):
    raw = (brief.get("date_kst") or "").strip()
    try:
        return datetime.fromisoformat(raw[:10]).date()
    except (TypeError, ValueError):
        return None


def _freshness(published_at, ref_date) -> str:
    if not published_at or ref_date is None:
        return ""
    try:
        dt = datetime.fromisoformat(str(published_at))
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = (ref_date - dt.astimezone(_KST).date()).days
    if days <= 0:
        return "오늘"
    if days <= 7:
        return "최근 7일"
    if days <= 31:
        return "최근 30일"
    return ""  # 31일 초과는 '최근'으로 위장하지 않는다


# 글로벌(해외사업) 렌즈 행의 '왜 중요' — 첫 매칭 우선. 특징적인 거시 동인(환율/공급망/원가)을
# 먼저 보고('수주'는 해외기사에 흔해 후순위), 그 외 발주/경쟁/수주로 분류한다. raw 제목/라벨만.
_GLOBAL_WHY_RULES = [
    (("환율", "원/달러", "달러 강세", "원화", "강달러", "환리스크"), "환율 부담"),
    (("호르무즈", "전쟁", "지정학", "제재", "봉쇄", "물류", "차단", "분쟁", "공급망"), "공급망 리스크"),
    (("유가", "원유", "원자재", "원가", "공사비", "자재", "철강", "구리", "LNG", "가스"), "원가 부담"),
    (("경쟁", "삼성물산", "대우건설", "GS건설", "DL이앤씨", "포스코이앤씨"), "경쟁사 동향"),
    (("발주 재개", "입찰", "예산 편성", "발주환경"), "발주 재개"),
    (("수주", "낙찰", "계약", "MOU", "수주전", "따냈", "선정", "발주처", "발주"), "수주 기회"),
]


def _global_why(sig) -> str:
    text = f"{sig.get('title') or ''} {sig.get('category_label') or ''}"
    for words, why in _GLOBAL_WHY_RULES:
        if any(w in text for w in words):
            return why
    return "해외사업 영향"


def _enrich_row(row: dict, sig: dict, ref_date) -> dict:
    """행에 신선도 라벨(있을 때만) + 글로벌 렌즈 '왜 중요'(global_business 태그일 때만) 부착."""
    fresh = _freshness(sig.get("published_at"), ref_date)
    if fresh:
        row["freshness"] = fresh
    if "global_business" in (row.get("lens") or []):
        row["whyGlobal"] = _global_why(sig)
    return row


def _metric_indices(sig) -> list:
    comps = sig.get("score_components") or []
    out = []
    for i, c in enumerate(comps[:6]):
        try:
            value = round(float(c.get("value") or 0), 1)
        except (TypeError, ValueError):
            value = 0
        out.append({"label": c.get("label") or c.get("key") or "지표",
                    "value": value, "color": _COMP_PALETTE[i % len(_COMP_PALETTE)]})
    return out


def _dedup_signals(*lists) -> list:
    seen, out = set(), []
    for lst in lists:
        for s in lst or []:
            k = _key(s)
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(s)
    return out


def _brief_signal_pool(brief: dict) -> list:
    """brief 전체에서 기사형 dict를 순회 수집한다.

    news_rows는 큐레이션 상위 피드로 유지하지만, 렌즈 전용 bank는 full brief에 들어온
    기사형 신호 전체를 써야 top-N 클리핑 때문에 렌즈가 비는 일이 없다.
    """
    seen, out = set(), []

    def walk(node):
        if isinstance(node, dict):
            if node.get("title") and (node.get("url") or node.get("source")):
                k = _key(node)
                if k and k not in seen:
                    seen.add(k)
                    out.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(brief)
    return out


def _lens_counts(rows) -> dict:
    counts = {}
    for r in rows:
        for l in r.get("lens") or []:
            counts[l] = counts.get(l, 0) + 1
    return counts


def _bank_counts(lens_banks: dict) -> dict:
    return {k: len(v or []) for k, v in (lens_banks or {}).items()}


def _decorate_immediate_row(row: dict, basis: str) -> dict:
    row["immediate_basis"] = basis
    row["immediateBasisLabel"] = (
        "엄격 즉시 알림" if basis == "strict_instant" else "즉시 확인 후보"
    )
    row["lens"] = sorted(set(row.get("lens") or []) | {"now"})
    if basis == "executive_review_candidate" and row.get("tag") != "즉시":
        row["tag"] = "후보"
        row["tagClass"] = "amber"
    return row


def _is_executive_review_candidate(sig: dict, new_keys: set) -> bool:
    if not _is_http(sig.get("url")):
        return False
    title = sig.get("title") or ""
    urgent_terms = (
        "중대재해", "특별감독", "벌점", "입찰제한", "영업정지", "철근", "누락",
        "하자", "공기지연", "소송", "공방", "리스크", "봉쇄", "차질", "급등",
        "발주", "수주", "단독",
    )
    return (
        _score_value(sig) >= 2.5
        or _is_hdec_direct(sig)
        or _is_risk_signal(sig)
        or _key(sig) in new_keys
        or any(term in title for term in urgent_terms)
    )


def _build_now_bank(brief: dict, new_keys: set, ref_date, base_pool: list) -> tuple[list, dict]:
    """Build the immediate lens honestly.

    Strict `top_immediate_signals` win. Only when no strict displayable rows exist do we
    expose real executive-review candidates, marked as candidates rather than alerts.
    """
    strict_raw = brief.get("top_immediate_signals") or []
    strict_pool = [s for s in _dedup_signals(strict_raw) if _is_http(s.get("url"))]
    strict_pool.sort(key=lambda s: _signal_rank_key(s, "strict_instant"))
    strict_pool = _drop_near_dups(strict_pool)

    basis = "strict_instant"
    selected = strict_pool
    if not selected:
        fallback = _dedup_signals(
            brief.get("hdec_direct_signals"),
            brief.get("risk_regulation_signals"),
            brief.get("top_new_issues"),
            brief.get("business_signals"),
            base_pool,
        )
        fallback = [s for s in fallback if _is_executive_review_candidate(s, new_keys)]
        fallback.sort(key=lambda s: _signal_rank_key(s, "executive_review_candidate"))
        selected = _drop_near_dups(fallback)
        basis = "executive_review_candidate"

    rows, seen_titles = [], set()
    for sig in selected:
        if len(rows) >= NOW_BANK_CAP:
            break
        extra = ["now"] + (["new"] if _key(sig) in new_keys else [])
        row = _decorate_immediate_row(
            _enrich_row(_row_from_signal(sig, extra), sig, ref_date),
            basis,
        )
        title_key = (row.get("title") or "").strip()
        if not title_key or title_key in seen_titles:
            continue
        rows.append(row)
        seen_titles.add(title_key)

    status = {
        "strict_count": len(strict_raw),
        "strict_displayable_count": len(strict_pool),
        "fallback_count": len(rows) if basis == "executive_review_candidate" else 0,
        "bank_count": len(rows),
    }
    return rows, status


def _build_lens_banks(full_pool: list, ai_rows: list, now_rows: list,
                      new_keys: set, ref_date) -> dict:
    """렌즈별 전용 row bank를 full brief 신호에서 만든다(렌즈당 최대 LENS_BANK_CAP건).

    전역 news_rows와 별도 큐다. exact title 중복만 제거하고, URL 없는 신호는 카드 원문 링크
    계약을 지키기 위해 제외한다. 행 자체는 _row_from_signal/_enrich_row에서만 파생한다.
    """
    ordered = [s for s in full_pool if _is_http(s.get("url"))]
    ordered.sort(key=_signal_rank_key)
    ordered = _drop_near_dups(ordered)

    banks = {k: [] for k in VALID_LENS}
    titles = {k: set() for k in VALID_LENS}

    def add_row(lens: str, row: dict) -> None:
        if lens not in banks:
            return
        title_key = (row.get("title") or "").strip()
        if not title_key or title_key in titles[lens]:
            return
        banks[lens].append(row)
        titles[lens].add(title_key)

    for sig in ordered:
        row = _enrich_row(_row_from_signal(sig, ["new"] if _key(sig) in new_keys else []),
                          sig, ref_date)
        title_key = (row.get("title") or "").strip()
        if not title_key:
            continue
        for lens in row.get("lens") or []:
            add_row(lens, row)

    # AI 뱅크는 직접 근거가 있는 행만 담는다(D7-L) — AI 탭(ai_rows)은 surface_contracts가
    # 소유하되, 뱅크에 들어가는 행은 raw 제목 근거를 한 번 더 통과시켜 곁다리 오염을 막는다.
    for row in ai_rows or []:
        # D7-Q: 빈 렌즈 행은 뱅크에 넣지 않는다(가짜 라벨 금지 · 위 ai_pool 필터로 이미 제외되나
        # 방어적 가드). raw 제목 직접 근거가 있는 행만 'ai' 뱅크에 담는다(곁다리 오염 방지).
        if row.get("lens") and _has_ai_evidence(row.get("title") or "", row.get("source") or ""):
            add_row("ai", row)

    for row in now_rows or []:
        add_row("now", row)

    caps = {k: LENS_BANK_CAP for k in VALID_LENS}
    caps["ai"] = AI_BANK_CAP
    caps["now"] = NOW_BANK_CAP
    for lens, rows in list(banks.items()):
        def row_score(row: dict) -> float:
            try:
                return float(row.get("score") or 0)
            except (TypeError, ValueError):
                return 0.0

        def tier(row: dict) -> int:
            try:
                return int((row.get("provenance") or {}).get("hdec_relevance_tier") or 9)
            except (TypeError, ValueError):
                return 9

        if lens == "ai":
            rows.sort(key=lambda r: (
                tier(r),
                -int(r.get("immediate_basis") == "strict_instant"),
                -int((r.get("provenance") or {}).get("executive_section") == "hdec_direct"),
                -row_score(r),
                r.get("title") or "",
            ))
        elif lens in {"developers", "trust_companies", "development_business"}:
            rows.sort(key=lambda r: (
                tier(r),
                -row_score(r),
                -int(r.get("immediate_basis") == "strict_instant"),
                r.get("title") or "",
            ))
        else:
            rows.sort(key=lambda r: (
                -row_score(r),
                -int(r.get("immediate_basis") == "strict_instant"),
                -int((r.get("provenance") or {}).get("executive_section") == "hdec_direct"),
                -int((r.get("provenance") or {}).get("radar_section") == "risk_regulation"
                     or r.get("cat") == "리스크"),
                r.get("title") or "",
            ))
        if lens == "ai":
            # D7-U: AI 뱅크는 국내 tier-2가 캡을 독점하지 않게 하이퍼스케일러/칩 슬롯을 예약한다.
            banks[lens] = _reserve_hyperscaler_rows(rows, caps["ai"])
        else:
            banks[lens] = rows[:caps.get(lens, LENS_BANK_CAP)]
    banks = {k: v for k, v in banks.items() if v}
    return banks


def _ai_tier(sig) -> int:
    """HDEC 관련성 tier (1=직접 … 5=일반). 저장된 분류 필드 우선, 없으면 raw로 재분류."""
    raw = sig.get("hdec_relevance_tier")
    if raw is None:
        raw = ai_value_chain.classify_ai_value_chain(
            sig.get("title") or "", sig.get("source") or "",
            sig.get("snippet") or "")["hdec_relevance_tier"]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 9


def _is_hyper_vc(sig) -> bool:
    """하이퍼스케일러/AI 칩 밸류체인 신호인가 (정책 기반 · raw 제목 근거)."""
    return ai_value_chain.is_hyperscaler_value_chain(
        sig.get("title") or "", sig.get("source") or "", sig.get("snippet") or "")


def _reserve_hyperscaler_rows(sorted_rows: list, cap: int) -> list:
    """tier 정렬된 'ai' 뱅크 후보에서 상위 cap건을 고르되, 하이퍼스케일러/칩 밸류체인 행
    최대 AI_HYPER_RESERVE건이 국내 tier-2 신호에 밀려 캡 밖으로 떨어지지 않게 예약한다(D7-U).

    하이퍼스케일러 행이 없으면 단순 상위 cap건(기존 동작) — 가짜 생성 없음. 예약분을 넣은 뒤
    HDEC 관련성 tier로 안정 재정렬해 'tier1 현대건설 직접 → tier2 데이터센터·전력 → tier3 칩'
    순서를 유지한다. 같은 기사가 다른 뱅크(civil/plant)에도 함께 노출되는 건 기존 멀티섹션과 동형.
    """
    if len(sorted_rows) <= cap:
        return sorted_rows
    hyper = [r for r in sorted_rows if _is_hyper_vc(r)]
    if not hyper:
        return sorted_rows[:cap]
    reserve = min(AI_HYPER_RESERVE, len(hyper))
    head = sorted_rows[:max(0, cap - reserve)]
    head_keys = {_key(r) for r in head}
    reserved = [r for r in hyper if _key(r) not in head_keys][:reserve]
    out = head + reserved
    if len(out) < cap:                                    # 예약분이 이미 head에 있던 날 백필
        taken = {_key(r) for r in out}
        for r in sorted_rows:
            if len(out) >= cap:
                break
            if _key(r) not in taken:
                out.append(r)
                taken.add(_key(r))
    out.sort(key=_ai_tier)                                # 표시용 tier 안정 재정렬
    return out[:cap]


def _derive(brief: dict) -> dict:
    """공유 brief → 대시보드 모델 조각 (featured/news_rows/ai_rows/counts)."""
    immediate = brief.get("top_immediate_signals") or []
    hdec = brief.get("hdec_direct_signals") or []
    new_issues = brief.get("top_new_issues") or []
    new_keys = {_key(s) for s in new_issues}
    ref_date = _ref_date(brief)  # 신선도 라벨 기준일(brief 기준일) — 가짜 날짜 없음

    # 전체 종합 가시 풀: score-first 정렬을 먼저 끝낸 뒤 featured를 고른다. 즉, featured는
    # 순위 표시용 카드일 뿐 낮은 점수 행을 상단에 끼워 넣는 예외가 아니다.
    overall_pool = _dedup_signals(immediate, hdec, brief.get("business_signals"),
                                  brief.get("risk_regulation_signals"),
                                  brief.get("competitor_supply_signals"),
                                  new_issues, brief.get("macro_economy_signals"))
    overall_pool = [s for s in overall_pool if _is_http(s.get("url"))]
    overall_pool.sort(key=_signal_rank_key)
    overall_pool = _drop_near_dups(overall_pool)
    # D7-Q: 유효 콘텐츠 렌즈가 없는 신호는 featured/news에서 제외(가짜 라벨 금지). featured는
    # 대표 카드라 렌즈가 비면 안 된다 — 약한 HDEC 오탐 가드 직후 _has_dashboard_lens로 거른다.
    featured_candidates = [s for s in overall_pool if not _is_weak_hdec_fp(s)]
    featured_candidates = [s for s in featured_candidates if _has_dashboard_lens(s)]
    if not featured_candidates:
        fallback = _dedup_signals(brief.get("ai_radar_signals"),
                                  brief.get("business_signals"),
                                  brief.get("risk_regulation_signals"))
        fallback = [s for s in fallback if _is_http(s.get("url"))
                    and not _is_weak_hdec_fp(s) and _has_dashboard_lens(s)]
        fallback.sort(key=_signal_rank_key)
        featured_candidates = _drop_near_dups(fallback)
    featured_sig = featured_candidates[0] if featured_candidates else None
    fkey = _key(featured_sig)

    # AI 행: featured 제외 + 근접 중복(재전송본) 제거 (briefing 순서 보존, 상위 AI_BANK_CAP건).
    # 각 행에 임원 실행 정보(왜/관련성/액션/카테고리) 부착 → 일반 테마 % 아닌 의사결정 신호.
    ai_pool = _drop_near_dups([s for s in (brief.get("ai_radar_signals") or [])
                               if _key(s) != fkey and _has_dashboard_lens(s)])
    selected_ai_pool = ai_pool[:AI_BANK_CAP]
    selected_ai_keys = {_key(s) for s in selected_ai_pool}
    ai_rows = [_enrich_row(_ai_enrich(_row_from_signal(s), s), s, ref_date)
               for s in selected_ai_pool]

    # 뉴스 풀: featured/AI와 정확 중복을 제거한 뒤 이미 score-first 정렬된 순서를 유지한다.
    # D7-Q: 유효 콘텐츠 렌즈가 없는 무관 기사는 제외하되, top_new_issues 분류로 'new' 상태를
    # 받은 행은 정당하므로 보존한다(여기서 'new'를 새로 붙이지 않는다 — 가짜 fallback 금지).
    pool = [s for s in overall_pool
            if _key(s) != fkey and _key(s) not in selected_ai_keys
            and (_has_dashboard_lens(s) or _key(s) in new_keys)]
    news_rows = [_enrich_row(_row_from_signal(s, ["new"] if _key(s) in new_keys else []),
                             s, ref_date)
                 for s in pool[:NEWS_ROW_CAP]]

    featured_row = (_enrich_row(_row_from_signal(featured_sig), featured_sig, ref_date)
                    if featured_sig else None)
    panel_rows = ([featured_row] if featured_row else []) + news_rows
    lens_counts = _lens_counts(panel_rows)
    now_rows, immediate_status = _build_now_bank(brief, new_keys, ref_date, overall_pool)
    lens_banks = _build_lens_banks(_brief_signal_pool(brief), ai_rows, now_rows,
                                   new_keys, ref_date)
    bank_counts = _bank_counts(lens_banks)
    # D7-U: 실제 노출되는 'ai' 뱅크에서 하이퍼스케일러/칩 밸류체인 건수를 센다(진단 카운트).
    ai_hyper_count = sum(1 for r in (lens_banks.get("ai") or []) if _is_hyper_vc(r))

    # 모든 콘텐츠 렌즈를 실제 카운트(없으면 0)로 채운다 — 정적 데모 값(예: domestic_site=2)이
    # 남아 '수집된 것처럼' 보이는 오해를 막는다(빈 렌즈는 0으로 정직 표기 + 빈 상태가 설명).
    nav_counts = {k: bank_counts.get(k, 0) for k in VALID_LENS}
    nav_counts["all"] = len(panel_rows)
    nav_counts["ai"] = bank_counts.get("ai", len(ai_rows))

    return {
        "featured_sig": featured_sig,
        "featured_row": featured_row,
        "news_rows": news_rows,
        "ai_rows": ai_rows,
        "lens_banks": lens_banks,
        "metric_indices": _metric_indices(featured_sig) if featured_sig else [],
        "lens_counts": lens_counts,
        "bank_counts": bank_counts,
        "nav_counts": nav_counts,
        "immediate_n": bank_counts.get("now", 0),
        "immediate_status": immediate_status,
        # D7-U: 하이퍼스케일러/밸류체인 노출 진단 카운트 — 빈 날에도 0을 명시(가짜 채움 금지).
        "ai_hyper_count": ai_hyper_count,
        "ai_value_chain_pool_count": len(brief.get("ai_value_chain_pool") or []),
    }


_TRUST_SVG = ('<span class="trust"><svg width="11" height="11" viewBox="0 0 24 24" fill="none">'
              '<path d="M9 12l2 2 4-4" stroke="#3F6FA8" stroke-width="2.2" stroke-linecap="round" '
              'stroke-linejoin="round"></path><circle cx="12" cy="12" r="9" stroke="#3F6FA8" '
              'stroke-width="1.6"></circle></svg>신뢰 출처</span>')
_LINK_SVG = ('<svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M7 17L17 7M17 7H9M17 7v8" '
             'stroke="#3F6FA8" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path></svg>')


def _render_featured(sig: dict, row: dict) -> str:
    """featured hero 카드를 실제 최상위 신호로 생성 (data-lens/data-category + 원문 링크)."""
    kind = sig.get("opportunity_or_risk") or "관찰"
    _cl, _cc, kind_cls = _KIND.get(kind, ("관찰", "#3F6FA8", "sky"))
    chip_keys = [l for l in row["lens"] if l not in ("now", "new")][:3]
    chips = "".join(f'<span class="flchip">{escape(_LENS_LABEL.get(k, k))}</span>'
                    for k in chip_keys)
    trust = _TRUST_SVG if sig.get("source_quality") == "trusted" else ""
    url = sig.get("url") or ""
    if _is_http(url):
        link = (f'<a class="extlink" href="{escape(url)}" target="_blank" rel="noopener noreferrer">'
                f'원문 보기 {_LINK_SVG}</a>')
    else:
        link = '<span style="font-size:12px; color:var(--mute4);">원문 링크 없음</span>'
    summary = escape(sig.get("one_line_reason") or sig.get("implication") or "")
    impact = escape(f"{sig.get('category_label') or ''} · {row['cat']} 신호 · 의사결정 관련도 점검")
    action = escape(sig.get("action_label") or "내용 확인 후 담당 라인 검토 배정")
    return (
        f'<article class="card featured" data-lens="{escape(" ".join(row["lens"]))}" '
        f'data-category="{escape(row["cat"])}">'
        '<div class="head"><div style="min-width:0;">'
        f'<div class="tagrow"><span class="tag {escape(row["tagClass"])}">{escape(row["tag"])}</span>'
        f'<span class="tag {kind_cls}">{escape(row["cat"])}</span></div>'
        f'<h2>{escape(sig.get("title") or "")}</h2>'
        f'<div class="meta"><b>{escape(row["source"])}</b><span class="sep">·</span>'
        f'<span>{escape(row["time"])}</span><span class="sep">·</span><span>{escape(row["related"])}</span>'
        f'<span class="sep">·</span><span>{escape(row["sources"])}</span>{trust}</div>'
        f'<div class="featlens"><span class="fllabel">렌즈</span>{chips}</div>'
        '</div>'
        f'<div class="score"><div class="v num" style="color:{row["scoreColor"]};">{escape(row["score"])}'
        f'<small> / 5</small></div><div class="l" style="color:{row["scoreColor"]};">'
        f'{escape(row["scoreLabel"])}</div></div>'
        '</div>'
        '<div class="read">'
        f'<div class="rd"><span class="rdb sum">요약</span><span>{summary}</span></div>'
        f'<div class="rd"><span class="rdb imp">영향</span><span>{impact}</span></div>'
        f'<div class="rd act"><span class="rdb act">액션</span><span>{action}</span></div>'
        '</div>'
        '<div class="idx"><div class="h">신호 지수 · 0–5</div>'
        '<div class="idxgrid" id="idxgrid"></div></div>'
        '<div class="cardfoot">'
        '<div style="display:flex; align-items:center; gap:14px; flex-wrap:wrap;">'
        f'{link}'
        f'<span style="font-size:12px; color:var(--mute4);">연계 {escape(sig.get("category_label") or "")}</span>'
        '</div>'
        '<span style="font-size:11.5px; color:var(--mute4);" class="num">자동 분류 · '
        f'{escape(sig.get("category") or "")}</span>'
        '</div></article>'
    )


def _inject_featured(html: str, featured_html: str) -> str:
    if not featured_html:
        return html
    new, n = re.subn(r'<article class="card featured".*?</article>', lambda _m: featured_html,
                     html, count=1, flags=re.S)
    if n != 1:
        print("ERROR: featured 카드 블록을 찾지 못함 (템플릿 구조 변경?)", file=sys.stderr)
        raise SystemExit(1)
    return new


def _fmt_market_value(value, decimals: int) -> str:
    """시세값을 천단위 구분 + 소수 자리로 포맷 (표시용 — 시세 자체는 바꾸지 않는다)."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if decimals <= 0:
        return f"{int(round(num)):,}"
    return f"{num:,.{decimals}f}"


def _market_link_counts(items: list) -> dict:
    """시장 지표 연동/미연동 분포 카운트(D7-U). 렌더 JS의 marketState와 동일 규칙:

    · linked  = 현재값 보유(미연동 아님). 지연/대용/보고 값 모두 포함(라벨로 출처 명시).
    · unlinked= 값 자체 없음(value null/—) → 하단 '미연동 관찰 후보'로 분리(deprioritized).
    · chartless= 값은 있으나 1개월 실데이터 히스토리(≥2점)가 없어 상세 차트가 없는 행(linked 부분집합).

    값을 만들어내지 않는다 — 미연동을 가짜로 linked로 올리지 않는다(정직성).
    """
    linked = unlinked = chartless = 0
    for it in items or []:
        v = it.get("value")
        has_value = v is not None and str(v).strip() not in ("", "—")
        hist = it.get("history") if isinstance(it.get("history"), dict) else {}
        arr = (hist or {}).get("1m") or []
        chartable = len([x for x in arr if isinstance(x, (int, float))]) >= 2
        if has_value:
            linked += 1
            if not chartable:
                chartless += 1
        else:
            unlinked += 1
    return {
        "market_linked_count": linked,
        "market_unlinked_count": unlinked,
        "market_chartless_count": chartless,
        "market_deprioritized_count": unlinked,
    }


def _overlay_market_history(model: dict, market_mode: str) -> None:
    """live 모드에서 지원 종목의 기간 히스토리(+표시 현재값/변동)를 공개 시세(지연) 실측으로 교체.

    mock(기본)에서는 템플릿의 결정적 데모 픽스처를 그대로 둔다 — 네트워크 0건. live에서 일부
    심볼이 실패하면 그 종목은 데모로 남는다(history_data_mode=mock_demo) — live로 위장하지 않는다.
    표시 현재값/변동을 히스토리 끝점과 일치시켜 차트·값이 어긋나지 않게 한다('지연·체결값 아님' 유지).
    """
    if (market_mode or "mock").strip().lower() != "live":
        return
    entries = market_history.history_for_model(mode="live")
    by_id = {it.get("id"): it for it in model.get("market_items") or []}
    for item_id, entry in entries.items():
        it = by_id.get(item_id)
        if not it or entry.get("history_data_mode") != market_history.LIVE_MODE:
            continue  # 실측 성공분만 교체(실패분은 데모 유지 = 정직)
        hist = entry["history"]
        it["history"] = hist
        it["history_source"] = entry["history_source"]
        it["history_data_mode"] = entry["history_data_mode"]
        it["history_updated_at"] = entry["history_updated_at"]
        it["history_decimals"] = entry["history_decimals"]
        latest = hist["1w"][-1]
        it["value"] = _fmt_market_value(latest, entry["history_decimals"])
        ref = hist["1m"][0] if hist.get("1m") else None
        if ref:
            pct = (latest - ref) / ref * 100
            if pct > 0.05:
                it["dir"], it["delta"] = "up", f"▲ +{pct:.1f}%"
            elif pct < -0.05:
                it["dir"], it["delta"] = "down", f"▼ {abs(pct):.1f}%"
            else:
                it["dir"], it["delta"] = "flat", "—"


def _inject_model(html: str, parts: dict, news_mode: str, market_mode: str = "mock",
                  brief: dict | None = None) -> str:
    m = re.search(r'(<script type="application/json" id="preview-model">)(.*?)(</script>)',
                  html, re.S)
    if not m:
        print("ERROR: preview-model JSON island을 찾지 못함", file=sys.stderr)
        raise SystemExit(1)
    model = json.loads(m.group(2))
    _overlay_market_history(model, market_mode)
    model["news_rows"] = parts["news_rows"]
    model["ai_rows"] = parts["ai_rows"]
    model["lens_banks"] = parts["lens_banks"]
    model["immediate_status"] = parts.get("immediate_status") or {}
    # 중앙 정책(data/lens_queries.json)에서 lens_policy를 주입 — 생성 대시보드의 렌즈 정책·
    # 연동 상태(collection)가 단일 소스에서 나오게 한다(템플릿 정적 정책을 덮어씀).
    # 빈 상태 설명(emptyDescHtml)이 이 정책을 읽어 렌즈별 정직 안내를 만든다.
    policy = lens_queries.policy_for_model()
    if policy:
        model["lens_policy"] = policy
    if parts["metric_indices"]:
        model["metric_indices"] = parts["metric_indices"]
    meta = dict(model.get("meta") or {})
    meta["news_data_mode"] = news_mode
    meta["demo"] = news_mode != "live"
    meta["collection"] = "자동 수집 (live)" if news_mode == "live" else "수동 · 데모"
    # 생성/최신 기사 시각(D7-N) — brief.generated_at(실제 빌드 시각, KST)을 헤더에 노출한다.
    # 템플릿 정적 기본값(2026·06·22 …)을 더 이상 쓰지 않는다(가짜 날짜 제거). latest_article_kst는
    # 실제 표시 행의 최신 published_at에서 파생(없으면 빈 문자열). published_kst는 하위호환을 위해
    # 남기되 generated_kst와 동일하게 맞춰 stale 값을 제거한다.
    brief = brief or {}
    generated_kst = _fmt_kst_full(brief.get("generated_at"))
    all_rows = list(parts.get("news_rows") or []) + list(parts.get("ai_rows") or [])
    if parts.get("featured_row"):
        all_rows.append(parts["featured_row"])
    for bank in (parts.get("lens_banks") or {}).values():
        all_rows.extend(bank or [])
    latest_kst = _latest_article_kst(all_rows)
    if generated_kst:
        meta["generated_kst"] = generated_kst
        meta["published_kst"] = generated_kst  # 하위호환: stale 기본값 대신 생성 시각
    meta["latest_article_kst"] = latest_kst
    meta["date_kst"] = brief.get("date_kst") or meta.get("date_kst") or ""
    # D7-U: 시장 연동/미연동 분포 + AI 하이퍼스케일러 노출 진단 카운트를 meta에 싣는다.
    # (대시보드 렌더 JS와 검증기가 단일 소스에서 읽는다 — 빈 날에도 0을 명시한다.)
    meta.update(_market_link_counts(model.get("market_items") or []))
    meta["ai_hyper_count"] = parts.get("ai_hyper_count", 0)
    meta["ai_value_chain_pool_count"] = parts.get("ai_value_chain_pool_count", 0)
    model["meta"] = meta
    # 현장/조직 감시 트리(D7-N) — 비공개 워치리스트(비공개 워치리스트 경로)가 있을 때만 주입한다.
    # 공개 빌드는 None → 키 자체를 넣지 않는다(샘플도 노출 안 함). 매칭 노드만/전체 트리 여부는
    # site_watchlist 리프가 SITE_WATCHLIST_EXPOSE_TREE로 결정한다(빌더는 행만 넘긴다). freshness('!')
    # 기준 시각은 리포트 생성 시각(brief.generated_at) — '!'가 벽시계가 아니라 이 리포트 시점 기준으로
    # '최근(≤30일) 매칭'을 뜻하게 한다(없으면 현재 시각). 과거(>30일) 매칭은 '!' 대신 '과거 매칭'.
    tree = site_watchlist.tree_for_model(all_rows, now=brief.get("generated_at"))
    if tree is not None:
        model["site_watch_tree"] = tree
    counts = parts["bank_counts"]
    for grp in ("business_lens", "ecosystem"):
        for it in model.get(grp) or []:
            pol = policy.get(it.get("id"), {})
            if pol.get("collection") == "unconfigured" or pol.get("supported") is False:
                it["count"] = None  # 미연동 유지 (count=null → '미연동' 배지)
            else:
                it["count"] = counts.get(it.get("id"), 0)
    new_json = json.dumps(model, ensure_ascii=False, indent=2)
    return html[:m.start()] + m.group(1) + "\n" + new_json + "\n" + m.group(3) + html[m.end():]


def _update_nav_counts(html: str, counts: dict, policy: dict | None = None) -> str:
    """좌측 nav의 정적 카운트 배지를 실제 분포로 갱신.

    기존 템플릿/배포본에 대기 배지처럼 .ncount가 없는 우측 슬롯이 있어도,
    실제 bank count가 있는 렌즈는 생성 시점에 숫자 배지로 복구한다. configured query
    렌즈의 0건은 미연동이 아니라 '금일 없음'으로 표기한다.
    """
    policy = policy or {}
    out = []
    for line in html.split("\n"):
        mm = re.search(r'data-filter="([^"]+)"', line)
        if mm and mm.group(1) in counts:
            key = mm.group(1)
            value = counts[key]
            if 'class="ncount"' in line:
                line = re.sub(r'(<span class="ncount">)\d+(</span>)',
                              lambda x: f'{x.group(1)}{value}{x.group(2)}', line, count=1)
            elif value > 0:
                line = re.sub(r'<span class="r">.*?</span></button>',
                              f'<span class="r"><span class="ncount">{value}</span></span></button>',
                              line, count=1)
            else:
                pol = policy.get(key) or {}
                unconfigured = (pol.get("collection") == "unconfigured"
                                or pol.get("supported") is False)
                if not unconfigured:
                    line = re.sub(
                        r'<span class="r">.*?</span></button>',
                        '<span class="r"><span class="dm delay" style="font-size:9px;" '
                        'title="금일 공개뉴스 매칭 결과 없음">금일 없음</span></span></button>',
                        line,
                        count=1,
                    )
        out.append(line)
    return "\n".join(out)


def _update_section_counts(html: str, immediate_n: int, ai_n: int, index_n: int) -> str:
    html = html.replace(
        '<span class="t">A · 즉시 확인</span><span class="ln"></span><span class="c">3건</span>',
        f'<span class="t">A · 즉시 확인</span><span class="ln"></span><span class="c">{immediate_n}건</span>')
    html = html.replace(
        'AI 사업·운영 신호 · AI ONLY</span><span class="ln"></span><span class="c">8건</span>',
        f'AI 사업·운영 신호 · AI ONLY</span><span class="ln"></span><span class="c">{ai_n}건</span>')
    html = html.replace('신호 인덱스 · 전체 21건 (기본 접힘)',
                        f'신호 인덱스 · 전체 {index_n}건 (기본 접힘)')
    return html


def _update_honesty(html: str, news_mode: str) -> str:
    """live면 뉴스 렌즈노트 라벨을 '자동 수집 기사'로 정직 표기 (실기사 ≠ 데모).

    시장/AIS/초기신호 등 데모 미리보기 블록의 '데모 데이터' 라벨은 그대로 둔다(여전히 데모).
    """
    if news_mode == "live":
        html = html.replace(
            '<span class="previewflag" style="margin:0 0 0 8px;">데모 데이터</span>',
            '<span class="previewflag" style="margin:0 0 0 8px;">자동 수집 기사</span>')
    return html


def _update_header_dates(html: str, brief: dict) -> str:
    """헤더의 stale 정적 날짜(2026·06·22 …)를 실제 생성 시각(생성 KST)으로 치환한다(D7-N).

    라벨은 템플릿에서 이미 '생성 KST'로 바뀌어 있고, 값/보조 표기(최신 기사)는 JS가
    MODEL.meta(generated_kst/latest_article_kst)에서 채운다. 여기서는 생성 HTML 소스 자체에
    stale 리터럴이 남지 않도록 값 텍스트를 치환한다 — 가짜 날짜 생성 금지(파싱 실패 시 무변경).
    """
    generated_kst = _fmt_kst_full((brief or {}).get("generated_at"))
    if generated_kst:
        html = html.replace(
            '<div class="v num" id="pubKst">2026·06·22 월 07:00</div>',
            f'<div class="v num" id="pubKst">{escape(generated_kst)}</div>')
    return html


def render_dashboard_html(brief: dict, market_mode: str = "mock") -> str:
    """공유 brief를 standalone 요약 대시보드 HTML로 렌더 (실기사 데이터 주입).

    market_mode="live"이면 지원 종목의 기간 히스토리를 공개 시세(지연) 실측으로 교체한다
    (네트워크는 market_history leaf가 소유). 기본 mock은 결정적 데모 픽스처(네트워크 0건).
    """
    try:
        html = SOURCE_TEMPLATE.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: dashboard template missing: {SOURCE_TEMPLATE}", file=sys.stderr)
        raise SystemExit(1) from exc

    news_mode = brief.get("news_data_mode") or "mock"
    html = html.replace(
        "<title>HDEC Executive Radar — 대시보드 미리보기 (Preview)</title>",
        f"<title>{EXPORT_TITLE}</title>", 1)
    if EXPORT_MARKER not in html:
        html = html.replace(
            "<!DOCTYPE html>\n",
            "<!DOCTYPE html>\n"
            f"<!--{EXPORT_MARKER} source=templates/dashboard_preview.html "
            f"target={DEFAULT_OUTPUT}-->\n"
            # 보이지 않는 데이터 모드 마커 — CI/검증기가 live·mock을 결정적으로 판별한다.
            f"<!--news-data-mode:{news_mode}-->\n", 1)

    parts = _derive(brief)
    if parts["featured_sig"]:
        html = _inject_featured(html, _render_featured(parts["featured_sig"], parts["featured_row"]))
    html = _inject_model(html, parts, news_mode, market_mode, brief=brief)
    html = _update_header_dates(html, brief)
    html = _update_nav_counts(html, parts["nav_counts"], lens_queries.policy_for_model())
    html = _update_section_counts(html, parts["immediate_n"], len(parts["ai_rows"]),
                                  len(parts["news_rows"]) + len(parts["ai_rows"]))
    html = _update_honesty(html, news_mode)
    return html


def dashboard_metadata(html: str, brief: dict) -> dict:
    """기계 검증용 메타데이터 (HTML 전문은 싣지 않는다)."""
    parts = _derive(brief)
    return {
        "title": EXPORT_TITLE,
        "source_template": str(SOURCE_TEMPLATE.relative_to(ROOT)),
        "default_output": DEFAULT_OUTPUT,
        "html_chars": len(html),
        "has_export_marker": EXPORT_MARKER in html,
        "has_preview_model": 'id="preview-model"' in html,
        "has_data_honesty_labels": (
            "데모 데이터" in html
            and "현재 체결값 아님" in html
            and "미연동" in html
        ),
        "news_data_mode": brief.get("news_data_mode"),
        "news_row_count": len(parts["news_rows"]),
        "ai_row_count": len(parts["ai_rows"]),
        "lens_bank_count": sum(len(v or []) for v in parts["lens_banks"].values()),
        "featured_title": (parts["featured_sig"] or {}).get("title"),
        "uses_real_articles": bool(parts["news_rows"]) and bool(parts["featured_sig"]),
    }


def format_summary(html: str, brief: dict) -> str:
    meta = dashboard_metadata(html, brief)
    return "\n".join([
        "== HDEC Executive Radar — Summary Dashboard Export (real articles) ==",
        f"[source] {meta['source_template']}",
        f"[default_output] {meta['default_output']}",
        f"[news_data_mode] {meta['news_data_mode']} · news_rows={meta['news_row_count']} "
        f"· ai_rows={meta['ai_row_count']} · lens_bank_rows={meta['lens_bank_count']}",
        f"[featured] {meta['featured_title']}",
        f"[html_chars] {meta['html_chars']}",
        "[contract] 실기사 주입(brief 공유) · 시장/AIS 데모 라벨 유지 · 전체 리포트=latest.html",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HDEC Executive Radar — 정적 요약 대시보드 export 빌더 (실기사 데이터)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true",
                       help="export 요약을 출력한다 (파일 쓰기 없음)")
    group.add_argument("--json", action="store_true",
                       help="기계 검증용 메타데이터 JSON을 출력한다")
    group.add_argument("--output", metavar="PATH",
                       help=f"HTML 파일을 PATH에 생성한다 (예: {DEFAULT_OUTPUT})")
    parser.add_argument("--market-mode", choices=("mock", "live"), default="mock",
                        help="시장 기간 히스토리 출처: mock=결정적 데모 픽스처(기본, 네트워크 0건), "
                             "live=공개 시세(지연) 실측(market_history leaf가 네트워크 소유)")
    args = parser.parse_args(argv)

    brief = build_brief_via_mock_pipeline()
    html = render_dashboard_html(brief, market_mode=args.market_mode)

    if args.json:
        print(json.dumps(dashboard_metadata(html, brief), ensure_ascii=False, indent=2))
        return 0

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        print(f"dashboard written: {out_path} ({len(html)} chars) "
              f"news_data_mode={brief.get('news_data_mode')} market_mode={args.market_mode}")
        return 0

    print(format_summary(html, brief))
    if args.dry_run:
        print(f"[dry-run] html_chars={len(html)} (쓰기 없음)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
