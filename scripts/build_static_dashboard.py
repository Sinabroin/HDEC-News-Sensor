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
from app import ai_value_chain, lens_queries, news_access  # noqa: E402
# 시장 기간 히스토리 provider(leaf) — 네트워크는 이 leaf만 소유한다(빌더는 소켓/urllib/env를
# 직접 들이지 않는다). mock(기본)은 결정적 데모 픽스처, --market-mode live일 때만 공개 시세 실측.
from app import market_history  # noqa: E402
from app import news_recency  # noqa: E402
# 명일 정오 시공 리스크 provider(leaf, D7-AE) — 기상 네트워크는 이 leaf만 소유한다.
# mock(기본)은 unavailable(데모 기상값 생성 금지 — 시장과 달리 기상은 데모 픽스처도 없다),
# --weather-mode live일 때만 Open-Meteo 공개 예보 실측. 실패 시 '기상 데이터 미수신'.
from app import weather_risk  # noqa: E402
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
    "기회": ("기회", "#38684A", "green"),
    "리스크": ("리스크", "#A9433A", "red"),
    "관찰": ("관찰", "#3E5C80", "sky"),
}
# score_band → (tag 라벨, tag class)
_BAND_TAG = {
    "즉시 확인": ("즉시", "red"),
    "검토 필요": ("검토 필요", "amber"),
    "추적 필요": ("참고", "sky"),
}
# score_components 색 팔레트 (featured 신호 지수)
_COMP_PALETTE = ["#3E5C80", "#38684A", "#98A0AE", "#8F6A2E", "#3E5C80", "#38684A"]


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
        # D7-AB: 국가급 AI 인프라 투자 신호를 Executive Read 후보로 승격 — 점수 '다음'에 두어
        # (점수를 뒤집지 않는 boost) 동점·유사 점수대에서 앞세운다. mock 신호엔 boost 해당이
        # 없어(0) mock 정렬은 불변이다. 단독 통과가 아니라 relevant∧boost_combo일 때만 1.
        -int(lens_queries.national_ai_infra_boost(sig.get("title") or "")),
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
    # 호르무즈 relevance guard(D7-AA) — 직접 호르무즈/Strait of Hormuz 언급, 또는 (지정학 geo
    # 앵커 ∧ 해상/원유 risk 앵커)일 때만 hormuz 렌즈로 태깅한다. 단순 LNG·중동·유가·해운 단일
    # 키워드만으로 들어온 오태깅을 제거한다. raw 제목만 본다 — category_label("중동·해외 수주
    # 환경")이 geo 앵커를 오주입해 일반 유가/중동 기사가 호르무즈에 새는 함정을 피한다.
    if lens_queries.hormuz_relevant(raw_title):
        keys.add("hormuz")
    else:
        keys.discard("hormuz")
    # AI 렌즈(D7-L) — 섹션/카테고리(injected)로 'ai'가 붙어도 raw 제목에 직접 AI/데이터센터
    # 인프라 근거가 없으면 제외한다(안전/규제 기사의 category 오염 차단). 근거가 있을 때만 유지.
    if ("ai" in keys
            and not _has_ai_evidence(raw_title, raw_source, raw_snippet)
            and value_chain.get("ai_value_chain_layer") != ai_value_chain.LAYER_CUSTOM_CHIP):
        keys.discard("ai")
    # 사이트 워치리스트(D7-M/D7-AE) — raw 제목의 직접 언급 또는 사이트 쿼리 수집 provenance가
    # 있으면 그 항목의 scope(국내/해외현장·해외지사·해외법인)와 business 렌즈(토목/건축주택/
    # 플랜트/New Energy)를 더한다. 워치리스트가 권위 있는 분류라 generic discard 이후에 적용한다.
    for match in _site_matches_for(sig):
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


# D7-AD-X — 수집 provider 토큰 → 사람이 읽는 라벨. 결합 토큰(Google+Naver)과 mock/미상을 정직히
# 표기한다. providers=(none) 회귀(대시보드 모델이 provider provenance를 잃던 문제)를 막는다.
_PROVIDER_LABELS = {
    "google_news_rss": "Google News RSS",
    "naver_news_api": "Naver News API",
    "google_news_rss+naver_news_api": "Google+Naver",
    "naver_news_api+google_news_rss": "Google+Naver",
    "mock": "데모(mock) 데이터",
}


def _provider_label(token: str) -> str:
    token = (token or "").strip()
    if not token:
        return "수집 provenance 없음"
    return _PROVIDER_LABELS.get(token) or token


def _signal_source_metadata(sig: dict) -> dict:
    """signal의 source_metadata_json(문자열)/source_metadata(dict)를 dict로 정규화한다.

    허용 5키(provider/query/source_url/collected_at/provider_response_id)만 담기며 비밀값이
    없다 — 표시/감사 전용 provenance다. 파싱 실패 시 빈 dict.
    """
    smj = sig.get("source_metadata_json")
    if isinstance(smj, str) and smj.strip():
        try:
            parsed = json.loads(smj)
        except (TypeError, ValueError):
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(smj, dict):
        return smj
    meta = sig.get("source_metadata")
    return meta if isinstance(meta, dict) else {}


def _site_matches_for(sig) -> list:
    """워치리스트 현장 매칭 — 단일 판정점 (D7-AE, 신뢰도 계약 D7-AE-RC1).

    실사용 QA 실패: query provenance만으로 매칭을 인정하면 "파나마 메트로 3호선"에
    무관한 대구 모노레일 기사가, "코즐로두이 원전"에 무관한 미국 원전시장 기사가
    붙는다(둘 다 제목·스니펫에 현장명이 전혀 없고 쿼리로만 걸림). 이제 두 신뢰도만
    인정하고, 그 외엔 매칭을 아예 반환하지 않는다(낮은 신뢰도는 숨기지 않고 버린다):

    - **high** — 제목 또는 스니펫에 현장명/별칭 직접 포함(classify_site_lenses,
      ≥4자 부분문자열, 기존 규칙 불변 + 스니펫 확장).
    - **medium** — 사이트 쿼리(site:*) 수집 provenance(source_metadata.query가 그
      현장의 exact-phrase 쿼리와 일치) **그리고** 제목+스니펫에 그 현장의 식별 토큰
      (국가/도시/고유 프로젝트명 — corroboration_tokens, 업종어 제외)이 하나 이상
      있을 때만. 식별 토큰이 하나도 없으면 매칭을 버린다(query-only는 인정 안 함).

    반환 항목은 classify_site_lenses와 동일 shape + matched_via('title'|'snippet'|'query')
    + confidence('high'|'medium') + reasons(list[str]).
    """
    out = []
    seen = set()
    title = sig.get("title") or ""
    snippet = sig.get("snippet") or ""
    for m in site_watchlist.classify_site_lenses(title, snippet):
        entry = dict(m)
        entry["confidence"] = "high"
        entry["reasons"] = [f"{entry.get('matched_via')}_direct_mention"]
        seen.add(entry.get("id"))
        out.append(entry)
    query = str(_signal_source_metadata(sig).get("query") or "")
    item = site_watchlist.match_item_for_query(query)
    if item and item.get("id") not in seen:
        hit = site_watchlist.corroboration_hit(item, f"{title} {snippet}")
        if hit:
            out.append({
                "id": item.get("id") or "",
                "name": item.get("name") or "",
                "label": item.get("name") or "",
                "scope": item.get("scope") or "",
                "business_lens": item.get("business_lens") or "",
                "org_unit": item.get("org_unit") or "",
                "matched_via": "query",
                "confidence": "medium",
                "reasons": ["query_exact_phrase", f"context_token:{hit}"],
            })
        # else: query-only, no corroborating context → dropped (가짜 매칭 생성 없음)
    return out


