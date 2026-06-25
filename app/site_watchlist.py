"""Site Watchlist 도메인 (leaf) — 사내 제공 현장/프로젝트/지사/법인 워치리스트 (P0-D7-M).

사용자가 제공한 실제 현대건설 국내·해외 현장/프로젝트/해외지사/해외법인 이름을 기반으로
공개 뉴스를 센싱한다. 핵심은 **프라이버시 경계**다:

- 기본값은 **공개 샘플**(data/site_watchlist.sample.json)만 읽는다 — 공개·플레이스홀더 항목만.
- 실제 내부 목록(data/private/site_watchlist.local.json)은 환경변수 SITE_WATCHLIST_PATH가
  명시적으로 설정됐을 때만 읽는다. 미설정이면 private 목록은 비어 있다(샘플로 폴백).
- private 파일이 없거나 깨져도 빌드를 실패시키지 않는다 — 비어 있는 목록으로 no-op한다.
- 전체 내부 목록을 공개 산출물(docs/* 대시보드 HTML)에 절대 덤프하지 않는다 — 매칭된
  공개 기사 행만 해당 라벨을 단다(기사가 이미 그 프로젝트명을 언급한 경우에만).

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
from datetime import datetime, timezone
from pathlib import Path

# app.config를 import하지 않는다 (DB_PATH 캐시 트랩 회피) — 경로를 직접 계산한다.
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_SAMPLE_PATH = _DATA_DIR / "site_watchlist.sample.json"
_PRIVATE_DIR = _DATA_DIR / "private"

# 운영자가 명시적으로 설정해야만 실제 내부 목록을 읽는다(미설정 = 공개 샘플만).
_ENV_PATH = "SITE_WATCHLIST_PATH"
_ENV_MAX_QUERIES = "SITE_WATCHLIST_MAX_QUERIES"

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

DEFAULT_MAX_QUERIES = 40          # 1회 실행당 사이트 쿼리 기본 상한(env로 override)
MAX_QUERIES_HARD_CAP = 200        # 폭주 방지 절대 상한
MAX_QUERIES_PER_ITEM = 3          # 항목당 쿼리 수 상한(breadth 우선)
_MIN_MATCH_LEN = 4                # 제목 부분문자열 오탐 방지(너무 짧은 이름/별칭 제외)

# 항목 로드 캐시 — (resolved_path, mtime) 키. 같은 경로 반복 호출(행 단위 분류)을 빠르게 한다.
_CACHE: dict = {}


# ---------------------------------------------------------------------------
# 로드 + 정규화 (프라이버시 게이팅)
# ---------------------------------------------------------------------------

def _resolve_source(path=None):
    """읽을 소스 경로와 private 여부를 결정한다.

    우선순위: 명시적 path 인자 > env SITE_WATCHLIST_PATH > 공개 샘플.
    path/env로 온 경로는 private(내부 목록)로 본다 — 샘플은 공개.
    """
    explicit = path or os.environ.get(_ENV_PATH)
    if explicit:
        return Path(explicit), True
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
    - 기본(env/arg 없음): 공개 샘플을 읽는다 → is_private=False, source='sample'.
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
    return {"items": items, "is_private": False, "source": "sample", "path": str(src_path)}


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
                "scope": item["scope"],
                "business_lens": item.get("business_lens") or "",
            })
    return out


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
