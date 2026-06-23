"""Topic Profiles 도메인 (P0-D5-A) — 구성 가능한 토픽 센싱 프로파일.

하드코딩된 뉴스 센싱을 "설정 가능한 토픽 프로파일"로 진화시키는 첫 단계다. 각 프로파일은
하나의 관심 주제(현대건설 직접 / 현대 그룹사 / 경쟁 시공사 / 신탁사 / 시행사)를 정의하고,
그 주제를 (1) live 검색에서 어떤 쿼리로 수집할지, (2) 수집된 기사를 어떤 규칙으로 그 주제에
속한다고 판정할지를 함께 담는다.

경계 원칙 (CLAUDE.md §4):
- 이 파일은 **순수 설정 + 결정적 매칭**만 한다. 네트워크/DB/LLM/외부 API를 절대 호출하지 않는다.
- 점수/등급/insight를 만들지 않는다 — 그것은 scoring/insight 도메인 소유다.
- 매칭은 설명 가능해야 한다(어떤 include/anchor가 맞았는지 돌려줄 수 있다). 제목/스니펫/출처
  텍스트의 결정적 부분문자열 매칭만 쓴다.
- live 검색은 이 모듈의 쿼리를 읽어 수집 그룹을 만든다(app/live_collector.py). briefing은
  match_topic_profile로 저장된 기사를 주제 섹션으로 파생한다(표시 전용, 재계산 없음).

판정 규칙 (한 줄): `include_keywords 중 하나 ∧ relevance_anchors 중 하나 ∧ exclude_keywords 0개`.
- include = 주체(엔티티) 매칭 — 이 기사가 그 회사/주제군에 대한 것인지.
- anchor  = 도메인 관련성 — 임원이 볼 만한 건설/수주/리스크/기술 맥락이 있는지.
- exclude = 노이즈 컷 — 채용·견학·ETF·주가·스포츠·신차 등은 엔티티가 맞아도 버린다.
anchor를 필수로 두기 때문에 "현대차 판매량 1위" 같은 generic Hyundai 기사는 anchor가 없어
프로파일에 들어오지 않는다(엔티티만으로는 부족).
"""

import unicodedata
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TopicProfile:
    """하나의 토픽 센싱 프로파일 (설정 단위)."""

    id: str
    label: str                       # 임원용 한국어 라벨 (기술 식별자 비노출)
    description: str                 # 사람용 목적 설명
    enabled: bool                    # 비활성 프로파일은 수집/분류에서 제외
    queries: tuple[str, ...]         # live 검색 수집 쿼리
    include_keywords: tuple[str, ...]   # 주체(엔티티) — 하나라도 맞아야 함
    relevance_anchors: tuple[str, ...]  # 도메인 관련성 — 하나라도 맞아야 함
    exclude_keywords: tuple[str, ...]   # 노이즈 — 하나라도 맞으면 즉시 제외
    max_items: int                   # 섹션/수집 그룹 상한 (1~10)
    surface_key: str                 # briefing JSON / 리포트 섹션 키
    priority: int = 5                # 낮을수록 우선 (정렬/수집 순서)
    weight: float = 1.0              # 선택적 가중치 (Day-2 점수 연동 예약)


# ---------------------------------------------------------------------------
# 5개 초기 프로파일 (P0-D5-A). 쿼리/키워드는 광범위 노이즈가 아니라 현대건설 임원의
# 의사결정 관련 커버리지(직접/그룹사/경쟁사/신탁/시행)에 집중한다.
# ---------------------------------------------------------------------------