def _news_provider_summary(all_rows: list, provider_status: dict | None) -> dict:
    """모델 row들의 provider 토큰 분포 + collector provider_status를 합쳐 요약을 만든다 (D7-AD-X).

    visible_count는 실제 대시보드에 노출된 고유 기사 기준(여러 surface에 중복 등장해도
    article_id/url로 1회만 센다). raw_count/status/dedup 카운트는 collector가 넘긴
    provider_status(있으면)에서 그대로 취한다. 비밀값 0건(자격증명은 유무 bool만).
    """
    seen: set = set()
    google = naver = both = unknown = 0
    for row in all_rows or []:
        key = row.get("article_id") or row.get("url") or row.get("title") or ""
        if not key or key in seen:
            continue
        seen.add(key)
        token = row.get("provider") or _signal_source_metadata(row).get("provider") or ""
        toks = {p for p in str(token).split("+") if p}
        has_g = "google_news_rss" in toks
        has_n = "naver_news_api" in toks
        if has_g and has_n:
            both += 1
        elif has_n:
            naver += 1
        elif has_g:
            google += 1
        else:
            unknown += 1
    status = provider_status if isinstance(provider_status, dict) else {}

    def _detail(key: str) -> dict:
        value = status.get(key)
        if isinstance(value, dict):
            return dict(value)
        return {"status": value} if value else {}

    return {
        "google_news_rss": {"visible_count": google, **_detail("google_news_rss")},
        "naver_news_api": {"visible_count": naver, **_detail("naver_news_api")},
        "both": {"visible_count": both},
        "unknown": {"visible_count": unknown},
    }


def _attach_provider_fields(row: dict) -> dict:
    """_row_from_signal을 거치지 않는 모델 row(카테고리 근거 기사 등)에도 provider provenance를
    부착한다 (D7-AD-X). source_metadata_json(허용 5키·비밀값 0)에서 파생하며, 이미 provider가
    있으면 건드리지 않는다 — 모든 surface의 row가 균일하게 provider 근거를 갖게 한다.
    """
    if not isinstance(row, dict) or row.get("provider"):
        return row
    meta = _signal_source_metadata(row)
    token = str(meta.get("provider") or "")
    row["provider"] = token
    row["collectionProvider"] = _provider_label(token)
    row.setdefault("collectionMethod", news_access.classify_collection_method(row))
    raw_url = row.get("url") or ""
    src_url = str(meta.get("source_url") or "")
    row.setdefault("aggregator_url", next(
        (u for u in (raw_url, src_url) if u and news_access.is_aggregator_url(u)), ""))
    row.setdefault("original_url", raw_url if _is_http(raw_url)
                   and not news_access.is_aggregator_url(raw_url)
                   and not news_access.detect_corp_warning_url(raw_url) else "")
    return row


