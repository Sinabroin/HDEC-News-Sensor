#!/usr/bin/env python3
"""D7-AK-6E R4-R12 §1 — primary_10 row-level decision trace + positive path.

The 2026-08-06 natural run 31058761846 counted raw_primary_10_rows=1,
teams_immediate_major_rows=0, selected=0 — correct, but unexplainable at row
level from the run log alone. This verifier proves, entirely offline
(network 0, real SMTP 0, production state writes 0):

* §1 the exact reconstructed natural-run primary_10 row (조선일보 건설株
  market-rally article, direct chosun.com URL) emits one categorical
  major-row trace with the exact rejection reason
  ``excluded_stock_market_dominant`` and is never selected — no gate was
  weakened to force a send;
* §2 the positive path end-to-end: a fully-eligible primary_10 article is
  selected, delivered through the production sender with an injected SMTP
  recorder (250 accepted), persisted to a temp ledger, and traced as
  ``stage=selected`` with an accepted SMTP status;
* §3 secondary_3 and promoted-official rows reaching the sender each emit
  exactly one trace entry;
* §4 ledger-blocked major rows are traced with their dedup reason;
* §5 the printed trace is log-safe: non-reversible refs and categorical codes
  only — never a URL, a title, or a credential;
* §6 the production ledger is read-only for every run in this verifier.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import send_teams_ai_push as sender  # noqa: E402
from verify_teams_ai_push_production import (  # noqa: E402
    _SMTPRecorder,
    _article,
    _fixture_credentials,
    _payload,
    _write,
)
from app.teams_push_state import empty_state, save_state  # noqa: E402

NOW = "2026-08-06T09:11:00+09:00"
CHECKS = 0
FAILURES: list[str] = []

# The exact reconstructed natural-run primary_10 row (run 31058761846):
# 조선일보, direct www.chosun.com URL via the Naver originallink, published
# 08:56 KST — a construction-stocks market-rally piece the R4-R9B stock hard
# gate must reject. Title/URL live only in this fixture, never in trace output.
CHOSUN_URL = (
    "https://www.chosun.com/economy/money/2026/08/06/"
    "CTWLQ64A7NCVLCFBTSLVVZ47TA/"
)
CHOSUN_TITLE = (
    "빅테크와 AI 데이터센터 협력, 美 에너지시장 진출 가능성도...고개드는 건설株"
)
CHOSUN_SNIPPET = (
    "국내 증시에서 건설주가 강세다. 빅테크와의 AI 데이터센터 협력, 미국 에너지 "
    "시장 진출 가능성 등이 주가를 끌어올리는 모습이다."
)


def check(name: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"PASS: {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL: {name}" + (f" — {detail[:300]}" if detail else ""))


def natural_run_primary_row() -> dict:
    return _article(
        article_key="run-31058761846-primary",
        title=CHOSUN_TITLE,
        summary=CHOSUN_SNIPPET,
        hdec_relevance="건설주 흐름 참고",
        source="조선일보",
        url=CHOSUN_URL,
        published_at="2026-08-05T23:56:00+00:00",
        score=2.4,
        shadow_urgency_status="ambiguous",
        shadow_would_pass=False,
        shadow_confirmed_event_types=[],
    )


def eligible_primary(key: str = "positive-primary", source: str = "연합뉴스",
                     url: str = "") -> dict:
    return _article(
        article_key=key,
        title="현대건설, AI 데이터센터 전력 인프라 공식 계약 체결",
        summary="현대건설이 AI 데이터센터 전력 인프라 공급 계약을 공식 체결했다.",
        hdec_relevance="현대건설 직접 영향(AI 데이터센터 전력 인프라)",
        source=source,
        url=url or f"https://www.yna.co.kr/view/{key}",
        score=4.7,
        shadow_confirmed_event_types=["contract_signed"],
    )


def run_deliver(tmp: Path, articles, *, send: bool = True, statuses=(250,) * 8):
    recorder = _SMTPRecorder(statuses)
    state_path = tmp / f"state-{abs(hash(str(sorted(a['article_key'] for a in articles)))) % 10**8}.json"
    save_state(empty_state(), state_path)
    artifact = _write(tmp / f"trace-{state_path.stem}.json", _payload(articles))
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        summary = sender.deliver(
            artifact_path=artifact,
            state_path=state_path,
            credentials=_fixture_credentials(),
            should_send=send,
            smtp_factory=recorder,
            max_articles=5,
            now_iso_value=NOW,
        )
        sender._print_summary(summary)
    return summary, recorder, stdout.getvalue()


def trace_by_source(summary, source: str):
    return [
        entry
        for entry in summary.get("major_row_decision_trace", ())
        if entry["source"] == source
    ]


def main() -> int:
    ledger = ROOT / "data" / "teams_push_state.json"
    ledger_before = ledger.read_bytes()

    with tempfile.TemporaryDirectory(prefix="r4r12-trace-") as tmp_name:
        tmp = Path(tmp_name)

        # -------------------------------------------------------------- §1
        summary, recorder, output = run_deliver(
            tmp, [natural_run_primary_row()], send=True
        )
        rows = trace_by_source(summary, "조선일보")
        check("§1 the natural-run primary_10 row emits exactly one trace",
              len(rows) == 1, f"rows={len(rows)}")
        row = rows[0] if rows else {}
        check("§1 trace carries tier=primary_10 / lane=immediate_major",
              row.get("source_tier") == "primary_10"
              and row.get("teams_lane") == "immediate_major",
              str(row))
        check("§1 exact rejection reason is excluded_stock_market_dominant",
              row.get("stage") == "policy_rejected"
              and row.get("rejection_reason") == "excluded_stock_market_dominant",
              f"{row.get('stage')}/{row.get('rejection_reason')}")
        check("§1 stock-market exclusion is flagged on the trace",
              row.get("stock_market_excluded") is True)
        check("§1 the row is never selected and never SMTP-attempted",
              row.get("final_selected") is False
              and summary["selected"] == 0
              and summary["SMTP_attempted"] == 0
              and not recorder.attempts)
        check("§1 aggregate counters stay reconciled",
              summary["counters_reconciled"] is True
              and summary["raw_primary_10_rows"] == 1
              and summary["teams_immediate_major_rows"] == 0)
        check("§1 trace line is printed in the run log",
              "Teams major-row trace: " in output
              and "tier=primary_10" in output
              and "rejection_reason=excluded_stock_market_dominant" in output)

        # -------------------------------------------------------------- §2
        summary, recorder, output = run_deliver(tmp, [eligible_primary()], send=True)
        rows = trace_by_source(summary, "연합뉴스")
        row = rows[0] if rows else {}
        check("§2 positive path: primary_10 article is selected",
              summary["selected"] == 1
              and summary["selected_primary_10_rows"] == 1)
        check("§2 positive path: exactly one SMTP attempt, accepted",
              summary["SMTP_attempted"] == 1
              and summary["SMTP_accepted"] == 1
              and len(recorder.attempts) == 1)
        check("§2 positive path: delivery persisted to the (temp) ledger",
              summary["state_committed"] == 1 and summary["state_changed"] is True)
        check("§2 trace records stage=selected with accepted SMTP status",
              row.get("stage") == "selected"
              and row.get("final_selected") is True
              and str(row.get("smtp_status", "")).startswith("accepted_"),
              str(row))
        check("§2 trace shows every gate green",
              row.get("ai_core") is True
              and row.get("hdec_relevant") is True
              and row.get("importance_sendable") is True
              and row.get("confirmed_event") is True
              and row.get("freshness_current") is True
              and row.get("stock_market_excluded") is False)

        # -------------------------------------------------------------- §3
        secondary = eligible_primary(
            key="positive-secondary", source="동아일보",
            url="https://www.donga.com/news/article/positive-secondary",
        )
        secondary.update(
            title="국가 AI 반도체 클러스터 전력망 증설 계약 확정",
            summary="국가 AI 반도체 클러스터의 전력망 증설 계약이 확정됐다.",
        )
        official = eligible_primary(
            key="positive-official", source="과학기술정보통신부",
            url="https://www.msit.go.kr/bbs/view.do?bbsSeqNo=94&nttSeqNo=5001",
        )
        official.update(
            title="과기정통부, 국가 AI 컴퓨팅센터 착공…GPU 인프라 구축 개시",
            summary="국가 AI 컴퓨팅센터 건설을 착공해 GPU 인프라 구축을 시작했다.",
            shadow_confirmed_event_types=["construction_confirmed"],
        )
        summary, recorder, output = run_deliver(
            tmp, [eligible_primary(), secondary, official], send=True
        )
        traces = summary.get("major_row_decision_trace", ())
        check("§3 every major-lane row emits exactly one trace entry",
              len(traces) == 3, f"traces={len(traces)}")
        check("§3 secondary_3 row is traced",
              any(entry["source_tier"] == "secondary_3" for entry in traces))
        check("§3 promoted-official row is traced with gate_class=promoted_official",
              any(entry["gate_class"] == "promoted_official" for entry in traces))
        check("§3 all three selected and delivered",
              summary["selected"] == 3 and summary["SMTP_accepted"] == 3)

        # -------------------------------------------------------------- §4
        state_path = tmp / "ledger-blocked-state.json"
        save_state(empty_state(), state_path)
        artifact = _write(tmp / "ledger-blocked.json", _payload([eligible_primary()]))
        first = sender.deliver(
            artifact_path=artifact, state_path=state_path,
            credentials=_fixture_credentials(), should_send=True,
            smtp_factory=_SMTPRecorder((250,)), max_articles=5,
            now_iso_value=NOW,
        )
        second = sender.deliver(
            artifact_path=artifact, state_path=state_path,
            credentials=_fixture_credentials(), should_send=True,
            smtp_factory=_SMTPRecorder((250,)), max_articles=5,
            now_iso_value=NOW,
        )
        blocked = trace_by_source(second, "연합뉴스")
        check("§4 ledger-blocked major row is traced with its dedup reason",
              first["SMTP_accepted"] == 1
              and second["SMTP_attempted"] == 0
              and len(blocked) == 1
              and blocked[0]["stage"] == "ledger_blocked"
              and blocked[0]["rejection_reason"].startswith("duplicate:"),
              str(blocked))

        # -------------------------------------------------------------- §5
        summary, _recorder, output = run_deliver(
            tmp, [natural_run_primary_row(), eligible_primary()], send=True
        )
        trace_lines = [
            line for line in output.splitlines()
            if line.startswith("Teams major-row trace: ")
        ]
        check("§5 trace lines exist for the mixed batch", len(trace_lines) == 2)
        check("§5 trace lines never contain a URL",
              all("http" not in line and "://" not in line for line in trace_lines))
        check("§5 trace lines never contain a title fragment",
              all("빅테크" not in line and "건설株" not in line
                  and "계약 체결" not in line for line in trace_lines))
        check("§5 trace lines never contain credentials or addresses",
              all("@" not in line for line in trace_lines))
        check("§5 refs are 12-hex non-reversible",
              all(
                  len(entry["article_ref"]) == 12
                  and all(ch in "0123456789abcdef" for ch in entry["article_ref"])
                  for entry in summary.get("major_row_decision_trace", ())
              ))

    # ------------------------------------------------------------------ §6
    check("§6 production ledger was read-only (byte-identical)",
          ledger.read_bytes() == ledger_before)

    print(f"checks={CHECKS} failures={len(FAILURES)}")
    print(
        "primary_positive_trace="
        + ("PASS" if not FAILURES else "FAIL")
        + " natural_run=31058761846 primary_10_rejection=excluded_stock_market_dominant"
        + " positive_path=fixture_proven_offline"
        + " network_calls=0 real_smtp_connections=0 teams_sends=0"
        + " telegram_sends=0 production_state_writes=0"
    )
    if FAILURES:
        for name in FAILURES:
            print(f"FAILED: {name}")
        return 1
    print("RESULT=D7-AK-6E_R4R12_PRIMARY_POSITIVE_TRACE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
