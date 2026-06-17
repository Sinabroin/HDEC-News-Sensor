"""P0-C1.7 검증기 — 카테고리별 근거 기사 드릴다운 회귀 검사.

목적: Top 3 요약이던 리포트를 카테고리별로 감사 가능한 임원용 근거 브리프로 만든다.
- brief에 category_sections가 있고, 카테고리 요약 카운트와 정합한다(수집 총량 감사 가능).
- 근거 기사 항목에 제목·출처·중요도·원문 링크가 있고, 본문 전문 필드는 없다.
- 정적 리포트에 '카테고리별 근거 기사' 섹션이 details/summary로 렌더되고 링크가 안전하다.
- 블로그/카페/커뮤니티성(excluded) 출처는 근거 목록에 노출되지 않는다(P0-C1.6 정책 유지).
- 대시보드에 카테고리 필터/섹션 마커가 있고, Telegram은 카테고리 근거를 '간결히'만 가리킨다.
- live 빌드 출력에 example.com/mock placeholder 링크가 없다(네트워크 없으면 SKIP).
- 기존 verifier(출처 품질 Top 3 가드 + 워크플로 게시 경로 포함)가 그대로 통과한다.

네트워크 호출 0건·비밀값 접근 0건으로 핵심 검사를 돌린다(live 빌드만 SKIP-friendly).
저장소의 radar.db는 절대 건드리지 않는다 — 모든 파이프라인은 temp DB subprocess에서 돈다.
금지어 리터럴은 이 파일이 코드 트리 grep에 걸리지 않도록 조각으로 조립한다
(lesson: banned-term-literal-in-defensive-code).

사용법:
    python3 scripts/verify_category_drilldown.py
"""

import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIEF_BUILDER = ROOT / "scripts" / "build_executive_brief.py"
REPORT_BUILDER = ROOT / "scripts" / "build_static_report.py"
DIGEST_BUILDER = ROOT / "scripts" / "build_telegram_digest.py"
BRIEFING_MODULE = ROOT / "app" / "briefing.py"
TEMPLATE = ROOT / "templates" / "index.html"
WORKFLOW = ROOT / ".github" / "workflows" / "telegram-notify.yml"
RADAR_DB = ROOT / "radar.db"

DRILLDOWN_TITLE = "카테고리별 근거 기사"
# P0-C1.8 — 드릴다운 기본 접힘 + 참고/제외·출처 품질 감사 + 용어 명확화
HELPER_TEXT = "카테고리를 펼쳐"
REVIEW_EXCLUDED_TITLE = "참고/제외 기사"
SOURCE_FILTERED_TITLE = "출처 품질 제외"
SOURCE_FILTERED_LABEL = "비뉴스성/낮은 신뢰 출처"
OLD_DAILY_LABEL = "일간 요약"   # 사용자 노출 표면에서 사라져야 하는 옛 라벨
NEW_DAILY_LABEL = "중요 신호"   # 임원용으로 완화된 새 라벨

# 함께 돌리는 기존 verifier (회귀 게이트) — 출처 품질 Top 3 가드/워크플로/리포트 안전성/
# brief·digest·quality 체인을 전이적으로 덮는다. WSL에서 파이프라인을 여러 번 돌리므로 넉넉히.
REGRESSION_VERIFIERS = ["verify_source_quality_filter.py", "verify_static_report.py"]

# 본문 전문 필드명 (rules.md §3) — 조각 조립으로 grep 규약 회피
BANNED_TERMS = ["".join(parts) for parts in [
    ("raw", "_payload"), ("full", "_text"),
    ("article", "_body"), ("full_rss", "_content"),
]]
TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")

# 코드 트리 본문-전문 스캔 대상 (스펙 문서 rules.md/PRD.md/.claude 제외 — 기존 verifier 규약)
SCAN_GLOBS = ["app/*.py", "app/*.sql", "scripts/*.py",
              "templates/*", "data/*.json", ".github/workflows/*", "docs/**/*"]

