"""Site Watchlist 도메인 (leaf) — 사내 제공 현장/프로젝트/지사/법인 워치리스트 (P0-D7-M).

사용자가 제공한 실제 현대건설 국내·해외 현장/프로젝트/해외지사/해외법인 이름을 기반으로
공개 뉴스를 센싱한다. D7-AD-Z부터 핵심은 **공개 현장 트리 노출**이다:

- 기본값은 **공개 현장 목록**(data/site_watchlist.public.json)을 읽는다 — 없을 때만 공개 샘플로 폴백한다.
- 실제 내부 목록(data/private/site_watchlist.local.json)은 환경변수 SITE_WATCHLIST_PATH가
  명시적으로 설정됐을 때만 읽는다. 미설정이면 private 목록은 비어 있다(샘플로 폴백).
- private 파일이 없거나 깨져도 빌드를 실패시키지 않는다 — 비어 있는 목록으로 no-op한다.
- 공개 승인된 현장 목록은 공개 산출물(docs/* 대시보드 HTML)에 트리로 포함한다.
  기사 매칭은 별도이며, 매칭 0건 현장도 정직하게 0으로 표시한다.

경계(이 파일만 한다 / 절대 안 한다):
- 한다: 워치리스트 로드(공개/비공개 게이팅), (1) 수집기용 bounded 사이트 쿼리 그룹 파생,
  (2) 빌더 태깅용 제목→사이트 렌즈 분류, (3) 집계 요약(이름 비노출), (4) 스키마 검증.
  순수 함수, 네트워크 0건, DB 0건, app.config import 0건(경로 자체 계산 = bootstrap 안전).
- 안 한다: 네트워크 호출, DB 접근, 점수/insight/발송, 가짜 기사/카운트 생성, 비공개 목록 노출.

가짜 데이터 금지: 사이트 쿼리는 NEWS_MODE=live 공개 RSS 경로에서만 쓰이고, 어떤 항목도
'수집된 것처럼' 위장하지 않는다(쿼리는 돌았으나 결과 없음 = audit에 정직하게 남는다).
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

# app.config를 import하지 않는다 (DB_PATH 캐시 트랩 회피) — 경로를 직접 계산한다.
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_SAMPLE_PATH = _DATA_DIR / "site_watchlist.sample.json"
_PUBLIC_PATH = _DATA_DIR / "site_watchlist.public.json"
_PRIVATE_DIR = _DATA_DIR / "private"

# 운영자가 명시적으로 설정해야만 실제 내부 목록을 읽는다(미설정 = 공개 샘플만).
_ENV_PATH = "SITE_WATCHLIST_PATH"
_ENV_MAX_QUERIES = "SITE_WATCHLIST_MAX_QUERIES"
# 트리 노출 게이트(D7-N): 설정 안 됨/!="1" = 매칭 노드만, "1" = 전체 비공개 트리(내부 빌드 전용).
_ENV_EXPOSE_TREE = "SITE_WATCHLIST_EXPOSE_TREE"

_KST = timezone(timedelta(hours=9))

# 유효한 범위(scope)와 사업 렌즈(business_lens) — VALID_LENS(빌더/템플릿)와 1:1.
SCOPES = ("domestic_site", "overseas_site", "overseas_branch", "overseas_subsidiary")
BUSINESS_LENSES = ("civil_infrastructure", "building_housing", "plant", "new_energy")

# (scope, business_lens) → 수집 쿼리 그룹 이름. group["name"]='site:*'라 query_audit이
# 사이트 출처를 자동으로 담는다(렌즈 그룹 'lens:*'과 구분).
_SCOPE_BIZ_GROUP = {
    ("domestic_site", "civil_infrastructure"): "site:domestic_civil",
    ("domestic_site", "building_housing"): "site:domestic_building",
    ("domestic_site", "plant"): "site:domestic_plant",
    ("domestic_site", "new_energy"): "site:domestic_new_energy",
    ("overseas_site", "civil_infrastructure"): "site:overseas_civil",
    ("overseas_site", "building_housing"): "site:overseas_building",
    ("overseas_site", "plant"): "site:overseas_plant",
    ("overseas_site", "new_energy"): "site:overseas_new_energy",
}
# 조직/거점·법인격은 사업 렌즈 없이 scope만으로 그룹을 만든다(프로젝트가 아니라 조직 단위).
_SCOPE_ONLY_GROUP = {
    "overseas_branch": "site:overseas_branch",
    "overseas_subsidiary": "site:overseas_subsidiary",
}
_GROUP_LABEL = {
    "site:domestic_civil": "국내현장·토목", "site:domestic_building": "국내현장·건축주택",
    "site:domestic_plant": "국내현장·플랜트", "site:domestic_new_energy": "국내현장·New Energy",
    "site:overseas_civil": "해외현장·토목", "site:overseas_building": "해외현장·건축주택",
    "site:overseas_plant": "해외현장·플랜트", "site:overseas_new_energy": "해외현장·New Energy",
    "site:overseas_branch": "해외지사", "site:overseas_subsidiary": "해외법인",
}

# 트리(D7-N) 표시용 라벨. scope = 빌더 VALID_LENS 키와 1:1(트리 패널이 렌즈 탭에 붙는다).
_SCOPE_LABEL = {
    "domestic_site": "국내 현장", "overseas_site": "해외 현장",
    "overseas_branch": "해외 지사", "overseas_subsidiary": "해외 법인",
}
_BIZ_LABEL = {
    "civil_infrastructure": "토목", "building_housing": "건축주택",
    "plant": "플랜트", "new_energy": "New Energy",
}
_BIZ_ORDER = ("civil_infrastructure", "building_housing", "plant", "new_energy", "")
# '!' 의미를 항상 명시한다(D7-N) — 위험 확정이 아니라 '최근 공개뉴스가 이 노드를 매칭'했다는 신호.
# 임원 센싱에서 '!'는 "과거에 한 번이라도 매칭"이 아니라 "최근(≤30일) 공개뉴스 매칭"을 뜻한다.
TREE_MARKER_LEGEND = "! = 최근 공개뉴스 매칭 있음 · 위험 확정 아님"

# 매칭 신선도(freshness) 버킷 — 발행 시각이 now 기준 얼마나 최근인지. is_recent_signal(=‘!’)은
# today/recent_7d/recent_30d 일 때만 True. old(>30일)·미상('')은 '!'를 받지 않는다(가짜 긴급 금지).
_RECENT_FRESHNESS = ("today", "recent_7d", "recent_30d")
_FRESHNESS_LABEL = {
    "today": "오늘 매칭", "recent_7d": "최근 7일", "recent_30d": "최근 30일",
    "old": "과거 매칭", "": "",
}
_RECENT_7D = timedelta(days=7)
_RECENT_30D = timedelta(days=30)

DEFAULT_MAX_QUERIES = 40          # 1회 실행당 사이트 쿼리 기본 상한(env로 override)
MAX_QUERIES_HARD_CAP = 200        # 폭주 방지 절대 상한
MAX_QUERIES_PER_ITEM = 3          # 항목당 쿼리 수 상한(breadth 우선)
_MIN_MATCH_LEN = 4                # 제목 부분문자열 오탐 방지(너무 짧은 이름/별칭 제외)

# 항목 로드 캐시 — (resolved_path, mtime) 키. 같은 경로 반복 호출(행 단위 분류)을 빠르게 한다.
_CACHE: dict = {}


# ---------------------------------------------------------------------------
# 로드 + 정규화 (프라이버시 게이팅)
# ---------------------------------------------------------------------------

def _path_is_public_source(path: Path) -> bool:
    """True if path points to the tracked public watchlist."""
    try:
        return path.resolve() == _PUBLIC_PATH.resolve()
    except OSError:
        return path.name == _PUBLIC_PATH.name


def _resolve_source(path=None) -> tuple[Path, bool]:
    """Resolve watchlist source.

    D7-AD-Z: the default dashboard source is now the tracked public watchlist
    data/site_watchlist.public.json when present. SITE_WATCHLIST_PATH remains an
    explicit local/operator override, but public export no longer null-gates site
    names by default.
    """
    if path:
        src = Path(path).expanduser()
        return src, not _path_is_public_source(src)

    env_path = os.environ.get(_ENV_PATH)
    if env_path:
        return Path(env_path).expanduser(), True

    if _PUBLIC_PATH.exists():
        return _PUBLIC_PATH, False

    return _SAMPLE_PATH, False

def _normalize_item(raw: dict) -> dict | None:
    """raw dict 한 건을 정규화한다. id/name/scope가 없으면 None(무시)."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    scope = str(raw.get("scope") or "").strip()
    if not name or scope not in SCOPES:
        return None
    item_id = str(raw.get("id") or "").strip() or _slug(scope, name)
    aliases = [str(a).strip() for a in (raw.get("aliases") or [])
               if isinstance(a, str) and str(a).strip()]
    business = str(raw.get("business_lens") or "").strip()
    if business and business not in BUSINESS_LENSES:
        business = ""
    try:
        tier = int(raw.get("tier") or 2)
    except (TypeError, ValueError):
        tier = 2
    tier = min(3, max(1, tier))
    return {
        "id": item_id,
        "name": name,
        "aliases": aliases,
        "scope": scope,
        "business_lens": business,
        "org_unit": str(raw.get("org_unit") or "").strip(),
        "tier": tier,
        "public_safe": bool(raw.get("public_safe")),
        "source": str(raw.get("source") or "").strip(),
        "review_status": str(raw.get("review_status") or "").strip(),
    }