def _row_from_signal(sig, extra_lens=()) -> dict:
    kind = sig.get("opportunity_or_risk") or "관찰"
    cat_label, cat_color, _kind_cls = _KIND.get(kind, ("관찰", "#3E5C80", "sky"))
    band = sig.get("score_band") or sig.get("alert_grade") or "추적 필요"
    tag, tag_class = _BAND_TAG.get(band, ("참고", "sky"))
    score = sig.get("final_score")
    score_str = "-" if score is None else f"{float(score):.1f}"
    if kind == "리스크":
        score_label, score_color = "리스크", "#A9433A"
    elif band == "즉시 확인":
        score_label, score_color = "즉시", "#A9433A"
    elif score is not None and float(score) >= 3.5:
        score_label, score_color = "중요", "#56637A"
    else:
        score_label, score_color = "관찰", "#56637A"
    spread = sig.get("spread") or {}
    related = (f"관련 {spread.get('related_count')}건"
               if spread.get("related_count") else "단독 신호")
    sources = (f"출처 {spread.get('source_count')}곳"
               if spread.get("source_count") else "출처 1곳")
    value_chain = ai_value_chain.classify_ai_value_chain(
        sig.get("title") or "", sig.get("source") or "", sig.get("snippet") or "")
    lens = sorted(set(_lens_for(sig)) | ({l for l in extra_lens} & VALID_LENS))
    access = news_access.classify_link_access(
        sig.get("url"),
        final_url=sig.get("final_url"),
        status_code=sig.get("link_status_code"),
        content_type=sig.get("link_content_type"),
        body_sample=sig.get("link_body_sample"),
    )
    collection_method = news_access.classify_collection_method(sig)
    # D7-AD-X — provider provenance(표시/감사 전용). DB source_metadata_json(허용 5키·비밀값 0)에서
    # 파생해 모델 row에 보존한다. 이 필드가 없으면 대시보드 모델의 provider 분포가 (none)으로
    # 소실된다(진단에서 관측된 회귀). 원문 href는 위 external_url이 담당하며 여기선 provenance만 싣는다.
    source_metadata = _signal_source_metadata(sig)
    provider_token = str(source_metadata.get("provider") or "")
    raw_url = sig.get("url") or ""
    src_url = str(source_metadata.get("source_url") or "")
    # aggregator_url: news.google.com/포털/검색 '경유' URL(있으면). url이 경유면 url을, 아니면
    # source_url이 경유면 그것을(merge가 원문으로 대체할 때 경유 URL을 source_url에 보존한다).
    aggregator_url = next(
        (u for u in (raw_url, src_url)
         if u and news_access.is_aggregator_url(u)), "")
    # original_url: url이 퍼블리셔 직링크(경유/차단 아님)일 때 그 값. 경유 URL이면 원문 직링크 없음.
    original_url = (raw_url if _is_http(raw_url)
                    and not news_access.is_aggregator_url(raw_url)
                    and not news_access.detect_corp_warning_url(raw_url) else "")
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
    # 사이트 워치리스트 매칭 provenance(D7-M/D7-N/D7-AE) — 제목이 프로젝트명을 직접 언급했거나
    # 그 현장명 사이트 쿼리로 수집된 행에만 출처를 단다(_site_matches_for 단일 판정점). 전체
    # 비공개 목록은 모델에 넣지 않는다(매칭된 항목만). 한 기사가 여러 항목과 매칭되면 matches
    # 리스트로 모두 담고, 첫 매칭을 display용 primary로 둔다(트리 노드의 alert_marker '!'를
    # 이 row→node 연결로 켠다 — D7-N site_watch_tree). matched_via로 근거(title/query)를 남긴다.
    site_matches = _site_matches_for(sig)
    if site_matches:
        m = site_matches[0]
        provenance["site_watch_match"] = True
        provenance["site_watch_id"] = m.get("id") or ""
        provenance["site_watch_label"] = m.get("label") or m.get("name") or ""
        provenance["site_watch_scope"] = m.get("scope") or ""
        provenance["site_watch_business_lens"] = m.get("business_lens") or ""
        provenance["site_watch_org_unit"] = m.get("org_unit") or ""
        provenance["site_watch_matched_via"] = m.get("matched_via") or "title"
        # D7-AE-RC1 — 신뢰도/근거(site_watch_match_confidence·_reasons). _site_matches_for가
        # 이미 threshold 미만(query-only, 무관 corroboration)은 걸러 반환하므로 여기 도달한
        # 항목은 전부 high/medium이다(low는 애초에 존재하지 않는다).
        provenance["site_watch_match_confidence"] = m.get("confidence") or "high"
        provenance["site_watch_match_reasons"] = list(m.get("reasons") or [])
        if sig.get("published_at"):
            provenance["site_watch_latest_published_at"] = sig.get("published_at")
        provenance["site_watch_matches"] = [
            {"id": x.get("id") or "", "label": x.get("label") or x.get("name") or "",
             "scope": x.get("scope") or "", "business_lens": x.get("business_lens") or "",
             "org_unit": x.get("org_unit") or "",
             "matched_via": x.get("matched_via") or "title",
             "confidence": x.get("confidence") or "high",
             "reasons": list(x.get("reasons") or [])}
            for x in site_matches
        ]
    return {
        "article_id": sig.get("article_id") or "",
        "tag": tag, "tagClass": tag_class,
        "title": sig.get("title") or "",
        "source": _better_source(sig),
        "time": _fmt_time(sig.get("published_at")),
        "published_at": sig.get("published_at") or "",
        "collected_at": sig.get("collected_at") or "",
        "snippet": sig.get("snippet") or "",
        "radarReason": sig.get("decision_reason") or sig.get("one_line_reason")
                       or sig.get("implication") or "",
        "whyImportant": sig.get("one_line_reason") or sig.get("implication") or "",
        "related": related, "sources": sources,
        "cat": cat_label, "catColor": cat_color,
        "score": score_str, "scoreLabel": score_label, "scoreColor": score_color,
        "lens": lens,
        "url": sig.get("url") if _is_http(sig.get("url")) else "",
        # 외부 '원문 사이트' href 전용 — 가장 원본에 가깝고 warning이 아닌 URL. url(원본
        # 수집값)/final_url(접근 진단)과 분리한다. warning URL이면 ""(링크 미생성).
        "external_url": news_access.choose_external_article_url(sig),
        "sourceDomain": access["source_domain"],
        "accessSourceType": access["source_type"],
        "collectionMethod": collection_method,
        # D7-AD-X — provider provenance(표시/감사 전용, 비밀값 0). provider 분포가 (none)으로
        # 소실되던 회귀를 막는다. Google News 경유 URL은 aggregator_url로, 퍼블리셔 원문은
        # original_url/external_url로 분리 노출한다(원문 href는 external_url이 담당).
        "provider": provider_token,
        "collectionProvider": _provider_label(provider_token),
        "source_metadata": source_metadata,
        "source_metadata_json": sig.get("source_metadata_json") or "",
        "aggregator_url": aggregator_url,
        "original_url": original_url,
        "canonical_url": sig.get("canonical_url") or "",
        "link_access_status": access["link_access_status"],
        "link_access_note": access["link_access_note"],
        "final_url": access["final_url"],
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


def _build_now_bank(brief: dict, new_keys: set, ref_date, base_pool: list,
                    news_mode: str = "mock") -> tuple[list, dict]:
    """Build the immediate lens honestly.

    Strict `top_immediate_signals` win. Only when no strict displayable rows exist do we
    expose real executive-review candidates, marked as candidates rather than alerts.
    live 모드에서는 72h 초과 stale 기사를 now 뱅크에서 제외(news_recency leaf).
    """
    ref_dt = None
    gen = (brief or {}).get("generated_at")
    if gen:
        try:
            ref_dt = datetime.fromisoformat(str(gen))
        except (TypeError, ValueError):
            ref_dt = None

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
    stale_filtered = 0
    for sig in selected:
        if len(rows) >= NOW_BANK_CAP:
            break
        if not news_recency.passes_immediate_recency(
                sig.get("published_at"), news_mode, ref_dt=ref_dt):
            stale_filtered += 1
            continue
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
        "stale_filtered_count": stale_filtered,
        "immediate_max_age_hours": news_recency.IMMEDIATE_MAX_AGE_HOURS,
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
    news_mode = brief.get("news_data_mode") or "mock"
    now_rows, immediate_status = _build_now_bank(
        brief, new_keys, ref_date, overall_pool, news_mode=news_mode)
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
              '<path d="M9 12l2 2 4-4" stroke="#3E5C80" stroke-width="2.2" stroke-linecap="round" '
              'stroke-linejoin="round"></path><circle cx="12" cy="12" r="9" stroke="#3E5C80" '
              'stroke-width="1.6"></circle></svg>신뢰 출처</span>')
_LINK_SVG = ('<svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M7 17L17 7M17 7H9M17 7v8" '
             'stroke="#3E5C80" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path></svg>')


def _render_featured(sig: dict, row: dict) -> str:
    """featured hero 카드를 실제 최상위 신호로 생성 (내부 reader + 보조 원문 링크)."""
    kind = sig.get("opportunity_or_risk") or "관찰"
    _cl, _cc, kind_cls = _KIND.get(kind, ("관찰", "#3E5C80", "sky"))
    chip_keys = [l for l in row["lens"] if l not in ("now", "new")][:3]
    chips = "".join(f'<span class="flchip">{escape(_LENS_LABEL.get(k, k))}</span>'
                    for k in chip_keys)
    trust = _TRUST_SVG if sig.get("source_quality") == "trusted" else ""
    # 외부 '원문 사이트' href는 warning URL을 배제한 원본 링크만 쓴다(진단 final_url 아님).
    url = news_access.choose_external_article_url(sig)
    article_id = row.get("article_id") or ""
    reader = (
        f'<button class="extlink article-reader-open" type="button" '
        f'data-article-id="{escape(article_id)}" '
        f'data-article-title="{escape(sig.get("title") or "")}" '
        'onclick="return openArticleReader(this);">기사 보기</button>'
    )
    if _is_http(url):
        source_link = (
            f'<a class="article-source-link" href="{escape(url)}" target="_blank" '
            f'rel="noopener noreferrer">원문 사이트 {_LINK_SVG}</a>'
        )
    else:
        source_link = '<span style="font-size:12px; color:var(--mute4);">원문 링크 없음</span>'
    access_status = row.get("link_access_status") or "unknown"
    access_label = {
        "ok": "원문 접근 가능",
        "corp_blocked": "원문 사이트 제한 가능",
        "redirected": "리다이렉트 감지",
        "timeout": "원문 확인 실패",
        "error": "원문 확인 실패",
    }.get(access_status, "접근 확인 전")
    access_badge = (
        f'<span class="access-badge {escape(access_status)}">{escape(access_label)}</span>'
    )
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
        f'{reader}{source_link}{access_badge}'
        f'<span style="font-size:12px; color:var(--mute4);">연계 {escape(sig.get("category_label") or "")}</span>'
        '</div>'
        '<span style="font-size:11.5px; color:var(--mute4);" class="num">자동 분류 · '
        f'{escape(sig.get("category") or "")}</span>'
        '</div></article>'
    )


_HORMUZ_CARD_MARKER = "<!-- Hormuz -->"
_HORMUZ_CARD_OPEN = '<div class="card hz"'