_HDEC_DIRECT = TopicProfile(
    id="hdec_direct",
    label="현대건설 직접",
    description=("현대건설 자체 보도·리스크·수주·기술·도시정비·해외사업·원전·데이터센터를 "
                 "직접 감지한다."),
    enabled=True,
    queries=(
        "현대건설",
        "현대건설 벌점",
        "현대건설 하자",
        "현대건설 철근누락",
        "현대건설 공기지연",
        "현대건설 안전",
        "현대건설 논란",
        "현대건설 단독",
        "현대건설 기획",
        "현대건설 도시정비",
        "현대건설 데이터센터",
        "현대건설 원전",
        "현대건설 해외사업",
    ),
    include_keywords=("현대건설", "힐스테이트", "디에이치", "HDEC"),
    relevance_anchors=(
        "벌점", "하자", "철근누락", "공기지연", "안전", "중대재해", "논란", "공방",
        "소송", "수주", "도시정비", "재건축", "재개발", "데이터센터", "원전", "SMR",
        "해외사업", "EPC", "품질", "ESG",
    ),
    exclude_keywords=("채용", "견학", "진로", "이벤트", "광고모델", "ETF", "주가만", "사세요"),
    max_items=5,
    surface_key="hdec_direct_signals",
    priority=1,
)

_HYUNDAI_GROUP = TopicProfile(
    id="hyundai_group",
    label="현대 그룹사",
    description=("현대 계열·범현대 이슈 중 현대건설 임원이 볼 만한 인프라·건설·에너지·"
                 "공급망 연결 신호를 감지한다."),
    enabled=True,
    queries=(
        "현대엔지니어링 데이터센터",
        "현대엔지니어링 플랜트",
        "현대엔지니어링 특허",
        "HD현대일렉트릭 현대건설",
        "현대일렉트릭 현대건설",
        "현대제철 건설 철강",
        "현대차그룹 건설 인프라",
        "현대로템 인프라",
        "현대글로비스 건설 물류",
        "현대오토에버 스마트건설",
        "현대모비스 로봇 건설",
    ),
    include_keywords=(
        "현대엔지니어링", "현대ENG", "현대차그룹", "현대자동차그룹", "현대차", "현대모비스",
        "현대글로비스", "현대로템", "HD현대", "HD현대일렉트릭", "현대일렉트릭", "현대제철",
        "현대위아", "현대오토에버",
    ),
    relevance_anchors=(
        "건설", "EPC", "인프라", "원전", "SMR", "전력", "에너지", "플랜트", "데이터센터",
        "스마트건설", "로봇", "설비", "품질", "안전", "ESG", "공급망", "철강", "전력기기",
        "수주", "해외사업", "특허", "기술", "투자", "정책",
    ),
    exclude_keywords=(
        "신차 출시", "자동차 판매량", "스포츠", "야구", "축구", "연예", "광고모델",
        "단순 사회공헌", "채용", "견학", "진로", "ETF", "주가", "사세요",
    ),
    max_items=5,
    surface_key="hyundai_group_signals",
    priority=2,
)

_COMPETITOR_CONTRACTORS = TopicProfile(
    id="competitor_contractors",
    label="경쟁 시공사",
    description=("주요 경쟁 시공사의 수주·리스크·데이터센터·원전·해외사업·안전/품질 이슈를 "
                 "감지한다."),
    enabled=True,
    queries=(
        "삼성물산 건설 수주",
        "삼성물산 데이터센터 EPC",
        "GS건설 데이터센터",
        "GS건설 안전 품질",
        "DL이앤씨 수주 데이터센터",
        "대우건설 데이터센터",
        "대우건설 원전",
        "포스코이앤씨 안전",
        "포스코이앤씨 압수수색",
        "롯데건설 PF",
        "SK에코플랜트 데이터센터",
        "HDC현대산업개발 안전",
        "건설사 데이터센터 수주",
        "건설사 벌점 안전 품질",
    ),
    include_keywords=(
        "삼성물산", "GS건설", "DL이앤씨", "대우건설", "포스코이앤씨", "롯데건설",
        "SK에코플랜트", "HDC현대산업개발", "호반건설", "중흥건설", "태영건설", "계룡건설",
        "한화 건설", "한화 건설부문",
    ),
    relevance_anchors=(
        "수주", "계약", "EPC", "데이터센터", "원전", "SMR", "해외사업", "플랜트",
        "도시정비", "재건축", "재개발", "PF", "안전", "중대재해", "벌점", "하자", "품질",
        "압수수색", "특별감독", "소송", "리스크", "공급망", "전력", "냉각",
    ),
    exclude_keywords=("채용", "견학", "진로", "광고", "분양 홍보만", "ETF", "주가만", "사세요"),
    max_items=5,
    surface_key="competitor_contractor_signals",
    priority=3,
)