def _slug(scope: str, name: str) -> str:
    base = "".join(c if c.isalnum() else "_" for c in name.lower())
    base = "_".join(p for p in base.split("_") if p)
    return f"{scope}_{base}"[:64]


def _read_items(src_path: Path) -> list | None:
    """JSON에서 정규화된 항목 리스트를 읽는다. 파일 없음/깨짐 → None(폴백 판단은 호출자)."""
    try:
        mtime = src_path.stat().st_mtime
    except OSError:
        return None
    cache_key = (str(src_path), mtime)
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    try:
        data = json.loads(src_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw_items = data.get("items") if isinstance(data, dict) else data
    items = []
    seen_ids = set()
    for raw in raw_items or []:
        item = _normalize_item(raw)
        if item is None or item["id"] in seen_ids:
            continue
        seen_ids.add(item["id"])
        items.append(item)
    _CACHE[cache_key] = items
    return items


def load_watchlist(path=None) -> dict:
    """워치리스트를 로드한다(프라이버시 게이팅).

    반환: {items, is_private, source, path}
    - 기본(env/arg 없음): 공개 현장 목록을 읽는다 → is_private=False, source='public'. 없으면 샘플로 폴백한다.
    - SITE_WATCHLIST_PATH 또는 path 인자 설정: 내부 목록을 읽는다 → is_private=True.
      파일이 없거나 깨지면 items=[] 로 no-op한다(source='private_missing', 빌드 실패 없음).
    """
    src_path, is_private = _resolve_source(path)
    if is_private:
        items = _read_items(src_path)
        if items is None:
            return {"items": [], "is_private": True, "source": "private_missing",
                    "path": str(src_path)}
        return {"items": items, "is_private": True, "source": "private",
                "path": str(src_path)}
    items = _read_items(src_path) or []
    return {"items": items, "is_private": False, "source": "public" if _path_is_public_source(src_path) else "sample", "path": str(src_path)}


# ---------------------------------------------------------------------------
# 수집 쿼리 그룹 (bounded · 회전)
# ---------------------------------------------------------------------------

def _resolve_max_queries(max_queries=None) -> int:
    if max_queries is None:
        raw = os.environ.get(_ENV_MAX_QUERIES)
        try:
            max_queries = int(raw) if raw else DEFAULT_MAX_QUERIES
        except (TypeError, ValueError):
            max_queries = DEFAULT_MAX_QUERIES
    return min(MAX_QUERIES_HARD_CAP, max(1, int(max_queries)))


def _default_rotation_key() -> int:
    """epoch 일수 — tier 2/3 항목을 날짜별로 회전시켜 시간이 지나며 전부 커버되게 한다."""
    return datetime.now(timezone.utc).toordinal()


def _group_name_for(item: dict) -> str | None:
    scope = item["scope"]
    if scope in _SCOPE_ONLY_GROUP:
        return _SCOPE_ONLY_GROUP[scope]
    biz = item.get("business_lens")
    if biz:
        return _SCOPE_BIZ_GROUP.get((scope, biz))
    return None


def _queries_for(item: dict) -> list:
    """항목 한 건의 공개 뉴스 안전 쿼리(최대 MAX_QUERIES_PER_ITEM건).

    국내: "이름" 현대건설 · "별칭" 현대건설 · "이름" 공사
    해외: "이름" Hyundai E&C · "이름" Hyundai Engineering & Construction · "이름" contractor
    """
    name = item["name"]
    aliases = item.get("aliases") or []
    scope = item["scope"]
    out: list = []
    if scope == "domestic_site":
        out.append(f'"{name}" 현대건설')
        if aliases:
            out.append(f'"{aliases[0]}" 현대건설')
        out.append(f'"{name}" 공사')
    else:  # overseas_site / overseas_branch / overseas_subsidiary
        out.append(f'"{name}" Hyundai E&C')
        out.append(f'"{name}" Hyundai Engineering & Construction')
        out.append(f'"{name}" contractor')
    # dedup(순서 보존) + 항목당 상한
    seen, deduped = set(), []
    for q in out:
        k = q.strip().casefold()
        if k and k not in seen:
            seen.add(k)
            deduped.append(q)
    return deduped[:MAX_QUERIES_PER_ITEM]


def _order_bucket(items: list, rotation_key: int) -> list:
    """그룹 한 개 안의 항목 정렬: tier 1 먼저(우선순위), 그다음 tier 2/3 — 둘 다 회전.

    rotation_key로 tier 1과 tier 2/3 각각을 회전시켜, 예산이 모자라도 시간이 지나며
    한 그룹 안의 모든 항목이 결국 검색되게 한다(tier 1은 항상 tier 2/3보다 앞선다).
    """
    tier1 = sorted([i for i in items if i["tier"] == 1], key=lambda i: i["id"])
    rest = sorted([i for i in items if i["tier"] != 1], key=lambda i: (i["tier"], i["id"]))
    if tier1:
        o = rotation_key % len(tier1)
        tier1 = tier1[o:] + tier1[:o]
    if rest:
        o = rotation_key % len(rest)
        rest = rest[o:] + rest[:o]
    return tier1 + rest


def collection_query_groups(path=None, max_queries=None, rotation_key=None) -> list:
    """수집기용 bounded 사이트 쿼리 그룹 — 내부(private) 워치리스트가 있을 때만.

    [{name:'site:*', label, scope, business_lens, queries:[...]}] 형태. 공개 샘플만 있거나
    private 목록이 비면 빈 리스트를 반환한다(가짜 수집 없음 · 수집기가 site 그룹을 만들지 않음).

    bounded + 공정성: 그룹(scope×사업) **라운드로빈**으로 항목을 뽑아 한 scope(예: 국내)가
    예산을 독식하지 않게 한다 — 모든 site 그룹이 매 실행 등장하도록(breadth). 그룹 안에서는
    tier 1을 먼저, tier 2/3은 날짜별 회전(coverage 순환). 총 쿼리 수는 max_queries(기본 40,
    env SITE_WATCHLIST_MAX_QUERIES)로 상한, 항목당 쿼리도 MAX_QUERIES_PER_ITEM으로 상한.
    """
    wl = load_watchlist(path)
    if not wl["is_private"] or not wl["items"]:
        return []
    budget = _resolve_max_queries(max_queries)
    if rotation_key is None:
        rotation_key = _default_rotation_key()

    # 1) 그룹별 버킷에 항목을 담고 그룹 안에서 정렬(tier 1 먼저, 회전).
    buckets: dict = {}
    for item in wl["items"]:
        gname = _group_name_for(item)
        if gname is None:
            continue
        buckets.setdefault(gname, []).append(item)
    for gname in buckets:
        buckets[gname] = _order_bucket(buckets[gname], rotation_key)

    # 2) 그룹 라운드로빈으로 예산 소진까지 항목을 선택(breadth 우선). 그룹 순서도 회전시켜
    #    예산이 모자랄 때 추가 depth가 특정 scope(알파벳순 국내)에 쏠리지 않게 한다.
    gnames = sorted(buckets)
    if gnames:
        o = rotation_key % len(gnames)
        gnames = gnames[o:] + gnames[:o]
    groups: dict = {}
    count = 0
    depth = 0
    max_depth = max((len(b) for b in buckets.values()), default=0)
    while count < budget and depth < max_depth:
        for gname in gnames:
            if count >= budget:
                break
            bucket = buckets[gname]
            if depth >= len(bucket):
                continue
            item = bucket[depth]
            group = groups.setdefault(gname, {
                "name": gname,
                "label": _GROUP_LABEL.get(gname, gname),
                "scope": item["scope"],
                "business_lens": item.get("business_lens") or "",
                "queries": [],
                "_seen": set(),
            })
            for q in _queries_for(item):
                if count >= budget:
                    break
                k = q.strip().casefold()
                if k in group["_seen"]:
                    continue
                group["_seen"].add(k)
                group["queries"].append(q)
                count += 1
        depth += 1

    result = []
    for g in groups.values():
        g.pop("_seen", None)
        if g["queries"]:
            result.append(g)
    return result


# ---------------------------------------------------------------------------
# 분류 (제목 → 사이트 렌즈) — 매칭된 기사에만, 비공개 목록 비노출
# ---------------------------------------------------------------------------

def classify_site_lenses(title, path=None) -> list:
    """기사 제목이 워치리스트 항목명/별칭을 직접 언급하면 그 항목의 렌즈 매칭을 돌려준다.

    반환: [{id, name, scope, business_lens}] (제목에 이름이 실제로 들어 있을 때만).
    부분문자열 오탐 방지를 위해 _MIN_MATCH_LEN보다 짧은 이름/별칭은 매칭에 쓰지 않는다.
    비공개 항목 전체를 노출하지 않는다 — 제목이 이미 언급한 항목만 라벨링한다.
    """
    t = title or ""
    if not t:
        return []
    wl = load_watchlist(path)
    out, seen_ids = [], set()
    for item in wl["items"]:
        if item["id"] in seen_ids:
            continue
        names = [item["name"], *item.get("aliases", [])]
        if any(len(n) >= _MIN_MATCH_LEN and n in t for n in names):
            seen_ids.add(item["id"])
            out.append({
                "id": item["id"],
                "name": item["name"],
                "label": item["name"],
                "scope": item["scope"],
                "business_lens": item.get("business_lens") or "",
                "org_unit": item.get("org_unit") or "",
            })
    return out


# ---------------------------------------------------------------------------
# 현장/조직 감시 트리 (D7-N) — 매칭 노드 + (운영자 노출 시) 전체 비공개 트리
# ---------------------------------------------------------------------------

def _fmt_kst(iso) -> str:
    """ISO datetime → KST 'YYYY-MM-DD HH:mm'. 파싱 실패/없음 → 빈 문자열(가짜 시각 금지)."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M")


def _coerce_now(now=None) -> datetime:
    """freshness 기준 시각(now)을 tz-aware UTC datetime으로 정규화한다.

    None이면 현재 UTC. tz 없는 datetime이 오면 UTC로 간주한다. 빌더는 리포트 생성 시각
    (brief.generated_at)을 넘겨 freshness가 '벽시계'가 아니라 '리포트 시점' 기준이 되게 한다.
    """
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(now))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _freshness(pub_iso, now=None) -> str:
    """발행 시각(ISO) → freshness 버킷: today / recent_7d / recent_30d / old / ''.

    '!' 마커의 근거가 되는 단일 판정 함수. 발행 시각이 없거나 파싱 실패하면 ''(미상) — 미상은
    '최근'으로 위장하지 않는다(가짜 긴급 금지). KST 같은 날짜면 today, 그 외엔 now와의 경과로
    7일/30일/그 이상(old)을 가른다. 살짝 미래(시차)인 값은 today/recent로 본다.
    """
    if not pub_iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(pub_iso))
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ref = _coerce_now(now)
    if dt.astimezone(_KST).date() == ref.astimezone(_KST).date():
        return "today"
    age = ref - dt
    if age <= _RECENT_7D:
        return "recent_7d"
    if age <= _RECENT_30D:
        return "recent_30d"
    return "old"


def expose_tree_enabled() -> bool:
    """전체 비공개 트리 노출 여부 — env SITE_WATCHLIST_EXPOSE_TREE == '1'일 때만 True.

    미설정/다른 값이면 False(매칭 노드만 노출). 공개 빌드는 애초에 트리를 만들지 않는다.
    """
    return os.environ.get(_ENV_EXPOSE_TREE) == "1"


def match_items_for_row(row_title, items) -> list:
    """주어진 items 목록에서 제목이 직접 언급한 항목을 돌려준다(목록 재로딩 없음).

    classify_site_lenses와 동일한 매칭 규칙(이름/별칭 ≥ _MIN_MATCH_LEN, 부분문자열). 빌더가
    이미 로드한 items로 행→노드 연결을 만들 때 쓴다(워치리스트 파일을 다시 읽지 않는다).
    """
    t = row_title or ""
    if not t or not items:
        return []
    out, seen = [], set()
    for item in items:
        iid = item.get("id")
        if not iid or iid in seen:
            continue
        names = [item.get("name") or "", *(item.get("aliases") or [])]
        if any(len(n) >= _MIN_MATCH_LEN and n in t for n in names):
            seen.add(iid)
            out.append(item)
    return out


def summarize_matches(rows) -> dict:
    """빌더 산출 행에서 워치리스트 항목 id별 공개 뉴스 매칭을 집계한다.

    반환: {item_id: {match_count, latest_published_at, article_keys:[...]}}.
    행 provenance의 site_watch_matches(여러 항목) 또는 site_watch_id(단일)를 읽는다. 같은 기사가
    여러 뱅크/탭에 중복 노출돼도 article_keys(url|title)로 중복 제거해 카운트를 부풀리지 않는다
    (가짜 카운트 금지). 매칭이 0인 항목은 여기서 등장하지 않는다(트리에서 match_count=0 처리).
    """
    agg: dict = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        prov = row.get("provenance") or {}
        ids = []
        matches = prov.get("site_watch_matches")
        if isinstance(matches, list) and matches:
            ids = [m.get("id") for m in matches if isinstance(m, dict) and m.get("id")]
        elif prov.get("site_watch_id"):
            ids = [prov.get("site_watch_id")]
        if not ids:
            continue
        key = row.get("url") or row.get("title") or ""
        pub = (prov.get("site_watch_latest_published_at")
               or prov.get("published_at") or "")
        for iid in ids:
            slot = agg.setdefault(iid, {"match_count": 0, "latest_published_at": "",
                                        "article_keys": []})
            if key and key not in slot["article_keys"]:
                slot["article_keys"].append(key)
            if pub and str(pub) > str(slot["latest_published_at"]):
                slot["latest_published_at"] = pub
    for slot in agg.values():
        slot["match_count"] = len(slot["article_keys"])
    return agg


def _tree_node(item: dict, match: dict, now=None) -> dict:
    """워치리스트 항목 한 건 → 트리 leaf 노드.

    '!'(alert_marker)는 **최근(≤30일) 공개뉴스 매칭**일 때만 켠다(D7-N) — match_count>0이라도
    발행 시각이 30일을 넘으면(old) '!'를 주지 않고 '과거 매칭'으로만 표시한다(임원 센싱에서
    '!'='지금 봐야 할 신호'를 보존). latest_kst는 표시용 날짜로 그대로 둔다(old여도 날짜는 보여줌).
    """
    mc = int(match.get("match_count") or 0) if match else 0
    pub = (match or {}).get("latest_published_at") or ""
    freshness = _freshness(pub, now) if mc > 0 else ""
    is_recent = freshness in _RECENT_FRESHNESS
    return {
        "id": item["id"],
        "label": item["name"],
        "scope": item["scope"],
        "business_lens": item.get("business_lens") or "",
        "org_unit": item.get("org_unit") or "",
        "tier": item.get("tier") or 2,
        "match_count": mc,
        "latest_published_at": pub,
        "latest_kst": _fmt_kst(pub),                 # 표시용 날짜(old여도 유지)
        "match_freshness": freshness,                # today/recent_7d/recent_30d/old/''
        "is_recent_signal": is_recent,               # '!'의 단일 근거
        "freshness_label": _FRESHNESS_LABEL.get(freshness, ""),  # 'old' → '과거 매칭'
        "alert_marker": "!" if is_recent else "",    # 최근 매칭만 '!'(과거/미상은 마커 없음)
        "article_keys": list((match or {}).get("article_keys") or []),
    }


def _group_key_label(scope: str, item: dict) -> tuple:
    """노드를 묶는 그룹 키/라벨/종류 — 현장은 business_lens, 조직/법인은 org_unit."""
    if scope in ("overseas_branch", "overseas_subsidiary"):
        org = item.get("org_unit") or ""
        return (org or "(미지정)", org or "(미지정 조직)", "org_unit")
    biz = item.get("business_lens") or ""
    return (biz, _BIZ_LABEL.get(biz, biz or "(기타)"), "business_lens")


def build_tree(items, matches=None, expose_full_tree=False, is_private=True, now=None) -> dict:
    """워치리스트 항목 + 매칭 집계 → scope(국내/해외현장·지사·법인) 계층 트리.

    - expose_full_tree=False: 매칭(match_count>0) 노드만 포함(비공개 이름 비노출 — 매칭된,
      즉 공개 뉴스가 이미 언급한 항목만 보인다).
    - expose_full_tree=True(SITE_WATCHLIST_EXPOSE_TREE=1, 내부 빌드 전용): 전체 트리 포함,
      미매칭 노드는 match_count=0·alert_marker="" (가짜 '!' 없음).

    freshness(D7-N): 노드 '!'는 **최근(≤30일) 공개뉴스 매칭**만(now 기준). 요약은 전체-시점
    매칭 수와 최근 매칭 수를 분리한다 — matched_nodes_all_time는 recent_matched_nodes보다 클 수
    있다(과거 매칭이 '!'를 받지 않으므로). now 미지정이면 현재 시각(빌더는 리포트 생성 시각 주입).

    반환: {is_private, expose_full_tree, marker_legend, total_nodes, matched_nodes,
    matched_nodes_all_time, recent_matched_nodes, old_matched_nodes, by_scope:{...}}.
    by_scope[scope] = {scope,label,match_count,node_count,recent_node_count,
    groups:[{key,label,kind,match_count,recent_node_count,nodes}]}.
    """
    matches = matches or {}
    ref = _coerce_now(now)
    by_scope: dict = {}
    total_nodes = 0
    matched_nodes = 0          # = matched_nodes_all_time (match_count>0, 신선도 무관)
    recent_matched = 0         # is_recent_signal True (≤30일)
    old_matched = 0            # match_count>0 이지만 old(>30일) — '!' 없음, '과거 매칭'
    for item in items or []:
        scope = item.get("scope")
        if scope not in SCOPES:
            continue
        match = matches.get(item.get("id")) or {}
        mc = int(match.get("match_count") or 0)
        if not expose_full_tree and mc <= 0:
            continue  # 매칭 노드만 — 미매칭 비공개 이름은 노출하지 않는다
        node = _tree_node(item, match, ref)
        is_recent = bool(node["is_recent_signal"])
        gkey, glabel, gkind = _group_key_label(scope, item)
        sc = by_scope.setdefault(scope, {
            "scope": scope, "label": _SCOPE_LABEL.get(scope, scope),
            "match_count": 0, "node_count": 0, "recent_node_count": 0, "_groups": {},
        })
        grp = sc["_groups"].setdefault(gkey, {
            "key": gkey, "label": glabel, "kind": gkind, "match_count": 0,
            "recent_node_count": 0, "nodes": [],
        })
        grp["nodes"].append(node)
        grp["match_count"] += mc
        sc["node_count"] += 1
        sc["match_count"] += mc
        total_nodes += 1
        if mc > 0:
            matched_nodes += 1
        if is_recent:
            recent_matched += 1
            grp["recent_node_count"] += 1
            sc["recent_node_count"] += 1
        elif node["match_freshness"] == "old":
            old_matched += 1
        # 매칭됐으나 발행 시각 미상(freshness='')은 recent도 old도 아니다(미상은 위장 금지) —
        # matched_nodes_all_time 에는 포함되나 '!'/'과거 매칭' 어느 쪽으로도 단정하지 않는다.

    # 그룹/노드 정렬: 노드는 최근 신호 먼저 → 매칭 많은 순 → tier → 라벨(과거 매칭은 아래로).
    out_scopes: dict = {}
    for scope, sc in by_scope.items():
        groups = list(sc.pop("_groups").values())
        for g in groups:
            g["nodes"].sort(key=lambda n: (0 if n["is_recent_signal"] else 1,
                                           -n["match_count"], n["tier"], n["label"]))
        if scope in ("overseas_branch", "overseas_subsidiary"):
            groups.sort(key=lambda g: (-g["recent_node_count"], -g["match_count"], g["label"]))
        else:
            order = {k: i for i, k in enumerate(_BIZ_ORDER)}
            groups.sort(key=lambda g: (order.get(g["key"], 99), g["label"]))
        sc["groups"] = groups
        out_scopes[scope] = sc

    return {
        "is_private": bool(is_private),
        "expose_full_tree": bool(expose_full_tree),
        "marker_legend": TREE_MARKER_LEGEND,
        "total_nodes": total_nodes,
        "matched_nodes": matched_nodes,                # 하위호환 별칭(= all_time)
        "matched_nodes_all_time": matched_nodes,       # 전체-시점 매칭 노드(신선도 무관)
        "recent_matched_nodes": recent_matched,        # 최근(≤30일) 매칭 = '!' 받는 노드
        "old_matched_nodes": old_matched,              # 과거(>30일) 매칭 — '!' 없음
        "by_scope": out_scopes,
    }


def tree_for_model(rows, path=None, now=None) -> dict | None:
    """빌더용 트리 파생 — 공개 대시보드도 현장명 트리를 노출한다.

    D7-AD-Z: 기본 공개 소스(data/site_watchlist.public.json)가 있으면 공개 빌드에서도
    국내현장/해외현장/해외지사/해외법인 전체 트리를 모델에 포함한다. 매칭 0건도 정직하게
    표시하고, '!'는 기존처럼 최근 공개뉴스 매칭이 있는 노드에만 부여한다.
    """
    wl = load_watchlist(path)
    matches = summarize_matches(rows)
    return build_tree(
        wl["items"],
        matches,
        expose_full_tree=True,
        is_private=False,
        now=now,
    )


# ---------------------------------------------------------------------------
# 요약 + 검증 (이름 비노출 · 스키마 점검)
# ---------------------------------------------------------------------------

def summarize_watchlist(path=None) -> dict:
    """집계 요약(이름 비노출 — 로그/출력 안전). scope/business_lens/tier별 카운트."""
    wl = load_watchlist(path)
    items = wl["items"]
    by_scope: dict = {}
    by_business: dict = {}
    by_tier: dict = {}
    for item in items:
        by_scope[item["scope"]] = by_scope.get(item["scope"], 0) + 1
        biz = item.get("business_lens") or "(none)"
        by_business[biz] = by_business.get(biz, 0) + 1
        by_tier[item["tier"]] = by_tier.get(item["tier"], 0) + 1
    return {
        "total": len(items),
        "is_private": wl["is_private"],
        "source": wl["source"],
        "by_scope": by_scope,
        "by_business_lens": by_business,
        "by_tier": by_tier,
    }


def scope_summary_for_model(path=None) -> dict:
    """이름 비노출 scope 집계 — 대시보드 좌측 '실행 범위' 네비 카운트용(D7-AD-P).

    tree_for_model(이름 포함 · 비공개 빌드 전용)과 달리 **카운트만** 담는다 → 공개·비공개 빌드
    모두에 안전하게 주입할 수 있다(현장명/별칭 0건). 공개 빌드(env 미설정)는 공개 샘플 집계를,
    운영자 빌드(SITE_WATCHLIST_PATH 설정)는 내부 목록 집계를 반환한다. 반환 dict에는 어떤
    현장명/별칭/조직명도 들어가지 않는다 — scope 키, 정수 카운트, scope 라벨(고정 상수)뿐이다
    (verify_site_watch_nav_tree.py가 '이름 비노출'을 잠근다).

    반환: {is_private, total, by_scope:{scope:count(4종 모두 0 포함)}, scope_labels:{scope:라벨}}.
    """
    wl = load_watchlist(path)
    by_scope = {s: 0 for s in SCOPES}
    for item in wl["items"]:
        sc = item.get("scope")
        if sc in by_scope:
            by_scope[sc] += 1
    return {
        "is_private": wl["is_private"],
        "total": len(wl["items"]),
        "by_scope": by_scope,
        "scope_labels": dict(_SCOPE_LABEL),
    }


def validate_watchlist(path=None) -> dict:
    """스키마 검증 — 잘못된 항목의 사유를 모은다(이름 대신 id로 식별)."""
    src_path, _is_private = _resolve_source(path)
    errors: list = []
    try:
        data = json.loads(src_path.read_text(encoding="utf-8"))
    except OSError:
        return {"valid": False, "count": 0, "errors": [f"파일 없음: {src_path}"]}
    except ValueError as exc:
        return {"valid": False, "count": 0, "errors": [f"JSON 파싱 실패: {exc}"]}
    raw_items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(raw_items, list):
        return {"valid": False, "count": 0, "errors": ["items 배열 없음"]}
    seen_ids = set()
    for idx, raw in enumerate(raw_items):
        ident = (raw.get("id") if isinstance(raw, dict) else None) or f"#{idx}"
        if not isinstance(raw, dict):
            errors.append(f"{ident}: dict 아님")
            continue
        if not str(raw.get("name") or "").strip():
            errors.append(f"{ident}: name 누락")
        scope = str(raw.get("scope") or "").strip()
        if scope not in SCOPES:
            errors.append(f"{ident}: scope 무효({scope!r})")
        biz = str(raw.get("business_lens") or "").strip()
        if biz and biz not in BUSINESS_LENSES:
            errors.append(f"{ident}: business_lens 무효({biz!r})")
        if scope in ("domestic_site", "overseas_site") and not biz:
            errors.append(f"{ident}: {scope}는 business_lens 필요")
        item_id = str(raw.get("id") or "").strip()
        if item_id and item_id in seen_ids:
            errors.append(f"{ident}: id 중복")
        seen_ids.add(item_id)
    return {"valid": not errors, "count": len(raw_items), "errors": errors}