_failures = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    tag = "PASS" if ok else "FAIL"
    line = f"[{tag}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def skip(message: str) -> None:
    print(f"[SKIP] {message}")


def _db_state() -> tuple | None:
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _clean_env(**extra: str) -> dict:
    env = {**os.environ, "APP_MODE": "mock"}
    for key in ("MESSAGE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS",
                "DB_PATH", "REPORT_URL", "NEWS_MODE"):
        env.pop(key, None)
    env.update(extra)
    return env


def run_script(path: Path, *flags: str, env: dict | None = None,
               timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(path), *flags],
        capture_output=True, text=True, env=env or _clean_env(),
        cwd=ROOT, timeout=timeout,
    )


# ---------- 정적 검사 ----------

def check_py_compile() -> None:
    bad = []
    targets = sorted(list((ROOT / "scripts").glob("*.py"))
                     + list((ROOT / "app").glob("*.py")))
    with tempfile.TemporaryDirectory(prefix="hdec_pyc_") as tmp:
        for i, path in enumerate(targets):
            try:
                py_compile.compile(str(path), cfile=os.path.join(tmp, f"{i}.pyc"),
                                   doraise=True)
            except py_compile.PyCompileError as exc:
                bad.append(f"{path.name}: {exc.msg.strip().splitlines()[-1]}")
    check("py_compile scripts/*.py app/*.py", not bad, "; ".join(bad))


def check_briefing_source() -> None:
    src = BRIEFING_MODULE.read_text(encoding="utf-8")
    check("briefing.py가 category_sections를 brief에 포함",
          '"category_sections"' in src and "_build_category_sections" in src)
    # 파생 전용 경계 유지: DB 쓰기/네트워크 없음 (P0-B2 계약)
    writes = [m for m in ("upsert_", "insert_", "executescript", "DELETE", "UPDATE")
              if m in src]
    check("briefing.py는 여전히 DB에 쓰지 않음 (드릴다운은 파생 전용)", not writes,
          "; ".join(writes))


# ---------- brief category_sections 구조 ----------

def _build_mock_brief() -> dict | None:
    proc = run_script(BRIEF_BUILDER, "--json")
    if not check("brief --json 동작 (mock)", proc.returncode == 0,
                 (proc.stderr or "").strip()[-200:]):
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        check("brief --json 파싱", False, str(exc))
        return None


