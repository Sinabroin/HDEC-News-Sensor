"""P0-C1.6 검증기 — 출처 품질 필터 회귀 검사.

목적: 공개 RSS(블로그·카페·커뮤니티 혼입)에서 임원용 신호 품질을 보장한다.
- 블로그/카페/커뮤니티/티스토리/유튜브성 출처는 제외하거나 즉시 알림 임계 아래로 캡한다.
- Top 3 주요 신호에는 excluded 출처가 들어가지 않는다.
- 정상 뉴스(신뢰/일반 출처)와 mock 데모 숫자는 그대로 유지된다.

핵심 원칙 (네트워크 없이 결정적으로 통과한다):
- 분류 로직은 app/source_quality.py(순수 함수)가 단일 소유한다.
- live 수집 경로는 live_collector가 excluded 출처를 수집 단계에서 버린다.
- scoring은 low/excluded를 점수 캡으로 즉시 알림 위로 못 올라가게 한다.
- 본문 전문은 저장하지 않는다 (기존 금지어 계약 유지).

저장소의 radar.db는 절대 건드리지 않는다 (파이프라인 동작 검사는 temp DB subprocess).

금지어 리터럴은 이 파일이 코드 트리 grep에 걸리지 않도록 조각으로 조립한다
(lesson: banned-term-literal-in-defensive-code).

사용법:
    python3 scripts/verify_source_quality_filter.py
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
RULES = ROOT / "data" / "source_quality_rules.json"
SQ_MODULE = ROOT / "app" / "source_quality.py"
LIVE_COLLECTOR = ROOT / "app" / "live_collector.py"
SCORING = ROOT / "app" / "scoring.py"
BRIEFING = ROOT / "app" / "briefing.py"
TEMPLATE = ROOT / "templates" / "index.html"
REPORT_BUILDER = ROOT / "scripts" / "build_static_report.py"
BRIEF_BUILDER = ROOT / "scripts" / "build_executive_brief.py"
DIGEST_BUILDER = ROOT / "scripts" / "build_telegram_digest.py"
WORKFLOW = ROOT / ".github" / "workflows" / "telegram-notify.yml"
RADAR_DB = ROOT / "radar.db"

# 본문 전문 필드명 (rules.md §3) — 조각 조립으로 grep 규약 회피
BANNED_TERMS = ["".join(parts) for parts in [
    ("raw", "_payload"), ("full", "_text"),
    ("article", "_body"), ("full_rss", "_content"),
]]
TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")
INSTANT_GRADE = "즉시 알림 후보"
INSTANT_THRESHOLD = 4.5

# 코드 트리 본문-전문 스캔 대상 (스펙 문서 rules.md/PRD.md/.claude 제외 — 기존 verifier 규약)
SCAN_GLOBS = ["app/*.py", "app/*.sql", "scripts/*.py",
              "templates/*", "data/*.json", ".github/workflows/*", "docs/**/*"]

# excluded 패턴에 반드시 포함돼야 하는 핵심 출처 유형 (조각 없이 표기 — 금지어 아님)
REQUIRED_EXCLUDED = ["블로그", "카페", "tistory", "youtube", "커뮤니티"]

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


def check_rules_file() -> None:
    if not check("data/source_quality_rules.json 존재", RULES.exists()):
        return
    try:
        data = json.loads(RULES.read_text(encoding="utf-8"))
    except ValueError as exc:
        check("rules JSON 파싱", False, str(exc))
        return
    check("rules JSON 파싱", True)

    excluded = " ".join(str(p).lower()
                        for p in (data.get("excluded_source_patterns") or []))
    missing = [t for t in REQUIRED_EXCLUDED if t.lower() not in excluded]
    check("excluded 패턴에 블로그/카페/tistory/youtube/커뮤니티 포함",
          not missing, "누락: " + ", ".join(missing))
    # 네이버 블로그/카페 명시 (공개 RSS 최빈 혼입원)
    check("excluded 패턴에 네이버 블로그/카페 명시",
          "네이버 블로그" in excluded and "네이버 카페" in excluded)
    check("trusted_source_patterns 1건 이상",
          bool(data.get("trusted_source_patterns")),
          f"{len(data.get('trusted_source_patterns') or [])}건")
    cap = data.get("score_cap") or {}
    check("score_cap에 excluded/low 상한 정의",
          cap.get("excluded") is not None and cap.get("low") is not None,
          str(cap))
    check("score_cap이 즉시 알림 임계(4.5)보다 낮음 (Top 3 차단)",
          float(cap.get("excluded", 9)) < INSTANT_THRESHOLD
          and float(cap.get("low", 9)) < INSTANT_THRESHOLD, str(cap))


def check_module_classify() -> None:
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("APP_MODE", "mock")
    from app import source_quality as sq

    cases = {
        "excluded": [("네이버 블로그", "데이터센터 글"), ("네이버 카페", "부동산 공유"),
                     ("테크리뷰 블로그", "써본 후기"), ("티스토리", "글"),
                     ("YouTube", "전력 영상"), ("디시인사이드", "커뮤니티 글")],
        "trusted": [("연합뉴스", "현대건설 수주"), ("한국경제", "데이터센터 EPC"),
                    ("국토교통부", "전력망 특별법")],
        "low": [("뉴스픽 재전송", "현대건설 수주")],
        "neutral": [("어느낯선통신", "데이터센터 전력 수주"), ("디지털데일리", "데이터센터")],
    }
    for expected, samples in cases.items():
        for src, title in samples:
            got = sq.classify(src, title)["source_quality"]
            check(f"classify('{src}') == {expected}", got == expected, f"got={got}")

    keys = set(sq.classify("연합뉴스", "x").keys())
    needed = {"source_quality", "source_type", "source_quality_reason",
              "source_quality_label"}
    check("classify 결과에 필수 키 완비", needed <= keys,
          ", ".join(sorted(needed - keys)))
    check("is_excluded(블로그)=True · is_excluded(연합뉴스)=False",
          sq.is_excluded("네이버 블로그", "글") is True
          and sq.is_excluded("연합뉴스", "수주") is False)
    check("trusted 출처는 제목 패턴 무시 (오강등 방지)",
          sq.classify("조선비즈", "네이버 블로그 규제 강화")["source_quality"] == "trusted")


def check_integration_source() -> None:
    lc = LIVE_COLLECTOR.read_text(encoding="utf-8")
    check("live_collector가 source_quality로 excluded 출처 제외",
          "source_quality" in lc and "is_excluded" in lc)
    sc = SCORING.read_text(encoding="utf-8")
    check("scoring이 source_quality 캡 적용 (cap_for)",
          "source_quality" in sc and "cap_for" in sc)
    br = BRIEFING.read_text(encoding="utf-8")
    check("briefing이 시그널에 source_quality 라벨 부착",
          "source_quality" in br and "source_quality_label" in br)


def check_parse_drop() -> None:
    """live_collector._parse_items가 블로그/카페/커뮤니티 item을 수집 단계에서 버리는지."""
    sys.path.insert(0, str(ROOT))
    from app import live_collector as lc

    xml = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        '<item><title>현대건설 데이터센터 EPC 수주</title>'
        '<link>https://news.example.com/a</link>'
        '<pubDate>Mon, 15 Jun 2026 09:00:00 +0900</pubDate>'
        '<description>요약</description><source>연합뉴스</source></item>'
        '<item><title>데이터센터 후기</title>'
        '<link>https://news.example.com/b</link>'
        '<pubDate>Mon, 15 Jun 2026 09:00:00 +0900</pubDate>'
        '<description>블로그</description><source>네이버 블로그</source></item>'
        '<item><title>부동산 정보</title>'
        '<link>https://news.example.com/c</link>'
        '<pubDate>Mon, 15 Jun 2026 09:00:00 +0900</pubDate>'
        '<description>카페</description><source>네이버 카페</source></item>'
        '<item><title>잡담</title>'
        '<link>https://news.example.com/d</link>'
        '<pubDate>Mon, 15 Jun 2026 09:00:00 +0900</pubDate>'
        '<description>커뮤니티</description><source>디시인사이드</source></item>'
        '</channel></rss>'
    )
    rows = lc._parse_items(xml, "q", "2026-06-15T00:00:00+09:00", 10)
    srcs = [r["source"] for r in rows]
    check("파서가 블로그/카페/커뮤니티 item 제외 (뉴스만 통과)",
          len(rows) == 1 and "연합뉴스" in srcs, f"통과 출처={srcs}")
    # 파서 출력은 허용 키 계약을 유지한다 (품질 필드를 raw에 부착하지 않음)
    allowed = {"id", "title", "source", "published_at", "url", "snippet",
               "source_metadata"}
    check("파서 raw dict가 허용 키만 유지 (품질 필드 미부착)",
          all(set(r.keys()) <= allowed for r in rows))


# ---------- live 파이프라인 시뮬레이션 (temp DB subprocess, 네트워크 없음) ----------

def _run_live_sim() -> dict | None:
    """live 수집을 신뢰+블로그+카페 혼합으로 시뮬레이션해 brief/리포트 사실을 모은다.

    fetch_all을 패치해 (신뢰 1 + 블로그 1 + 카페 1)을 반환하게 한다 — 제목은 서로 달라
    dedup으로 사라지지 않으므로 scoring 캡까지 실제로 탄다. 전부 temp DB에서 돈다.
    """
    code = (
        "import os, sys, json, tempfile\n"
        "d=tempfile.mkdtemp()\n"
        "os.environ['DB_PATH']=os.path.join(d,'t.db')\n"
        "os.environ['APP_MODE']='mock'\n"
        "os.environ['NEWS_MODE']='live'\n"
        "sys.path.insert(0,'.'); sys.path.insert(0,'scripts')\n"
        "from app import db, collector, scoring, insight, briefing, live_collector as lc\n"
        "mix=[\n"
        " {'id':'live_t1','title':'현대건설 데이터센터 전력 EPC 수주 우선협상대상자 선정',"
        "'source':'연합뉴스','published_at':'2026-06-14T09:00:00+09:00',"
        "'url':'https://n.ex/t1','snippet':'현대건설 데이터센터 전력 건설 EPC 수주','source_metadata':{}},\n"
        " {'id':'live_b1','title':'현대건설 데이터센터 전력 EPC 수주 본격 착수 심층 분석',"
        "'source':'테크블로그','published_at':'2026-06-14T09:00:00+09:00',"
        "'url':'https://n.ex/b1','snippet':'현대건설 데이터센터 전력 건설 EPC 수주','source_metadata':{}},\n"
        " {'id':'live_c1','title':'현대건설 사우디 플랜트 해외수주 추진 동향 상세',"
        "'source':'부동산 카페','published_at':'2026-06-14T09:00:00+09:00',"
        "'url':'https://n.ex/c1','snippet':'현대건설 사우디 플랜트 해외수주','source_metadata':{}},\n"
        "]\n"
        "lc.fetch_all=lambda *a,**k:mix\n"
        "db.init_db(); collected=collector.run()['collected']\n"
        "scoring.score_all(); insight.generate_all()\n"
        "b=briefing.build_brief()\n"
        "from build_static_report import render_report_html\n"
        "import re as _re\n"
        "html,_=render_report_html(b)\n"
        "m=_re.search(r'<section aria-label=\"참고/제외 및 출처 품질 감사\">.*?</section>', html, _re.S)\n"
        "audit=m.group(0) if m else ''\n"
        "outside=html.replace(audit,'') if audit else html\n"
        "rows={r['id']:r for r in db.fetch_articles_with_scores()}\n"
        "tops=b['top_immediate_signals']+b['top_new_issues']\n"
        "def f(i):\n"
        "  r=rows.get(i)\n"
        "  return None if not r else {'source':r['source'],'score':r['final_score'],'grade':r['alert_grade']}\n"
        "out={'mode':b['news_data_mode'],'collected':collected,\n"
        " 'blog':f('live_b1'),'cafe':f('live_c1'),'trusted':f('live_t1'),\n"
        " 'top':[{'source':s.get('source'),'sq':s.get('source_quality')} for s in tops],\n"
        " 'top_has_quality':all('source_quality' in s for s in tops) if tops else False,\n"
        " 'report_has_blog':'테크블로그' in outside,'report_has_cafe':'부동산 카페' in outside,\n"
        " 'audit_has_blog':'테크블로그' in audit,'audit_has_cafe':'부동산 카페' in audit}\n"
        "print(json.dumps(out, ensure_ascii=False))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=ROOT, timeout=240)
    if proc.returncode != 0:
        check("live 시뮬레이션 실행", False, (proc.stderr or "").strip()[-300:])
        return None
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("live 시뮬레이션 출력 파싱", False, (proc.stdout or "")[-300:])
        return None


def check_live_filter(sim: dict | None) -> None:
    if not sim:
        return
    check("live 시뮬: 3건 수집 (신뢰+블로그+카페)", sim.get("collected") == 3,
          str(sim.get("collected")))
    blog, cafe, trusted = sim.get("blog"), sim.get("cafe"), sim.get("trusted")

    check("신뢰 출처는 정상 점수 유지 (캡 없음, 즉시 알림 가능)",
          bool(trusted) and (trusted["score"] or 0) >= INSTANT_THRESHOLD,
          str(trusted))
    for label, item in (("블로그", blog), ("카페", cafe)):
        check(f"{label} 출처가 즉시 알림 임계 아래로 캡됨",
              bool(item) and (item["score"] or 0) < INSTANT_THRESHOLD, str(item))
        check(f"{label} 출처가 즉시 알림 후보 아님",
              bool(item) and item["grade"] != INSTANT_GRADE, str(item))

    top = sim.get("top") or []
    exc_in_top = [t for t in top
                  if "블로그" in (t.get("source") or "") or "카페" in (t.get("source") or "")]
    check("Top 3/Top 5에 블로그/카페 출처 없음", not exc_in_top, str(exc_in_top))
    check("Top 시그널의 source_quality가 전부 excluded 아님",
          all(t.get("sq") != "excluded" for t in top), str(top))
    check("Top 시그널 entry에 source_quality 필드 존재", sim.get("top_has_quality"))
    check("정적 리포트 Top/드릴다운(감사 섹션 외)에 블로그/카페 출처 미노출",
          not sim.get("report_has_blog") and not sim.get("report_has_cafe"),
          f"blog={sim.get('report_has_blog')} cafe={sim.get('report_has_cafe')}")
    # P0-C1.8: 제외된 비뉴스성 출처는 '출처 품질 제외' 감사 섹션에서만 투명하게 노출한다
    check("출처 품질 제외 audit 섹션에 블로그/카페 노출 (감사 투명성)",
          sim.get("audit_has_blog") and sim.get("audit_has_cafe"),
          f"audit_blog={sim.get('audit_has_blog')} audit_cafe={sim.get('audit_has_cafe')}")


# ---------- mock 경로 무결성 + brief 품질 필드 ----------

def check_mock_brief_fields() -> None:
    proc = subprocess.run([sys.executable, str(BRIEF_BUILDER), "--json"],
                          capture_output=True, text=True,
                          env=_clean_env(), cwd=ROOT, timeout=300)
    if not check("mock brief --json 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-200:]):
        return
    try:
        b = json.loads(proc.stdout)
    except ValueError as exc:
        check("mock brief --json 파싱", False, str(exc))
        return
    check("mock 경로 news_data_mode=mock 유지", b.get("news_data_mode") == "mock")
    check("mock total_articles > 0", (b.get("total_articles") or 0) > 0,
          str(b.get("total_articles")))
    imm = b.get("top_immediate_signals") or []
    check("mock 즉시 시그널 1~3건", 1 <= len(imm) <= 3, f"{len(imm)}건")
    need = {"source_quality", "source_quality_label", "source_type"}
    check("brief 시그널에 source_quality 필드 존재 (brief JSON)",
          bool(imm) and all(need <= set(s.keys()) for s in imm))
    check("mock Top 3 출처 품질이 전부 excluded 아님",
          all(s.get("source_quality") != "excluded" for s in imm))
    check("brief에 source_quality_note 존재", bool(b.get("source_quality_note")))


def check_mock_digest_intact() -> None:
    """mock 다이제스트가 깨지지 않고 약한-출처 안내가 mock을 오염시키지 않는지."""
    proc = subprocess.run([sys.executable, str(DIGEST_BUILDER), "--json"],
                          capture_output=True, text=True,
                          env=_clean_env(), cwd=ROOT, timeout=300)
    if not check("mock digest --json 동작", proc.returncode == 0,
                 (proc.stderr or "").strip()[-200:]):
        return
    data = json.loads(proc.stdout)
    msg_chars = data.get("message_chars")
    check("mock digest 길이 정상 (<=3000)",
          isinstance(msg_chars, int) and msg_chars <= 3000, str(msg_chars))
    # mock은 즉시 후보가 있으므로 '약한 출처' 안내가 붙지 않아야 한다
    proc2 = subprocess.run([sys.executable, str(DIGEST_BUILDER)],
                           capture_output=True, text=True,
                           env=_clean_env(), cwd=ROOT, timeout=300)
    check("mock digest에 live 전용 '주간 모니터링 후보 중심' 안내 미포함",
          "주간 모니터링 후보 중심" not in (proc2.stdout or ""))


# ---------- 대시보드 / 리포트 표시 ----------

def check_dashboard() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    check("대시보드가 Top 3를 등급으로 게이트 (excluded는 즉시 후보 불가)",
          'alert_grade === "즉시 알림 후보"' in html
          or "alert_grade === '즉시 알림 후보'" in html)
    check("대시보드에 출처 품질 칩 렌더 (srcQualityChip)",
          "srcQualityChip" in html and "source_quality" in html)
    check("대시보드에 출처 품질 고지 노출 (source_quality_note)",
          "source_quality_note" in html)


def check_report_links_and_note() -> None:
    """mock 리포트로 원문 링크 안전성 + 출처 품질 고지를 확인한다 (네트워크 없음)."""
    with tempfile.TemporaryDirectory(prefix="hdec_sq_") as tmp:
        out = Path(tmp) / "latest.html"
        proc = subprocess.run([sys.executable, str(REPORT_BUILDER),
                               "--output", str(out)],
                              capture_output=True, text=True,
                              env=_clean_env(), cwd=ROOT, timeout=300)
        if not check("mock report --output 동작", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "").strip()[-200:]):
            return
        html = out.read_text(encoding="utf-8")

    check("리포트에 출처 품질 고지 포함", "출처 품질 필터" in html)
    # 원문 링크(href)가 새 탭 + noopener noreferrer로 안전하게 열리는지
    bad = []
    for m in re.finditer(r"<a\b[^>]*>", html):
        tag = m.group(0)
        href = re.search(r'href="([^"]*)"', tag)
        if not href or not href.group(1).lower().startswith(("http://", "https://")):
            continue
        if 'target="_blank"' not in tag or "noopener" not in tag or "noreferrer" not in tag:
            bad.append(tag[:70])
    anchors = [m.group(0) for m in re.finditer(r"<a\b[^>]*>", html)
               if re.search(r'href="https?://', m.group(0))]
    check("리포트에 원문 링크(href) 1건 이상", bool(anchors), f"{len(anchors)}건")
    check("리포트 원문 링크가 새 탭 + noopener noreferrer", not bad,
          "; ".join(bad[:2]))


# ---------- 안전성 (본문 전문 미저장) + 워크플로 ----------

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
    check("코드 트리에 본문 전문 필드명 0건 (raw/full 계열)", not hits,
          "; ".join(hits[:3]))


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


def main() -> int:
    print(f"== verify_source_quality_filter @ {ROOT} ==")
    db_before = _db_state()

    check_py_compile()
    check_rules_file()
    check_module_classify()
    check_integration_source()
    check_parse_drop()

    sim = _run_live_sim()
    check_live_filter(sim)

    check_mock_brief_fields()
    check_mock_digest_intact()
    check_dashboard()
    check_report_links_and_note()
    check_no_article_bodies()
    check_workflow_intact()

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