_TRUST_COMPANIES = TopicProfile(
    id="trust_companies",
    label="신탁사",
    description=("부동산 신탁사·정비사업·PF·책임준공·사업관리·시행/시공 리스크 연결 신호를 "
                 "감지한다."),
    enabled=True,
    queries=(
        "한국토지신탁 정비사업",
        "한국자산신탁 정비사업",
        "대한토지신탁 정비사업",
        "KB부동산신탁 정비사업",
        "신한자산신탁 정비사업",
        "하나자산신탁 정비사업",
        "코람코자산신탁 개발사업",
        "교보자산신탁 책임준공",
        "우리자산신탁 PF",
        "대신자산신탁 부동산 개발",
        "신탁사 책임준공 리스크",
        "신탁사 정비사업 수주",
        "부동산신탁 PF 리스크",
    ),
    include_keywords=(
        "한국토지신탁", "한국자산신탁", "대한토지신탁", "KB부동산신탁", "신한자산신탁",
        "하나자산신탁", "코람코자산신탁", "교보자산신탁", "우리자산신탁", "대신자산신탁",
        "무궁화신탁", "신영부동산신탁", "부동산신탁", "신탁사", "책임준공",
        "관리형 토지신탁", "차입형 토지신탁",
    ),
    relevance_anchors=(
        "정비사업", "재건축", "재개발", "도시정비", "PF", "책임준공", "사업관리", "분양",
        "인허가", "시공사", "시행사", "수주", "계약", "리스크", "부실", "연체", "채무",
        "보증", "공사비", "조합", "개발사업",
    ),
    exclude_keywords=("단순 인사", "채용", "이벤트", "사회공헌", "주가만", "ETF", "광고"),
    max_items=5,
    surface_key="trust_company_signals",
    priority=4,
)

_DEVELOPERS = TopicProfile(
    id="developers",
    label="시행사",
    description=("시행사/디벨로퍼·PF·분양·인허가·토지확보·개발사업·시공사 선정 관련 신호를 "
                 "감지한다."),
    enabled=True,
    queries=(
        "시행사 PF 리스크",
        "시행사 분양 리스크",
        "디벨로퍼 개발사업 PF",
        "엠디엠 개발사업",
        "신영 개발사업",
        "피데스개발 분양",
        "DS네트웍스 개발사업",
        "시행사 시공사 선정",
        "시행사 책임준공",
        "부동산 개발사업 인허가",
        "도시개발사업 시행사",
        "민간참여 공공주택 시행사",
    ),
    include_keywords=(
        "시행사", "디벨로퍼", "개발사", "엠디엠", "MDM", "신영", "피데스개발", "DS네트웍스",
        "더랜드", "알비디케이", "마스턴", "이지스자산운용", "코람코", "시행", "개발사업",
        "도시개발사업", "민간참여", "공공주택",
    ),
    relevance_anchors=(
        "PF", "브릿지론", "본PF", "분양", "미분양", "인허가", "토지확보", "토지매입",
        "시공사 선정", "책임준공", "공사비", "착공", "준공", "조합", "도시개발", "개발사업",
        "수주", "계약", "리스크", "부실", "연체", "보증", "사업성",
    ),
    exclude_keywords=("채용", "이벤트", "사회공헌", "광고", "단순 브랜드 홍보", "ETF", "주가만"),
    max_items=5,
    surface_key="developer_signals",
    priority=5,
)

# 등록 순서 = priority 순서. get_*는 enabled만 노출한다.
_PROFILES: tuple[TopicProfile, ...] = (
    _HDEC_DIRECT,
    _HYUNDAI_GROUP,
    _COMPETITOR_CONTRACTORS,
    _TRUST_COMPANIES,
    _DEVELOPERS,
)


# ---------------------------------------------------------------------------
# 조회 헬퍼
# ---------------------------------------------------------------------------

def all_topic_profiles() -> tuple[TopicProfile, ...]:
    """등록된 전체 프로파일(비활성 포함)을 priority 순으로 반환한다."""
    return tuple(sorted(_PROFILES, key=lambda p: (p.priority, p.id)))