def check_brief_category_sections(brief: dict) -> None:
    sections = brief.get("category_sections")
    if not check("brief에 category_sections 존재 (비어 있지 않음)",
                 isinstance(sections, list) and bool(sections),
                 f"{len(sections or [])}건"):
        return

    counts = brief.get("category_counts") or []
    cc = {c.get("key"): c.get("count") for c in counts}
    # 1) 섹션 수와 카테고리 요약 수가 일치 (같은 카테고리 집합)
    check("category_sections 수 == category_counts 수",
          len(sections) == len(counts), f"{len(sections)} vs {len(counts)}")
    # 2) 각 섹션 total_count가 카테고리 요약 카운트와 일치 (드릴다운이 카운트를 정당화)
    mismatch = [s.get("category_key") for s in sections
                if cc.get(s.get("category_key")) != s.get("total_count")]
    check("각 카테고리 섹션 total_count가 요약 카운트와 일치", not mismatch,
          "; ".join(str(m) for m in mismatch[:4]))
    # 3) 섹션 total_count 합계 == 요약 카운트 합계 (수집 총량 감사 가능)
    sum_sections = sum(s.get("total_count", 0) for s in sections)
    sum_counts = sum(v or 0 for v in cc.values())
    check("섹션 total_count 합계 == 요약 카운트 합계 (감사 가능)",
          sum_sections == sum_counts, f"{sum_sections} vs {sum_counts}")

    # 4) 섹션 필수 필드
    need = {"category_key", "category_label", "total_count", "instant_count",
            "source_count", "top_articles"}
    bad_fields = [s.get("category_key") for s in sections
                  if not need <= set(s.keys())]
    check("각 섹션에 필수 필드(key/label/total/instant/source_count/top_articles) 완비",
          not bad_fields, "; ".join(str(m) for m in bad_fields[:4]))

    # 5) 근거 기사 항목에 제목·출처·중요도·URL 존재
    arts = [a for s in sections for a in (s.get("top_articles") or [])]
    check("category_sections에 근거 기사 항목 존재", bool(arts), f"{len(arts)}건")
    art_need = {"article_id", "title", "source", "final_score", "url",
                "source_quality", "category_label", "why_it_matters"}
    bad_arts = [a.get("article_id") for a in arts if not art_need <= set(a.keys())]
    check("근거 기사 필드(title/source/score/url/quality/why) 완비",
          not bad_arts, "; ".join(str(m) for m in bad_arts[:4]))
    check("근거 기사 제목/출처가 비어 있지 않음",
          all((a.get("title") or "").strip() and (a.get("source") or "").strip()
              for a in arts))
    check("근거 기사 중요도(final_score) 값 존재",
          all(a.get("final_score") is not None for a in arts))
    check("근거 기사에 원문 링크(http) 1건 이상 존재",
          any(str(a.get("url") or "").startswith(("http://", "https://"))
              for a in arts))

    # 6) 핵심 정책: 근거 목록(top_articles)에 excluded 품질(블로그/카페) 출처 미노출
    leaked = [a.get("source") for a in arts if a.get("source_quality") == "excluded"]
    check("근거 목록에 excluded(블로그/카페/커뮤니티) 출처 미노출", not leaked,
          "; ".join(str(s) for s in leaked[:4]))

    # 7) shown <= total, 'top N + 외 n건' 정합 (수집 총량을 숨기지 않음)
    bad_shown = [s.get("category_key") for s in sections
                 if s.get("shown_count", len(s.get("top_articles") or []))
                 > s.get("total_count", 0)]
    check("섹션 shown_count <= total_count (수집 총량 보존)", not bad_shown,
          "; ".join(str(m) for m in bad_shown[:4]))


def check_brief_no_body_fields(brief: dict) -> None:
    # 드릴다운 + 참고/제외 + 출처 품질 제외 구조를 한 번에 스캔 (P0-C1.8)
    blob = json.dumps([brief.get("category_sections") or [],
                       brief.get("review_excluded_evidence") or {},
                       brief.get("source_filtered_evidence") or {}],
                      ensure_ascii=False).lower()
    hits = [t for t in BANNED_TERMS if t in blob]
    check("드릴다운/감사 구조에 본문 전문 필드명 0건", not hits, ", ".join(hits))
    # 근거/감사 항목은 요약/링크만 — snippet(본문 절단) 같은 본문성 키도 싣지 않는다
    check("드릴다운/감사 항목에 snippet/본문 키 없음", "snippet" not in blob)


