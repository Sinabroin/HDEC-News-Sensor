#!/usr/bin/env python3
"""원문 링크 회귀 진단 (D7-AD-X).

목적: "어제 잘 되던 원문보기가 오늘 갑자기 사내망 warning으로 막힌다"는 관찰이
우리 코드(수집 방식/링크 생성)의 회귀 때문인지, 외부 보안정책/데이터 변화 때문인지
**분리**한다. 네트워크 호출은 하지 않는다 — 이미 빌드된 대시보드 HTML의 임베드
모델(``preview-model`` JSON)과 렌더링 규칙만 정적으로 분석한다.

핵심 원칙:
  * 대시보드의 외부 링크 href는 클라이언트에서 ``article.url``(+ D7-AD-X 이후
    ``external_url``)로 렌더된다. 따라서 "모델의 url/external_url" == "사용자가 클릭하는
    href"다. 이 스크립트는 그 값만 비교한다.
  * warning URL(``hdec.kr/warning`` / ``WARNING.jpg``)이 외부 href로 들어갔는지,
    href가 ``final_url``(진단용) 같은 엉뚱한 필드로 바뀌었는지, aggregator(구글뉴스/
    네이버/다음) 경유 비중이 급증했는지 정량화한다.

사용:
    python3 scripts/diagnose_article_link_regression.py \
        --base   /tmp/hdec-link-base-858c829.html \
        --current /tmp/hdec-link-current.html \
        [--origin /tmp/hdec-link-origin-main.html] \
        [--live   docs/daily/dashboard-latest.html] \
        --report  /tmp/hdec-link-regression-report.md \
        --base-json /tmp/hdec-link-regression-base.json \
        --current-json /tmp/hdec-link-regression-current.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

# 외부 aggregator/portal/search host — 원문(퍼블리셔) 직링크가 아니라 경유 URL이다.
AGGREGATOR_HOSTS = (
    "news.google.com", "google.com", "n.news.naver.com", "news.naver.com",
    "v.daum.net", "news.daum.net", "search.naver.com", "bing.com",
)
WARNING_MARKERS = ("hdec.kr/warning", "warning.jpg")

# 대시보드 모델에서 기사 row를 담는 배열/구조.
ROW_CONTAINERS = ("featured_row", "news_rows", "ai_rows")

_MODEL_RE = re.compile(
    r'<script[^>]*id="preview-model"[^>]*>(.*?)</script>', re.S)


def _domain(url: str | None) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _is_http(url: str | None) -> bool:
    return bool(re.match(r"^https?://", str(url or "").strip()))


def _is_warning(url: str | None) -> bool:
    v = str(url or "").strip().lower()
    return any(m in v for m in WARNING_MARKERS)


def _host_kind(url: str | None) -> str:
    """href host를 publisher/aggregator/warning/none으로 분류."""
    if not _is_http(url):
        return "warning" if _is_warning(url) else "none"
    if _is_warning(url):
        return "warning"
    dom = _domain(url)
    if any(dom == h or dom.endswith("." + h) for h in AGGREGATOR_HOSTS):
        return "aggregator"
    return "publisher"


def _norm_title(title: str | None) -> str:
    return re.sub(r"\s+", " ", str(title or "").strip()).lower()


def load_model(html_path: Path) -> dict:
    html = html_path.read_text(encoding="utf-8")
    m = _MODEL_RE.search(html)
    if not m:
        raise ValueError(f"preview-model script 태그를 찾을 수 없음: {html_path}")
    return json.loads(m.group(1))


def _iter_rows(model: dict):
    """모델의 모든 기사 row를 (surface, row)로 순회한다(featured/news/ai/lens/category)."""
    fr = model.get("featured_row")
    if isinstance(fr, dict) and fr:
        yield "featured", fr
    for key in ("news_rows", "ai_rows"):
        for row in model.get(key) or []:
            if isinstance(row, dict):
                yield key, row
    for lens, rows in (model.get("lens_banks") or {}).items():
        for row in rows or []:
            if isinstance(row, dict):
                yield f"lens:{lens}", row
    for section in model.get("nav_category_sections") or []:
        for row in (section or {}).get("articles") or []:
            if isinstance(row, dict):
                yield f"category:{section.get('id') or section.get('label') or ''}", row


def _metadata_provider(row: dict) -> str:
    meta = row.get("source_metadata_json") or row.get("source_metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (TypeError, ValueError):
            meta = {}
    if isinstance(meta, dict):
        return str(meta.get("provider") or "")
    return ""


def _article_id(row: dict) -> str:
    return str(
        row.get("article_id")
        or (row.get("provenance") or {}).get("article_id")
        or "")


def _external_href(row: dict) -> str:
    """대시보드 JS가 실제 외부 링크로 쓰는 값을 재현한다.

    D7-AD-X 이후 빌더가 ``external_url``을 주입하면 그것을, 아니면 기존처럼 ``url``을
    쓴다(둘 다 http(s)일 때만, warning URL은 링크로 만들지 않는다).
    """
    for field in ("external_url", "url"):
        v = row.get(field)
        if _is_http(v) and not _is_warning(v):
            return str(v)
    return ""


def _has_new_access_fields(row: dict) -> bool:
    return any(k in row for k in
               ("link_access_status", "accessSourceType", "final_url"))


def _record(surface: str, row: dict) -> dict:
    url = row.get("url") or ""
    href = _external_href(row)
    reader_style = _has_new_access_fields(row)
    return {
        "surface": surface,
        "article_id": _article_id(row),
        "title_norm": _norm_title(row.get("title")),
        "title": row.get("title") or "",
        "source": row.get("source") or "",
        "display_source": row.get("display_source") or row.get("source") or "",
        "published_at": row.get("published_at") or row.get("time") or "",
        "card_type": surface.split(":", 1)[0],
        # 외부 CTA 렌더 결과(우리가 클릭하는 href).
        "external_href": href,
        "external_href_domain": _domain(href),
        "external_href_kind": _host_kind(href),
        # 후보 URL 필드들 — 어떤 필드가 href로 쓰이는지 추적.
        "url": url,
        "url_domain": _domain(url),
        "external_url": row.get("external_url") or "",
        "original_url": row.get("original_url") or "",
        "canonical_url": row.get("canonical_url") or "",
        "final_url": row.get("final_url") or "",
        "final_url_is_warning": _is_warning(row.get("final_url")),
        # 접근성 진단 메타(표시 전용).
        "link_access_status": row.get("link_access_status") or "",
        "sourceDomain": row.get("sourceDomain") or "",
        "accessSourceType": row.get("accessSourceType") or "",
        "collectionMethod": row.get("collectionMethod") or "",
        "provider": _metadata_provider(row),
        "has_original_link": bool(_is_http(url)),
        # CTA 형태: 신규(내부 reader "기사 보기" + 보조 "원문 사이트") vs 구(직접 "원문 보기").
        "cta_style": "reader+source" if reader_style else "direct_source",
        "visible_link_text": ("기사 보기 / 원문 사이트" if reader_style else "원문 보기"),
    }


def build_inventory(model: dict) -> dict:
    """모델 하나에서 기사별 링크 레코드와 집계 요약을 만든다."""
    by_key: dict[str, dict] = {}
    surfaces: dict[str, list[str]] = {}
    for surface, row in _iter_rows(model):
        rec = _record(surface, row)
        key = rec["article_id"] or rec["url"] or rec["title_norm"]
        if not key:
            continue
        surfaces.setdefault(key, []).append(surface)
        # 대표 레코드는 최초 등장(featured/news 우선 순회 순서)로 고정.
        by_key.setdefault(key, rec)
    for key, rec in by_key.items():
        rec["surfaces"] = sorted(set(surfaces.get(key, [])))

    records = list(by_key.values())
    kinds = _distribution(r["external_href_kind"] for r in records)
    src_types = _distribution(
        (r["accessSourceType"] or "unknown") for r in records)
    warning_hrefs = [r for r in records if r["external_href_kind"] == "warning"]
    final_warning = [r for r in records if r["final_url_is_warning"]]
    href_from_final = [
        r for r in records
        if r["external_href"] and r["final_url"]
        and r["external_href"] == r["final_url"]
        and r["external_href"] != r["url"]
    ]
    return {
        "record_count": len(records),
        "records": records,
        "summary": {
            "href_kind_distribution": kinds,
            "source_type_distribution": src_types,
            "aggregator_href_count": kinds.get("aggregator", 0),
            "publisher_href_count": kinds.get("publisher", 0),
            "no_link_count": kinds.get("none", 0),
            "warning_href_count": len(warning_hrefs),
            "warning_href_titles": [r["title"] for r in warning_hrefs][:20],
            "final_url_warning_count": len(final_warning),
            "href_taken_from_final_url_count": len(href_from_final),
            "cta_style": _distribution(r["cta_style"] for r in records),
        },
    }


def _distribution(values) -> dict:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def compare(base: dict, current: dict) -> dict:
    """base/current를 title_norm으로 매칭해 href/domain/필드 변화를 센다."""
    b = {r["title_norm"]: r for r in base["records"] if r["title_norm"]}
    c = {r["title_norm"]: r for r in current["records"] if r["title_norm"]}
    common = sorted(set(b) & set(c))

    href_changed, domain_changed = [], []
    pub_to_agg, agg_to_pub = [], []
    href_to_warning, lost_link = [], []
    for t in common:
        rb, rc = b[t], c[t]
        if rb["external_href"] != rc["external_href"]:
            href_changed.append({
                "title": rc["title"], "base": rb["external_href"],
                "current": rc["external_href"]})
        if rb["external_href_domain"] != rc["external_href_domain"]:
            domain_changed.append({
                "title": rc["title"], "base": rb["external_href_domain"],
                "current": rc["external_href_domain"]})
        bk, ck = rb["external_href_kind"], rc["external_href_kind"]
        if bk == "publisher" and ck == "aggregator":
            pub_to_agg.append(rc["title"])
        if bk == "aggregator" and ck == "publisher":
            agg_to_pub.append(rc["title"])
        if bk != "warning" and ck == "warning":
            href_to_warning.append(rc["title"])
        if rb["external_href"] and not rc["external_href"]:
            lost_link.append(rc["title"])

    return {
        "base_records": base["record_count"],
        "current_records": current["record_count"],
        "matched_by_title": len(common),
        "only_in_base": sorted(set(b) - set(c)),
        "only_in_current": sorted(set(c) - set(b)),
        "href_changed_count": len(href_changed),
        "href_changed": href_changed,
        "domain_changed_count": len(domain_changed),
        "domain_changed": domain_changed,
        "publisher_to_aggregator": pub_to_agg,
        "aggregator_to_publisher": agg_to_pub,
        "href_became_warning": href_to_warning,
        "original_link_lost": lost_link,
    }


def _md_dist(title: str, dist: dict) -> list[str]:
    out = [f"- **{title}**:"]
    if not dist:
        out.append("  - (없음)")
    for k, v in dist.items():
        out.append(f"  - `{k}`: {v}")
    return out


def render_report(inputs: dict, cmp_result: dict | None) -> str:
    lines = ["# 원문 링크 회귀 진단 리포트 (D7-AD-X)", ""]
    lines.append("네트워크 호출 없이 빌드된 대시보드 HTML의 임베드 모델만 분석했다.")
    lines.append("대시보드 외부 링크 href = 모델의 `external_url`/`url`(http(s)·비-warning).")
    lines.append("")
    for label, inv in inputs.items():
        s = inv["summary"]
        lines.append(f"## 입력: {label} (기사 {inv['record_count']}건)")
        lines += _md_dist("href 종류 분포", s["href_kind_distribution"])
        lines += _md_dist("source_type 분포", s["source_type_distribution"])
        lines += _md_dist("CTA 스타일", s["cta_style"])
        lines.append(f"- **aggregator 경유 href**: {s['aggregator_href_count']}")
        lines.append(f"- **publisher 직링크 href**: {s['publisher_href_count']}")
        lines.append(f"- **원문 링크 없음**: {s['no_link_count']}")
        lines.append(f"- **warning URL이 href로 들어간 건**: "
                     f"{s['warning_href_count']}  ← 0이어야 안전")
        lines.append(f"- **final_url이 warning인 건(진단 메타)**: "
                     f"{s['final_url_warning_count']}")
        lines.append(f"- **href가 final_url에서 유래(오용 신호)**: "
                     f"{s['href_taken_from_final_url_count']}  ← 0이어야 안전")
        lines.append("")
    if cmp_result is not None:
        lines.append("## base ↔ current 매칭 비교")
        lines.append(f"- base {cmp_result['base_records']}건 / "
                     f"current {cmp_result['current_records']}건 / "
                     f"제목 매칭 {cmp_result['matched_by_title']}건")
        lines.append(f"- **href 변경 기사 수**: {cmp_result['href_changed_count']}")
        lines.append(f"- **domain 변경 기사 수**: {cmp_result['domain_changed_count']}")
        lines.append(f"- **publisher→aggregator 전환**: "
                     f"{len(cmp_result['publisher_to_aggregator'])}")
        lines.append(f"- **aggregator→publisher 전환**: "
                     f"{len(cmp_result['aggregator_to_publisher'])}")
        lines.append(f"- **href가 warning으로 바뀐 기사**: "
                     f"{len(cmp_result['href_became_warning'])}")
        lines.append(f"- **원문 링크 사라진 기사**: "
                     f"{len(cmp_result['original_link_lost'])}")
        lines.append("")
        for rec in cmp_result["href_changed"][:30]:
            lines.append(f"  - `{rec['title'][:50]}`: "
                         f"{rec['base']} → {rec['current']}")
        lines.append("")
        verdict = ("링크 회귀 없음 (href/domain 불변)"
                   if cmp_result["href_changed_count"] == 0
                   and not cmp_result["href_became_warning"]
                   else "링크 변화 감지 — 상단 목록 확인")
        lines.append(f"### 판정: {verdict}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="원문 링크 회귀 진단(오프라인)")
    ap.add_argument("--base", type=Path)
    ap.add_argument("--current", type=Path)
    ap.add_argument("--origin", type=Path)
    ap.add_argument("--live", type=Path,
                    help="실데이터 대시보드(예: docs/daily/dashboard-latest.html)")
    ap.add_argument("--report", type=Path,
                    default=Path("/tmp/hdec-link-regression-report.md"))
    ap.add_argument("--base-json", type=Path,
                    default=Path("/tmp/hdec-link-regression-base.json"))
    ap.add_argument("--current-json", type=Path,
                    default=Path("/tmp/hdec-link-regression-current.json"))
    args = ap.parse_args(argv)

    inputs: dict[str, dict] = {}
    base_inv = current_inv = None
    for label, path, out_json in (
            ("base", args.base, args.base_json),
            ("current", args.current, args.current_json),
            ("origin/main", args.origin, None),
            ("live(committed)", args.live, None)):
        if not path:
            continue
        if not path.exists():
            print(f"[skip] {label}: 파일 없음 {path}")
            continue
        try:
            inv = build_inventory(load_model(path))
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"[skip] {label}: 모델 파싱 실패 — {exc}")
            continue
        inputs[f"{label} ({path})"] = inv
        if label == "base":
            base_inv = inv
        elif label == "current":
            current_inv = inv
        if out_json:
            out_json.write_text(
                json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[write] {label} 인벤토리 → {out_json}")

    if not inputs:
        print("입력 HTML이 하나도 없어 리포트를 생성할 수 없습니다.")
        return 2

    cmp_result = None
    if base_inv and current_inv:
        cmp_result = compare(base_inv, current_inv)

    args.report.write_text(render_report(inputs, cmp_result), encoding="utf-8")
    print(f"[write] 리포트 → {args.report}")
    if cmp_result is not None:
        print(f"href 변경 {cmp_result['href_changed_count']}건 · "
              f"warning href 전환 {len(cmp_result['href_became_warning'])}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
