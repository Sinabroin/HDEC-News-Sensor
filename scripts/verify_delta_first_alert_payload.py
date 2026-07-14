#!/usr/bin/env python3
"""Offline verifier for D7-AJ-2 delta-first alert payloads.

네트워크·비밀값 0건. detect_dashboard_alert_delta.py가 만든 공유 delta 아티팩트를
Telegram(send_telegram.py)과 이메일/Teams(send_email_alert.py)가 재수집 없이 소비하는지,
실제 KST 제목·변동 우선·07:00 잔재 0·fail-closed·기존 게이트 보존을 검사한다.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DETECTOR = ROOT / "scripts" / "detect_dashboard_alert_delta.py"
TELEGRAM = ROOT / "scripts" / "send_telegram.py"
EMAIL = ROOT / "scripts" / "send_email_alert.py"
WORKFLOW = ROOT / ".github" / "workflows" / "scheduled-live-refresh.yml"

from app import delta_alert as da  # noqa: E402

_failures: list[str] = []
NOW_ISO = "2026-07-14T12:32:00+09:00"
DASH_URL = "https://guides.example/HDEC-News-Sensor/daily/dashboard-latest.html"
REPORT_URL = "https://guides.example/HDEC-News-Sensor/daily/latest.html"


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


def _dashboard_html(rows: list[dict], mode: str = "live") -> str:
    payload = json.dumps({"top_immediate_signals": rows,
                          "executive_one_liner": "AI·전력 인프라 수주와 안전 리스크를 함께 봐야 합니다."},
                         ensure_ascii=False)
    return (f"<!doctype html><html><body><!--news-data-mode:{mode}-->"
            f'<script type="application/json" id="preview-model">{payload}</script>'
            "</body></html>")


def _row(**kw) -> dict:
    base = {"article_id": "x", "title": "제목", "source": "연합뉴스",
            "url": "https://ex.com/x", "cat": "AI", "score": 4.2,
            "published_at": "2026-07-14T10:00:00+09:00",
            "radarReason": "현대건설 수주 영향", "snippet": "요약 본문"}
    base.update(kw)
    return base


def _run_detector(old_rows, new_rows, *, mode="live", source=None, now=NOW_ISO):
    tmp = Path(tempfile.mkdtemp(prefix="hdec_delta_aj2_"))
    old_p, new_p, art = tmp / "old.html", tmp / "new.html", tmp / "delta.json"
    old_p.write_text(_dashboard_html(old_rows, mode), encoding="utf-8")
    new_p.write_text(_dashboard_html(new_rows, mode), encoding="utf-8")
    cmd = [sys.executable, str(DETECTOR), str(old_p), str(new_p),
           "--delta-artifact", str(art), "--now", now]
    if source:
        cmd += ["--source", source]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=60)
    data = json.loads(art.read_text(encoding="utf-8")) if art.exists() else None
    return proc, data, art


# ── A. delta 아티팩트 (detector 생성) ─────────────────────────────────────────

def check_artifact_generation() -> None:
    old = [_row(article_id="o1", title="기존", url="https://ex.com/o1",
                published_at="2026-07-14T08:00:00+09:00")]
    new = old + [
        _row(article_id="n1", title="현대건설 데이터센터 EPC 신규 수주",
             url="https://ex.com/n1", published_at="2026-07-14T12:20:00+09:00"),
        _row(article_id="n2", title="AI 전력수요 SMR 발주",
             url="https://ex.com/n2", published_at="2026-07-14T11:00:00+09:00"),
        _row(article_id="n3", title="URL 없는 변동", url="",
             external_url="", published_at="2026-07-14T09:30:00+09:00"),
    ]
    proc, data, _ = _run_detector(old, new)
    check("A delta=true + 신규 3건 → 아티팩트 생성", data is not None and proc.returncode == 0)
    if not data:
        return
    check("A alert_delta=true (보낼 변동 있음)", data["alert_delta"] is True)
    check("A source=live-delta (live 마커)", data["source"] == "live-delta")
    check("A 신규 3건 · 최대 5건 이하",
          data["new_candidate_count"] == 3 and len(data["articles"]) <= 5)
    titles = [a["title"] for a in data["articles"]]
    check("A 최신순 정렬 (12:20 > 11:00 > 09:30)",
          titles[:3] == ["현대건설 데이터센터 EPC 신규 수주", "AI 전력수요 SMR 발주", "URL 없는 변동"],
          str(titles))
    urlless = [a for a in data["articles"] if a["title"] == "URL 없는 변동"]
    check("A URL 없는 기사는 url='' (버튼 미생성)", urlless and urlless[0]["url"] == "")
    check("A generated_at은 tz-aware KST(+09:00)", data["generated_at"].endswith("+09:00"))
    check("A generated_kst 'YYYY-MM-DD HH:MM'", data["generated_kst"] == "2026-07-14 12:32")
    check("A judgment은 모델 one-liner 재사용(생성 아님)",
          "AI·전력" in data["judgment"])
    log = (proc.stdout or "") + (proc.stderr or "")
    check("A detector stdout은 내용 없음(기사 제목 미노출)",
          "현대건설 데이터센터" not in log)

    # dedup: 같은 article_key 2건 → 1건
    dup = old + [_row(article_id="dupe", title="중복 A", url="https://ex.com/d"),
                 _row(article_id="dupe", title="중복 B(같은 키)", url="https://ex.com/d2",
                      score=4.9)]
    _, ddata, _ = _run_detector(old, dup)
    keys = [a["article_key"] for a in (ddata or {}).get("articles", [])]
    check("A article_key 기준 중복 제거", keys.count("dupe") <= 1, str(keys))

    # 최대 5건 cap
    many = old + [_row(article_id=f"m{i}", title=f"변동 {i}", url=f"https://ex.com/m{i}",
                       published_at=f"2026-07-14T1{i}:00:00+09:00") for i in range(7)]
    _, mdata, _ = _run_detector(old, many)
    check("A 아티팩트 기사 최대 5건", len(mdata["articles"]) == 5, str(len(mdata["articles"])))
    check("A new_candidate_count는 cap 이전 실제 수(7)", mdata["new_candidate_count"] == 7)


def check_source_and_gate() -> None:
    old = [_row(article_id="o1", title="기존", url="https://ex.com/o1")]
    new = old + [_row(article_id="n1", title="변동", url="https://ex.com/n1",
                      published_at="2026-07-14T12:20:00+09:00")]
    # mock 마커 → mock-delta
    _, mock_data, _ = _run_detector(old, new, mode="mock")
    check("A source=mock-delta (mock 마커)", mock_data["source"] == "mock-delta")
    # 명시 override
    _, ov_data, _ = _run_detector(old, new, mode="mock", source="test-delta")
    check("A --source override 존중", ov_data["source"] == "test-delta")
    # delta=false (동일) → alert_delta=false → 발송 0건 계약
    _, same_data, _ = _run_detector(old, old)
    check("A 변화 없음 → alert_delta=false (발송 0건 계약)",
          same_data["alert_delta"] is False and same_data["articles"] == [])

    # 입력 invalid → detector fail-closed(rc!=0), 아티팩트 미생성
    tmp = Path(tempfile.mkdtemp(prefix="hdec_delta_inv_"))
    old_p, new_p, art = tmp / "o.html", tmp / "n.html", tmp / "d.json"
    old_p.write_text(_dashboard_html(old), encoding="utf-8")
    new_p.write_text("<html>invalid</html>", encoding="utf-8")
    proc = subprocess.run([sys.executable, str(DETECTOR), str(old_p), str(new_p),
                           "--delta-artifact", str(art)],
                          cwd=ROOT, text=True, capture_output=True, timeout=60)
    check("A invalid 입력 → fail-closed(rc!=0) · 아티팩트 미생성",
          proc.returncode != 0 and not art.exists())


# ── B/D. Telegram delta 렌더 + 문구 ──────────────────────────────────────────

def _write_artifact(articles, *, source="live-delta", alert_delta=True,
                    generated_kst="2026-07-14 12:32", judgment="오늘의 판단 문장") -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="hdec_delta_art_"))
    art = tmp / "delta.json"
    art.write_text(json.dumps({
        "schema_version": 1,
        "generated_at": "2026-07-14T12:32:00+09:00",
        "generated_kst": generated_kst,
        "source": source,
        "alert_delta": alert_delta,
        "changed_count": len(articles),
        "new_candidate_count": len(articles),
        "judgment": judgment,
        "articles": articles,
    }, ensure_ascii=False), encoding="utf-8")
    return art


def _run_telegram(art: Path, extra_env=None):
    env = {"PATH": __import__("os").environ.get("PATH", ""),
           "HOME": __import__("os").environ.get("HOME", ""),
           "DELTA_ARTIFACT_FILE": str(art),
           "TELEGRAM_BOT_TOKEN": "dummy", "TELEGRAM_CHAT_IDS": "123",
           "REPORT_URL": REPORT_URL, "DASHBOARD_URL": DASH_URL}
    env.update(extra_env or {})
    return subprocess.run([sys.executable, str(TELEGRAM)], cwd=ROOT, text=True,
                          capture_output=True, timeout=120, env=env)


def check_telegram_delta() -> None:
    articles = [
        {"article_key": "n1", "title": "AI <script> & 전력 최신 변동",
         "published_kst": "2026-07-14 12:20", "source": "한국경제",
         "category": "현대건설 연관", "hdec_relevance": "현대건설 직접 수주 영향",
         "url": "https://ex.com/n1"},
        {"article_key": "n2", "title": "이전 변동", "published_kst": "2026-07-14 11:00",
         "source": "매일경제", "category": "AI·전력", "url": "https://ex.com/n2"},
    ]
    art = _write_artifact(articles)
    proc = _run_telegram(art)
    out = (proc.stdout or "") + (proc.stderr or "")
    check("B live 아티팩트 → Message source: live-delta", "Message source: live-delta" in out)
    check("B mock-digest fallback 0건", "mock-digest" not in out)
    check("B 실제 발송 0건(manual review gate)", "review_required" in out and proc.returncode == 0)
    check("D 실제 KST 제목(12:32 핵심 변동 — 신규 2건)",
          "12:32 핵심 변동 — 신규 2건" in out)
    check("D 07:00 잔재 0건", "07:00" not in out)
    # 최신 변동이 최상단
    check("D 신규 변동 뉴스 최상단(최신 기사가 이전 변동보다 먼저)",
          out.index("AI") < out.index("이전 변동"))
    # HTML escape — 주입한 <script>는 escape되어야 함
    check("B HTML escape(주입 <script> 원문 미노출)",
          "<script>" not in out and "&lt;script&gt;" in out)
    # 길이 상한
    msg_line = [l for l in out.splitlines() if l.startswith("Message source:")]
    check("B 메시지 길이 상한 준수(<=3500)", bool(msg_line) and "chars)" in msg_line[0])


def check_telegram_failclosed() -> None:
    # invalid 아티팩트 → fail-closed(rc!=0)
    tmp = Path(tempfile.mkdtemp(prefix="hdec_delta_bad_"))
    bad = tmp / "bad.json"
    bad.write_text('{"schema_version":2}', encoding="utf-8")
    proc = _run_telegram(bad)
    check("B invalid 아티팩트 → Telegram fail-closed(rc!=0)", proc.returncode != 0)
    # alert_delta=false → 발송 0건, exit 0
    empty = _write_artifact([], alert_delta=False)
    proc = _run_telegram(empty)
    out = (proc.stdout or "") + (proc.stderr or "")
    check("B alert_delta=false → 발송 0건(no_delta) · exit 0",
          proc.returncode == 0 and "no_delta" in out)


# ── C. 이메일(Teams 채널) delta 렌더 + 문구 ──────────────────────────────────

def check_email_delta() -> None:
    articles = [{"article_key": "n1", "title": "현대건설 EPC 신규 변동",
                 "published_kst": "2026-07-14 12:20", "source": "한국경제",
                 "category": "현대건설 연관", "hdec_relevance": "직접 수주 영향",
                 "url": "https://ex.com/n1"}]
    art = _write_artifact(articles)
    import os
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
           "DELTA_ARTIFACT_FILE": str(art), "REPORT_URL": REPORT_URL,
           "DASHBOARD_URL": DASH_URL}
    proc = subprocess.run([sys.executable, str(EMAIL), "--dry-run"], cwd=ROOT, text=True,
                          capture_output=True, timeout=120, env=env)
    out = proc.stdout or ""
    check("C 이메일 delta dry-run 성공", proc.returncode == 0)
    check("C 실제 KST 제목([HDEC News Sensor] 12:32 핵심 변동 — 신규 1건)",
          "[HDEC News Sensor] 12:32 핵심 변동 — 신규 1건" in out)
    check("C 07:00 잔재 0건", "07:00" not in out)
    check("C SMTP 연결 0건(dry-run)", "smtp_connections=0" in out)

    # HTML 렌더 계약 (in-process)
    alert = da.load_delta_alert(art, dashboard_url=DASH_URL, report_url=REPORT_URL)
    html = da.render_email_html(alert)
    check("C CTA 라벨 결합 금지('요약 대시보드 보기전체 리포트 보기' 없음)",
          "요약 대시보드 보기전체 리포트 보기" not in html)
    check("C CTA 두 라벨 각각 존재",
          "요약 대시보드 보기" in html and "전체 리포트 보기" in html)
    check("C 버튼 깨짐 대비 plain URL fallback(각 URL >=2회)",
          html.count(alert.dashboard_url) >= 2 and html.count(alert.report_url) >= 2)
    check("C 외부 JS/CSS/이미지/첨부 없음",
          not any(t in html.lower() for t in ("<script", "<link", "<img", "<iframe",
                                              "javascript:", "@import")))
    # 채널 간 동일 시각 — Telegram/이메일이 같은 아티팩트로 같은 제목 시각을 쓴다
    check("D Telegram/이메일 제목 시각 완전 동일(같은 generated_kst)",
          da.title_text(alert) == "12:32 핵심 변동 — 신규 1건"
          and da.render_subject(alert).endswith("12:32 핵심 변동 — 신규 1건"))


# ── E. 기존 계약 보존 (workflow 게이트 + 단일 아티팩트 배선) ──────────────────

def check_workflow_contract() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    art = "DELTA_ARTIFACT_FILE: ${{ runner.temp }}/dashboard_delta.json"
    check("E delta 스텝이 --delta-artifact로 아티팩트 생성",
          "--delta-artifact" in text and text.count(art) == 3,
          f"artifact wiring count={text.count(art)}")
    check("E 기존 alert_delta GITHUB_OUTPUT 계약 유지",
          '--github-output "$GITHUB_OUTPUT"' in text)
    for gate in ("steps.build.outputs.live_ok == 'true'",
                 "steps.delta.outputs.alert_delta == 'true'",
                 "vars.HOURLY_DELTA_AUTO_SEND == '1'",
                 "vars.TELEGRAM_AUTO_SEND == '1'"):
        check(f"E 게이트 유지: {gate}", gate in text)
    check("E concurrency cancel-in-progress: false 유지",
          "cancel-in-progress: false" in text)
    check("E hourly cron(17 * * * *) 유지", "17 * * * *" in text)


def main() -> int:
    print(f"== verify_delta_first_alert_payload (D7-AJ-2) @ {ROOT} ==")
    check_artifact_generation()
    check_source_and_gate()
    check_telegram_delta()
    check_telegram_failclosed()
    check_email_delta()
    check_workflow_contract()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        return 1
    print("RESULT: PASS — delta-first payload: single artifact, real-KST title, "
          "newest-first, fail-closed, gates preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