def check_brief_audit_evidence(brief: dict) -> None:
    """참고/제외(낮은 관련성 뉴스) + 출처 품질 제외(비뉴스성 출처) 감사 구조 — P0-C1.8.

    두 기준을 분리해 노출하는지, 항목 필드가 완비됐는지, Top 노출 가드가 유지되는지 본다.
    """
    review = brief.get("review_excluded_evidence")
    filtered = brief.get("source_filtered_evidence")
    if not check("brief에 review_excluded_evidence(dict) 존재", isinstance(review, dict)):
        return
    if not check("brief에 source_filtered_evidence(dict) 존재", isinstance(filtered, dict)):
        return

    # 참고/제외 항목: 정상 뉴스 — 제목/출처/중요도/URL(있을 때)
    r_items = review.get("items") or []
    need = {"title", "source", "final_score", "url", "category_label"}
    bad = [a.get("title") for a in r_items if not need <= set(a.keys())]
    check("참고/제외 항목 필드(title/source/score/url/category) 완비", not bad,
          "; ".join(str(b) for b in bad[:3]))
    check("참고/제외 항목 제목/출처 비어 있지 않음",
          all((a.get("title") or "").strip() and (a.get("source") or "").strip()
              for a in r_items))
    check("참고/제외 항목 중요도(final_score) 존재 (채점된 정상 뉴스)",
          all(a.get("final_score") is not None for a in r_items))
    bad_url = [a.get("title") for a in r_items
               if a.get("url") and not str(a["url"]).startswith(("http://", "https://"))]
    check("참고/제외 항목 URL은 있으면 http(s)", not bad_url,
          "; ".join(str(b) for b in bad_url[:3]))
    # 핵심 분리: 참고/제외(정상 뉴스)에 출처 품질 excluded가 섞이지 않는다 (두 기준 비혼동)
    leaked = [a.get("source") for a in r_items if a.get("source_quality") == "excluded"]
    check("참고/제외 목록에 출처 품질 excluded 미혼입 (두 기준 분리)", not leaked,
          "; ".join(str(s) for s in leaked[:3]))

    # 출처 품질 제외 항목: '비뉴스성/낮은 신뢰 출처' 라벨을 명시
    f_items = filtered.get("items") or []
    check("출처 품질 제외 항목 audit_label == 비뉴스성/낮은 신뢰 출처",
          all(a.get("audit_label") == SOURCE_FILTERED_LABEL for a in f_items),
          f"{[a.get('audit_label') for a in f_items][:3]}")
    check("출처 품질 제외 항목 제목/출처 존재",
          all((a.get("title") or "").strip() and (a.get("source") or "").strip()
              for a in f_items))

    # Top 노출 가드 재확인: 즉시/신규 시그널에 출처 품질 excluded 미노출
    sigs = (brief.get("top_immediate_signals") or []) + (brief.get("top_new_issues") or [])
    leaked_top = [s.get("source") for s in sigs if s.get("source_quality") == "excluded"]
    check("Top 시그널에 출처 품질 excluded 미노출 (Top 3 가드)", not leaked_top,
          "; ".join(str(s) for s in leaked_top[:3]))


# ---------- 정적 리포트 드릴다운 ----------

def _audit_section(html: str) -> str:
    """정적 리포트의 '참고/제외 · 출처 품질 감사' 섹션만 추려낸다 (라벨 격리 검사용)."""
    m = re.search(r'<section aria-label="참고/제외 및 출처 품질 감사">.*?</section>',
                  html, re.S)
    return m.group(0) if m else ""


def _anchor_safety_issues(html: str) -> list[str]:
    bad = []
    for m in re.finditer(r"<a\b[^>]*>", html):
        tag = m.group(0)
        href = re.search(r'href="([^"]*)"', tag)
        if not href or not href.group(1).lower().startswith(("http://", "https://")):
            continue
        if 'target="_blank"' not in tag:
            bad.append("target 누락: " + tag[:60])
        if "noopener" not in tag or "noreferrer" not in tag:
            bad.append("rel noopener noreferrer 누락: " + tag[:60])
    return bad