def get_enabled_topic_profiles() -> tuple[TopicProfile, ...]:
    """enabled=True 프로파일만 priority 순으로 반환한다."""
    return tuple(p for p in all_topic_profiles() if p.enabled)


def get_topic_profile(profile_id: str) -> TopicProfile | None:
    """id로 프로파일을 찾는다(없으면 None). enabled 여부와 무관."""
    for profile in _PROFILES:
        if profile.id == profile_id:
            return profile
    return None


def iter_topic_queries() -> list[str]:
    """enabled 프로파일의 쿼리를 priority 순으로 모으되 문자열을 dedup한다.

    동일 쿼리가 여러 프로파일에 있어도 한 번만 반환한다(쿼리 폭증 방지). 등장 순서를 보존한다.
    """
    seen: set[str] = set()
    out: list[str] = []
    for profile in get_enabled_topic_profiles():
        for query in profile.queries:
            key = query.strip().casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(query)
    return out


# ---------------------------------------------------------------------------
# 결정적 매칭 (네트워크/LLM 없음 — 부분문자열 기반, 설명 가능)
# ---------------------------------------------------------------------------

def _norm(text: str | None) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold()


def _article_text(article: dict) -> str:
    """제목+스니펫+출처를 한 번 정규화한 매칭용 텍스트."""
    parts = [article.get("title"), article.get("snippet"), article.get("source")]
    return _norm(" ".join(p for p in parts if p))


def _hits(text: str, terms: tuple[str, ...]) -> list[str]:
    """terms 중 text에 부분문자열로 등장하는 것들(설명용으로 원형 보존)."""
    return [t for t in terms if _norm(t) in text]


def match_topic_profile(article: dict, profile: TopicProfile) -> bool:
    """기사가 프로파일에 속하는지: include ∧ anchor ∧ ¬exclude (결정적)."""
    if not profile.enabled:
        return False
    text = _article_text(article)
    if _hits(text, profile.exclude_keywords):
        return False
    if not _hits(text, profile.include_keywords):
        return False
    if not _hits(text, profile.relevance_anchors):
        return False
    return True


def topic_profile_reason(article: dict, profile: TopicProfile) -> str | None:
    """매칭 사유(어떤 엔티티·관련성 신호가 맞았는지) — 미매칭이면 None."""
    if not match_topic_profile(article, profile):
        return None
    text = _article_text(article)
    entity = _hits(text, profile.include_keywords)
    anchor = _hits(text, profile.relevance_anchors)
    entity_label = entity[0] if entity else profile.label
    anchor_label = " · ".join(anchor[:3]) if anchor else "관련 신호"
    return f"{entity_label} 관련 · {anchor_label}"


def classify_topic_profiles(article: dict) -> list[str]:
    """기사가 속하는 enabled 프로파일 id 목록을 priority 순으로 반환한다(없으면 빈 리스트)."""
    return [p.id for p in get_enabled_topic_profiles()
            if match_topic_profile(article, p)]


# ===========================================================================
# P0-D5-D — 조직/사업 부문 렌즈 분류 체계 (Layer 1/2/3)
# ---------------------------------------------------------------------------
# 현대건설은 다수의 사업본부·사업부·실·연구원으로 구성된다. 모든 조직을 1차 대시보드
# 렌즈로 노출하면 화면이 시끄럽고 조직 개편에 취약해진다. 그래서 3계층으로 나눈다:
#   Layer-1 business_lens    임원 가시 사업 부문 7종 — TopicProfile 재사용(노출 렌즈)
#   Layer-2 org_unit_tag     운영자/설정용 지원·관리 조직 태그 7종 — 분류 메타(렌즈 아님)
#   Layer-3 execution_scope  실행 지리/범위 태그 4종 — 분류 메타
# 매칭은 전부 결정적(NFKC casefold 부분문자열)이며 LLM/네트워크/DB를 쓰지 않는다.
# 사업 부문 렌즈는 기존 TopicProfile과 동일한 include ∧ anchor ∧ ¬exclude 규칙을 따른다.
# anchor(건설/수주/현장/현대건설 등 사업 관련성)를 필수로 둬 generic 경제·에너지 기사가
# 렌즈를 도배하지 않게 한다(사용자 relevance anchor 규칙).
#
# 사업 부문 렌즈는 별도 _BUSINESS_LENSES 튜플에 등록한다(_PROFILES와 분리). 따라서
# iter_topic_queries(=live 수집 쿼리)와 get_enabled_topic_profiles(=briefing 엔티티 섹션)에
# 섞이지 않는다 → live 수집 동작과 기존 topic_profile_catalog가 불변이다. briefing은
# 묶음 구조(business_lens_signals dict + business_lens_catalog)로만 노출한다.
# ===========================================================================

