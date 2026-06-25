#!/usr/bin/env python3
"""D7-M — 로컬 텍스트 → 비공개 사이트 워치리스트 JSON 변환기.

운영자가 로컬에 붙여넣은 파이프 구분 텍스트(예: /tmp/hdec_site_watchlist_raw.txt)를 읽어
data/private/site_watchlist.local.json(gitignored)을 만든다. 생성 파일은 절대 커밋되지
않으며(.gitignore), 이 스크립트는 data/private/ 밖으로는 쓰지 않는다(프라이버시 가드).

텍스트 한 줄 = 항목 1건. '#'로 시작하는 줄과 빈 줄은 무시한다.
형식(파이프 '|' 구분):

    scope | business_lens | org_unit | 프로젝트명 | 별칭1,별칭2 | tier

  - scope        : domestic_site | overseas_site | overseas_branch | overseas_subsidiary
  - business_lens: civil_infrastructure | building_housing | plant | new_energy
                   (해외지사/해외법인은 비워 둔다)
  - org_unit     : 담당 본부/부서 (선택)
  - 프로젝트명    : 정식 명칭 (필수)
  - 별칭          : 쉼표로 구분 (선택)
  - tier         : 1=매 실행 검색, 2/3=날짜별 회전 (비우면 2)

사용법:
    python3 scripts/prepare_site_watchlist_from_text.py \\
        --input /tmp/hdec_site_watchlist_raw.txt \\
        --output data/private/site_watchlist.local.json
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import site_watchlist as sw  # noqa: E402

_PRIVATE_DIR = (ROOT / "data" / "private").resolve()


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _slug(scope: str, name: str, idx: int) -> str:
    base = "".join(c if c.isalnum() else "_" for c in name.lower())
    base = "_".join(p for p in base.split("_") if p)
    return f"{scope}_{base}"[:60] or f"{scope}_{idx}"


def parse_lines(text: str) -> tuple[list, list]:
    """텍스트를 항목 리스트로 파싱한다. (items, errors)를 반환한다(가짜 행 생성 없음)."""
    items, errors, seen_ids = [], [], set()
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            errors.append(f"line {lineno}: 필드 부족(>=4 필요) — {line[:60]!r}")
            continue
        scope = _norm(parts[0])
        business = _norm(parts[1])
        org_unit = _norm(parts[2])
        name = _norm(parts[3])
        aliases = [_norm(a) for a in (parts[4].split(",") if len(parts) > 4 else [])
                   if _norm(a)]
        tier_raw = parts[5] if len(parts) > 5 else ""
        if scope not in sw.SCOPES:
            errors.append(f"line {lineno}: scope 무효({scope!r})")
            continue
        if business and business not in sw.BUSINESS_LENSES:
            errors.append(f"line {lineno}: business_lens 무효({business!r})")
            continue
        if scope in ("domestic_site", "overseas_site") and not business:
            errors.append(f"line {lineno}: {scope}는 business_lens 필요 — {name!r}")
            continue
        if not name:
            errors.append(f"line {lineno}: 프로젝트명 누락")
            continue
        try:
            tier = min(3, max(1, int(tier_raw))) if tier_raw else 2
        except ValueError:
            tier = 2
        item_id = _slug(scope, name, lineno)
        if item_id in seen_ids:
            continue  # 중복 제거(동일 이름)
        seen_ids.add(item_id)
        items.append({
            "id": item_id,
            "name": name,
            "aliases": aliases,
            "scope": scope,
            "business_lens": business,
            "org_unit": org_unit,
            "tier": tier,
        })
    return items, errors


def _summarize(items: list) -> None:
    by_scope, by_biz, by_tier = {}, {}, {}
    for it in items:
        by_scope[it["scope"]] = by_scope.get(it["scope"], 0) + 1
        biz = it["business_lens"] or "(none)"
        by_biz[biz] = by_biz.get(biz, 0) + 1
        by_tier[it["tier"]] = by_tier.get(it["tier"], 0) + 1
    print(f"총 {len(items)}건")
    print("  scope별:        " + ", ".join(f"{k}={v}" for k, v in sorted(by_scope.items())))
    print("  business_lens별: " + ", ".join(f"{k}={v}" for k, v in sorted(by_biz.items())))
    print("  tier별:         " + ", ".join(f"{k}={v}" for k, v in sorted(by_tier.items())))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="로컬 텍스트 → 비공개 사이트 워치리스트 JSON (data/private/ 전용)")
    parser.add_argument("--input", required=True, help="파이프 구분 텍스트 파일 경로")
    parser.add_argument("--output", default="data/private/site_watchlist.local.json",
                        help="출력 JSON(반드시 data/private/ 하위)")
    parser.add_argument("--source", default="user_screenshot_manual_seed",
                        help="각 항목 source 표기")
    parser.add_argument("--review-status", default="needs_review",
                        help="각 항목 review_status 표기")
    args = parser.parse_args(argv)

    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = (ROOT / out_path)
    out_resolved = out_path.resolve()
    # 프라이버시 가드: data/private/ 밖으로는 절대 쓰지 않는다.
    if _PRIVATE_DIR not in out_resolved.parents:
        print(f"거부: 출력 경로가 data/private/ 밖이다 — {out_resolved}", file=sys.stderr)
        return 2

    in_path = Path(args.input)
    try:
        text = in_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"입력 파일 읽기 실패: {exc}", file=sys.stderr)
        return 1

    items, errors = parse_lines(text)
    for e in errors:
        print(f"[skip] {e}", file=sys.stderr)
    if not items:
        print("항목 0건 — 출력하지 않는다(가짜 데이터 생성 금지).", file=sys.stderr)
        return 1

    for it in items:
        it["public_safe"] = False
        it["source"] = args.source
        it["review_status"] = args.review_status

    payload = {
        "version": 1,
        "note": ("비공개 내부 워치리스트 — gitignored. 절대 커밋 금지. "
                 "scripts/prepare_site_watchlist_from_text.py 로 로컬 생성."),
        "generated_by": "prepare_site_watchlist_from_text.py",
        "items": items,
    }
    out_resolved.parent.mkdir(parents=True, exist_ok=True)
    out_resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    print(f"생성: {out_resolved} ({len(items)}건)")
    _summarize(items)
    # 스키마 재검증(로드 경로와 동일 규칙).
    report = sw.validate_watchlist(str(out_resolved))
    print(f"검증: {'OK' if report['valid'] else 'FAIL'}"
          + ("" if report["valid"] else f" — {report['errors'][:5]}"))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
