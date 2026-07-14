#!/usr/bin/env python3
"""Offline verifier for D7-AJ-3 Teams delta Adaptive Card delivery.

네트워크·비밀값 0건. Teams는 webhook Adaptive Card를 우선 전송하고 성공 시 채널 이메일을
보내지 않으며(중복 0), 미설정/실패면 이메일로 fallback한다. webhook URL은 카드/로그/공개
artifact 어디에도 노출되지 않는다. 네트워크는 주입한 opener로 mock 처리한다.
"""

from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import delta_alert as da  # noqa: E402
import send_email_alert as sender  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "scheduled-live-refresh.yml"
PUBLIC_HTML = [ROOT / "docs" / "index.html", *sorted((ROOT / "docs" / "daily").glob("*.html"))]
# 절대 노출돼선 안 되는 표식이 있는 fixture webhook URL (실제 값 아님).
SECRET_WEBHOOK = "https://prod-99.webhook.office.com/workflows/SECRET-TOKEN-XYZ/triggers/x"
DASH_URL = "https://guides.example/HDEC-News-Sensor/daily/dashboard-latest.html"
REPORT_URL = "https://guides.example/HDEC-News-Sensor/daily/latest.html"

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


def _alert(n: int = 2):
    articles = [
        {"article_key": f"n{i}", "title": f"현대건설 변동 {i}",
         "published_kst": f"2026-07-14 12:{20 - i:02d}", "source": "한국경제",
         "category": "현대건설 연관", "hdec_relevance": "직접 수주 영향",
         "url": f"https://ex.com/n{i}"}
        for i in range(1, n + 1)
    ]
    return da.parse_delta_alert({
        "schema_version": 1, "generated_at": "2026-07-14T12:32:00+09:00",
        "generated_kst": "2026-07-14 12:32", "source": "live-delta",
        "alert_delta": True, "changed_count": n, "new_candidate_count": n,
        "judgment": "AI·전력 인프라 수주와 안전 리스크를 함께 봐야 합니다.",
        "articles": articles,
    }, dashboard_url=DASH_URL, report_url=REPORT_URL)


# ── 네트워크 mock opener들 (실제 소켓 0건) ────────────────────────────────────

class _FakeResp:
    def __init__(self, code: int):
        self._code = code

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    @property
    def status(self) -> int:
        return self._code


def _make_opener(code=None, exc=None):
    calls: list[bytes] = []

    def opener(request, timeout=None):
        calls.append(request.data)
        opener.url = request.full_url
        if exc is not None:
            raise exc
        return _FakeResp(code)

    opener.calls = calls
    return opener


# ── A. Adaptive Card 구조 ────────────────────────────────────────────────────

def check_card_structure() -> None:
    card = da.build_teams_card(_alert(2))
    check("A 최상위 type=message", card.get("type") == "message")
    att = (card.get("attachments") or [{}])[0]
    content = att.get("content") or {}
    check("A attachment이 Adaptive Card",
          att.get("contentType") == "application/vnd.microsoft.card.adaptive"
          and content.get("type") == "AdaptiveCard")
    body_texts = [b.get("text", "") for b in content.get("body", [])]
    check("A 카드 상단: HDEC EXECUTIVE RADAR",
          any("HDEC EXECUTIVE RADAR" in t for t in body_texts))
    check("A 카드 상단: 실제 KST + 신규 N건",
          any("2026-07-14 12:32 KST · 신규 2건" == t for t in body_texts))
    check("A 07:00 잔재 0건", all("07:00" not in t for t in body_texts))
    check("A 오늘의 판단 포함", any("오늘의 판단:" in t for t in body_texts))
    actions = content.get("actions") or []
    titles = [a.get("title", "") for a in actions]
    check("A Action.OpenUrl: 요약 대시보드 + 전체 리포트",
          "요약 대시보드 보기" in titles and "전체 리포트 보기" in titles)
    check("A Action.OpenUrl: 기사별 원문 2건",
          sum(1 for t in titles if t.startswith("원문 보기")) == 2)
    check("A 모든 action이 Action.OpenUrl",
          all(a.get("type") == "Action.OpenUrl" and a.get("url") for a in actions))
    # 카드에는 webhook URL이 담기지 않는다 (대시보드/리포트/기사 URL만)
    import json as _json
    card_json = _json.dumps(card, ensure_ascii=False)
    check("A 카드에 webhook URL 미포함", SECRET_WEBHOOK not in card_json
          and "webhook.office.com" not in card_json)


# ── B. post_teams_card — 네트워크 mock · 상태만 반환 · URL 비노출 ─────────────

def check_post_card() -> None:
    ok_opener = _make_opener(code=202)
    delivered, status = sender.post_teams_card(SECRET_WEBHOOK, {"type": "message"}, ok_opener)
    check("B 2xx → 성공(accepted)", delivered is True and status == "accepted_202")
    check("B 실제 POST 1건(mock)", len(ok_opener.calls) == 1)
    check("B 반환 상태에 webhook URL 미노출", SECRET_WEBHOOK not in status)

    http_opener = _make_opener(exc=urllib.error.HTTPError(SECRET_WEBHOOK, 500, "e", {}, None))
    d2, s2 = sender.post_teams_card(SECRET_WEBHOOK, {"type": "message"}, http_opener)
    check("B HTTP 5xx → 실패(http_error_500)", d2 is False and s2 == "http_error_500")
    check("B HTTP 에러 상태에 URL 미노출", SECRET_WEBHOOK not in s2)

    tx_opener = _make_opener(exc=urllib.error.URLError("no route"))
    d3, s3 = sender.post_teams_card(SECRET_WEBHOOK, {"type": "message"}, tx_opener)
    check("B transport 예외 → 실패(transport_error)", d3 is False and s3 == "transport_error")

    reject_opener = _make_opener(code=403)
    d4, s4 = sender.post_teams_card(SECRET_WEBHOOK, {"type": "message"}, reject_opener)
    check("B non-2xx(403) → 실패(rejected)", d4 is False and s4 == "rejected_403")


