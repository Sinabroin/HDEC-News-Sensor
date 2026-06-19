"""P0-D3P verifier — operator vs executive 리포트 뷰 + 리스크 사건 근거 링크 회귀 검사.

결정적, 오프라인(네트워크 0건·비밀값 0건), 임시 출력 파일만 사용한다. mock 파이프라인으로
정적 리포트를 두 audience 모드로 빌드한 뒤 다음을 검증한다:

- operator 뷰는 운영자 감사 표면(리스크 사건 클러스터 / 운영자 확인 / 발송불가)을 유지한다.
- executive 뷰는 '주요 리스크 사건'으로 리네이밍하고 운영자 전용 문구를 노출하지 않는다.
- 리스크 사건 근거 기사는 양쪽 모두 원문 링크(target=_blank rel=noopener noreferrer)로 렌더한다.
- executive 뷰는 기사 링크(href) 외 외부 리소스가 없고, telegram/webhook/token 문자열이 없다.
- executive 뷰도 HDEC Executive Radar와 핵심 섹션을 유지한다.

repo radar.db는 절대 건드리지 않는다 (mock 파이프라인이 임시 DB를 쓴다).

사용법:
    python3 scripts/verify_report_audience_modes.py
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_BUILDER = ROOT / "scripts" / "build_static_report.py"
RADAR_DB = ROOT / "radar.db"

# operator 본문에 반드시 남아 있어야 하는 운영자 감사 표지 (검증용 회귀 가드).
OPERATOR_REQUIRED = ["리스크 사건 클러스터", "운영자 확인", "발송불가"]

# 운영자 전용 — executive 본문에 등장하면 안 되는 문구 (P0-D3P 계약).
EXEC_FORBIDDEN = [
    "운영자 확인", "발송불가", "운영자 점검", "출처 품질 제외 결과",
    "참고/제외 기사", "중복 제어", "검증용", "review_required", "send_allowed",
]

# 모드 무관 핵심 마커 — executive에서도 유지되어야 한다.
CORE_MARKERS = [
    "HDEC Executive Radar", "Executive Daily Brief", "오늘의 Executive Signal",
    "주요 테마", "카테고리 요약", "AI 관련", "리스크·규제", "거시경제", "전체 근거",
]

# Telegram bot token 모양(숫자ID:시크릿) — 어디에도 노출 금지
TOKEN_SHAPE = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _clean_env(**extra: str) -> dict:
    env = {**os.environ, "APP_MODE": "mock"}
    for key in ("MESSAGE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_IDS",
                "DB_PATH", "REPORT_URL", "NEWS_MODE", "MACRO_MODE",
                "REPORT_AUDIENCE"):
        env.pop(key, None)
    env.update(extra)
    return env


def _db_state():
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _build(out_path: Path, *flags: str, env: dict | None = None):
    return subprocess.run(
        [sys.executable, str(REPORT_BUILDER), "--output", str(out_path), *flags],
        capture_output=True, text=True, env=env or _clean_env(), cwd=ROOT, timeout=300)


def _risk_section(html: str, label: str) -> str:
    """리스크 사건 <section aria-label="..."> 내용만 추출한다 (하나의 details만 감싼다)."""
    m = re.search(r'<section aria-label="' + re.escape(label) + r'">(.*?)</section>',
                  html, re.S)
    return m.group(1) if m else ""


def _evidence_anchors(section_html: str) -> list[tuple[str, str]]:
    """'근거' 라인 안의 http(s) 앵커들을 (open_tag, href)로 돌려준다."""
    out = []
    for m in re.finditer(r"<strong>근거</strong>(.*?)</p>", section_html, re.S):
        for a in re.finditer(r"<a\b[^>]*>", m.group(1)):
            tag = a.group(0)
            href = re.search(r'href="([^"]*)"', tag)
            if href and href.group(1).lower().startswith(("http://", "https://")):
                out.append((tag, href.group(1)))
    return out


def _anchor_safe(tag: str) -> bool:
    return ('target="_blank"' in tag and "noopener" in tag and "noreferrer" in tag)


def _external_resource_issues(html: str) -> list[str]:
    """기사 원문 링크(href)만 허용 — 그 외 외부 리소스/참조는 위반으로 잡는다."""
    issues = []
    stripped = re.sub(r'href="[^"]*"', 'href=""', html)
    stripped = re.sub(r"href='[^']*'", "href=''", stripped)
    low = stripped.lower()
    if "http://" in low or "https://" in low:
        issues.append("href 외 http(s) 발견")
    for tag in ("<script", "<iframe", "<img", "<link ", "<link>", "<object", "<embed"):
        if tag in html.lower():
            issues.append(f"외부 리소스 태그 {tag}")
    for token in ("@import", "url(http", "src=http", 'src="http', "src='http"):
        if token in html.lower():
            issues.append(f"외부 참조 {token}")
    return issues


def _unsafe_anchors(html: str) -> list[str]:
    bad = []
    for m in re.finditer(r"<a\b[^>]*>", html):
        tag = m.group(0)
        href = re.search(r'href="([^"]*)"', tag)
        if not href or not href.group(1).lower().startswith(("http://", "https://")):
            continue
        if not _anchor_safe(tag):
            bad.append(tag[:70])
    return bad


def check_operator(html: str) -> None:
    for term in OPERATOR_REQUIRED:
        check(f"operator: '{term}' 유지", term in html)
    sec = _risk_section(html, "리스크 사건 클러스터")
    if not check("operator: '리스크 사건 클러스터' 섹션 추출", bool(sec)):
        return
    anchors = _evidence_anchors(sec)
    check("operator: 리스크 근거 기사에 원문 링크 존재", bool(anchors),
          f"{len(anchors)} anchors")
    unsafe = [t for t, _ in anchors if not _anchor_safe(t)]
    check("operator: 근거 링크 target=_blank rel=noopener noreferrer",
          bool(anchors) and not unsafe, "; ".join(unsafe[:2]))


def check_executive(html: str) -> None:
    check("executive: '주요 리스크 사건' 섹션명 사용", "주요 리스크 사건" in html)
    for term in EXEC_FORBIDDEN:
        check(f"executive: 운영자 전용 '{term}' 미노출", term not in html)
    check("executive: '운영자 확인용 요약' 문구 미사용", "운영자 확인용 요약" not in html)

    missing = [m for m in CORE_MARKERS if m not in html]
    check("executive: 핵심 섹션/제목 유지", not missing, "; ".join(missing))

    sec = _risk_section(html, "주요 리스크 사건")
    if check("executive: '주요 리스크 사건' 섹션 추출", bool(sec)):
        anchors = _evidence_anchors(sec)
        check("executive: 리스크 근거 기사에 원문 링크 존재", bool(anchors),
              f"{len(anchors)} anchors")
        unsafe = [t for t, _ in anchors if not _anchor_safe(t)]
        check("executive: 근거 링크 target=_blank rel=noopener noreferrer",
              bool(anchors) and not unsafe, "; ".join(unsafe[:2]))

    issues = _external_resource_issues(html)
    check("executive: 기사 링크(href) 외 외부 리소스 없음", not issues,
          "; ".join(issues[:3]))
    bad = _unsafe_anchors(html)
    check("executive: 모든 외부 링크 새 탭 + noopener noreferrer", not bad,
          "; ".join(bad[:2]))

    low = html.lower()
    check("executive: telegram/webhook 문자열 없음",
          "telegram" not in low and "webhook" not in low)
    check("executive: token 모양 문자열 없음", not TOKEN_SHAPE.search(html))


def check_default_and_env() -> None:
    """기본값=operator, env REPORT_AUDIENCE 인식, CLI가 env보다 우선, 잘못된 값은 폴백."""
    with tempfile.TemporaryDirectory(prefix="hdec_aud_") as tmp:
        p = _build(Path(tmp) / "def.html")
        check("기본 빌드(플래그/env 없음) → audience=operator",
              "audience=operator" in (p.stdout or ""), (p.stdout or "").strip()[-120:])
        p = _build(Path(tmp) / "env.html", env=_clean_env(REPORT_AUDIENCE="executive"))
        check("env REPORT_AUDIENCE=executive 인식",
              "audience=executive" in (p.stdout or ""))
        p = _build(Path(tmp) / "cli.html", "--audience", "operator",
                   env=_clean_env(REPORT_AUDIENCE="executive"))
        check("CLI --audience가 env REPORT_AUDIENCE보다 우선",
              "audience=operator" in (p.stdout or ""))
        p = _build(Path(tmp) / "bad.html", env=_clean_env(REPORT_AUDIENCE="garbage"))
        check("잘못된 REPORT_AUDIENCE 값 → operator 안전 폴백",
              "audience=operator" in (p.stdout or ""))


def main() -> int:
    print(f"== verify_report_audience_modes @ {ROOT} ==")
    before = _db_state()
    with tempfile.TemporaryDirectory(prefix="hdec_aud_") as tmp:
        op = Path(tmp) / "operator.html"
        ex = Path(tmp) / "executive.html"
        p_op = _build(op, "--audience", "operator")
        p_ex = _build(ex, "--audience", "executive")
        if not check("operator 빌드 성공 (exit 0, 파일 생성)",
                     p_op.returncode == 0 and op.exists() and op.stat().st_size > 0,
                     (p_op.stderr or "").strip()[-200:]):
            return 1
        if not check("executive 빌드 성공 (exit 0, 파일 생성)",
                     p_ex.returncode == 0 and ex.exists() and ex.stat().st_size > 0,
                     (p_ex.stderr or "").strip()[-200:]):
            return 1
        check_operator(op.read_text(encoding="utf-8"))
        check_executive(ex.read_text(encoding="utf-8"))
    check_default_and_env()
    after = _db_state()
    check("repo radar.db 변경/생성되지 않음 (temp 격리)", before == after,
          f"before={before} after={after}")

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
