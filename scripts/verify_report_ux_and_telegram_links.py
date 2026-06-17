"""P0-C3 검증기 — Report UX & Telegram Link Polish.

네트워크/비밀값 없이 다음을 결정적으로 검사한다:
- Telegram digest가 HTML 링크 제목을 만들고 동적 텍스트를 escape한다.
- MESSAGE fallback은 HTML parse mode에서도 plain-text safe다.
- 정적 리포트 primary nav가 앵커 점프가 아니라 CSS-only 탭/필터다.
- 임원 화면에서 '판정 신뢰도', '현대건설 직접', 검토/추적 필요 라벨이 사라졌다.
- live macro 값과 '시장지표: 미연동'이 한 화면에 공존하지 않는다.
- 원문 링크 안전성, 본문 전문 필드 금지, token/X platform 금지를 유지한다.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_BUILDER = ROOT / "scripts" / "build_static_report.py"
DIGEST_BUILDER = ROOT / "scripts" / "build_telegram_digest.py"
SENDER = ROOT / "scripts" / "send_telegram.py"
TEMPLATE = ROOT / "templates" / "index.html"
RADAR_DB = ROOT / "radar.db"

TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")
BANNED_TERMS = ["".join(parts) for parts in [
    ("raw", "_payload"), ("full", "_text"),
    ("article", "_body"), ("full_rss", "_content"),
    ("api.", "x.com"), ("twit", "ter"), ("x bearer", " token"),
]]

_failures = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _clean_env(**extra: str) -> dict:
    env = {**os.environ, "APP_MODE": "mock", "NEWS_MODE": "mock"}
    for key in ("MESSAGE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS",
                "DB_PATH", "REPORT_URL", "TELEGRAM_BOT_USERNAME",
                "TELEGRAM_PERSONAL_BOT_URL"):
        env.pop(key, None)
    env.update(extra)
    return env


def _db_state() -> tuple | None:
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _anchor_safety_issues(html: str) -> list[str]:
    issues = []
    for m in re.finditer(r"<a\b[^>]*>", html):
        tag = m.group(0)
        href = re.search(r'href="([^"]*)"', tag)
        if not href or not href.group(1).lower().startswith(("http://", "https://")):
            continue
        if 'target="_blank"' not in tag:
            issues.append("target missing: " + tag[:80])
        if "noopener" not in tag or "noreferrer" not in tag:
            issues.append("rel missing: " + tag[:80])
    return issues


def run_script(path: Path, *flags: str, env: dict | None = None,
               timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(path), *flags],
        capture_output=True, text=True, env=env or _clean_env(),
        cwd=ROOT, timeout=timeout,
    )


def check_telegram_links() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_telegram_digest as tg
    import send_telegram

    data = {
        "header": "HDEC Executive Radar",
        "news_data_mode": "live",
        "date_kst": "2026-06-17",
        "executive_one_liner": "검증용 <태그> & 링크 escape",
        "status_board": [{"label": "중요 신호", "value": 1}],
        "hdec_signals": [{
            "article_id": "h1", "hdec_bucket": 2,
            "title": "현대건설 <연관> & EPC",
            "url": "https://www.reuters.com/business/",
        }],
        "top_signals": [{
            "article_id": "a1", "rank": 1,
            "title": "AI <DC> & 전력",
            "url": "https://www.reuters.com/technology/",
            "final_score": 4.8, "alert_grade": "즉시 알림 후보",
        }, {
            "article_id": "a2", "rank": 2,
            "title": "잘못된 URL은 링크 금지",
            "url": "ftp://invalid.local/item",
            "final_score": 4.2,
        }, {
            "article_id": "a3", "rank": 3,
            "title": "URL 없음도 안전",
            "final_score": 3.9,
        }],
        "ai_first": True,
        "biz_signals": [{
            "article_id": "b1", "title": "해외 발주 & EPC",
            "url": "https://www.bbc.com/news/business",
        }],
        "risk_signals": [],
        "category_counts": [],
        "macro_data_mode": "live",
        "macro_snapshot": {
            "macro_data_mode": "live",
            "source": "Yahoo Finance",
            "updated_at": "2026-06-17T09:00:00+09:00",
            "values": [{"label": "KOSPI", "value": "2860.1", "unit": "", "direction": "up"}],
        },
    }
    msg = tg.format_digest_message(data)
    check("Telegram 제목 링크 생성", '<a href="https://www.reuters.com/technology/">' in msg)
    check("Telegram 제목 HTML escape", "AI &lt;DC&gt; &amp; 전력" in msg)
    check("invalid URL은 링크로 만들지 않음", 'href="ftp://invalid.local' not in msg)
    check("URL 누락 항목도 digest 생성 유지", "URL 없음도 안전" in msg)
    check("기존 버튼 payload 유지",
          "reply_markup" in send_telegram.build_payload(
              "DRY", msg, "https://www.hdec.kr/report",
              "https://t.me/hdec_bot?start=ask_today"))
    payload = send_telegram.build_payload("DRY", msg, "", "")
    check("Telegram payload parse_mode=HTML", payload.get("parse_mode") == "HTML")
    # P0-D1.5: macro 기준시각은 KST 벽시계로 표기한다 (raw +09:00/+00:00 offset 비노출).
    check("live macro digest에 Yahoo Finance + KST 참고시각 표기",
          "Yahoo Finance" in msg and "시장지표 참고시각" in msg
          and "2026-06-17 09:00" in msg and "(KST 기준)" in msg)
    check("live macro digest에 raw UTC/ISO offset 비노출",
          "+00:00" not in msg and "+09:00" not in msg)
    check("live macro digest에 시장지표 미연동 없음", "시장지표 미연동" not in msg)
    for bad in ("[AI 관련 Top 3]", "[주요 테마]", "뉴스 자동 수집", "판정 신뢰도"):
        check(f"digest에 '{bad}' 없음", bad not in msg)
    check("digest에 검토/추적 필요 라벨 없음", "검토 필요" not in msg and "추적 필요" not in msg)
    check("digest에 user-facing 현대건설 직접 없음", "현대건설 직접" not in msg)
    check("digest token-like 문자열 없음", not TOKEN_SHAPE.search(msg))

    resolver = ("import sys; sys.path.insert(0, 'scripts'); "
                "from send_telegram import resolve_message; "
                "m,s=resolve_message(); print(s); print(m)")
    proc = subprocess.run([sys.executable, "-c", resolver], capture_output=True,
                          text=True, cwd=ROOT, timeout=120,
                          env=_clean_env(MESSAGE="plain <x> & y"))
    lines = (proc.stdout or "").splitlines()
    check("MESSAGE env fallback source 유지", proc.returncode == 0 and lines[:1] == ["env-message"])
    check("MESSAGE env fallback HTML escape", len(lines) >= 2 and "plain &lt;x&gt; &amp; y" in lines[1])


def check_report_tabs_and_labels() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_p0c3_report_") as tmp:
        out = Path(tmp) / "latest.html"
        proc = run_script(REPORT_BUILDER, "--output", str(out))
        if not check("static report 생성", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "").strip()[-200:]):
            return
        html = out.read_text(encoding="utf-8")

    check("report 탭/필터 UI 존재", 'class="radar-tabs-ui"' in html)
    check("report primary nav anchor jump 없음",
          'href="#hdec-radar"' not in html and 'href="#ai-radar"' not in html
          and 'href="#biz-radar"' not in html)
    check("report default tab checked 존재",
          bool(re.search(r'id="radar-tab-(hdec|ai)"[^>]* checked', html)))
    check("report 탭 라벨 현대건설 연관 포함", "현대건설 연관" in html)
    check("전체 근거가 기본 selected 아님", 'id="radar-tab-evidence" checked' not in html)
    check("전체 근거/세부 details open 없음", not re.search(r"<details[^>]*\bopen\b", html))
    check("report 판정 신뢰도 없음", "판정 신뢰도" not in html)
    check("report raw confidence 없음", "confidence " not in html.lower())
    check("report user-facing 현대건설 직접 없음", "현대건설 직접" not in html)
    check("report 검토/추적 필요 라벨 없음", "검토 필요" not in html and "추적 필요" not in html)
    check("report 원문 링크 안전", not _anchor_safety_issues(html))
    check("report <script> 없음", "<script" not in html.lower())
    hits = [t for t in BANNED_TERMS if t in html.lower()]
    check("report 본문 전문/X platform/token 금지어 없음", not hits, ", ".join(hits))
    check("report token-like 문자열 없음", not TOKEN_SHAPE.search(html))

    dash = TEMPLATE.read_text(encoding="utf-8")
    check("dashboard 판정 신뢰도 문구 없음", "판정 신뢰도" not in dash)
    check("dashboard 현대건설 연관 탭 라벨", "현대건설 연관" in dash)


def check_macro_live_contradiction() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_executive_brief import build_brief_via_mock_pipeline
    from build_static_report import render_report_html

    brief = build_brief_via_mock_pipeline()
    brief["macro_data_mode"] = "live"
    brief["macro_source"] = "Yahoo Finance"
    brief["macro_updated_at"] = "2026-06-17T09:00:00+09:00"
    # P0-D1.5: data_warning은 _data_warning 실제 출력과 동일하게 KST 참고시각으로 표기한다.
    brief["data_warning"] = "뉴스: 데모(mock) 데이터 · 시장지표: Yahoo Finance · 참고시각 2026-06-17 09:00 (KST 기준)"
    brief["macro_snapshot"] = {
        "macro_data_mode": "live",
        "source": "Yahoo Finance",
        "updated_at": "2026-06-17T09:00:00+09:00",
        "values": [{"label": "KOSPI", "value": "2860.1", "unit": "", "direction": "up"}],
        "disclaimer": "Yahoo Finance 기준",
    }
    html, _sections = render_report_html(brief)
    check("live macro report Yahoo Finance 표기", "Yahoo Finance" in html)
    # P0-D1.5: 리포트도 macro 기준시각을 KST 벽시계로 표시한다 (raw +00:00/+09:00 offset 비노출).
    check("live macro report KST 참고시각 표기",
          "시장지표 참고시각" in html and "2026-06-17 09:00" in html
          and "(KST 기준)" in html)
    check("live macro report raw UTC/ISO offset 비노출",
          "+00:00" not in html and "+09:00" not in html)
    check("live macro 값과 시장지표: 미연동 동시표시 없음", "시장지표: 미연동" not in html)


def main() -> int:
    print(f"== verify_report_ux_and_telegram_links @ {ROOT} ==")
    db_before = _db_state()

    check_telegram_links()
    check_report_tabs_and_labels()
    check_macro_live_contradiction()

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