def _strip_hormuz_demo_card(html: str) -> str:
    """공개 산출물에서 호르무즈 AIS 데모 관찰 카드를 제거한다 (D7-AE-RC1).

    사용자 실사용 QA: "AIS 하한 추정 · proxy / 데모 데이터 / 212척 같은 값이 보이면
    실패다." 이 카드(선박 수·시간대별 통과·선종 분포·해협 모식도)는 전부 preview
    데모 고정값이고, 라이브 AIS 소스가 없다(저장소 전수 검색으로도 사용자가 언급한
    GitHub 연동 참조를 찾지 못함 — docs/operations/D7ADX_MARKET_SOURCE_INTEGRATION.md
    참고). 실 소스가 생기기 전까지 공개 화면에서는 숨긴다.

    templates/dashboard_preview.html(내부 비프로덕션 /dashboard-preview 라우트)은 이
    함수를 거치지 않아 원본 그대로 데모 카드를 유지한다 — 디자인 미리보기 목적은
    남아있고, 여기서 지우는 건 오직 공개 정적 산출물(docs/daily/dashboard-latest.html)
    뿐이다. 인터랙션 스크립트(#hzTimeSeg 기반 IIFE)는 `el("hzTimeSeg")`가 없으면
    즉시 return하도록 이미 가드돼 있어(byte-identical 유지 원칙상 스크립트는 건드리지
    않는다) 카드만 지워도 런타임 에러가 나지 않는다.
    """
    start = html.find(_HORMUZ_CARD_MARKER)
    if start < 0:
        print("WARNING: Hormuz demo card marker not found — nothing to strip "
              "(template may have changed; verify_hormuz_demo_removed.py will catch a leak)",
              file=sys.stderr)
        return html
    card_open = html.find(_HORMUZ_CARD_OPEN, start)
    if card_open < 0:
        print("WARNING: Hormuz demo card <div class=\"card hz\"> not found after marker",
              file=sys.stderr)
        return html
    # 열림/닫힘 <div> 깊이를 세어 카드의 진짜 닫는 태그를 찾는다(중첩 div 다수 포함).
    depth = 0
    tag_re = re.compile(r"<div\b|</div>")
    end = -1
    for m in tag_re.finditer(html, card_open):
        if m.group(0) == "</div>":
            depth -= 1
            if depth == 0:
                end = m.end()
                break
        else:
            depth += 1
    if end < 0:
        print("WARNING: Hormuz demo card closing tag not found (unbalanced divs?)",
              file=sys.stderr)
        return html
    result = html[:start] + html[end:]
    # 제거 이음매에 남는 공백줄(들여쓰기만 있던 blank line)을 좁게 정리한다 —
    # git diff --check가 trailing whitespace를 에러로 잡는다. 이음매 근처(±120자)만
    # 정규화해 문서 나머지(특히 인터랙션 스크립트 블록)의 byte-identity에는 영향 없다.
    seam_start = max(0, start - 40)
    seam_end = min(len(result), start + 80)
    seam = re.sub(r"(?:[ \t]*\n){2,}", "\n\n", result[seam_start:seam_end])
    return result[:seam_start] + seam + result[seam_end:]


_WX_GRADE_CLS = {"낮음": "wx-low", "주의": "wx-watch", "높음": "wx-high"}


def _wx_num(value, unit: str, digits: int = 0) -> str:
    if value is None:
        return ""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return ""
    if num != num or num in (float("inf"), float("-inf")):  # NaN/inf guard
        return ""
    return f"{num:.{digits}f}{unit}"


def _render_weather_rows_html(rows: list) -> tuple[str, list]:
    """templates/dashboard_preview.html의 renderSiteWeather() JS와 동일 규칙으로 행 HTML을
    만든다(서버=클라이언트 이중 렌더 — 한쪽이 바뀌면 둘 다 고친다는 뜻). 헤더 행은 그대로
    두고(정적) 데이터 행만 생성한다."""
    html_parts = []
    reason_lines = []
    for r in rows:
        off = r.get("row_status") != "ok"

        def cell(v: str) -> str:
            return ('<span class="wx-off">미수신</span>' if (off or not v)
                    else f'<span class="wx-val">{escape(v)}</span>')

        flags = r.get("flags") or []
        if flags:
            flags_html = f'<span class="wx-flag">{escape("·".join(flags))}</span>'
        elif off:
            flags_html = '<span class="wx-off">미수신</span>'
        else:
            flags_html = '<span class="wx-val">—</span>'
        cls = "wx-unknown" if off else _WX_GRADE_CLS.get(r.get("risk_grade"), "wx-unknown")
        risk_text = "확인 필요" if off else (r.get("risk_grade") or "")
        html_parts.append(
            '<div class="wx-row" role="row">'
            f'<span class="wx-rgn" title="{escape(r.get("basis") or "")} 대표점">'
            f'{escape(r.get("region") or "")}</span>'
            + cell(_wx_num(r.get("precip_prob"), "%")) + cell(_wx_num(r.get("precip_mm"), "mm", 1))
            + cell(_wx_num(r.get("wind_ms"), "m/s", 1)) + cell(_wx_num(r.get("gust_ms"), "m/s", 1))
            + flags_html
            + '<span class="wx-manual">수동 확인</span>'
            + f'<span class="wx-risk {cls}">{escape(risk_text)}</span></div>')
        if not off and r.get("risk_labels"):
            reason_lines.append(
                f'{escape(r.get("region") or "")}({escape(r.get("basis") or "")}) — '
                f'{escape(" · ".join(r["risk_labels"]))}')
    if reason_lines:
        html_parts.append(
            '<div class="wx-row"><span class="wx-reasons"><b>리스크 근거</b> · '
            + " / ".join(reason_lines) + "</span></div>")
    return "".join(html_parts), reason_lines


