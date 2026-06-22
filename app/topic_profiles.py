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