# 모든 사업 부문 렌즈가 공유하는 노이즈 컷. bare "주가"는 "발주가" 오탐(P0-C1.11 교훈)
# 때문에 금지 — ETF/코스피/코스닥으로 종목성 기사를 거른다.
_BIZ_EXCLUDE: tuple[str, ...] = (
    "여행", "관광", "ETF", "코스피", "코스닥", "채용", "견학", "진로", "이벤트",
    "신차", "자동차 판매", "연료 소비", "사세요", "야구", "축구", "광고모델",
)

# 여러 렌즈가 공유하는 사업 관련성 anchor (건설/수주/현장/발주 맥락).
_BIZ_ANCHOR: tuple[str, ...] = (
    "건설", "건설사", "시공", "시공사", "현장", "수주", "발주", "입찰", "계약",
    "공사", "프로젝트", "현대건설", "EPC", "리스크", "공급망",
)

_CIVIL_INFRA = TopicProfile(
    id="civil_infrastructure",
    label="토목",
    description="SOC·도로·철도·항만·교량·터널 등 토목/공공 인프라 발주·수주·시공 신호를 감지한다.",
    enabled=True,
    queries=(
        "토목 SOC 발주", "GTX 철도 수주", "항만 공항 건설", "교량 터널 시공",
        "민자사업 턴키 입찰", "공공공사 발주 토목", "수자원 댐 하천 정비",
    ),
    include_keywords=(
        "토목", "SOC", "도로", "철도", "GTX", "항만", "공항", "교량", "터널",
        "지하공간", "수자원", "댐", "하천", "인프라", "공공공사",
    ),
    relevance_anchors=_BIZ_ANCHOR + ("턴키", "민자사업", "공공"),
    exclude_keywords=_BIZ_EXCLUDE,
    max_items=5,
    surface_key="civil_infrastructure",
    priority=1,
)

_BUILDING_HOUSING = TopicProfile(
    id="building_housing",
    label="건축주택",
    description="국내건축·주택·도시정비·분양·공사비·하자/품질 등 건축주택 사업 신호를 감지한다.",
    enabled=True,
    queries=(
        "도시정비 재건축 수주", "아파트 하자 품질 건설사", "공사비 갈등 조합",
        "분양 미분양 리스크", "리모델링 시공사 선정", "책임준공 PF 주택",
    ),
    include_keywords=(
        "국내건축", "주택", "아파트", "힐스테이트", "디에이치", "도시정비", "재건축",
        "재개발", "리모델링", "분양", "미분양", "조합", "공사비", "하자",
    ),
    relevance_anchors=_BIZ_ANCHOR + (
        "품질", "안전", "PF", "책임준공", "신탁사", "시행사", "시공사 선정",
    ),
    exclude_keywords=_BIZ_EXCLUDE,
    max_items=5,
    surface_key="building_housing",
    priority=2,
)

_PLANT = TopicProfile(
    id="plant",
    label="플랜트",
    description="플랜트·원전·LNG·발전소·정유/석유화학 EPC와 중동 해외 발주·수주 신호를 감지한다.",
    enabled=True,
    queries=(
        "LNG 플랜트 발주", "원전 EPC 수주", "석유화학 정유 플랜트",
        "중동 플랜트 공급망", "발전소 건설 수주", "수소 플랜트 사우디",
    ),
    include_keywords=(
        "플랜트", "원전", "LNG", "발전소", "정유", "석유화학", "수소", "전력", "에너지",
    ),
    relevance_anchors=_BIZ_ANCHOR + (
        "FEED", "중동", "사우디", "카타르", "UAE", "이라크", "해외사업", "플랜트",
    ),
    exclude_keywords=_BIZ_EXCLUDE,
    max_items=5,
    surface_key="plant",
    priority=3,
)