def _render_weather_section(html: str, weather: dict) -> str:
    """명일 정오 시공 리스크 표를 서버 사이드에서 채운다 (D7-AE-RC2).

    사용자 실사용 QA: weather_data_mode=live에 weather_rows도 있는데 '초기 화면'에는
    정적 '미연동' 표가 보였다 — renderSiteWeather() 자체는 정상 동작(헤드리스 실측 확인,
    콘솔 에러 0건)이지만, 이 표가 문서 후반부의 거대한 단일 <script> 블록이 전부 파싱된
    "이후"에만 JS로 교체되므로, 그 전까지의 첫 페인트(파일 크기 수백KB)에서는 정적
    placeholder가 그대로 보일 수 있다. 뉴스 featured 카드(_render_featured)와 동일하게
    빌드 시점에 실측값을 HTML에 직접 구워 넣어 JS 실행 여부와 무관하게 첫 화면부터
    정확하게 만든다. renderSiteWeather()는 그대로 둔다 — 같은 MODEL 값을 읽어 같은 결과로
    재렌더하므로 멱등하고(해가 없음), 향후 JS만 수정되는 경우에도 안전망 역할을 한다.
    """
    mode = weather.get("weather_data_mode") or ""
    rows = weather.get("weather_rows") or []
    if mode != "live" or not rows:
        if weather.get("weather_live_attempted"):
            html = html.replace(
                '<span class="wx-badge" id="wxBadge">기상 데이터 소스 미연동</span>',
                '<span class="wx-badge" id="wxBadge">기상 데이터 미수신</span>', 1)
            reason = weather.get("weather_unavailable_reason")
            if reason:
                html = html.replace(
                    '<div class="wx-note" id="wxNote">',
                    f'<div class="wx-note" id="wxNote"><div>{escape(reason)}</div>', 1)
        return html  # 정적 '미연동' 표 유지 — 가짜 값 없음(mock/미시도와 동일 규칙)

    rows_html, _reasons = _render_weather_rows_html(rows)
    grid_start = html.find('id="wxGrid">')
    if grid_start < 0:
        return html
    grid_open_end = grid_start + len('id="wxGrid">')
    depth = 0
    tag_re = re.compile(r"<div\b|</div>")
    inner_end = -1
    for tag_m in tag_re.finditer(html, grid_open_end):
        if tag_m.group(0) == "</div>":
            if depth == 0:
                inner_end = tag_m.start()
                break
            depth -= 1
        else:
            depth += 1
    if inner_end < 0:
        return html
    header_row_m = re.search(r'<div class="wx-row wx-gh".*?</div>', html[grid_open_end:inner_end], re.S)
    header_row = header_row_m.group(0) if header_row_m else ""
    html = html[:grid_open_end] + header_row + rows_html + html[inner_end:]

    badge_text = ("출처 " + escape(weather.get("weather_source") or "")
                  + " · 수집 " + escape(weather.get("weather_updated_at") or "") + " KST")
    html = re.sub(
        r'<span class="wx-badge" id="wxBadge">[^<]*</span>',
        f'<span class="wx-badge" id="wxBadge">{badge_text}</span>', html, count=1)
    html = html.replace(
        '<span class="wx-when" id="wxWhen">기준 · 명일(D+1) 정오 12:00</span>',
        '<span class="wx-when" id="wxWhen">기준 · 명일(D+1) 정오 12:00 · 권역 현지시각</span>', 1)
    return html


def _inject_featured(html: str, featured_html: str) -> str:
    if not featured_html:
        return html
    new, n = re.subn(r'<article class="card featured".*?</article>', lambda _m: featured_html,
                     html, count=1, flags=re.S)
    if n != 1:
        print("ERROR: featured 카드 블록을 찾지 못함 (템플릿 구조 변경?)", file=sys.stderr)
        raise SystemExit(1)
    return new


# D7-AD-N: 임원 아코디언 섹션 — brief.accordion_sections를 네이티브 <details>로 서버 렌더한다.
# JS 0줄(각 <details>는 브라우저 기본 동작으로 독립 개폐), 블록 흐름이라 펼쳐져도 다른 목록을
# 가리지 않는다. 기사 카드는 제목·출처·태그·본문 보기 링크만 노출하고 본문 전문은 싣지 않는다
# (rules.md §3). 빈 섹션도 헤딩과 '현재 수집된 항목 없음'을 정직히 유지한다(가짜 기사/날씨 없음).
_ACC_INJECT_MARKER = "<!-- ACCORDION-INJECT -->"


def _render_accordion_card(article: dict) -> str:
    title = escape(article.get("title") or "")
    tag = escape(article.get("section_tag") or article.get("category_label") or "")
    src = escape(article.get("display_source") or article.get("source") or "출처 미상")
    url = article.get("url") or ""
    open_link = ""
    if _is_http(url) and article.get("has_original_link"):
        open_link = (f'<a class="acc-open" href="{escape(url)}" target="_blank" '
                     'rel="noopener noreferrer">본문 보기 →</a>')
    tag_html = f'<span class="acc-tag">{tag}</span>' if tag else ""
    return (
        '<div class="acc-card">'
        f'<div class="t">{title}</div>'
        f'<div class="acc-meta">{tag_html}<span class="acc-src">{src}</span>{open_link}</div>'
        '</div>'
    )


def _render_accordion_section(section: dict) -> str:
    label = escape(section.get("label") or "")
    key = escape(section.get("key") or "")
    article_count = int(section.get("article_count") or 0)
    issue_count = int(section.get("issue_count") or 0)
    is_open = bool(section.get("default_open"))
    articles = section.get("articles") or []
    # 헤더 카운트: "N개 이슈 · 기사 M건" — 빈 섹션은 '기사 0건'으로 정직히 표기.
    # 단, 기상·날씨는 뉴스 수집 섹션이 아니라 데이터 소스 미연동 placeholder이므로 '기사 0건'을
    # 쓰지 않고 '데이터 소스 미연동'으로 표기한다(D7-AD-R · 실제 현장 기상은 시장 탭 '현장 기상' 카드).
    if article_count:
        count_txt = f"{issue_count}개 이슈 · 기사 {article_count}건"
    elif (section.get("key") or "") == "weather":
        count_txt = "데이터 소스 미연동"
    else:
        count_txt = "기사 0건"
    flag = '<span class="acc-flag">NEW</span>' if (is_open and article_count) else ""
    head = (
        '<summary>'
        f'<span class="acc-sl"><span class="acc-name">{label}</span>{flag}</span>'
        f'<span class="acc-count">{escape(count_txt)}</span>'
        '</summary>'
    )
    if articles:
        body = ('<div class="acc-list">'
                + "".join(_render_accordion_card(a) for a in articles) + '</div>')
        note = section.get("note") or ""
        if note:
            body += f'<div class="acc-note">{escape(note)}</div>'
    else:
        body = (f'<div class="acc-empty">'
                f'{escape(section.get("empty_message") or "현재 수집된 항목 없음")}</div>')
    open_attr = " open" if is_open else ""
    return (f'<details class="acc-sec" data-acc="{key}"{open_attr}>'
            f'{head}{body}</details>')


def _inject_accordion(html: str, brief: dict) -> str:
    """D7-AD-W: visible accordion 제거 — 분류 데이터는 preview-model.nav_category_sections로만 주입."""
    if _ACC_INJECT_MARKER in html:
        return html.replace(_ACC_INJECT_MARKER, "", 1)
    return html


