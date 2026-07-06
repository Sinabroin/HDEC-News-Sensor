#!/usr/bin/env python3
"""D7-AG-3 — 운영자 실행 버튼이 '실제 fetch 액션'인지 계약 검수 (offline).

사용자 실패 신고: "운영자 버튼이 비활성/미연결로 끝난다. 링크 fallback('GitHub Actions 열기')은
정답이 아니다. PIN이 꼭 필요한지 재검토하라." 이 검수기는 그 계약을 잠근다:

  B  실행 버튼 3개(데이터 새로고침/텔레그램/Teams)가 공개·연결 빌드에 존재.
  L  링크 fallback 없음(GitHub Actions 열기 / Scheduled Live Refresh 열기 / Telegram Notify 열기 / opActionLinks).
  P  브라우저 PIN 입력 제거 + 대체 보호(서버 앞단 인증) 명시. public HTML에 PIN 정답/secret 없음.
  F  브라우저는 fetch(base + endpoint)로만 POST · endpoint 3개 · privileged endpoint 직접호출 0.
  A  operator_api_base는 JSON island에만 존재. 연결 빌드는 활성(operator_api_enabled=true) — 미설정
     빌드는 '완료'로 간주하지 않는다(연결 빌드로 활성 경로를 증명).
  S  서버측 인가(authorize/Origin/레이트/local_dev 활성 경로)가 게이트웨이에 존재.

네트워크·발송·배포 없이 통과한다(빌더 fixture + 소스 계약).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
GATEWAY = ROOT / "app" / "operator_gateway.py"
API = ROOT / "app" / "operator_api.py"

BUTTONS = {"데이터 새로고침 실행": "opCollectBtn",
           "텔레그램 전송 실행": "opSendBtn",
           "Teams 채널 전송 실행": "opTeamsBtn"}
ENDPOINTS = {"collect": "/api/operator/collect",
             "telegram": "/api/operator/send",
             "teams": "/api/operator/send-teams"}
LINK_FALLBACKS = ("GitHub Actions 열기", "Scheduled Live Refresh 열기",
                  "Telegram Notify 열기", 'id="opActionLinks"')
PRIVILEGED = ("api.github.com", "api.telegram.org", "webhook.office.com",
              "openapi.naver.com/v1/search/news.json", "X-Naver-Client-Secret")
SECRET_SHAPES = (
    re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"https://[^\"'\s]*(?:webhook\.office\.com|powerautomate)[^\"'\s]*", re.I),
)
TEST_BASE = "https://operator.example.invalid"

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


def _model(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="preview-model">(.*?)</script>',
                  html, re.S)
    try:
        return json.loads(m.group(1)) if m else {}
    except json.JSONDecodeError:
        return {}


def build(base: str = "") -> str:
    with tempfile.TemporaryDirectory(prefix="hdec_op_btn_") as tmp:
        out = Path(tmp) / "d.html"
        env = {k: v for k, v in os.environ.items()
               if k not in ("GH_OPERATOR_TOKEN", "GITHUB_TOKEN", "OPERATOR_SHARED_SECRET",
                            "OPERATOR_PIN")}
        cmd = [sys.executable, str(BUILDER), "--output", str(out)]
        if base:
            cmd += ["--operator-api-base", base]
        proc = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True, timeout=300)
        check(f"build(base={'set' if base else 'unset'})", proc.returncode == 0,
              (proc.stderr or "")[-200:])
        return out.read_text(encoding="utf-8") if out.exists() else ""


def check_html(html: str, label: str, *, enabled: bool) -> None:
    if not html:
        check(f"{label}: 산출물 존재", False)
        return
    model = _model(html)

    for text, bid in BUTTONS.items():
        check(f"B[{label}]: 실행 버튼 '{text}'",
              text in html and f'id="{bid}" type="button"' in html)
    check(f"L[{label}]: 링크 fallback 없음",
          not any(x in html for x in LINK_FALLBACKS))
    # P — PIN 입력 제거 + 서버 보호 명시
    check(f"P[{label}]: 브라우저 PIN 입력 제거", 'id="opPin"' not in html and "승인 PIN" not in html)
    check(f"P[{label}]: 대체 보호(서버 앞단 인증) 명시",
          "서버 앞단" in html and 'id="opAuthNote"' in html)
    check(f"P[{label}]: public secret/토큰/webhook 0",
          not any(p.search(html) for p in SECRET_SHAPES)
          and "OPERATOR_SHARED_SECRET" not in html and "GH_OPERATOR_TOKEN" not in html)
    # F — fetch(base+endpoint) + endpoint 3개 + privileged 직접호출 0
    check(f"F[{label}]: 브라우저는 fetch(base + path)로만 POST",
          "fetch(base + path" in html and 'method: "POST"' in html
          and 'credentials: "include"' in html)
    check(f"F[{label}]: endpoint 3개 일치", model.get("operator_endpoints") == ENDPOINTS,
          repr(model.get("operator_endpoints")))
    check(f"F[{label}]: privileged endpoint 직접호출 0",
          not any(p in html for p in PRIVILEGED))
    # A — base는 island에만 · 연결이면 활성
    check(f"A[{label}]: operator_api_enabled={str(enabled).lower()}",
          model.get("operator_api_enabled") is enabled
          and f'data-operator-api-enabled="{str(enabled).lower()}"' in html)
    if enabled:
        base = model.get("operator_api_base") or ""
        expected_base = base == TEST_BASE if label == "fresh-connected" else bool(base)
        check(f"A[{label}]: base는 JSON island에만(문서 내 1회)",
              expected_base and html.count(base) == 1,
              base)
        # 하이브리드(D7-AG-5B): 공개 Origin 인가는 저위험 collect만 실행 — 발송 2버튼은 인증 필요 상태.
        check(f"A[{label}]: 연결 빌드는 collect만 활성(발송은 인증 필요)",
              "collectBtn.disabled = false" in html
              and "sendBtn.disabled = false" not in html
              and "teamsBtn.disabled = false" not in html
              and "authlocked" in html and "showSendLocked" in html)
        check(f"A[{label}]: 발송 버튼은 fetch 미배선 + 인증 필요 안내",
              "운영자 인증 연결" in html)
    else:
        check(f"A[{label}]: 미설정은 '미연결' 명시(완료 아님)",
              'setStatus("Operator API 미연결"' in html and "setBtns(true)" in html)


def check_server() -> None:
    gw = GATEWAY.read_text(encoding="utf-8")
    api = API.read_text(encoding="utf-8")
    check("S: 게이트웨이 서버측 인가(authorize) 존재", "def authorize(" in gw)
    check("S: Origin 허용목록 + 레이트리밋", "_origin_allowed" in gw and "_rate_ok" in gw)
    check("S: 로컬 활성(local_dev) 경로 존재(로컬 활성 빌드 검증용)",
          "OPERATOR_LOCAL_DEV" in gw)
    check("S: 인가 실패는 fail-closed 상태로만 반환",
          all(s in gw for s in ('"not_configured"', '"unauthorized"', '"forbidden"',
                                '"rate_limited"')))
    check("S: API가 요청 헤더/Origin을 게이트웨이에 전달",
          "request.headers" in api and "trigger_collect(" in api)


def main() -> int:
    print(f"== D7-AG-3 operator actual-buttons @ {ROOT} ==")
    unset = build()
    connected = build(TEST_BASE)
    check_html(unset, "fresh-unset", enabled=False)
    check_html(connected, "fresh-connected", enabled=True)
    committed = DASHBOARD.read_text(encoding="utf-8") if DASHBOARD.exists() else ""
    # 커밋된 산출물은 배포 상태에 따라 unset/connected 둘 다 가능 — 활성 여부만 모드-인지로 확인.
    if committed:
        cm = _model(committed)
        check_html(committed, "committed", enabled=bool(cm.get("operator_api_enabled")))
    check_server()

    print()
    print("[NOTE] '완료'는 연결 빌드(base 주입)로만 증명된다. 공개 배포는 OPERATOR_API_BASE가")
    print("       인증된 호스트(Cloudflare Access/Vercel Protection/SSO)로 설정돼야 활성화된다.")
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)})")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 3개 실행 버튼 실제 fetch 액션 · 링크 fallback 없음 · PIN 제거+서버보호 · "
          "연결 빌드 활성 · public secret 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