# ── C. plan_teams_delivery — webhook 우선 · 이메일 fallback · 중복 금지 ───────

def _targets(*kinds):
    return [sender.DeliveryTarget(f"t_{k}", f"{k}@ex.com", k) for k in kinds]


def check_delivery_plan() -> None:
    alert = _alert(1)

    # webhook 없음 → 카드 미전송, Teams 채널 이메일 1건 (fallback)
    targets = _targets("teams_channel")
    delivered, email_targets, status = sender.plan_teams_delivery(alert, targets, "")
    check("C webhook 없음 → 이메일 1건(카드 0)",
          delivered is False and len(email_targets) == 1
          and email_targets[0].recipient_kind == "teams_channel" and status == "webhook_skipped")

    # webhook 성공 → 카드 1건, Teams 이메일 0건 (중복 없음)
    ok_poster = lambda url, card: (True, "accepted_202")  # noqa: E731
    targets = _targets("teams_channel")
    delivered, email_targets, status = sender.plan_teams_delivery(alert, targets, SECRET_WEBHOOK, ok_poster)
    check("C webhook 성공 → 카드 1건 · Teams 이메일 0건(중복 금지)",
          delivered is True
          and not any(t.recipient_kind == "teams_channel" for t in email_targets))

    # webhook 성공 + 메일박스 → 카드 1건 + 메일박스 이메일 유지, Teams 이메일만 제거
    targets = _targets("mailbox", "teams_channel")
    delivered, email_targets, status = sender.plan_teams_delivery(alert, targets, SECRET_WEBHOOK, ok_poster)
    kinds = sorted(t.recipient_kind for t in email_targets)
    check("C webhook 성공 시 메일박스는 유지, Teams 이메일만 제거",
          delivered is True and kinds == ["mailbox"])

    # webhook 실패 → 카드 시도 1건, Teams 채널 이메일 1건 (fallback)
    fail_poster = lambda url, card: (False, "http_error_500")  # noqa: E731
    targets = _targets("teams_channel")
    delivered, email_targets, status = sender.plan_teams_delivery(alert, targets, SECRET_WEBHOOK, fail_poster)
    check("C webhook 실패 → 카드 시도 + Teams 이메일 fallback 1건",
          delivered is False and len(email_targets) == 1
          and email_targets[0].recipient_kind == "teams_channel")

    # delta 아님(daily 브리프) → webhook 미시도, 원래 target 유지
    delivered, email_targets, status = sender.plan_teams_delivery(None, _targets("teams_channel"),
                                                                  SECRET_WEBHOOK, ok_poster)
    check("C delta 아님 → webhook 미시도(daily 브리프 이메일 경로 불변)",
          delivered is False and status == "webhook_skipped" and len(email_targets) == 1)


# ── D. 워크플로 배선 + 비밀값 · 공개 artifact 잔재 0 ─────────────────────────

def check_workflow_and_secrets() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    check("D teams 스텝이 webhook을 secrets에서 주입",
          "TEAMS_WORKFLOW_WEBHOOK_URL: ${{ secrets.TEAMS_WORKFLOW_WEBHOOK_URL }}" in text)
    check("D webhook URL 하드코딩 없음(https webhook.office.com/powerautomate 리터럴 0)",
          "https://" not in text.split("TEAMS_WORKFLOW_WEBHOOK_URL")[1].split("\n")[0])
    src = (ROOT / "scripts" / "send_email_alert.py").read_text(encoding="utf-8")
    check("D sender가 webhook URL을 출력하지 않음(상태 라벨만)",
          "print(webhook_url" not in src and 'f"{webhook_url' not in src)
    check("D sender가 https만 허용(정직성)", 'startswith("https://")' in src)

    # 공개 artifact에 webhook 흔적 0
    leaks = []
    shape = "webhook.office.com"
    for path in PUBLIC_HTML:
        if not path.exists():
            continue
        t = path.read_text(encoding="utf-8")
        if "TEAMS_WORKFLOW_WEBHOOK_URL" in t or shape in t or "powerautomate" in t.lower():
            leaks.append(str(path.relative_to(ROOT)))
    check("D 공개 artifact에 webhook 이름/URL 흔적 0", not leaks, ", ".join(leaks[:5]))


def main() -> int:
    print(f"== verify_teams_delta_card (D7-AJ-3) @ {ROOT} ==")
    check_card_structure()
    check_post_card()
    check_delivery_plan()
    check_workflow_and_secrets()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        return 1
    print("RESULT: PASS — Teams delta Adaptive Card: webhook-first, email fallback, "
          "no duplicate, network mocked, zero webhook-URL leakage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
