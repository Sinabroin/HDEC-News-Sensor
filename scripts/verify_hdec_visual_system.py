#!/usr/bin/env python3
"""D7-AE-RC1 verifier — HDEC 시각 시스템(Pretendard · serif 제거 · 로고 정직성).

사용자 실사용 QA 실패: "디자인이 전반적으로 구리다. Pretendard 체를 적용하라. 현대건설
로고 asset을 찾아 사용하라. 없으면 가짜 로고를 만들지 마라. serif/Georgia 스타일은
제거하라." 진단: templates/dashboard_preview.html(공개 대시보드 소스)은 Pretendard가
전혀 없는 시스템 폰트 스택을 썼고, 마스트헤드·featured 기사 제목에 Georgia/Times New
Roman serif를 썼다(scripts/build_static_report.py는 이미 Pretendard 우선 스택을 쓰고
있어 대조됐다). repo 전수 검색 결과 로고 asset은 존재하지 않는다.

이 verifier가 잠그는 계약:
  A. body font-family에 Pretendard가 최우선 순위로 존재(외부 CDN 아닌 순수 이름 선언).
  B. Georgia/Times New Roman이 템플릿 어디에도 없다(주석 포함 — 재발 방지).
  C. 로고 — repo에 실제 이미지 asset이 없으므로, 템플릿이 가짜/외부 로고를 참조하지
     않는다(외부 이미지 URL 0건, hyundai 도메인 미참조) + docs에 "logo asset needed"가
     문서화돼 있다.
  D. 헤더 문구가 "현대건설 임원용 신호 브리프"로 명확하다(사용자 지정 문구).
  E. "데모 데이터" 배지가 상시 노출 요소(마스트헤드/헤더 통계줄)에 없다 — Phase G가
     고친 시장 드로어 조건부 배지와 함께, 시각 정직성 관점에서 보강 확인.
  F. 폰트 선언이 네트워크 요청 0건(@import/<link rel=stylesheet> 폰트 CDN 없음).
  G. 공개 산출물(fresh mock 빌드 + 커밋본 있으면)에도 동일 계약이 적용된다.

완전 OFFLINE · 네트워크 0건.
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
VISUAL_DOC = ROOT / "docs" / "operations" / "D7AE_RC1_VISUAL_SYSTEM.md"

_failures: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def info(msg: str) -> None:
    print(f"[INFO] {msg}")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _check_html(html: str, label: str) -> None:
    # A · Pretendard 최우선
    m = re.search(r'font-family:\s*Pretendard\s*,', html)
    check(f"A[{label}]: body font-family가 Pretendard로 시작", m is not None)

    # B · Georgia/Times New Roman 완전 부재(주석 포함)
    check(f"B[{label}]: Georgia 문자열 0건", "Georgia" not in html)
    check(f"B[{label}]: Times New Roman 문자열 0건", "Times New Roman" not in html)

    # C · 로고 정직성(D7-AE-RC3 갱신) — 공식 CI asset(docs/assets/brand/hdec-logo.svg,
    # 출처: hdec.kr 공식 사이트 헤더)이 확보돼 계약이 반전됐다: 빌더 산출물은 공식 로고를
    # data-URI로 임베드해야 하고(외부 fetch 0건), 템플릿은 <img> 로고가 없어야 한다
    # (임베드는 빌더 소유 — placeholder/가짜 로고 생성 금지 원칙은 유지).
    img_tags = re.findall(r"<img\b[^>]*>", html, re.I)
    logo_imgs = [t for t in img_tags if "cilogo" in t.lower() or "logo" in t.lower()
                 or "hyundai" in t.lower()]
    if label.startswith("template"):
        check(f"C[{label}]: 템플릿에 <img> 로고 없음(임베드는 빌더 소유)", not logo_imgs,
              str(logo_imgs[:2]))
    else:
        ok = (len(logo_imgs) >= 1
              and all('src="data:image/svg+xml;base64,' in t for t in logo_imgs)
              and any('alt="현대건설"' in t for t in logo_imgs))
        check(f"C[{label}]: 공식 CI 로고가 data-URI로 임베드(외부 URL 아님·alt=현대건설)",
              ok, str(logo_imgs[:1])[:120])
    # 외부 fetch 금지 — http(s) src를 가진 <img>가 하나도 없어야 한다(로고 포함 전부
    # data-URI). 도메인 substring 검사는 쓰지 않는다: 'hdec.kr/warning'은 사내 차단페이지
    # 감지 JS의 정당한 리터럴이다(이미지 fetch 아님).
    http_imgs = [t for t in img_tags if re.search(r'src="https?://', t, re.I)]
    check(f"C[{label}]: http(s) src <img> 0건(로고 포함 전부 data-URI/무이미지)",
          not http_imgs, str(http_imgs[:2])[:120])

    # D · 헤더 문구
    check(f"D[{label}]: 헤더 문구 '현대건설 임원용 신호 브리프' 존재",
          "현대건설 임원용 신호 브리프" in html)
    check(f"D[{label}]: 옛 문구('임원용 외부 신호 브리프') 잔존 없음",
          "임원용 외부 신호 브리프" not in html)

    # E · 마스트헤드/헤더 통계줄에 '데모 데이터' 배지가 상시 노출되지 않음. <header 태그
    # 자체(문서 맨 위 주석/<head>/CSS 제외)부터 그 닫는 태그까지만 본다 — 파일 앞부분의
    # 개발자용 HTML 주석(예: "데모 데이터다(실데이터 아님)" 설명)을 오탐하지 않는다.
    header_start = html.find("<header")
    header_end = html.find("</header>")
    header_html = (html[header_start:header_end]
                   if 0 <= header_start < header_end else "")
    check(f"E[{label}]: 헤더(마스트헤드) 영역에 '데모 데이터' 배지 없음",
          bool(header_html) and "데모 데이터" not in header_html,
          header_html[:120] if "데모 데이터" in header_html else f"header_html_len={len(header_html)}")

    # F · 폰트 CDN(@import/<link rel=stylesheet> 폰트) 0건.
    check(f"F[{label}]: @import 폰트 CDN 없음", "@import" not in html)
    check(f"F[{label}]: <link> 폰트 CDN(fonts.googleapis/fonts.gstatic 등) 없음",
          "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html
          and "cdn.jsdelivr.net" not in html)


def check_template() -> None:
    tpl = _read(TEMPLATE)
    if not check("템플릿 파일 로드", bool(tpl)):
        return
    _check_html(tpl, "template(/dashboard-preview)")
    # D7-AE-RC3 — placeholder 아이콘(.mark 추상 SVG)은 제거됐고 로고 슬롯 주석만 남는다.
    check("template: placeholder 아이콘(.mark) 제거 + 빌더 로고 슬롯 주석 존재",
          '<div class="mark">' not in tpl and "_embed_brand_logo" in tpl)
    check("template: (구 계약 무효화 확인) 마스트헤드 추상 SVG placeholder 부재",
          '<div class="mark">' not in tpl)


def check_visual_doc() -> None:
    doc = _read(VISUAL_DOC)
    check("G1: 시각 시스템 조사 문서 존재", bool(doc))
    check("G2: 'logo asset needed' 문서화(사용자 지시 준수)",
          "logo asset needed" in doc)
    check("G3: repo 전수 검색 결과(로고 없음) 기록", "로고 asset이 없다" in doc)
    check("G4: 인터넷에서 로고를 가져오지 않았다는 확인 기록",
          "인터넷에서 로고를 가져오지 않았다" in doc)
    check("G5: Pretendard 적용 근거(CDN 아닌 font-family 선언) 기록",
          "font-family" in doc and "CDN" in doc)
    # D7-AE-RC3 — 공식 CI 확보로 '로고 없음' 결론이 해소됐음이 같은 문서에 기록돼야 한다.
    check("G6: RC3 공식 CI 확보(docs/assets/brand) 해소 기록",
          "hdec-logo.svg" in doc and "해소" in doc)


def check_fresh_build() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_visual_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                              cwd=ROOT, capture_output=True, text=True, timeout=300,
                              env={**os.environ})
        if not check("mock 빌드 동작(오프라인)", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        _check_html(out.read_text(encoding="utf-8"), "fresh-mock-build")


def check_committed() -> None:
    html = _read(DASHBOARD)
    if not html:
        info("커밋된 공개 대시보드 없음 — SKIP")
        return
    _check_html(html, "committed")


def main() -> int:
    print(f"== verify_hdec_visual_system (D7-AE-RC1) @ {ROOT} ==")
    check_template()
    check_visual_doc()
    check_fresh_build()
    check_committed()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — HDEC 시각 시스템(Pretendard · serif 제거 · 로고 정직성 · "
          "헤더 문구 · 데모 배지 미상시노출) (D7-AE-RC1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
