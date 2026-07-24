#!/usr/bin/env python3
"""Offline verifier for D7-AK-1/2/4B — hourly gate + shadow evidence contract.

시간당 delta 알림이 '임원에게 의미 있는' 변동에서만, 그리고 '지금 보낼 가치가 있을 때만'
열리는지 검증한다. 네트워크·비밀값·실제 발송 0건. 대시보드 재정렬·surface 이동·tracking
URL·메타/시각 변화만으로는 절대 알림이 열려선 안 되고, 신규 기사·중요도 상승·현대건설
관련성 상승·실질 내용 변경에서만 열려야 한다.

핵심 계약:
  · 두 계층 분리 — change classification(무슨 변화인가)과 hourly eligibility(보낼 가치가
    있는가)는 별개다. 저가치/stale이어도 change_type은 그대로 남고, 정책 결과만 덧붙는다.
  · GITHUB_OUTPUT alert_delta = hourly_eligible_count>=1 (raw fingerprint 변화는 진단용).
  · 실제 자동 sender 게이트는 shadow_alert_delta=true, 즉 확정 긴급 사건이 1건 이상일 때만 열린다.
  · detector current 계약: meaningful_candidate_count == hourly_eligible_count.
  · 실제 shadow-aware sender 계약: 실제 sender 대상 수 == shadow_would_pass_count —
    shadow_would_pass=true 기사만 sender가 소비한다.
  · 카운트 항등식 — dedup = pre_policy_meaningful + ignored,
    pre_policy_meaningful = hourly_eligible + low_value + stale + unknown_time.
  · --delta-artifact 유무와 무관하게 동일 classifier가 판단한다.
  · 무의미 변동 실행에서는 raw_alert_delta=true·changed_count>0여도 meaningful=0·alert_delta=false
    이며, 워크플로 Telegram/Teams step 조건이 false, Skip step 조건이 true다.
  · legacy v1 아티팩트(change_type 없음)도 loader가 깨지지 않는다.
  · 새 classifier가 만든 아티팩트의 모든 기사에는 change_type·change_reasons가 있고
    전부 hourly_eligible=true·hourly_suppression_reasons=[]다.
  · scheduled-live-refresh.yml의 Verify pipeline이 이 검증기를 실제로 호출한다.

결정성: 모든 detector 실행에 고정 --now(NOW_ISO)를 넘긴다. 픽스처의 published_at도 전부
고정 상수라, 이 검증기를 실행하는 실제 날짜가 바뀌어도 결과가 변하지 않는다(D7-AJ-1의
fixture-aging 재발 방지). 날짜를 주기적으로 갱신하는 방식은 쓰지 않는다.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DETECTOR = ROOT / "scripts" / "detect_dashboard_alert_delta.py"
TELEGRAM = ROOT / "scripts" / "send_telegram.py"
WORKFLOW = ROOT / ".github" / "workflows" / "scheduled-live-refresh.yml"

from app import delta_alert as da  # noqa: E402
from app import delta_classifier as dc  # noqa: E402
from app import radar_signals as rs  # noqa: E402

NOW_ISO = "2026-07-14T12:32:00+09:00"
# 고정 기준시각(NOW_ISO)에 대한 상대 위치를 상수로 고정한다 — 벽시계를 쓰지 않는다.
FRESH_2H = "2026-07-14T10:00:00+09:00"    # NOW - 2.5h  (즉시확인 창 안)
FRESH_NEW = "2026-07-14T12:20:00+09:00"   # NOW - 0.2h
STALE_80H = "2026-07-11T04:32:00+09:00"   # NOW - 80h   (IMMEDIATE_MAX_AGE_HOURS=72 초과)
HDEC_DIRECT_PROV = {"hdec_relevance_tier": 1, "executive_section": "hdec_direct"}

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
                 valid_new: bool = True, mode: str = "live", now: str = NOW_ISO):
    """detector를 subprocess로 실행하고 (proc, github_dict, artifact_data)를 돌려준다.

    --now는 항상 명시적으로 넘긴다(기본 NOW_ISO). 신선도 게이트가 벽시계가 아니라 이 값을
    보므로, 검증기는 실행 날짜와 무관하게 같은 결과를 낸다."""
    tmp = Path(tempfile.mkdtemp(prefix="hdec_ak1_"))
    old_p, new_p, gh, art = tmp / "o.html", tmp / "n.html", tmp / "gh.txt", tmp / "d.json"
    old_p.write_text(_html(old_model, mode), encoding="utf-8")
    new_p.write_text(_html(new_model, mode) if valid_new else "<html>invalid</html>",
                     encoding="utf-8")
    cmd = [sys.executable, str(DETECTOR), str(old_p), str(new_p),
           "--github-output", str(gh), "--now", now]
    if artifact:
        cmd += ["--delta-artifact", str(art)]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=60)
    github = dict(l.split("=", 1) for l in gh.read_text(encoding="utf-8").splitlines() if "=" in l) \
        if gh.exists() else {}
    data = json.loads(art.read_text(encoding="utf-8")) if (artifact and art.exists()) else None
    return proc, github, data


def _types(data: dict) -> dict:
    return (data or {}).get("change_type_counts", {})


def _gh_counts(gh: dict) -> dict:
    """GITHUB_OUTPUT의 정책 카운트만 int로 뽑아 비교하기 쉽게 만든다."""
    keys = ("deduplicated_candidate_count", "pre_policy_meaningful_count", "meaningful_count",
            "hourly_eligible_count", "ignored_count", "suppressed_low_value_count",
            "suppressed_stale_count", "suppressed_unknown_time_count")
    return {k: int(gh.get(k, "0")) for k in keys}


def _shadow_counts(gh: dict) -> dict:
    keys = (
        "shadow_would_pass_count", "shadow_confirmed_count",
        "shadow_ambiguous_count", "shadow_blocked_count", "shadow_none_count",
        "shadow_unavailable_count",
    )
    return {k: int(gh.get(k, "0")) for k in keys}


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
    """현재 workflow가 쓰는 비교/AND/괄호 OR GitHub Actions if 식만 평가한다."""
    if not expr:
        return None

    def split_top_level(value: str, operator: str) -> list[str] | None:
        parts = []
        start = 0
        depth = 0
        quoted = False
        i = 0
        while i < len(value):
            char = value[i]
            if char == "'":
                quoted = not quoted
                i += 1
                continue
            if not quoted:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth < 0:
                        return None
                elif depth == 0 and value.startswith(operator, i):
                    parts.append(value[start:i].strip())
                    start = i + len(operator)
                    i = start
                    continue
            i += 1
        if quoted or depth != 0:
            return None
        parts.append(value[start:].strip())
        return parts

    def comparison(term: str) -> bool | None:
        m = re.fullmatch(
            r"([A-Za-z_][A-Za-z0-9_.]*)\s*(==|!=)\s*'([^']*)'",
            term.strip(),
        )
        if not m:
            return None
        lhs, op, literal = m.group(1), m.group(2), m.group(3)
        actual = ctx.get(lhs, "")
        return actual == literal if op == "==" else actual != literal

    terms = split_top_level(expr, "&&")
    if terms is None:
        return None
    values = []
    for term in terms:
        if term.startswith("(") and term.endswith(")"):
            alternatives = split_top_level(term[1:-1], "||")
            if alternatives is None or len(alternatives) < 2:
                return None
            alternative_values = [comparison(item) for item in alternatives]
            if any(value is None for value in alternative_values):
                return None
            values.append(any(alternative_values))
            continue
        value = comparison(term)
        if value is None:
            return None
        values.append(value)
    return all(values)


def check_sender_gates_closed_on_zero_meaningful() -> None:
    # D7-AK-6C — the article-level Teams sender moved to teams-ai-news-watch.yml and is no
    # longer shadow-gated. This hourly workflow only auto-sends Telegram (shadow-gated), so
    # these gate checks cover Telegram + the skip step; Teams' "0 candidates → 0 send" is
    # proven in verify_teams_ai_push_production.py.
    text = WORKFLOW.read_text(encoding="utf-8")
    tg_if = _step_if(text, "Hourly telegram digest (delta-gated auto-send)")
    skip_if = _step_if(text, "Skip automatic Telegram alert (no confirmed urgency)")
    check("workflow if-conditions found for telegram/skip",
          bool(tg_if and skip_if))
    check("hourly workflow no longer runs the Teams sender (moved to watch)",
          "python3 scripts/send_teams_ai_push.py" not in text)

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
        "steps.delta.outputs.alert_delta": gh.get("alert_delta", ""),  # 진단용 current gate
        "steps.delta.outputs.shadow_alert_delta": gh.get(
            "shadow_alert_delta", ""
        ),
        "vars.HOURLY_DELTA_AUTO_SEND": "1",
        "vars.TELEGRAM_AUTO_SEND": "1",
        "github.ref": "refs/heads/main",
        "github.event_name": "schedule",
        "github.event.inputs.force_dry_run": "",
    }
    check("meaningless → Telegram step condition = false", _eval_gate(tg_if, ctx_zero) is False)
    check("meaningless → Skip Telegram alert condition = true", _eval_gate(skip_if, ctx_zero) is True)

    # 대조군 1: current meaningful이더라도 shadow 확정 사건이 아니면 sender는 닫힌다.
    _, gh_meaningful, _ = run_detector(
        old,
        {
            "top_immediate_signals": [
                _article(),
                _article(
                    article_id="z1",
                    url="https://ex.com/z1",
                    title="신규 산업 동향 기사",
                    published_at="2026-07-14T12:20:00+09:00",
                ),
            ]
        },
    )
    ctx_meaningful = dict(
        ctx_zero,
        **{
            "steps.delta.outputs.alert_delta": gh_meaningful.get(
                "alert_delta", ""
            ),
            "steps.delta.outputs.shadow_alert_delta": gh_meaningful.get(
                "shadow_alert_delta", ""
            ),
        },
    )
    check(
        "meaningful but unconfirmed → Telegram closed ∧ Skip open",
        gh_meaningful.get("alert_delta") == "true"
        and gh_meaningful.get("shadow_alert_delta") == "false"
        and _eval_gate(tg_if, ctx_meaningful) is False
        and _eval_gate(skip_if, ctx_meaningful) is True,
    )

    # 대조군 2: shadow confirmed 확정 사건에서만 sender가 열리고 Skip이 닫힌다.
    _, gh_confirmed, _ = run_detector(
        old,
        {
            "top_immediate_signals": [
                _article(),
                _article(
                    article_id="z2",
                    url="https://ex.com/z2",
                    title="현대건설, UAE 원전 EPC 계약 체결",
                    published_at="2026-07-14T12:20:00+09:00",
                ),
            ]
        },
    )
    ctx_confirmed = dict(
        ctx_zero,
        **{
            "steps.delta.outputs.alert_delta": gh_confirmed.get(
                "alert_delta", ""
            ),
            "steps.delta.outputs.shadow_alert_delta": gh_confirmed.get(
                "shadow_alert_delta", ""
            ),
        },
    )
    check(
        "shadow confirmed → Telegram open ∧ Skip closed",
        gh_confirmed.get("alert_delta") == "true"
        and gh_confirmed.get("shadow_alert_delta") == "true"
        and _eval_gate(tg_if, ctx_confirmed) is True
        and _eval_gate(skip_if, ctx_confirmed) is False,
    )


def check_workflow_production_guards() -> None:
    """Production effect 단계의 main/forced-dry-run guard를 실제 if 식으로 평가한다."""
    text = WORKFLOW.read_text(encoding="utf-8")
    publish_if = _step_if(text, "Publish to Pages (commit live docs)")
    tg_if = _step_if(text, "Hourly telegram digest (delta-gated auto-send)")
    skip_if = _step_if(text, "Skip automatic Telegram alert (no confirmed urgency)")

    production = {
        "steps.build.outputs.live_ok": "true",
        "steps.delta.outputs.shadow_alert_delta": "true",
        "vars.HOURLY_DELTA_AUTO_SEND": "1",
        "vars.TELEGRAM_AUTO_SEND": "1",
        "github.ref": "refs/heads/main",
        "github.event_name": "schedule",
        "github.event.inputs.force_dry_run": "",
    }

    def effect_results(ctx: dict) -> tuple[bool | None, bool | None]:
        return (
            _eval_gate(publish_if, ctx),
            _eval_gate(tg_if, ctx),
        )

    check(
        "production scheduled main → Publish/Telegram open ∧ Skip closed",
        effect_results(production) == (True, True)
        and _eval_gate(skip_if, production) is False,
    )

    non_main = dict(production)
    non_main["github.ref"] = "refs/heads/fix/test"
    check(
        "non-main branch → Publish/Telegram closed",
        effect_results(non_main) == (False, False),
    )

    forced_dry_run = dict(production)
    forced_dry_run.update({
        "github.event_name": "workflow_dispatch",
        "github.event.inputs.force_dry_run": "true",
    })
    check(
        "forced dry-run → Publish/Telegram closed",
        effect_results(forced_dry_run) == (False, False),
    )

    manual_non_dry = dict(production)
    manual_non_dry.update({
        "github.event_name": "workflow_dispatch",
        "github.event.inputs.force_dry_run": "false",
    })
    check(
        "main manual non-dry → Publish/Telegram open",
        effect_results(manual_non_dry) == (True, True),
    )


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

    # 정책에서 걸린 기사도 마찬가지 — suppression 진단은 카운트만 남기고 제목/URL은 안 남긴다.
    sup_title, sup_url = "SUPPRESSEDCANARYTITLE", "https://leak.example/suppressed-path"
    proc2, gh2, data2 = run_detector(old, {"top_immediate_signals": [
        _article(), _article(article_id="c2", url=sup_url, title=sup_title,
                             score=1.0, published_at=STALE_80H)]})
    log2 = (proc2.stdout or "") + (proc2.stderr or "")
    gh2_text = "\n".join(f"{k}={v}" for k, v in gh2.items())
    check("정책에서 걸린 기사의 제목/URL도 stdout/GITHUB_OUTPUT/아티팩트에 남지 않는다",
          sup_title not in log2 and sup_url not in log2
          and sup_title not in gh2_text and sup_url not in gh2_text
          and sup_title not in json.dumps(data2, ensure_ascii=False)
          and int(gh2.get("suppressed_low_value_count", "0")) == 1)


def check_pipeline_wiring() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    verify_block = _step_block(text, "Verify pipeline (mock-safe, no secrets)")
    check("scheduled-live-refresh.yml Verify pipeline calls this verifier",
          "python3 scripts/verify_meaningful_delta_quality.py" in verify_block)
    check("Verify pipeline py_compiles the new classifier + verifier",
          "app/delta_classifier.py" in verify_block
          and "scripts/verify_meaningful_delta_quality.py" in verify_block)


# ── F. 시간당 알림 자격 — 가치 × 신선도 게이트 (D7-AK-2) ─────────────────────
# 분류(change_type)는 그대로 두고, '지금 보낼 가치가 있는가'만 따로 판정하는 계층이다.

def check_value_gate() -> None:
    base = {"top_immediate_signals": [_article()]}

    def _new(**kw) -> dict:
        return {"top_immediate_signals": [_article(), _article(**kw)]}

    # 1) fresh + score 4.2 + 비직접 → 자격 있음 (점수만으로 통과)
    _, gh, data = run_detector(base, _new(
        article_id="v1", url="https://ex.com/v1", title="가치 있는 신규 기사",
        score=4.2, published_at=FRESH_NEW))
    check("fresh + score 4.2 + 비직접 → hourly_eligible=1 · alert_delta=true",
          gh.get("hourly_eligible_count") == "1" and gh.get("alert_delta") == "true"
          and len(data["articles"]) == 1)

    # 2) fresh + score 2.0 + 현대건설 직접 → 자격 있음 (점수 미달이어도 직접이면 통과)
    _, gh, _ = run_detector(base, _new(
        article_id="v2", url="https://ex.com/v2", title="저점수 현대건설 직접 기사",
        score=2.0, published_at=FRESH_NEW, provenance=HDEC_DIRECT_PROV))
    check("fresh + score 2.0 + 현대건설 직접 → hourly_eligible=1 (점수 미달이어도 통과)",
          gh.get("hourly_eligible_count") == "1" and gh.get("alert_delta") == "true")

    # 3) fresh + score 2.0 + 비직접 → 분류는 new_article이지만 자격 없음(저가치)
    _, gh, data = run_detector(base, _new(
        article_id="v3", url="https://ex.com/v3", title="저가치 신규 기사",
        score=2.0, published_at=FRESH_NEW))
    counts = _gh_counts(gh)
    check("fresh + score 2.0 + 비직접 → pre_policy=1 ∧ eligible=0 ∧ low_value=1 ∧ alert_delta=false",
          counts["pre_policy_meaningful_count"] == 1 and counts["hourly_eligible_count"] == 0
          and counts["suppressed_low_value_count"] == 1 and gh.get("alert_delta") == "false",
          str(counts))
    check("저가치로 걸려도 change_type은 new_article로 보존된다(분류≠정책)",
          _types(data).get("new_article") == 1 and data["articles"] == [])
    reasons = [a for a in (data or {}).get("articles", [])]
    check("정책에서 걸린 기사는 articles에 넣지 않는다(카운트로만 남김)", reasons == [])


def check_recency_gate() -> None:
    base = {"top_immediate_signals": [_article()]}

    def _new(**kw) -> dict:
        return {"top_immediate_signals": [_article(), _article(**kw)]}

    # 4) stale 80h + score 4.8 + 현대건설 직접 → 고가치여도 즉시 알림 자격 없음
    _, gh, data = run_detector(base, _new(
        article_id="s1", url="https://ex.com/s1", title="80시간 지난 고가치 기사",
        score=4.8, published_at=STALE_80H, provenance=HDEC_DIRECT_PROV))
    counts = _gh_counts(gh)
    check("stale 80h + score 4.8 + 직접 → pre_policy=1 ∧ eligible=0 ∧ stale=1 ∧ alert_delta=false",
          counts["pre_policy_meaningful_count"] == 1 and counts["hourly_eligible_count"] == 0
          and counts["suppressed_stale_count"] == 1 and gh.get("alert_delta") == "false",
          str(counts))
    check("stale로 걸려도 change_type은 보존(대시보드/일간 리포트에서 삭제하지 않음)",
          _types(data).get("new_article") == 1)

    # 5) live + published_at 없음 → fail-closed (시각 불명은 즉시 알림에 올리지 않는다)
    _, gh, _ = run_detector(base, _new(
        article_id="s2", url="https://ex.com/s2", title="게시시각 불명 기사",
        score=4.8, published_at="", provenance=HDEC_DIRECT_PROV))
    counts = _gh_counts(gh)
    check("live + published_at 없음 → eligible=0 ∧ unknown_time=1 (fail-closed)",
          counts["hourly_eligible_count"] == 0
          and counts["suppressed_unknown_time_count"] == 1
          and gh.get("alert_delta") == "false", str(counts))

    # 6) mock 모드 → 기존 news_recency mock 계약 유지(창 적용 안 함). 가치 게이트는 그대로.
    _, gh, _ = run_detector(base, _new(
        article_id="s3", url="https://ex.com/s3", title="mock 데모의 오래된 기사",
        score=4.2, published_at=STALE_80H), mode="mock")
    check("mock 모드 → stale이어도 자격 유지 (기존 news_recency mock 계약)",
          gh.get("hourly_eligible_count") == "1" and gh.get("suppressed_stale_count") == "0")
    _, gh, _ = run_detector(base, _new(
        article_id="s4", url="https://ex.com/s4", title="mock 시각 불명",
        score=4.2, published_at=""), mode="mock")
    check("mock 모드 → 시각 불명이어도 자격 유지 (mock 계약)",
          gh.get("hourly_eligible_count") == "1"
          and gh.get("suppressed_unknown_time_count") == "0")
    # mock이어도 저가치는 걸린다 — 신선도만 모드 의존이고 가치는 모드 무관.
    _, gh, _ = run_detector(base, _new(
        article_id="s5", url="https://ex.com/s5", title="mock 저가치",
        score=1.0, published_at=FRESH_NEW), mode="mock")
    check("mock 모드여도 가치 게이트는 적용된다 (low_value=1)",
          gh.get("suppressed_low_value_count") == "1"
          and gh.get("hourly_eligible_count") == "0")


def check_content_update_gate() -> None:
    # 7) fresh material_content_update + score 4.2 → 자격 있음 (check#10 계약 유지)
    old = {"top_immediate_signals": [_article()]}
    _, gh, data = run_detector(
        old, {"top_immediate_signals": [_article(snippet="전혀 다른 새로운 핵심 내용으로 갱신됨")]})
    check("fresh 내용 갱신 + score 4.2 → material_content_update ∧ eligible=1 ∧ alert_delta=true",
          _types(data).get("material_content_update") == 1
          and gh.get("hourly_eligible_count") == "1" and gh.get("alert_delta") == "true")

    # 8) stale material_content_update + 현대건설 직접 → 자격 없음.
    #    오래된 기사의 요약/출처 표기가 흔들릴 때마다 매시간 재알림되던 경로를 막는다
    #    (D7-AK-2A: 16일 지난 기사 1건이 스니펫 진동만으로 4회 재분류됨).
    stale_direct = dict(article_id="osc", url="https://ex.com/osc", title="오래된 진동 기사",
                        score=4.8, published_at=STALE_80H, provenance=HDEC_DIRECT_PROV)
    old_osc = {"top_immediate_signals": [_article(**stale_direct, snippet="원본 요약")]}
    new_osc = {"top_immediate_signals": [_article(**stale_direct, snippet="출처 표기만 바뀐 요약")]}
    _, gh, data = run_detector(old_osc, new_osc)
    counts = _gh_counts(gh)
    check("stale 내용 갱신 + 직접 → material_content_update로 분류되되 eligible=0 ∧ stale=1",
          _types(data).get("material_content_update") == 1
          and counts["hourly_eligible_count"] == 0 and counts["suppressed_stale_count"] == 1
          and gh.get("alert_delta") == "false", str(counts))


def check_low_value_run_closes_workflow() -> None:
    """9) 저가치 신규 기사만 있는 실행 → 발송 step 전부 닫히고 Skip이 열린다."""
    text = WORKFLOW.read_text(encoding="utf-8")
    tg_if = _step_if(text, "Hourly telegram digest (delta-gated auto-send)")
    skip_if = _step_if(text, "Skip automatic Telegram alert (no confirmed urgency)")

    base = {"top_immediate_signals": [_article()]}
    low = {"top_immediate_signals": [_article()] + [
        _article(article_id=f"lo{i}", url=f"https://ex.com/lo{i}", title=f"저가치 신규 {i}",
                 score=1.0 + i * 0.2, published_at=FRESH_NEW) for i in range(6)]}
    _, gh, data = run_detector(base, low)
    counts = _gh_counts(gh)
    check("저가치 신규만: raw_alert_delta=true ∧ pre_policy>0 ∧ eligible=0 ∧ alert_delta=false",
          gh.get("raw_alert_delta") == "true" and counts["pre_policy_meaningful_count"] == 6
          and counts["hourly_eligible_count"] == 0 and gh.get("alert_delta") == "false"
          and data["alert_delta"] is False and data["articles"] == [], str(counts))

    ctx = {
        "steps.build.outputs.live_ok": "true",
        "steps.delta.outputs.alert_delta": gh.get("alert_delta", ""),
        "steps.delta.outputs.shadow_alert_delta": gh.get(
            "shadow_alert_delta", ""
        ),
        "vars.HOURLY_DELTA_AUTO_SEND": "1",
        "vars.TELEGRAM_AUTO_SEND": "1",
        "github.ref": "refs/heads/main",
        "github.event_name": "schedule",
        "github.event.inputs.force_dry_run": "",
    }
    check("저가치 신규만 → Telegram step = false",  _eval_gate(tg_if, ctx) is False)
    check("저가치 신규만 → Skip Telegram alert = true", _eval_gate(skip_if, ctx) is True)


def check_eligible_ordering() -> None:
    """10) 자격 통과 8건 → 아티팩트 5건 cap + 점수/현대건설 직접 우선 정렬.

    회귀 방지: 저가치 기사에 '가장 최신' 게시시각을 주고 고가치를 오래되게 만든다. 예전
    최신순 우선 정렬이었다면 top-5가 저가치 5건으로 채워진다(D7-AK-2A의 실제 결함).
    """
    base = {"top_immediate_signals": [_article()]}
    #                 id     score  direct  published(오래된↔최신)
    spec = [("d47", 4.7, True,  "2026-07-14T04:00:00+09:00"),
            ("d20", 2.0, True,  "2026-07-14T05:00:00+09:00"),
            ("s49", 4.9, False, "2026-07-14T06:00:00+09:00"),
            ("s40", 4.0, False, "2026-07-14T07:00:00+09:00"),
            ("s39", 3.9, False, "2026-07-14T08:00:00+09:00"),
            ("s38", 3.8, False, "2026-07-14T09:00:00+09:00"),
            ("s37", 3.7, False, "2026-07-14T10:00:00+09:00"),
            ("s36", 3.6, False, "2026-07-14T11:00:00+09:00")]
    rows = [_article(article_id=k, url=f"https://ex.com/{k}", title=f"신규 {k}",
                     score=sc, published_at=pub,
                     provenance=HDEC_DIRECT_PROV if direct else
                     {"hdec_relevance_tier": 5, "executive_section": "business"})
            for k, sc, direct, pub in spec]
    _, gh, data = run_detector(base, {"top_immediate_signals": [_article()] + rows})
    check("자격 8건 → hourly_eligible=8 ∧ 아티팩트 5건 cap",
          gh.get("hourly_eligible_count") == "8" and len(data["articles"]) == 5,
          f"eligible={gh.get('hourly_eligible_count')} articles={len(data['articles'])}")
    keys = [a["article_key"] for a in data["articles"]]
    check("정렬: 현대건설 직접 우선 → 점수 높은 순 (최신순은 마지막 tiebreaker)",
          keys == ["d47", "d20", "s49", "s40", "s39"], str(keys))
    check("아티팩트 기사는 전부 hourly_eligible=true ∧ suppression 사유 없음",
          all(a.get("hourly_eligible") is True and a.get("hourly_suppression_reasons") == []
              for a in data["articles"]))


def check_reference_time_determinism() -> None:
    """11) 결과는 --now에만 의존한다 — 검증기 실행 날짜가 바뀌어도 불변.

    같은 픽스처를 서로 다른 --now로 두 번 돌려 신선도 판정이 --now를 따라 뒤집히는지 본다.
    벽시계를 봤다면 두 실행이 같은 결과를 냈을 것이다. 픽스처 날짜를 주기적으로 갱신하는
    대신 이 방식으로 고정한다.
    """
    base = {"top_immediate_signals": [_article()]}
    new = {"top_immediate_signals": [
        _article(), _article(article_id="t1", url="https://ex.com/t1", title="시각 기준 검증 기사",
                             score=4.2, published_at=FRESH_NEW)]}
    _, gh_now, _ = run_detector(base, new, now=NOW_ISO)
    # 같은 기사, 기준시각만 7일 뒤 → 동일 published_at이 stale로 뒤집힌다.
    _, gh_later, _ = run_detector(base, new, now="2026-07-21T12:32:00+09:00")
    check("--now=NOW_ISO → fresh (eligible=1)", gh_now.get("hourly_eligible_count") == "1")
    check("--now=+7d → 같은 기사가 stale (eligible=0 ∧ stale=1) — 기준시각은 --now만 본다",
          gh_later.get("hourly_eligible_count") == "0"
          and gh_later.get("suppressed_stale_count") == "1",
          f"later={_gh_counts(gh_later)}")
    # 동일 입력 재실행 → 완전 동일 결과(실행 시점 비의존).
    _, gh_repeat, _ = run_detector(base, new, now=NOW_ISO)
    check("동일 입력·동일 --now 재실행 → GITHUB_OUTPUT 완전 동일(결정적)",
          _gh_counts(gh_repeat) == _gh_counts(gh_now))


def check_count_identities() -> None:
    """카운트 항등식 — 어떤 실행에서도 합이 어긋나면 안 된다(진단 신뢰성)."""
    base = {"top_immediate_signals": [_article()]}
    mixed = {"top_immediate_signals": [
        _article(),  # 불변
        _article(article_id="ok", url="https://ex.com/ok", title="자격 통과",
                 score=4.2, published_at=FRESH_NEW),
        _article(article_id="lo", url="https://ex.com/lo", title="저가치",
                 score=1.0, published_at=FRESH_NEW),
        _article(article_id="st", url="https://ex.com/st", title="오래됨",
                 score=4.6, published_at=STALE_80H),
        _article(article_id="ut", url="https://ex.com/ut", title="시각 불명",
                 score=4.6, published_at=""),
    ], "business_signals": [_article()]}  # surface move → ignored
    _, gh, data = run_detector(base, mixed)
    c = _gh_counts(gh)
    check("항등식: dedup == pre_policy_meaningful + ignored",
          c["deduplicated_candidate_count"]
          == c["pre_policy_meaningful_count"] + c["ignored_count"], str(c))
    check("항등식: pre_policy == eligible + low_value + stale + unknown_time",
          c["pre_policy_meaningful_count"] == c["hourly_eligible_count"]
          + c["suppressed_low_value_count"] + c["suppressed_stale_count"]
          + c["suppressed_unknown_time_count"], str(c))
    check("meaningful_count == hourly_eligible_count == 실제 아티팩트 기사 수",
          c["meaningful_count"] == c["hourly_eligible_count"] == len(data["articles"]) == 1)
    check("아티팩트 카운트가 GITHUB_OUTPUT과 동일(단일 경로)",
          data["pre_policy_meaningful_count"] == c["pre_policy_meaningful_count"]
          and data["hourly_eligible_count"] == c["hourly_eligible_count"]
          and data["meaningful_candidate_count"] == c["meaningful_count"]
          and data["suppressed_low_value_count"] == c["suppressed_low_value_count"]
          and data["suppressed_stale_count"] == c["suppressed_stale_count"]
          and data["suppressed_unknown_time_count"] == c["suppressed_unknown_time_count"])
    check("schema_version은 1 유지(추가 필드는 전부 optional additive)",
          data["schema_version"] == 1)

    # ── legacy v1 하위호환 vs shadow-aware v1 계약 분리 (D7-AK-4B-R7D) ─────────
    # 소비자 계약은 shadow_ 필드 유무로 갈린다. 새 아티팩트는 shadow_* 필드를 실어
    # shadow-aware v1로 소비되고(shadow gate + confirmed-only articles), 그 필드가 전혀
    # 없는 진짜 legacy v1 아티팩트만 기존 alert_delta·전체 articles 계약으로 소비된다.
    # 두 계약을 각각 검증한다 — production parser의 confirmed-only 방어는 건드리지 않는다.

    # A. 진짜 legacy v1 — shadow_ 필드를 top-level·기사에서 모두 제거해 재현한다.
    legacy = copy.deepcopy(data)
    for key in [k for k in legacy if k.startswith("shadow_")]:
        del legacy[key]
    for art in legacy["articles"]:
        if isinstance(art, dict):
            for key in [k for k in art if k.startswith("shadow_")]:
                del art[key]
    # legacy 계약은 shadow 필터 없이 전체 유효 기사를 유지한다.
    legacy_valid = [
        a for a in legacy["articles"]
        if isinstance(a, dict) and " ".join(str(a.get("title") or "").split())
    ][: da.MAX_ARTICLES]
    try:
        legacy_alert = da.parse_delta_alert(legacy)
        legacy_ok = (
            legacy_alert.schema_version == 1
            and legacy_alert.alert_delta == legacy["alert_delta"]
            and len(legacy_alert.articles) == len(legacy_valid)
        )
    except da.InvalidDeltaArtifact:
        legacy_ok = False
    check("진짜 legacy v1 artifact는 기존 alert_delta와 전체 articles 계약으로 파싱된다(하위호환)",
          legacy_ok)

    # B. shadow-aware v1 — 원본 data(shadow_ 필드 보유)는 shadow gate + confirmed-only.
    #    기대 confirmed 수는 fixture에 하드코딩하지 않고 shadow_would_pass=true 기사로 센다.
    shadow_confirmed = [
        a for a in data["articles"]
        if isinstance(a, dict) and a.get("shadow_would_pass") is True
    ]
    confirmed_titles = {
        " ".join(str(a.get("title") or "").split()) for a in shadow_confirmed
    }
    try:
        shadow_alert = da.parse_delta_alert(data)
        parsed_titles = {a.title for a in shadow_alert.articles}
        shadow_ok = (
            shadow_alert.schema_version == 1
            and shadow_alert.alert_delta == data["shadow_alert_delta"]
            and len(shadow_alert.articles) == len(shadow_confirmed)
            # shadow_would_pass가 true가 아닌 기사는 sender articles에서 제외된다.
            and parsed_titles <= confirmed_titles
            and shadow_alert.meaningful_candidate_count == data["shadow_would_pass_count"]
        )
    except da.InvalidDeltaArtifact:
        shadow_ok = False
    check("shadow-aware v1 artifact는 shadow gate와 confirmed-only articles 계약으로 파싱된다",
          shadow_ok)


# ── I. D7-AK-4B confirmed-event evidence shadow 계약 ────────────────────────

def check_shadow_evidence_contract() -> None:
    """Shadow evidence is title-first, categorical, additive, and gate-neutral."""

    def evidence(
        title: str,
        snippet: str = "",
        *,
        change_type: str = "",
        change_reasons: tuple[str, ...] = (),
        policy_override: dict | None = None,
    ) -> dict:
        return rs.evaluate_hourly_urgency_shadow(
            {"title": title, "snippet": snippet},
            change_type=change_type,
            change_reasons=change_reasons,
            policy_override=policy_override,
        )

    positive_cases = (
        ("확정 수주·계약", "현대건설, UAE 원전 EPC 계약 체결",
         "confirmed_contract_order_award"),
        ("공식 협약/MOU", "현대건설, ABC와 원전 사업 MOU 체결",
         "confirmed_formal_partnership"),
        ("Meta 구체 buildout", "메타, 5GW 하이페리온 데이터센터 구축...500억달러 투입",
         "confirmed_investment_buildout"),
        ("PF 조달 완료", "A건설 프로젝트 PF 조달 완료",
         "confirmed_finance_credit_liquidity"),
        ("중대재해 특별감독", "현대건설 현장 중대재해 발생…고용노동부 특별감독 착수",
         "confirmed_safety_enforcement"),
        ("규제 취소·중단 명령", "정부, 데이터센터 인허가 취소·사업 중단 명령",
         "confirmed_regulatory_decision"),
        ("프로젝트 상업운전", "현대건설 원전 프로젝트 상업운전 개시",
         "confirmed_project_milestone"),
    )
    for name, title, event_type in positive_cases:
        result = evidence(title)
        check(
            f"shadow confirmed: {name}",
            result["shadow_urgency_status"] == "confirmed"
            and result["shadow_would_pass"] is True
            and event_type in result["shadow_confirmed_event_types"],
            str(result),
        )

    ambiguous_cases = (
        ("미래 기술 협력", "현대건설, 철도기술연구원과 미래 인프라 기술 협력"),
        ("사업 확대 추진", "대우건설, 데이터센터 사업 확대 추진"),
        ("시장 진출 검토", "현대엔지니어링, 미국 플랜트 시장 진출 검토"),
        ("장기 비전", "현대건설, 원전·SMR 장기 비전 공개"),
        ("자금 확보 계획", "현대건설, 신사업 자금 확보 계획"),
        ("portfolio 확대 전략", "대우건설, 에너지 portfolio 확대 전략"),
        ("정책 방향 발표", "정부, 데이터센터 정책 방향 발표"),
    )
    for name, title in ambiguous_cases:
        result = evidence(title)
        check(
            f"shadow ambiguous: {name}",
            result["shadow_urgency_status"] == "ambiguous"
            and result["shadow_would_pass"] is False,
            str(result),
        )

    blocked_cases = (
        ("책 출간", "AI 시대 전력산업 구조 개편 책 출간"),
        ("인터뷰", "건설사 대표 인터뷰…데이터센터 전략을 말하다"),
        ("채용 기사", "효성, 창사 첫 문과생 전용 공채"),
        ("업계 종합 기획", "[기획] 건설업계 AI 데이터센터 수주 경쟁"),
    )
    for name, title in blocked_cases:
        result = evidence(title, "다른 기업이 데이터센터 공급 계약을 체결했다")
        check(
            f"shadow blocked: {name}",
            result["shadow_urgency_status"] == "blocked"
            and result["shadow_would_pass"] is False,
            str(result),
        )

    outlook = evidence("AI 데이터센터 시장 확대 가능성…수주 증가 전망")
    check("전망·가능성은 confirmed가 아닌 ambiguous",
          outlook["shadow_urgency_status"] == "ambiguous"
          and outlook["shadow_would_pass"] is False, str(outlook))
    readiness = evidence("현대건설, 원전 신사업 준비·대비")
    check("준비·대비는 confirmed가 아닌 ambiguous",
          readiness["shadow_urgency_status"] == "ambiguous"
          and readiness["shadow_would_pass"] is False, str(readiness))

    snippet_only = evidence(
        "건설산업 일반 동향",
        "메타, 5GW 데이터센터 구축에 500억달러 투입",
    )
    check("snippet-only 계약/buildout 표현은 confirmed를 열지 않는다",
          snippet_only["shadow_urgency_status"] == "ambiguous"
          and snippet_only["shadow_would_pass"] is False
          and snippet_only["title_positive_groups"] == []
          and "confirmed_investment_buildout" in snippet_only["snippet_positive_groups"]
          and snippet_only["shadow_evidence_source"] == "snippet_only", str(snippet_only))

    domain_only = evidence("현대건설 SMR 플랜트 사업 현황")
    check("HDEC 이름 + SMR/플랜트 domain만으로 confirmed가 열리지 않는다",
          domain_only["shadow_urgency_status"] == "none"
          and domain_only["shadow_would_pass"] is False, str(domain_only))

    crossing = evidence(
        "포스코, 티타늄 판재 사업 재검토…고부가 철강 선택과 집중",
        change_type="priority_upgrade",
        change_reasons=("score crossed 3.5 threshold (2.6->3.6)",),
    )
    check("priority_upgrade score crossing-only는 shadow blocked",
          crossing["shadow_urgency_status"] == "blocked"
          and crossing["shadow_would_pass"] is False, str(crossing))
    crossing_with_event = evidence(
        "현대건설, UAE 원전 EPC 계약 체결",
        change_type="priority_upgrade",
        change_reasons=("score crossed 3.5 threshold (2.6->3.6)",),
    )
    check("priority crossing과 별개인 title 확정 사건은 독립 평가",
          crossing_with_event["shadow_urgency_status"] == "confirmed"
          and crossing_with_event["shadow_would_pass"] is True,
          str(crossing_with_event))

    unavailable = evidence("현대건설, UAE 원전 EPC 계약 체결", policy_override={})
    check("missing/malformed shadow policy → unavailable·fail-closed",
          unavailable["shadow_urgency_status"] == "unavailable"
          and unavailable["shadow_would_pass"] is False, str(unavailable))
    malformed_policy = json.loads(json.dumps(rs._HOURLY_URGENCY_SHADOW))
    malformed_policy["positive_event_groups"]["confirmed_contract_order_award"]["any"] = "bad"
    malformed_shape = evidence(
        "현대건설, UAE 원전 EPC 계약 체결",
        policy_override=malformed_policy,
    )
    check("malformed shadow term shape → unavailable·fail-closed",
          malformed_shape["shadow_urgency_status"] == "unavailable"
          and malformed_shape["shadow_would_pass"] is False, str(malformed_shape))

    policy = json.loads((ROOT / "data" / "radar_signal_policy.json").read_text(encoding="utf-8"))
    namespace = policy.get("hourly_urgency_shadow")

    def has_numeric(value: object) -> bool:
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, dict):
            return any(has_numeric(item) for item in value.values())
        if isinstance(value, list):
            return any(has_numeric(item) for item in value)
        return False

    check("isolated shadow policy namespace loads", rs.shadow_urgency_policy_loaded())
    check("shadow namespace adds no numeric threshold",
          isinstance(namespace, dict) and not has_numeric(namespace))
    policy_text = json.dumps(namespace, ensure_ascii=False)
    check("shadow policy contains no audited full-title allowlist",
          all(title not in policy_text for _, title, _ in positive_cases)
          and all(title not in policy_text for _, title in ambiguous_cases))

    # Existing actor/event/infra/exclusion extraction remains pinned to its old semantics.
    existing = rs.extract_ai_radar_signals({
        "title": "현대건설, 美 FANCO와 차세대 SMR 협력…4세대 원자로 협력망 확대",
        "snippet": "",
    })
    check("existing radar extractor result unchanged by isolated namespace",
          existing["signals"] == {
              "actor": ["hdec"],
              "event": [],
              "infra": ["advanced_energy"],
              "exclusion": [],
          }, str(existing["signals"]))

    # Detector integration: current gate is open for both confirmed and none.
    base = {"top_immediate_signals": [_article()]}
    confirmed_model = {"top_immediate_signals": [
        _article(),
        _article(article_id="shadow-confirmed", url="https://ex.com/shadow-confirmed",
                 title="메타, 5GW 데이터센터 구축...500억달러 투입",
                 published_at=FRESH_NEW),
    ]}
    proc_c, gh_c, data_c = run_detector(base, confirmed_model)
    article_c = data_c["articles"][0]
    shadow_article_fields = {
        "shadow_urgency_status", "shadow_would_pass",
        "shadow_confirmed_event_types", "shadow_ambiguous_event_types",
        "shadow_negative_contexts", "shadow_evidence_source",
    }
    check("shadow confirmed여도 current alert_delta/eligible/artifact count 유지",
          proc_c.returncode == 0 and gh_c.get("alert_delta") == "true"
          and gh_c.get("hourly_eligible_count") == "1"
          and len(data_c["articles"]) == 1 and data_c["alert_delta"] is True
          and gh_c.get("shadow_alert_delta") == "true"
          and gh_c.get("shadow_confirmed_count") == "1"
          and shadow_article_fields.issubset(article_c), str(gh_c))

    none_model = {"top_immediate_signals": [
        _article(),
        _article(article_id="shadow-none", url="https://ex.com/shadow-none",
                 title="현대건설 SMR 플랜트 사업 현황", published_at=FRESH_NEW),
    ]}
    _, gh_n, data_n = run_detector(base, none_model)
    check("shadow none이어도 current alert_delta=true·artifact 기사 유지",
          gh_n.get("alert_delta") == "true"
          and gh_n.get("hourly_eligible_count") == "1"
          and gh_n.get("shadow_alert_delta") == "false"
          and gh_n.get("shadow_none_count") == "1"
          and len(data_n["articles"]) == 1 and data_n["alert_delta"] is True,
          str(gh_n))

    ordered = {"top_immediate_signals": [
        _article(),
        _article(article_id="shadow-d", url="https://ex.com/shadow-d",
                 title="현대건설 SMR 플랜트 사업 현황", score=2.0,
                 published_at=FRESH_NEW, provenance=HDEC_DIRECT_PROV),
        _article(article_id="shadow-s49", url="https://ex.com/shadow-s49",
                 title="메타, 5GW 데이터센터 구축...500억달러 투입", score=4.9,
                 published_at=FRESH_NEW),
        _article(article_id="shadow-s40", url="https://ex.com/shadow-s40",
                 title="건설산업 일반 동향", score=4.0, published_at=FRESH_NEW),
    ]}
    _, gh_o, data_o = run_detector(base, ordered)
    check("shadow telemetry does not change current article ordering",
          [a["article_key"] for a in data_o["articles"]]
          == ["shadow-d", "shadow-s49", "shadow-s40"])
    shadow_o = _shadow_counts(gh_o)
    check("shadow count identity over D7-AK-2 eligible articles",
          int(gh_o["hourly_eligible_count"]) == sum(
              shadow_o[key] for key in (
                  "shadow_confirmed_count", "shadow_ambiguous_count",
                  "shadow_blocked_count", "shadow_none_count",
                  "shadow_unavailable_count",
              )
          )
          and shadow_o["shadow_would_pass_count"]
          == shadow_o["shadow_confirmed_count"], str(shadow_o))

    mixed = {"top_immediate_signals": [
        _article(),
        _article(article_id="shadow-ok", url="https://ex.com/shadow-ok",
                 title="메타, 5GW 데이터센터 구축...500억달러 투입",
                 score=4.2, published_at=FRESH_NEW),
        _article(article_id="shadow-low", url="https://ex.com/shadow-low",
                 title="현대건설, UAE 원전 EPC 계약 체결",
                 score=1.0, published_at=FRESH_NEW),
        _article(article_id="shadow-stale", url="https://ex.com/shadow-stale",
                 title="현대건설, ABC와 원전 사업 MOU 체결",
                 score=4.8, published_at=STALE_80H),
    ]}
    _, gh_m, _ = run_detector(base, mixed)
    check("low-value/stale/ignored articles are outside shadow evaluator counts",
          gh_m.get("pre_policy_meaningful_count") == "3"
          and gh_m.get("hourly_eligible_count") == "1"
          and gh_m.get("suppressed_low_value_count") == "1"
          and gh_m.get("suppressed_stale_count") == "1"
          and gh_m.get("shadow_confirmed_count") == "1", str(gh_m))

    # The classifier must also aggregate unavailable when its loaded namespace fails.
    original_shadow_policy = rs._HOURLY_URGENCY_SHADOW
    try:
        rs._HOURLY_URGENCY_SHADOW = {}
        unavailable_classification = dc.classify_delta(
            [],
            [("top_immediate_signals", _article(
                article_id="shadow-unavailable", url="https://ex.com/shadow-unavailable",
                title="현대건설, UAE 원전 EPC 계약 체결", published_at=FRESH_NEW,
            ))],
            news_mode="live",
            reference_dt=datetime.fromisoformat(NOW_ISO),
        )
    finally:
        rs._HOURLY_URGENCY_SHADOW = original_shadow_policy
    check("eligible article under unavailable policy remains current-eligible but shadow-closed",
          unavailable_classification.hourly_eligible_count == 1
          and unavailable_classification.shadow_unavailable_count == 1
          and unavailable_classification.shadow_would_pass_count == 0)

    # Sender consumer contract:
    # 한 실행에 confirmed와 일반 동향이 섞여도 실제 본문에는 confirmed만 남아야 한다.
    mixed_sender_model = {
        "top_immediate_signals": [
            _article(),
            _article(
                article_id="sender-confirmed",
                url="https://ex.com/sender-confirmed",
                title="현대건설, UAE 원전 EPC 계약 체결",
                score=4.8,
                published_at=FRESH_NEW,
            ),
            _article(
                article_id="sender-none",
                url="https://ex.com/sender-none",
                title="현대건설 SMR 시장 동향",
                score=4.2,
                published_at=FRESH_NEW,
            ),
        ]
    }

    _, mixed_gh, mixed_artifact = run_detector(
        base,
        mixed_sender_model,
    )

    mixed_alert = da.parse_delta_alert(mixed_artifact)
    mixed_telegram = da.render_telegram(mixed_alert)
    mixed_email = da.render_email_text(mixed_alert)

    check(
        "mixed artifact → current eligible는 2건이지만 sender는 confirmed 1건만 소비",
        mixed_gh.get("hourly_eligible_count") == "2"
        and mixed_gh.get("shadow_would_pass_count") == "1"
        and len(mixed_artifact["articles"]) == 2
        and mixed_alert.alert_delta is True
        and mixed_alert.sendable is True
        and mixed_alert.meaningful_candidate_count == 1
        and len(mixed_alert.articles) == 1
        and mixed_alert.articles[0].title
        == "현대건설, UAE 원전 EPC 계약 체결",
    )

    check(
        "mixed artifact render → unconfirmed 기사 제목 완전 제외",
        "현대건설, UAE 원전 EPC 계약 체결" in mixed_telegram
        and "현대건설, UAE 원전 EPC 계약 체결" in mixed_email
        and "현대건설 SMR 시장 동향" not in mixed_telegram
        and "현대건설 SMR 시장 동향" not in mixed_email,
    )

    # current alert_delta는 true지만 shadow confirmed가 0건인 실행은
    # parser의 2차 방어에서도 sendable=false여야 한다.
    unconfirmed_sender_model = {
        "top_immediate_signals": [
            _article(),
            _article(
                article_id="sender-unconfirmed-only",
                url="https://ex.com/sender-unconfirmed-only",
                title="현대건설 SMR 시장 동향",
                score=4.2,
                published_at=FRESH_NEW,
            ),
        ]
    }

    _, unconfirmed_gh, unconfirmed_artifact = run_detector(
        base,
        unconfirmed_sender_model,
    )

    unconfirmed_alert = da.parse_delta_alert(
        unconfirmed_artifact
    )

    check(
        "unconfirmed-only artifact → current=true여도 sender 2차 방어는 닫힘",
        unconfirmed_gh.get("alert_delta") == "true"
        and unconfirmed_gh.get("shadow_alert_delta") == "false"
        and unconfirmed_artifact["alert_delta"] is True
        and unconfirmed_artifact["shadow_alert_delta"] is False
        and unconfirmed_alert.alert_delta is False
        and unconfirmed_alert.sendable is False
        and unconfirmed_alert.meaningful_candidate_count == 0
        and unconfirmed_alert.articles == (),
    )

    # Static wiring: current alert_delta는 진단/선별 계약으로 유지하되,
    # 실제 sender 진입은 shadow confirmed 결과만 소비한다.
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    detector_text = DETECTOR.read_text(encoding="utf-8")
    tg_if = _step_if(
        workflow_text,
        "Hourly telegram digest (delta-gated auto-send)",
    )
    skip_if = _step_if(
        workflow_text,
        "Skip automatic Telegram alert (no confirmed urgency)",
    )

    check(
        "Telegram sender step gate uses shadow confirmed urgency",
        "steps.delta.outputs.shadow_alert_delta == 'true'" in tg_if
        and "steps.delta.outputs.alert_delta == 'true'" not in tg_if,
    )
    check(
        "detector current alert_delta formula remains hourly_eligible_count>=1",
        "alert_delta = classification.hourly_eligible_count >= 1"
        in detector_text,
    )
    check(
        "workflow skip path uses shadow confirmed urgency",
        "steps.delta.outputs.shadow_alert_delta != 'true'" in skip_if
        and "no confirmed urgency — skip telegram" in workflow_text,
    )

    # Count-only stdout/GITHUB_OUTPUT; the artifact may contain the title by contract.
    canary_title = "SHADOWSECRET 계약 체결"
    canary_url = "https://leak.example/shadow-secret"
    proc_l, gh_l, _ = run_detector(base, {"top_immediate_signals": [
        _article(),
        _article(article_id="shadow-log", url=canary_url, title=canary_title,
                 score=4.2, published_at=FRESH_NEW),
    ]})
    logs = (proc_l.stdout or "") + (proc_l.stderr or "")
    gh_log = "\n".join(f"{key}={value}" for key, value in gh_l.items())
    check("shadow stdout/GITHUB_OUTPUT expose counts only",
          canary_title not in logs and canary_url not in logs
          and canary_title not in gh_log and canary_url not in gh_log
          and "shadow urgency:" in logs)

    # Only the hourly delta classifier may consume the evaluator; daily/report paths stay isolated.
    consumers = []
    for path in (ROOT / "app").glob("*.py"):
        if path.name == "radar_signals.py":
            continue
        if "evaluate_hourly_urgency_shadow" in path.read_text(encoding="utf-8"):
            consumers.append(path.name)
    check("daily/report classifiers do not consume shadow evaluator",
          consumers == ["delta_classifier.py"], str(consumers))


def main() -> int:
    print(f"== verify_meaningful_delta_quality (D7-AK-1/2/4B) @ {ROOT} ==")
    check_ignored_changes()
    check_meaningful_changes()
    check_identity_matching()
    check_sender_gates_closed_on_zero_meaningful()
    check_workflow_production_guards()
    check_sender_second_defense()
    check_schema_compat()
    check_log_hygiene()
    check_pipeline_wiring()
    check_value_gate()
    check_recency_gate()
    check_content_update_gate()
    check_low_value_run_closes_workflow()
    check_eligible_ordering()
    check_reference_time_determinism()
    check_count_identities()
    check_shadow_evidence_contract()

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
    print("RESULT: PASS — hourly delta gate: noise suppressed, classification preserved, "
          "low-value/stale alerts withheld, high-value ordering first, deterministic on --now, "
          "sender gates closed unless shadow urgency is confirmed, "
          "shadow evidence promoted to production sender gate, "
          "schema-compatible, content-free")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