def check_static_report_mock() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_cd_") as tmp:
        out = Path(tmp) / "latest.html"
        proc = run_script(REPORT_BUILDER, "--output", str(out))
        if not check("mock report --output 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "").strip()[-200:]):
            return
        html = out.read_text(encoding="utf-8")

    check(f"리포트에 '{DRILLDOWN_TITLE}' 섹션 존재", DRILLDOWN_TITLE in html)
    check("리포트 드릴다운이 details/summary로 렌더 (JS 없이)",
          "<details" in html and "<summary>" in html and "cat-drill" in html)
    check("리포트 드릴다운에 카테고리 기사 줄(cd-art) 1건 이상", "cd-art" in html)
    check("리포트 드릴다운에 중요도 표기 (/ 5.0) 포함",
          "중요도" in html and "/ 5.0" in html)

    # P0-C1.8 (1): 카테고리 드릴다운 기본 접힘 + 펼침 안내 문구
    check("드릴다운 헬퍼 안내 문구 존재 (펼쳐서 확인 유도)", HELPER_TEXT in html)
    check("카테고리 details에 open 속성 기본값 없음 (모두 접힘)",
          not re.search(r'<details class="cat-drill"\s+open', html))
    check("리포트 전체에 열린 <details ... open> 0건 (자동 펼침 없음)",
          not re.search(r"<details[^>]*\bopen\b", html))
    check("닫힌 cat-drill details 존재 (details/summary 유지)",
          '<details class="cat-drill">' in html)

    # P0-C1.8 (2): 참고/제외 기사 + 출처 품질 제외 감사 섹션
    check(f"리포트에 '{REVIEW_EXCLUDED_TITLE}' 섹션 존재", REVIEW_EXCLUDED_TITLE in html)
    check(f"리포트에 '{SOURCE_FILTERED_TITLE}' 감사 문구 존재", SOURCE_FILTERED_TITLE in html)
    check("리포트에 '비뉴스성/낮은 신뢰 출처' 라벨/안내 존재", SOURCE_FILTERED_LABEL in html)
    # 출처 품질 제외 라벨은 audit 섹션 안에만 — Top 3/드릴다운 본문에 새지 않는다
    audit = _audit_section(html)
    check("'비뉴스성/낮은 신뢰 출처' 라벨이 audit 섹션 안에만 존재 (Top 3 비혼입)",
          html.count(SOURCE_FILTERED_LABEL) == audit.count(SOURCE_FILTERED_LABEL)
          and audit.count(SOURCE_FILTERED_LABEL) >= 1,
          f"html={html.count(SOURCE_FILTERED_LABEL)} audit={audit.count(SOURCE_FILTERED_LABEL)}")

    # P0-C1.8 (3): 용어 명확화 — 옛 라벨 제거, 새 라벨 노출
    check(f"리포트에 옛 라벨 '{OLD_DAILY_LABEL}' 없음 (용어 명확화)",
          OLD_DAILY_LABEL not in html)
    check(f"리포트 현황판/감사에 새 라벨 '{NEW_DAILY_LABEL}' 노출", NEW_DAILY_LABEL in html)

    # 원문 링크(href) 안전성 — 새 탭 + noopener noreferrer
    anchors = [m.group(0) for m in re.finditer(r"<a\b[^>]*>", html)
               if re.search(r'href="https?://', m.group(0))]
    check("리포트에 원문 링크(href) 1건 이상", bool(anchors), f"{len(anchors)}건")
    check("리포트 원문 링크가 새 탭 + noopener noreferrer",
          not _anchor_safety_issues(html))

    # JS/CDN 0건 (네이티브 details만) — 외부 스크립트/리소스 금지
    low = html.lower()
    check("리포트에 <script> 없음 (드릴다운은 네이티브 details)",
          "<script" not in low)
    for tag in ("<iframe", "<img", "<link ", "<object", "<embed"):
        check(f"리포트에 외부 리소스 태그 {tag.strip('<')} 없음", tag not in low)
    hits = [t for t in BANNED_TERMS if t in low]
    check("리포트에 본문 전문 필드/용어 0건", not hits, ", ".join(hits))


def check_static_report_live_optional() -> None:
    """NEWS_MODE=live 빌드 출력에 example.com/mock placeholder 링크가 없는지.

    네트워크가 없으면 mock fallback → SKIP (가짜 live 성공 주장 안 함).
    """
    with tempfile.TemporaryDirectory(prefix="hdec_cdlive_") as tmp:
        out = Path(tmp) / "latest.html"
        try:
            proc = run_script(REPORT_BUILDER, "--output", str(out),
                              env=_clean_env(NEWS_MODE="live"), timeout=240)
        except subprocess.TimeoutExpired:
            skip("live 빌드 타임아웃 — SKIP (가짜 성공 주장 안 함)")
            return
        if proc.returncode != 0 or not out.exists():
            skip(f"live 빌드 실패 — SKIP ({(proc.stderr or '').strip()[-120:]})")
            return
        html = out.read_text(encoding="utf-8")

    if "news_data_mode=live" not in (proc.stdout or ""):
        skip("live 수집 0건/네트워크 차단 → mock fallback — SKIP")
        return

    print("[LIVE] news_data_mode=live 리포트 생성됨")
    check("LIVE: 드릴다운 섹션 존재", DRILLDOWN_TITLE in html)
    hrefs = re.findall(r'href="([^"]*)"', html)
    bad_links = [h for h in hrefs
                 if "example.com" in h.lower() or "mock" in h.lower()]
    check("LIVE: example.com/mock placeholder 링크 없음", not bad_links,
          ", ".join(bad_links[:3]))
    http_hrefs = [h for h in hrefs if h.lower().startswith(("http://", "https://"))]
    check("LIVE: 실제 기사 href(http) 1건 이상", bool(http_hrefs), f"{len(http_hrefs)}건")
    check("LIVE: 드릴다운 링크가 새 탭 + noopener noreferrer",
          not _anchor_safety_issues(html))
    # P0-C1.8: live 리포트도 감사 섹션 + 용어 명확화 유지
    check("LIVE: 참고/제외·출처 품질 감사 섹션 존재",
          REVIEW_EXCLUDED_TITLE in html and SOURCE_FILTERED_TITLE in html)
    check("LIVE: 옛 라벨 '일간 요약' 없음", OLD_DAILY_LABEL not in html)
    audit = _audit_section(html)
    if SOURCE_FILTERED_LABEL in html:  # 제외 항목이 표시되면 audit 섹션 안에만 (Top 3 비혼입)
        check("LIVE: 출처 품질 제외 라벨이 audit 섹션 안에만 존재",
              html.count(SOURCE_FILTERED_LABEL) == audit.count(SOURCE_FILTERED_LABEL))


# ---------- 대시보드 / Telegram ----------

def check_dashboard() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    check("대시보드가 category_sections를 소비",
          "category_sections" in html)
    check("대시보드에 카테고리 필터/섹션 마커 존재 (selectCategory/cat-section)",
          "selectCategory" in html and "cat-section" in html)
    check(f"대시보드에 '{DRILLDOWN_TITLE}' 표시 영역 존재", DRILLDOWN_TITLE in html)
    # 기존 기능 보존: 기사 상세 열기 + 출처 품질 칩 + 즉시 후보 게이트
    check("대시보드 기사 상세 진입 유지 (selectArticle)", "selectArticle" in html)
    check("대시보드 출처 품질 칩 유지 (srcQualityChip)", "srcQualityChip" in html)
    check("대시보드 Top 3 등급 게이트 유지",
          'alert_grade === "즉시 알림 후보"' in html
          or "alert_grade === '즉시 알림 후보'" in html)

    # P0-C1.8 (4): 카테고리 자동 선택 제거 + 참고/제외 감사 + 용어 명확화
    check("대시보드가 카테고리를 자동 선택하지 않음 (sections[0] 자동 지정 없음)",
          "state.activeCat = sections[0]" not in html)
    check("대시보드 카테고리 미선택 안내 문구 존재",
          "카테고리를 선택하면 근거 기사가 표시됩니다" in html)
    check("대시보드에 참고/제외 감사 영역 존재 (renderAuditArea + audit-area)",
          "renderAuditArea" in html and 'id="audit-area"' in html)
    check("대시보드가 참고/제외·출처 품질 감사 데이터를 소비",
          "review_excluded_evidence" in html and "source_filtered_evidence" in html)
    check("대시보드에 현황판 버킷 설명(status_board_legend) 노출",
          "status_board_legend" in html and 'id="brief-legend"' in html)
    check(f"대시보드에 옛 라벨 '{OLD_DAILY_LABEL}' 없음", OLD_DAILY_LABEL not in html)
    check(f"대시보드에 새 라벨 '{NEW_DAILY_LABEL}' 노출", NEW_DAILY_LABEL in html)


def check_telegram_brief_mention() -> None:
    proc = run_script(DIGEST_BUILDER)
    if not check("digest 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-200:]):
        return
    msg = proc.stdout or ""
    check("digest가 카테고리별 근거 기사를 가리킴", DRILLDOWN_TITLE in msg)
    check("digest가 상세는 리포트로 위임 (리포트 언급)", "리포트" in msg)
    # '간결히'만 — 드릴다운을 통째로 넣지 않는다 (카테고리 근거 언급은 1회, details 마크업 없음)
    check("digest의 카테고리 근거 언급은 1회뿐 (드릴다운 미포함)",
          msg.count(DRILLDOWN_TITLE) == 1, f"{msg.count(DRILLDOWN_TITLE)}회")
    check("digest에 HTML details/summary 마크업 없음 (상세 드릴다운 미포함)",
          "<details" not in msg and "<summary" not in msg)
    # P0-C1.8: Telegram 용어 명확화 + 참고/제외 간결 안내
    check(f"digest에 옛 라벨 '{OLD_DAILY_LABEL}' 없음", OLD_DAILY_LABEL not in msg)
    check(f"digest 현황판에 새 라벨 '{NEW_DAILY_LABEL}' 노출", NEW_DAILY_LABEL in msg)
    check("digest가 참고/제외 기사를 간결히 가리킴 (1회)",
          "참고/제외" in msg and msg.count("참고/제외 기사") <= 1)


# ---------- 워크플로 게시 경로 ----------

def check_workflow_intact() -> None:
    if not check("telegram-notify.yml 존재", WORKFLOW.exists()):
        return
    text = WORKFLOW.read_text(encoding="utf-8")
    check("workflow가 NEWS_MODE=live로 live 리포트 게시",
          bool(re.search(r"NEWS_MODE:\s*live", text))
          and "build_static_report.py --output docs/daily/latest.html" in text)
    check("workflow가 live 성공 시에만 commit (live_ok 게이트)",
          "steps.report.outputs.live_ok == 'true'" in text)
    check("workflow가 send_telegram.py로 발송", "send_telegram.py" in text)


# ---------- 안전성 (본문 전문 미저장) + 기존 verifier 회귀 ----------

def check_no_article_bodies() -> None:
    hits = []
    for pattern in SCAN_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8").lower()
            except (UnicodeDecodeError, OSError):
                continue
            for term in BANNED_TERMS:
                if term in text:
                    hits.append(f"{path.relative_to(ROOT)}: {term}")
            if TOKEN_SHAPE.search(path.read_text(encoding="utf-8")):
                hits.append(f"{path.relative_to(ROOT)}: token-shape")
    check("코드 트리(+docs)에 본문 전문 필드명/token 모양 0건", not hits,
          "; ".join(hits[:3]))


def check_existing_verifiers() -> None:
    """출처 품질 Top 3 가드 + 워크플로 + 리포트 안전성 + brief/digest 체인 회귀."""
    for name in REGRESSION_VERIFIERS:
        path = ROOT / "scripts" / name
        if not path.exists():
            check(f"기존 {name} 존재", False)
            continue
        proc = run_script(path, timeout=900)
        check(f"기존 {name} 통과 (exit 0)", proc.returncode == 0,
              "" if proc.returncode == 0 else (proc.stdout or "").strip()[-300:])


def main() -> int:
    print(f"== verify_category_drilldown @ {ROOT} ==")
    db_before = _db_state()

    check_py_compile()
    check_briefing_source()

    brief = _build_mock_brief()
    if brief:
        check_brief_category_sections(brief)
        check_brief_no_body_fields(brief)
        check_brief_audit_evidence(brief)

    check_static_report_mock()
    check_static_report_live_optional()
    check_dashboard()
    check_telegram_brief_mention()
    check_workflow_intact()
    check_no_article_bodies()
    check_existing_verifiers()

    check("repo의 radar.db가 검증 중 변경/생성되지 않음 (temp DB 격리)",
          _db_state() == db_before)

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