_NEW_ENERGY = TopicProfile(
    id="new_energy",
    label="New Energy",
    description="SMR·수소·재생에너지·전력망·데이터센터 전력 등 신에너지 인프라 구축 신호를 감지한다.",
    enabled=True,
    queries=(
        "SMR 전력망 데이터센터", "수소 탄소중립 건설", "해상풍력 송전망 EPC",
        "데이터센터 전력 냉각", "ESS 재생에너지 인프라", "CCUS 전력 인프라",
    ),
    include_keywords=(
        "SMR", "수소", "재생에너지", "해상풍력", "태양광", "ESS", "전력망", "송전",
        "배전", "데이터센터 전력", "데이터센터", "냉각", "에너지 인프라", "탄소중립",
        "CCUS", "원전",
    ),
    relevance_anchors=_BIZ_ANCHOR + (
        "인프라", "전력망", "송전", "배전", "데이터센터", "냉각", "송전망",
    ),
    exclude_keywords=_BIZ_EXCLUDE,
    max_items=5,
    surface_key="new_energy",
    priority=4,
)

_DEVELOPMENT_BUSINESS = TopicProfile(
    id="development_business",
    label="개발사업",
    description="시행/디벨로퍼·PF·인허가·토지확보·복합개발·시공사 선정 등 개발사업 신호를 감지한다.",
    enabled=True,
    queries=(
        "시행사 PF 리스크", "GBC 개발사업 인허가", "디벨로퍼 시공사 선정",
        "복합개발 토지확보", "민간참여 공공주택", "브릿지론 본PF 분양",
    ),
    include_keywords=(
        "개발사업", "시행사", "디벨로퍼", "복합개발", "공모사업", "민간참여",
        "공공주택", "GBC",
    ),
    relevance_anchors=_BIZ_ANCHOR + (
        "PF", "브릿지론", "본PF", "인허가", "토지확보", "토지매입", "시공사 선정",
        "분양", "사업성", "착공", "준공",
    ),
    exclude_keywords=_BIZ_EXCLUDE,
    max_items=5,
    surface_key="development_business",
    priority=5,
)

_GLOBAL_BUSINESS = TopicProfile(
    id="global_business",
    label="글로벌",
    description="해외수주·중동/사우디·해외현장/지사/법인·환율·지정학(호르무즈) 등 글로벌 사업 신호를 감지한다.",
    enabled=True,
    queries=(
        "해외수주 플랜트 사우디", "중동 카타르 UAE 발주", "호르무즈 지정학 유가",
        "해외법인 환율 리스크", "해외현장 해외지사", "글로벌 해외사업 진출",
    ),
    include_keywords=(
        "해외사업", "해외수주", "해외현장", "해외지사", "해외법인", "글로벌", "중동",
        "사우디", "카타르", "UAE", "이라크", "호르무즈", "지정학",
    ),
    relevance_anchors=_BIZ_ANCHOR + ("환율", "유가", "물류", "진출", "해외사업"),
    exclude_keywords=_BIZ_EXCLUDE,
    max_items=5,
    surface_key="global_business",
    priority=6,
)

_SAFETY_QUALITY = TopicProfile(
    id="safety_quality",
    label="안전·품질",
    description="중대재해·특별감독·벌점·철근누락·부실시공·하자 등 안전/품질 리스크 신호를 감지한다.",
    enabled=True,
    queries=(
        "중대재해 특별감독 건설현장", "철근누락 부실시공 하자",
        "안전 품질 벌점 제재", "현장점검 행정처분", "공기지연 안전관리 리스크",
    ),
    include_keywords=(
        "중대재해", "특별감독", "벌점", "부실시공", "철근누락", "하자", "사고", "산재",
    ),
    relevance_anchors=_BIZ_ANCHOR + (
        "안전", "품질", "안전관리", "품질관리", "공기지연", "제재", "행정처분",
        "현장점검", "건설현장",
    ),
    exclude_keywords=_BIZ_EXCLUDE,
    max_items=5,
    surface_key="safety_quality",
    priority=7,
)

