#!/usr/bin/env python3
"""Offline verifier for D7-AK-1 — meaningful hourly-delta quality gate.

시간당 delta 알림이 '임원에게 의미 있는' 변동에서만 열리는지 검증한다. 네트워크·비밀값·
실제 발송 0건. 대시보드 재정렬·surface 이동·tracking URL·메타/시각 변화만으로는 절대 알림이
열려선 안 되고, 신규 기사·중요도 상승·현대건설 관련성 상승·실질 내용 변경에서만 열려야 한다.

핵심 계약:
  · GITHUB_OUTPUT alert_delta = meaningful_count>=1 (raw fingerprint 변화는 진단용).
  · --delta-artifact 유무와 무관하게 동일 classifier가 판단한다.
  · 무의미 변동 실행에서는 raw_alert_delta=true·changed_count>0여도 meaningful=0·alert_delta=false
    이며, 워크플로 Telegram/Teams step 조건이 false, Skip step 조건이 true다.
  · legacy v1 아티팩트(change_type 없음)도 loader가 깨지지 않는다.
  · 새 classifier가 만든 아티팩트의 모든 기사에는 change_type·change_reasons가 있다.
  · scheduled-live-refresh.yml의 Verify pipeline이 이 검증기를 실제로 호출한다.
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
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DETECTOR = ROOT / "scripts" / "detect_dashboard_alert_delta.py"
TELEGRAM = ROOT / "scripts" / "send_telegram.py"
WORKFLOW = ROOT / ".github" / "workflows" / "scheduled-live-refresh.yml"

from app import delta_alert as da  # noqa: E402

NOW_ISO = "2026-07-14T12:32:00+09:00"
_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


# ── 대시보드/디텍터 하네스 ────────────────────────────────────────────────────

def _html(model: dict, mode: str = "live") -> str:
    payload = json.dumps(model, ensure_ascii=False)
    return (f"<!doctype html><html><body><!--news-data-mode:{mode}-->"
            f'<script type="application/json" id="preview-model">{payload}</script>'
            "</body></html>")


def _article(**overrides: object) -> dict:
    row = {
        "article_id": "a1",
        "title": "현대건설 관련 기사",
        "source": "연합뉴스",
        "url": "https://ex.com/a1",
        "category_label": "AI",
        "score": 4.2,
        "published_at": "2026-07-14T10:00:00+09:00",
        "snippet": "원본 요약 본문",
        "provenance": {"hdec_relevance_tier": 5, "executive_section": "business"},
    }
    row.update(overrides)
    return row


def run_detector(old_model: dict, new_model: dict, *, artifact: bool = True,
                 valid_new: bool = True):
    """detector를 subprocess로 실행하고 (proc, github_dict, artifact_data)를 돌려준다."""
    tmp = Path(tempfile.mkdtemp(prefix="hdec_ak1_"))
    old_p, new_p, gh, art = tmp / "o.html", tmp / "n.html", tmp / "gh.txt", tmp / "d.json"
    old_p.write_text(_html(old_model), encoding="utf-8")
    new_p.write_text(_html(new_model) if valid_new else "<html>invalid</html>", encoding="utf-8")
    cmd = [sys.executable, str(DETECTOR), str(old_p), str(new_p),
           "--github-output", str(gh), "--now", NOW_ISO]
    if artifact:
        cmd += ["--delta-artifact", str(art)]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=60)
    github = dict(l.split("=", 1) for l in gh.read_text(encoding="utf-8").splitlines() if "=" in l) \
        if gh.exists() else {}
    data = json.loads(art.read_text(encoding="utf-8")) if (artifact and art.exists()) else None
    return proc, github, data


def _types(data: dict) -> dict:
    return (data or {}).get("change_type_counts", {})


# ── A. 무의미 변동 → 게이트 닫힘 ──────────────────────────────────────────────

def check_ignored_changes() -> None:
    old = {"top_immediate_signals": [_article()]}

    # 1) 완전 동일 → meaningful=0
    _, gh, data = run_detector(old, {"top_immediate_signals": [_article()]})
    check("identical dashboard → meaningful=0 · alert_delta=false",
          gh.get("meaningful_count") == "0" and gh.get("alert_delta") == "false"
          and data["articles"] == [])

    # 2) 같은 surface 내 재정렬(rank-only) → 변화 자체가 감지 안 됨(의미 0)
    two = {"top_immediate_signals": [
        _article(article_id="a1", url="https://ex.com/a1"),
        _article(article_id="a2", url="https://ex.com/a2", title="두번째 기사")]}
    two_rev = {"top_immediate_signals": list(reversed(two["top_immediate_signals"]))}
    _, gh, _ = run_detector(two, two_rev)
    check("rank-only reorder within surface → meaningful=0",
          gh.get("meaningful_count") == "0" and gh.get("alert_delta") == "false")

    # 3) surface 이동만 → surface_move, meaningful=0
    _, gh, data = run_detector(old, {"business_signals": [_article()]})
    check("surface move only → meaningful=0 · surface_move",
          gh.get("meaningful_count") == "0" and _types(data).get("surface_move") == 1)

    # 4) tracking query만 → duplicate_reappearance, meaningful=0
    _, gh, data = run_detector(
        old, {"top_immediate_signals": [_article(url="https://ex.com/a1?utm_source=x&fbclid=y")]})
    check("tracking-query-only → meaningful=0 · duplicate_reappearance",
          gh.get("meaningful_count") == "0"
          and _types(data).get("duplicate_reappearance") == 1)

    # 5) 제목의 whitespace만 → title_fingerprint 정규화로 완전 억제(의미 0, 알림 없음)
    _, gh, _ = run_detector(
        old, {"top_immediate_signals": [_article(title="현대건설  관련 기사 ")]})
    check("whitespace-only title → fully suppressed (meaningful=0 · alert_delta=false)",
          gh.get("meaningful_count") == "0" and gh.get("alert_delta") == "false")

    # 6) 요약 whitespace만 → 의미 0
    _, gh, _ = run_detector(
        old, {"top_immediate_signals": [_article(snippet="원본   요약   본문 ")]})
    check("whitespace-only summary → meaningful=0", gh.get("meaningful_count") == "0")


# ── B. 의미 있는 변동 → 게이트 열림 + 정확한 change_type ──────────────────────

def check_meaningful_changes() -> None:
    old = {"top_immediate_signals": [_article()]}

    # 7) 실제 신규 URL → new_article
    _, gh, data = run_detector(old, {"top_immediate_signals": [
        _article(), _article(article_id="new1", url="https://ex.com/new1",
                             title="완전히 새로운 수주 기사",
                             published_at="2026-07-14T12:20:00+09:00")]})
    check("real new URL → meaningful>=1 · new_article",
          gh.get("alert_delta") == "true" and _types(data).get("new_article") == 1)

    # 8) score가 4.5 상향 통과 → priority_upgrade
    _, gh, data = run_detector(old, {"top_immediate_signals": [_article(score=4.8)]})
    check("score crosses 4.5 → priority_upgrade",
          gh.get("alert_delta") == "true" and _types(data).get("priority_upgrade") == 1)

    # 9) hdec 관련성 상승(tier 5→2 · indirect→direct) → hdec_relevance_upgrade
    _, gh, data = run_detector(old, {"top_immediate_signals": [
        _article(provenance={"hdec_relevance_tier": 2, "executive_section": "hdec_direct"})]})
    check("indirect→direct hdec relevance → hdec_relevance_upgrade",
          gh.get("alert_delta") == "true" and _types(data).get("hdec_relevance_upgrade") == 1)

    # 10) 실질 요약 변경 → material_content_update
    _, gh, data = run_detector(
        old, {"top_immediate_signals": [_article(snippet="전혀 다른 새로운 핵심 내용으로 갱신됨")]})
    check("real summary change → material_content_update",
          gh.get("alert_delta") == "true" and _types(data).get("material_content_update") == 1)

    # 11) 같은 신규 기사가 여러 surface → 아티팩트엔 1건
    multi = {
        "top_immediate_signals": [_article(article_id="m1", url="https://ex.com/m1",
                                           title="다중 surface 신규",
                                           published_at="2026-07-14T12:20:00+09:00")],
        "business_signals": [_article(article_id="m1", url="https://ex.com/m1",
                                      title="다중 surface 신규",
                                      published_at="2026-07-14T12:20:00+09:00")],
    }
    _, gh, data = run_detector(old, multi)
    keys = [a["article_key"] for a in data["articles"]]
    check("multi-surface same article → single artifact entry",
          len(data["articles"]) == 1 and data["duplicate_collapsed_count"] >= 1,
          f"keys={keys}")

    # 12) 8건 meaningful → meaningful=8 · 아티팩트 cap 5 · 표시정렬(hdec→priority→new)
    many_new = [_article(article_id=f"k{i}", url=f"https://ex.com/k{i}", title=f"신규 {i}",
                         published_at=f"2026-07-14T1{i}:00:00+09:00") for i in range(8)]
    _, gh, data = run_detector(old, {"top_immediate_signals": [_article()] + many_new})
    check("8 meaningful → meaningful_count=8 · artifact capped at 5",
          gh.get("meaningful_count") == "8" and len(data["articles"]) == 5)
    check("every artifact article carries change_type + change_reasons",
          all(a.get("change_type") and isinstance(a.get("change_reasons"), list) and a["change_reasons"]
              for a in data["articles"]))


# ── C. canonical identity — alias 교집합 + fail-closed ────────────────────────

def check_identity_matching() -> None:
    # 13) URL 없고 제목 변경돼도 안정 article_id 같으면 기존 기사로 매칭 → material
    old = {"top_immediate_signals": [
        _article(article_id="stable-9", url="https://ex.com/s9", title="원 제목")]}
    new = {"top_immediate_signals": [
        _article(article_id="stable-9", url="", title="완전히 바뀐 제목입니다")]}
    _, gh, data = run_detector(old, new)
    check("url-less + changed title + same article_id → matched as material (not new_article)",
          _types(data).get("material_content_update") == 1 and _types(data).get("new_article", 0) == 0)

    # 14) 어느 alias로도 매칭 불가 → material로 추정하지 않는다(fail-closed).
    old2 = {"top_immediate_signals": [
        _article(article_id="X", url="https://ex.com/one", title="첫 기사",
                 source="연합뉴스", published_at="2026-07-14T09:00:00+09:00")]}
    new2 = {"top_immediate_signals": [
        _article(article_id="Y", url="", title="관련 없는 다른 기사",
                 source="매일경제", published_at="2026-07-14T12:00:00+09:00")]}
    _, gh, data = run_detector(old2, new2)
    check("no alias match → never guessed as material_content_update",
          _types(data).get("material_content_update", 0) == 0)


# ── D. 무의미 실행에서 sender step이 열리지 않는다 (워크플로 if 실제 평가) ─────

def _step_block(text: str, name: str) -> str:
    m = re.search(rf"(?ms)^\s+- name: {re.escape(name)}\s*$\n(.*?)(?=^\s+- name: |\Z)", text)
    return m.group(1) if m else ""


def _step_if(text: str, name: str) -> str:
    block = _step_block(text, name)
    m = re.search(r"^\s*if:\s*(.+)$", block, re.M)
    return m.group(1).strip() if m else ""


def _eval_gate(expr: str, ctx: dict) -> bool | None:
    """`A == 'x' && B != 'y' && …` 형태의 GitHub Actions if 식을 평가한다."""
    if not expr:
        return None
    for term in expr.split("&&"):
        m = re.match(r"(.+?)\s*(==|!=)\s*'([^']*)'\s*$", term.strip())
        if not m:
            return None
        lhs, op, literal = m.group(1).strip(), m.group(2), m.group(3)
        actual = ctx.get(lhs, "")
        if op == "==" and actual != literal:
            return False
        if op == "!=" and not (actual != literal):
            return False
    return True


def check_sender_gates_closed_on_zero_meaningful() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    tg_if = _step_if(text, "Hourly telegram digest (delta-gated auto-send)")
    teams_if = _step_if(text, "Hourly Teams channel email (delta-gated auto-send)")
    skip_if = _step_if(text, "Skip automatic alerts (no delta)")
    check("workflow if-conditions found for telegram/teams/skip",
          bool(tg_if and teams_if and skip_if))

    # 무의미 변동을 실제로 흘려 alert_delta=false를 얻는다.
    old = {"top_immediate_signals": [_article()]}
    _, gh, data = run_detector(old, {"business_signals": [_article()]})
    simultaneous = (
        gh.get("raw_alert_delta") == "true"
        and int(gh.get("changed_count", "0")) > 0
        and gh.get("meaningful_count") == "0"
        and gh.get("alert_delta") == "false"
        and data["alert_delta"] is False
    )
    check("meaningless run: raw_alert_delta=true ∧ changed>0 ∧ meaningful=0 ∧ alert_delta=false",
          simultaneous, f"github={gh}")

    ctx_zero = {
        "steps.build.outputs.live_ok": "true",
        "steps.delta.outputs.alert_delta": gh.get("alert_delta", ""),  # 'false'
        "vars.HOURLY_DELTA_AUTO_SEND": "1",
        "vars.TELEGRAM_AUTO_SEND": "1",
    }
    check("meaningless → Telegram step condition = false", _eval_gate(tg_if, ctx_zero) is False)
    check("meaningless → Teams step condition = false", _eval_gate(teams_if, ctx_zero) is False)
    check("meaningless → Skip automatic alerts condition = true", _eval_gate(skip_if, ctx_zero) is True)

    # 대조군: 의미 있는 변동이면 sender가 열리고 skip이 닫힌다.
    _, gh_true, _ = run_detector(old, {"top_immediate_signals": [
        _article(), _article(article_id="z1", url="https://ex.com/z1", title="신규",
                             published_at="2026-07-14T12:20:00+09:00")]})
    ctx_open = dict(ctx_zero, **{"steps.delta.outputs.alert_delta": gh_true.get("alert_delta", "")})
    check("meaningful → Telegram/Teams open ∧ Skip closed",
          gh_true.get("alert_delta") == "true"
          and _eval_gate(tg_if, ctx_open) is True
          and _eval_gate(teams_if, ctx_open) is True
          and _eval_gate(skip_if, ctx_open) is False)


def check_sender_second_defense() -> None:
    """무의미 아티팩트(alert_delta=false)를 sender가 받으면 2차 방어로 no_delta·발송 0건."""
    old = {"top_immediate_signals": [_article()]}
    tmp = Path(tempfile.mkdtemp(prefix="hdec_ak1_send_"))
    art = tmp / "delta.json"
    _, _, data = run_detector(old, {"business_signals": [_article()]})
    art.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
           "DELTA_ARTIFACT_FILE": str(art),
           "TELEGRAM_BOT_TOKEN": "dummy", "TELEGRAM_CHAT_IDS": "123"}
    proc = subprocess.run([sys.executable, str(TELEGRAM)], cwd=ROOT, text=True,
                          capture_output=True, timeout=120, env=env)
    out = (proc.stdout or "") + (proc.stderr or "")
    check("sender 2nd defense: meaningless artifact → no_delta · exit 0 (0 sends)",
          proc.returncode == 0 and "no_delta" in out and "delivered=" not in out)


# ── E. schema 호환 + 로그 위생 + 파이프라인 배선 ─────────────────────────────

def check_schema_compat() -> None:
    # legacy v1 (change_type/meaningful_candidate_count 없음) → loader 정상, 뱃지 없음.
    legacy = {
        "schema_version": 1, "generated_kst": "2026-07-14 12:32", "source": "live-delta",
        "alert_delta": True, "new_candidate_count": 2,
        "articles": [
            {"article_key": "a", "title": "레거시 A", "published_kst": "2026-07-14 12:00",
             "source": "x", "category": "AI", "url": "https://ex.com/a"},
            {"article_key": "b", "title": "레거시 B", "published_kst": "2026-07-14 11:00",
             "source": "y", "category": "AI", "url": "https://ex.com/b"}],
    }
    try:
        alert = da.parse_delta_alert(legacy)
        loaded = True
    except da.InvalidDeltaArtifact:
        loaded = False
    check("legacy v1 artifact without change_type loads without error", loaded)
    if loaded:
        check("legacy artifact → conservative defaults (title uses article count, no badge)",
              da.title_text(alert) == "12:32 핵심 변동 — 중요 2건"
              and da.change_summary_line(alert) == ""
              and all(a.change_type == "" for a in alert.articles))


def check_log_hygiene() -> None:
    # 기사 제목/URL은 stdout/GITHUB_OUTPUT 어디에도 나오면 안 된다.
    old = {"top_immediate_signals": [_article()]}
    canary_title, canary_url = "SECRETLEAKCANARYTITLE", "https://leak.example/canary-path"
    proc, gh, _ = run_detector(old, {"top_immediate_signals": [
        _article(), _article(article_id="c1", url=canary_url, title=canary_title,
                             published_at="2026-07-14T12:20:00+09:00")]})
    log = (proc.stdout or "") + (proc.stderr or "")
    gh_text = "\n".join(f"{k}={v}" for k, v in gh.items())
    check("detector stdout/GITHUB_OUTPUT contain no article title/URL",
          canary_title not in log and canary_url not in log
          and canary_title not in gh_text and canary_url not in gh_text)


def check_pipeline_wiring() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    verify_block = _step_block(text, "Verify pipeline (mock-safe, no secrets)")
    check("scheduled-live-refresh.yml Verify pipeline calls this verifier",
          "python3 scripts/verify_meaningful_delta_quality.py" in verify_block)
    check("Verify pipeline py_compiles the new classifier + verifier",
          "app/delta_classifier.py" in verify_block
          and "scripts/verify_meaningful_delta_quality.py" in verify_block)


def main() -> int:
    print(f"== verify_meaningful_delta_quality (D7-AK-1) @ {ROOT} ==")
    check_ignored_changes()
    check_meaningful_changes()
    check_identity_matching()
    check_sender_gates_closed_on_zero_meaningful()
    check_sender_second_defense()
    check_schema_compat()
    check_log_hygiene()
    check_pipeline_wiring()

    # invalid 입력 → fail-closed (rc!=0 · alert_delta=false · 아티팩트 미생성)
    proc, gh, data = run_detector({"top_immediate_signals": [_article()]}, {}, valid_new=False)
    check("invalid model → fail-closed (rc!=0 · alert_delta=false · no artifact)",
          proc.returncode != 0 and gh.get("alert_delta") == "false" and data is None)

    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("RESULT: PASS — meaningful-delta gate: noise suppressed, real signals kept, "
          "sender gates closed on zero meaningful, schema-compatible, content-free")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