def _fmt_market_value(value, decimals: int) -> str:
    """시세값을 천단위 구분 + 소수 자리로 포맷 (표시용 — 시세 자체는 바꾸지 않는다)."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if decimals <= 0:
        return f"{int(round(num)):,}"
    return f"{num:,.{decimals}f}"


_MARKET_AUTO_LIVE_MODES = {"live_market", "delayed_market", "live_macro", "delayed_macro"}


def _market_is_auto_live(it: dict) -> bool:
    """이 행이 메인 뷰 자격(자동 live/delayed)이 있는지 — 템플릿 JS isAutoLive와 동일 규칙.

    두 조건 모두 필요하다(D7-AE-RC1): (1) data_mode(설계상 분류)가 허용 목록 —
    proxy_market/manual_or_reported/unavailable은 값이 있어도 영구적으로 자격이 없다
    (대용 종목은 기간 히스토리가 live로 성공해도 여전히 대용). (2) history_data_mode가
    있다면 그것도 허용 목록 — 정적 data_mode는 안 바뀌므로, live 빌드에서 실측이 실패해
    데모로 대체된 행(예: cnykrw)을 잡아낸다.
    """
    if it.get("data_mode") not in _MARKET_AUTO_LIVE_MODES:
        return False
    hist_mode = it.get("history_data_mode")
    if hist_mode and hist_mode not in _MARKET_AUTO_LIVE_MODES:
        return False
    return True


def _market_link_counts(items: list) -> dict:
    """시장 지표 연동/미연동 분포 카운트(D7-U, D7-AE-RC1 갱신). 렌더 JS의 marketState/
    isAutoLive와 동일 규칙:

    · linked  = 값 보유 ∧ 자동 live/delayed(_market_is_auto_live). proxy/manual/미연동/
      데모대체는 값이 있어도 제외한다(메인 뷰에 안 보이므로 linked로 세면 안 된다).
    · unlinked= linked가 아닌 전부(값 없음 + proxy/manual/데모대체) → '연동 후보' 접힘으로.
    · chartless= linked 중 1개월 실데이터 히스토리(≥2점)가 없어 상세 차트가 없는 행.

    값을 만들어내지 않는다 — 미연동/대용/데모대체를 가짜로 linked로 올리지 않는다(정직성).
    """
    linked = unlinked = chartless = 0
    for it in items or []:
        v = it.get("value")
        has_value = v is not None and str(v).strip() not in ("", "—")
        auto_live = has_value and _market_is_auto_live(it)
        hist = it.get("history") if isinstance(it.get("history"), dict) else {}
        arr = (hist or {}).get("1m") or []
        chartable = len([x for x in arr if isinstance(x, (int, float))]) >= 2
        if auto_live:
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


def _market_has_chart(it: dict) -> bool:
    """market_item이 이미 기간 차트(1m ≥2 숫자점)를 갖는지 — JS isClickable과 동일 규칙."""
    arr = (it.get("history") or {}).get("1m") or []
    return len([x for x in arr if isinstance(x, (int, float))]) >= 2


def _apply_history_entry(it: dict, entry: dict) -> None:
    """market_history 엔트리(데모/실측)를 market_item에 부착하고 표시값/변동을 끝점과 일치시킨다.

    표시 현재값/변동을 히스토리 끝점과 맞춰 차트·값이 어긋나지 않게 한다('체결값 아님' 유지).
    엔트리가 자기 출처(history_data_mode: mock_demo | delayed_market)를 들고 있어 정직하다.
    """
    hist = entry.get("history") or {}
    if len(hist.get("1w") or []) < 1 or len(hist.get("1m") or []) < 2:
        return  # 불완전 엔트리는 부착하지 않는다(빈/단일점 차트 금지)
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


def _apply_point_quote(it: dict, value: float, decimals: int, *,
                       data_mode: str, source: str, as_of: str | None,
                       direction: str | None = None, source_id: str | None = None,
                       frequency: str | None = None,
                       proxy_note: str | None = None) -> None:
    """단일 관측값만 부착(기간 차트 없음) — FRED 등 leaf 실측. 가짜 history 생성 금지."""
    it["value"] = _fmt_market_value(value, decimals)
    it["data_mode"] = data_mode
    it["value_source"] = source
    if source_id:
        it["value_source_id"] = source_id
    if frequency:
        it["value_frequency"] = frequency
    if as_of:
        it["value_as_of"] = as_of
    d = direction or "flat"
    if d == "up":
        it["dir"], it["delta"] = "up", "▲"
    elif d == "down":
        it["dir"], it["delta"] = "down", "▼"
    else:
        it["dir"], it["delta"] = "flat", "—"
    if proxy_note:
        it["proxy"] = True
        it["proxy_note"] = proxy_note


def _overlay_fred_live_quotes(model: dict, market_mode: str) -> None:
    """live 모드에서 FRED 공개 CSV leaf로 보조 금리 등 단일값을 부착(D7-AD-X).

    네트워크는 app.fred_market leaf만 소유. 실패 시 None 유지(가짜 값 금지).
    """
    if (market_mode or "mock").strip().lower() != "live":
        return
    from app import fred_market  # noqa: E402 — leaf 격리

    snap = fred_market.fetch_quotes_by_id(use_cache=False)
    if not snap:
        return
    by_id = {it.get("id"): it for it in model.get("market_items") or []}
    for iid, q in (snap.get("quotes_by_id") or {}).items():
        it = by_id.get(iid)
        if it is None or q.get("value") is None:
            continue
        note = "FRED OECD 월간 장기금리 · 일간 국고채 10Y 아님" if iid == "kr_10y" else None
        # kr_10y는 월간 OECD 장기금리 '대용'이다 — delayed로 위장하지 않고 proxy_market으로
        # 라벨한다(D7-AE 감사 keep_proxy_with_caveat · verify_market_history_coverage 3b 정합).
        _apply_point_quote(
            it, float(q["value"]), 2,
            data_mode="proxy_market" if iid == "kr_10y" else "delayed_market",
            source=q.get("source") or snap.get("source") or "FRED (public CSV)",
            as_of=q.get("as_of"),
            direction=q.get("direction"),
            source_id=q.get("source_id"),
            frequency=q.get("frequency"),
            proxy_note=note,
        )


def _demote_unbacked_market_values(model: dict, market_mode: str) -> None:
    """live 빌드에서 '살아 있는 것처럼 보이는 정적 표시값'을 제거한다 (D7-AE 시장 소스 감사).

    템플릿 데모 유니버스에는 값은 있으나 어떤 live 소스(market_history 지연/데모 히스토리,
    FRED 단일값)도 붙지 않는 지표가 있다(아연·두바이유·JKM·SAR/QAR 환율·미 CPI/기준금리).
    mock(데모 미리보기)에서는 '데모 데이터' 라벨 아래 정당하지만, live 게시본에서는 갱신
    근거가 없는 정적 숫자가 현재값처럼 읽힌다(감사에서 실측 대비 큰 괴리 확인 — 예: CHF/KRW
    정적 1712 vs 실측 1919). live 모드에서 이런 행의 값을 제거해 '미연동 관찰 후보'로
    정직하게 강등한다:
      · delayed/proxy 라벨인데 소스가 없으면 → data_mode=unavailable(값 없음)
      · manual_or_reported는 라벨 유지 + 값만 제거(보고 대기 — cement와 동일 상태)
    값을 만들지 않는다 — 제거만 한다(가짜 현재가 방지 · docs/D7AE_MARKET_SOURCE_AUDIT.md).
    """
    if (market_mode or "mock").strip().lower() != "live":
        return
    for it in model.get("market_items") or []:
        has_value = (it.get("value") is not None
                     and str(it.get("value")).strip() not in ("", "—"))
        if not has_value:
            continue
        if it.get("history_data_mode") or it.get("value_source"):
            continue  # market_history(실측/데모 정직 라벨) 또는 FRED 실측이 붙은 행
        it["value"] = None
        it["dir"], it["delta"] = "flat", "—"
        it.pop("spark", None)
        it["static_value_demoted"] = True
        if it.get("data_mode") != "manual_or_reported":
            it["data_mode"] = "unavailable"


def _overlay_market_history(model: dict, market_mode: str) -> None:
    """지원 종목의 기간 히스토리를 부착한다 — 차트 없는 종목은 결정적 데모, live는 실측으로 교체.

    두 단계(둘 다 market_history leaf가 단일 출처):
      1) 데모 폴백(네트워크 0건) — 아직 기간 차트가 없는 지원 종목에 leaf의 **결정적** 데모
         픽스처(history_data_mode=mock_demo)를 채운다. D7-Z2로 추가된 종목(철근·환율 등)은
         템플릿에 데모가 없어 live 실패 시 빈 차트로 남았다 — 이 폴백이 그 회귀(D7-Z3 게이트)를
         막는다. 이미 차트가 있는 종목(템플릿 데모)은 건드리지 않아 표시값/변동을 보존한다.
      2) live 실측(market_mode=="live"일 때만) — 공개 시세(지연) 1년 히스토리를 받아 **성공분만**
         (LIVE_MODE) 덮어쓴다. 실패 심볼은 1)의 데모로 정직하게 남는다 — live로 위장하지 않는다.

    mock(기본)은 1)만 수행하고 네트워크는 0건이다. 가짜 live 값을 만들지 않는다 — 데모는
    mock_demo로 정직 표기되고 '현재 체결값 아님'을 유지한다.
    """
    by_id = {it.get("id"): it for it in model.get("market_items") or []}

    # 1) 데모 폴백 — history_for_model("mock")은 leaf 결정적 데모만 반환(네트워크 미접근).
    #    차트가 없는 지원 종목만 채운다(이미 있는 종목의 값/변동은 보존).
    for item_id, entry in market_history.history_for_model(mode="mock").items():
        it = by_id.get(item_id)
        if it is not None and not _market_has_chart(it):
            _apply_history_entry(it, entry)

    # mock(기본)에서는 데모 픽스처까지만 — 네트워크 0건.
    if (market_mode or "mock").strip().lower() != "live":
        return

    # 2) live 실측으로 성공분만 덮어쓴다(실패분은 1)의 데모 유지 = 정직).
    for item_id, entry in market_history.history_for_model(mode="live").items():
        if entry.get("history_data_mode") != market_history.LIVE_MODE:
            continue  # 실측 성공분(delayed_market)만 교체
        it = by_id.get(item_id)
        if it is not None:
            _apply_history_entry(it, entry)


def _sync_site_scope_lens_banks_to_tree(model: dict, tree: dict | None, candidate_rows: list[dict]) -> None:
    """D7-AD-Z: site scope article lists must mean actual site-watch matches.

    Before D7-AD-Z, domestic_site/overseas_site lens banks could contain generic
    "건설현장/해외 프로젝트" articles even when no individual watchlist site matched.
    Now the public site watchlist is visible, so the 1차 site scope article list must
    be derived only from site_watch_tree node.article_keys. If every node has 0
    article_keys, the scope article list is empty.
    """
    if not isinstance(model, dict) or not isinstance(tree, dict):
        return
    banks = model.setdefault("lens_banks", {})
    if not isinstance(banks, dict):
        return

    rows_by_key = {}
    for row in candidate_rows or []:
        if not isinstance(row, dict):
            continue
        for key in (row.get("url"), row.get("title")):
            if key and key not in rows_by_key:
                rows_by_key[key] = row

    for scope in getattr(site_watchlist, "SCOPES", ()):
        keys = []
        sc = (tree.get("by_scope") or {}).get(scope) or {}
        for group in sc.get("groups") or []:
            for node in group.get("nodes") or []:
                keys.extend(node.get("article_keys") or [])

        seen = set()
        rows = []
        for key in keys:
            row = rows_by_key.get(key)
            if not row:
                continue
            rk = row.get("url") or row.get("title") or key
            if rk in seen:
                continue
            seen.add(rk)
            rows.append(row)
        banks[scope] = rows

def _inject_model(html: str, parts: dict, news_mode: str, market_mode: str = "mock",
                  brief: dict | None = None, operator_api_base: str = "",
                  weather_mode: str = "mock") -> str:
    m = re.search(r'(<script type="application/json" id="preview-model">)(.*?)(</script>)',
                  html, re.S)
    if not m:
        print("ERROR: preview-model JSON island을 찾지 못함", file=sys.stderr)
        raise SystemExit(1)
    model = json.loads(m.group(2))
    # D7-AA — 운영자 버튼의 운영 API base URL(공개값)을 island에 주입한다. 빈 값이면 프론트는
    # 버튼을 비활성 + 미설정 안내만 한다(GitHub 이동 없음). 빌더 소스는 env를 직접 읽지 않고
    # CLI(--operator-api-base)로만 받는다(verify_dashboard_real_data 1d 계약).
    model["operator_api_base"] = (operator_api_base or "").strip()
    _overlay_market_history(model, market_mode)
    _overlay_fred_live_quotes(model, market_mode)
    # 시장 소스 감사(D7-AE) — live 게시본에서 소스 없는 정적 표시값을 미연동으로 강등한다.
    # 반드시 두 overlay 뒤·_market_link_counts(meta) 앞에서 호출한다(카운트 일관성).
    _demote_unbacked_market_values(model, market_mode)
    # 명일 정오 시공 리스크(D7-AE) — weather_risk leaf가 모드/네트워크/실패 판정을 소유한다.
    # mock=unavailable(값 0건), live=Open-Meteo 실측 또는 '기상 데이터 미수신'. 빌더는 env를
    # 읽지 않고 CLI(--weather-mode)로만 받는다. now=brief.generated_at으로 D+1 판정 결정화.
    model.update(weather_risk.snapshot_for_model(
        mode=weather_mode, now=(brief or {}).get("generated_at")))
    model["news_rows"] = parts["news_rows"]
    model["ai_rows"] = parts["ai_rows"]
    model["lens_banks"] = parts["lens_banks"]
    model["featured_row"] = parts.get("featured_row")
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
    # 보안팀 점검용 domain inventory. 관측된 메타데이터만 집계하며 외부 접속은 하지 않는다.
    # 접근 상태는 표시 전용이고 row 선별/점수/렌즈에는 사용되지 않는다.
    model["news_source_inventory"] = news_access.build_source_inventory(all_rows)
    # D7-AD-X — provider provenance 요약(표시/감사 전용). provider 분포가 (none 70)으로 소실되던
    # 회귀를 막고, collector가 넘긴 provider_status(Google RSS + Naver API의 raw/dedup 카운트)를
    # 함께 노출한다 — Naver가 '보조 코드'가 아니라 명시적 1급 provider임을 모델에 기록한다(비밀값 0).
    model["news_provider_summary"] = _news_provider_summary(
        all_rows, brief.get("news_provider_status"))
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
        _sync_site_scope_lens_banks_to_tree(model, tree, all_rows)
    # D7-AD-P — 이름 비노출 scope 집계(좌측 '실행 범위' 네비 카운트). tree(이름 포함·비공개 전용)와
    # 달리 카운트만 담아 공개 빌드에도 안전하게 주입한다(현장명 0건 · verifier가 잠금). 비공개 게이팅은
    # 리프(site_watchlist)가 환경변수로 담당한다 — 빌더는 환경변수를 직접 읽지 않는다(D7-N 4d 보존).
    model["site_scope_summary"] = site_watchlist.scope_summary_for_model()
    # D7-AD-W — 좌측 navcat flat list용 분류 데이터(기상·날씨 제외). visible accordion 대신
    # preview-model에서 JS가 #categoryNewsList를 렌더한다(가짜 기사 생성 없음).
    sections = brief.get("accordion_sections") or []
    nav_sections = [s for s in sections if (s.get("key") or "") != "weather"]
    # D7-AD-X — 카테고리 근거 기사도 provider provenance를 갖게 한다(_row_from_signal 미경유 surface).
    for section in nav_sections:
        for article in section.get("articles") or []:
            _attach_provider_fields(article)
    model["nav_category_sections"] = nav_sections
    # D7-AA — 부가 관찰(SUPPORTING LENSES: business_lens/ecosystem/watch_next) 섹션은 제거됨.
    # 이전의 그룹별 count 주입 루프도 함께 제거한다(템플릿 모델에 더 이상 해당 키가 없음).
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

    D7-AE: 헤더 상태 stat 2개도 함께 정직화한다 — live 게시본이 본문은 실기사인데
    헤더가 '데모 mock'/'수동 · OFF'로 남아 서로 모순되던 표기를 수정한다(표기만 교체 ·
    데이터 생성 없음). mock 빌드는 기존 문구를 그대로 둔다(정직한 데모 표기).
    """
    if news_mode == "live":
        html = html.replace(
            '<span class="previewflag" style="margin:0 0 0 8px;">데모 데이터</span>',
            '<span class="previewflag" style="margin:0 0 0 8px;">자동 수집 기사</span>')
        html = html.replace(
            '<span class="dot" style="background:#C79248;"></span>\n'
            '        <div><div class="k">뉴스 데이터</div><div class="v">데모 mock</div></div>',
            '<span class="dot" style="background:#38684A;"></span>\n'
            '        <div><div class="k">뉴스 데이터</div><div class="v">자동 수집 (live)</div></div>')
        html = html.replace(
            '<div><div class="k">수집 상태</div><div class="v">수동 · OFF</div></div>',
            '<div><div class="k">수집 상태</div><div class="v">공개 RSS · 지연 시세</div></div>')
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