# Layer-1 사업 부문 렌즈 (등록 순서 = priority). _PROFILES와 분리된 별도 튜플.
_BUSINESS_LENSES: tuple[TopicProfile, ...] = (
    _CIVIL_INFRA,
    _BUILDING_HOUSING,
    _PLANT,
    _NEW_ENERGY,
    _DEVELOPMENT_BUSINESS,
    _GLOBAL_BUSINESS,
    _SAFETY_QUALITY,
)


@dataclass(frozen=True)
class LensTag:
    """Layer-2/3 분류 태그 (조직 단위 / 실행 범위). 노출 렌즈가 아닌 메타 태그."""

    id: str
    label: str
    layer: str                       # "org_unit" | "execution_scope"
    keywords: tuple[str, ...]        # 결정적 부분문자열 매칭 키워드
    note: str = ""                   # 사람용 설명 / 실제 조직 매핑


# Layer-2 지원·관리 조직 태그 (운영자/설정용 — 1차 렌즈로 노출하지 않는다).
# 키워드는 충돌 안전한 정식 조직명 위주(bare "PI"=API 오탐, bare "감사"=감사합니다 오탐 회피).
_ORG_UNIT_TAGS: tuple[LensTag, ...] = (
    LensTag("pi", "PI", "org_unit", ("PI본부",), "PI본부 (프로세스 혁신)"),
    LensTag("finance_accounting", "재경", "org_unit", ("재경본부", "재경"), "재경본부"),
    LensTag("corporate_support", "경영지원", "org_unit",
            ("경영지원본부", "경영지원"), "경영지원본부"),
    LensTag("strategy_planning", "전략기획", "org_unit",
            ("전략기획사업부", "전략기획"), "전략기획사업부"),
    LensTag("audit_compliance", "감사", "org_unit",
            ("감사실", "내부감사", "감사위원회"), "감사실"),
    LensTag("construction_technology_research", "HGM건설기술연구원", "org_unit",
            ("HGM건설기술연구원", "건설기술연구원"), "HGM건설기술연구원"),
    LensTag("gbc_development", "GBC개발", "org_unit",
            ("GBC개발사업단", "GBC개발"), "GBC개발사업단"),
)

# Layer-3 실행 지리/범위 태그.
_EXECUTION_SCOPE_TAGS: tuple[LensTag, ...] = (
    LensTag("domestic_site", "국내현장", "execution_scope",
            ("국내현장", "국내 현장", "국내 건설현장", "국내 사업장", "고용부 특별감독"),
            "국내 건설현장·국내 사업장"),
    LensTag("overseas_site", "해외현장", "execution_scope",
            ("해외현장", "해외 현장", "해외 플랜트", "해외 사업장",
             "사우디 현장", "카타르 프로젝트"),
            "해외 건설현장·해외 프로젝트"),
    LensTag("overseas_branch", "해외지사", "execution_scope",
            ("해외지사", "중동지사", "중동 지사"), "해외지사"),
    LensTag("overseas_subsidiary", "해외법인", "execution_scope",
            ("해외법인", "현지법인", "현지 법인"), "해외 현지법인"),
)

# 실제 조직 단위 → 계층 매핑 (현대건설 조직도). business_lenses는 Layer-1 렌즈 id,
# org_units는 Layer-2 태그 id를 가리킨다 (둘 다 위에 정의된 id만 참조).
UNIT_MAPPING: tuple[dict, ...] = (
    {"unit": "토목사업본부", "business_lenses": ("civil_infrastructure",), "org_units": ()},
    {"unit": "건축주택사업본부", "business_lenses": ("building_housing",), "org_units": ()},
    {"unit": "플랜트사업본부", "business_lenses": ("plant",), "org_units": ()},
    {"unit": "NewEnergy사업부", "business_lenses": ("new_energy",), "org_units": ()},
    {"unit": "안전품질본부", "business_lenses": ("safety_quality",), "org_units": ()},
    {"unit": "개발사업부", "business_lenses": ("development_business",), "org_units": ()},
    {"unit": "글로벌사업부", "business_lenses": ("global_business",), "org_units": ()},
    {"unit": "PI본부", "business_lenses": (), "org_units": ("pi",)},
    {"unit": "재경본부", "business_lenses": (), "org_units": ("finance_accounting",)},
    {"unit": "경영지원본부", "business_lenses": (), "org_units": ("corporate_support",)},
    {"unit": "HGM건설기술연구원", "business_lenses": (),
     "org_units": ("construction_technology_research",)},
    {"unit": "전략기획사업부", "business_lenses": (), "org_units": ("strategy_planning",)},
    {"unit": "감사실", "business_lenses": (), "org_units": ("audit_compliance",)},
    {"unit": "GBC개발사업단", "business_lenses": ("development_business",),
     "org_units": ("gbc_development",)},
)


# ---------------------------------------------------------------------------
# Layer-1 사업 부문 렌즈 조회/분류 (TopicProfile 재사용 — match_topic_profile 공유)
# ---------------------------------------------------------------------------

def all_business_lenses() -> tuple[TopicProfile, ...]:
    """등록된 전체 사업 부문 렌즈(비활성 포함)를 priority 순으로 반환한다."""
    return tuple(sorted(_BUSINESS_LENSES, key=lambda p: (p.priority, p.id)))


def get_enabled_business_lenses() -> tuple[TopicProfile, ...]:
    """enabled=True 사업 부문 렌즈만 priority 순으로 반환한다."""
    return tuple(p for p in all_business_lenses() if p.enabled)


def get_business_lens(lens_id: str) -> TopicProfile | None:
    """id로 사업 부문 렌즈를 찾는다(없으면 None)."""
    for lens in _BUSINESS_LENSES:
        if lens.id == lens_id:
            return lens
    return None


def classify_business_lenses(article: dict) -> list[str]:
    """기사가 속하는 enabled 사업 부문 렌즈 id 목록을 priority 순으로 반환한다."""
    return [p.id for p in get_enabled_business_lenses()
            if match_topic_profile(article, p)]


def business_lens_reason(article: dict, lens: TopicProfile) -> str | None:
    """사업 부문 렌즈 매칭 사유 — 미매칭이면 None (topic_profile_reason 재사용)."""
    return topic_profile_reason(article, lens)


# ---------------------------------------------------------------------------
# Layer-2/3 메타 태그 조회/분류 (anchor 없이 키워드 부분문자열만 — 분류 메타용)
# ---------------------------------------------------------------------------

def all_org_unit_tags() -> tuple[LensTag, ...]:
    """Layer-2 조직 단위 태그 전체."""
    return _ORG_UNIT_TAGS


def get_org_unit_tag(tag_id: str) -> LensTag | None:
    for tag in _ORG_UNIT_TAGS:
        if tag.id == tag_id:
            return tag
    return None


def all_execution_scope_tags() -> tuple[LensTag, ...]:
    """Layer-3 실행 범위 태그 전체."""
    return _EXECUTION_SCOPE_TAGS


def get_execution_scope_tag(tag_id: str) -> LensTag | None:
    for tag in _EXECUTION_SCOPE_TAGS:
        if tag.id == tag_id:
            return tag
    return None


def match_lens_tag(article: dict, tag: LensTag) -> bool:
    """기사가 메타 태그에 속하는지: 키워드 부분문자열 하나라도 매칭(결정적)."""
    return bool(_hits(_article_text(article), tag.keywords))


def classify_org_units(article: dict) -> list[str]:
    """기사에 매칭되는 Layer-2 조직 단위 태그 id 목록(없으면 빈 리스트)."""
    return [t.id for t in _ORG_UNIT_TAGS if match_lens_tag(article, t)]


def classify_execution_scopes(article: dict) -> list[str]:
    """기사에 매칭되는 Layer-3 실행 범위 태그 id 목록(없으면 빈 리스트)."""
    return [t.id for t in _EXECUTION_SCOPE_TAGS if match_lens_tag(article, t)]