def render_dashboard_html(brief: dict, market_mode: str = "mock",
                          operator_api_base: str = "",
                          weather_mode: str = "mock") -> str:
    """공유 brief를 standalone 요약 대시보드 HTML로 렌더 (실기사 데이터 주입).

    market_mode="live"이면 지원 종목의 기간 히스토리를 공개 시세(지연) 실측으로 교체한다
    (네트워크는 market_history leaf가 소유). 기본 mock은 결정적 데모 픽스처(네트워크 0건).
    weather_mode="live"이면 명일 정오 시공 리스크를 Open-Meteo 실측으로 채운다(weather_risk
    leaf 소유 · 실패 시 '기상 데이터 미수신' — 가짜 값 0건). 기본 mock은 unavailable.
    operator_api_base는 운영자 버튼이 호출할 공개 API base URL(비밀값 아님 · 빈 값=미설정).
    """
    try:
        html = SOURCE_TEMPLATE.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: dashboard template missing: {SOURCE_TEMPLATE}", file=sys.stderr)
        raise SystemExit(1) from exc

    # D7-AE-RC1 — 공개 정적 산출물에서만 호르무즈 AIS 데모 카드를 제거한다(실 소스 없음).
    html = _strip_hormuz_demo_card(html)

    news_mode = brief.get("news_data_mode") or "mock"
    html = html.replace(
        "<title>HDEC Executive Radar — 대시보드 미리보기 (Preview)</title>",
        f"<title>{EXPORT_TITLE}</title>", 1)
    # 운영본 브랜드 부제를 '요약 대시보드'로 정렬한다 — 이메일 '요약 대시보드 보기' CTA의
    # 목적지 역할이 화면에서 분명히 보이게 하고, 비프로덕션 'PREVIEW' 표기를 운영본에서 제거.
    # D7-AE-RC1: 헤더 문구를 "현대건설 임원용 신호 브리프"로 명확히 정리(사용자 지정 문구).
    html = html.replace(
        "현대건설 임원용 신호 브리프 · 대시보드 미리보기 (PREVIEW)",
        "현대건설 임원용 신호 브리프 · 요약 대시보드", 1)
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
    html = _inject_model(html, parts, news_mode, market_mode, brief=brief,
                         operator_api_base=operator_api_base,
                         weather_mode=weather_mode)
    # D7-AE-RC2 — JSON island에 이미 주입된 weather_* 값을 그대로 되읽어(재요청/재조회 없음)
    # wxGrid/wxBadge를 서버에서 채운다 — JS(renderSiteWeather)와 동일 소스, 동일 결과.
    model_m = re.search(r'<script type="application/json" id="preview-model">(.*?)</script>',
                        html, re.S)
    if model_m:
        weather_snapshot = {k: v for k, v in json.loads(model_m.group(1)).items()
                            if k.startswith("weather_")}
        html = _render_weather_section(html, weather_snapshot)
    html = _update_header_dates(html, brief)
    html = _update_nav_counts(html, parts["nav_counts"], lens_queries.policy_for_model())
    html = _update_section_counts(html, parts["immediate_n"], len(parts["ai_rows"]),
                                  len(parts["news_rows"]) + len(parts["ai_rows"]))
    html = _update_honesty(html, news_mode)
    html = _inject_accordion(html, brief)
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
    parser.add_argument("--weather-mode", choices=("mock", "live"), default="mock",
                        help="명일 정오 시공 리스크 출처: mock=미연동 상태 유지(기본, 네트워크 0건 · "
                             "데모 기상값 생성 금지), live=Open-Meteo 공개 예보 실측(weather_risk "
                             "leaf가 네트워크 소유 · 실패 시 '기상 데이터 미수신')")
    parser.add_argument("--operator-api-base", metavar="URL", default="",
                        help="운영자 버튼이 호출할 공개 Operator API base URL(비밀값 아님). "
                             "미지정(기본)이면 버튼은 비활성 + '운영 API 미설정' 안내만 표시한다"
                             "(GitHub 이동 없음). 빌더는 env를 직접 읽지 않고 이 CLI로만 받는다.")
    args = parser.parse_args(argv)

    brief = build_brief_via_mock_pipeline()
    # D7-AE-RC2 — market/weather는 --live인데 뉴스는 (NEWS_MODE=live를 안 잊고 켜지 않아)
    # mock으로 수집됐는지 확인한다. RC1의 마지막 수동 rebuild가 정확히 이 조합으로
    # news_data_mode를 조용히 mock으로 되돌렸다(공개 헤더가 실제 상태와 모순되는 결과가
    # 됨). brief는 이미 build_brief_via_mock_pipeline()이 만든 값이라 빌더가 env를 직접
    # 읽지 않는다(verify_dashboard_real_data 1d 계약 준수) — 막지는 않는다(뉴스는 mock,
    # 시세/기상만 live로 테스트하는 것도 유효한 조합이라) 눈에 띄게 경고만 한다.
    if (args.market_mode == "live" or args.weather_mode == "live") \
            and brief.get("news_data_mode") != "live":
        print("WARNING: --market-mode/--weather-mode=live인데 뉴스는 mock으로 수집됨"
              "(NEWS_MODE=live 환경변수가 설정되지 않은 것으로 보임) — 헤더는 정직하게 "
              "'데모 mock'으로 남습니다. 공개 라이브 산출물을 원하면 NEWS_MODE=live를 "
              "함께 설정하세요.", file=sys.stderr)
    html = render_dashboard_html(brief, market_mode=args.market_mode,
                                 operator_api_base=args.operator_api_base,
                                 weather_mode=args.weather_mode)

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
