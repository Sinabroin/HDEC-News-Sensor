"""Verifier for P0-D3M watch-mode urgent signal queue.

The verifier uses only fixture articles and a temp WATCH_STATE_PATH. It does not
touch repo radar.db, does not call Telegram APIs, and proves that urgent signals
remain review-only by default.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import urgent_signals, watch_state  # noqa: E402

RADAR_DB = ROOT / "radar.db"
HUMAN_GATE = ROOT / "scripts" / "verify_human_review_gate.py"
STATIC_GATE = ROOT / "scripts" / "verify_static_report.py"
WATCH_CLI = ROOT / "scripts" / "watch_urgent_signals.py"

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    tag = "PASS" if ok else "FAIL"
    line = f"[{tag}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _db_state() -> tuple | None:
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _article(article_id: str, title: str, source: str = "연합뉴스",
             snippet: str = "", url_suffix: str | None = None) -> dict:
    suffix = url_suffix or article_id
    return {
        "id": article_id,
        "title": title,
        "source": source,
        "published_at": "2026-06-18T08:30:00+09:00",
        "url": f"https://example.com/watch/{suffix}",
        "snippet": snippet,
    }


def _fixture_articles() -> list[dict]:
    return [
        _article(
            "fx_hdec_risk",
            "현대건설, 중대재해 사고 관련 조사 착수",
            "연합뉴스",
            "현대건설 현장에서 중대재해 사고가 발생해 관계기관 조사가 시작됐다."),
        _article(
            "fx_hdec_smr",
            "현대건설, SMR 원전 EPC 본계약 체결",
            "한국경제",
            "소형모듈원자로 EPC 프로젝트 본계약으로 원전 사업 포트폴리오 확대가 예상된다."),
        _article(
            "fx_policy",
            "정부, 건설산업 중대재해 안전규제 시행령 확정",
            "서울경제",
            "공공입찰과 건설현장 안전규제 rules가 강화돼 대형 건설사 영향이 예상된다."),
        _article(
            "fx_competitor",
            "삼성물산, 해외 데이터센터 EPC 대형 수주",
            "매일경제",
            "경쟁 건설사의 데이터센터 해외 EPC 대형 수주로 입찰 경쟁 구도 변화가 예상된다."),
        _article(
            "fx_stock",
            "AI 테마주 급등… 증권가 수혜주 옥석 가리기",
            "Mock News",
            "투자의견과 목표가 상향 종목이 소개됐다."),
        _article(
            "fx_sports",
            "현대건설 배구 박정아, 스타랭킹 상위권",
            "스포츠뉴스",
            "선수 랭킹 기사다."),
        _article(
            "fx_sales",
            "현대건설 힐스테이트 모델하우스 홍보관 개관",
            "홍보뉴스",
            "분양 홍보관과 청약 일정 안내."),
        _article(
            "fx_roundup",
            "금융권 이모저모, 은행권 PF 소식 모음",
            "경제브리핑",
            "generic roundup."),
    ]


def _classes(result: dict) -> dict[str, str]:
    return {item["article_id"]: item["urgency_class"] for item in result["queue"]}


def _statuses(result: dict) -> dict[str, str]:
    return {item["article_id"]: item["seen_status"] for item in result["queue"]}


def _all_review_only(result: dict) -> bool:
    return all(
        item.get("send_allowed") is False and item.get("review_required") is True
        for item in result["queue"]
    )


def check_first_run(state_path: Path) -> dict:
    state = watch_state.load_state(state_path)
    result = urgent_signals.evaluate_articles(_fixture_articles(), state)
    classes = _classes(result)

    check("A first run detects urgent HDEC direct risk",
          classes.get("fx_hdec_risk") == urgent_signals.URGENCY_SEND_CANDIDATE,
          str(classes.get("fx_hdec_risk")))
    check("A first run detects HDEC nuclear/order signal",
          classes.get("fx_hdec_smr") == urgent_signals.URGENCY_REVIEW_TODAY,
          str(classes.get("fx_hdec_smr")))
    check("A first run detects policy/regulatory signal",
          classes.get("fx_policy") == urgent_signals.URGENCY_REVIEW_TODAY,
          str(classes.get("fx_policy")))
    check("A first run detects lower-priority competitor move",
          classes.get("fx_competitor") == urgent_signals.URGENCY_REVIEW_TODAY,
          str(classes.get("fx_competitor")))
    bad_ids = {"fx_stock", "fx_sports", "fx_sales", "fx_roundup"}
    queued_bad = bad_ids.intersection(classes)
    check("A hard exclusions absent from urgent queue", not queued_bad,
          ", ".join(sorted(queued_bad)))
    check("A all queued entries are review-only/send_allowed=false",
          _all_review_only(result))
    check("A explicit human-review gate marker in digest",
          "Telegram send: blocked by human review gate" in result["review_digest"])
    urgent_signals.commit_result(result, state_path)
    return result


def check_second_run(state_path: Path) -> None:
    state = watch_state.load_state(state_path)
    result = urgent_signals.evaluate_articles(_fixture_articles(), state)
    summary = result["summary"]
    check("B second run urgent candidates drop to zero",
          summary["urgent_candidate_count"] == 0,
          str(summary["urgent_candidate_count"]))
    check("B second run marks same fixture as known duplicates",
          summary["skipped_duplicate_count"] == len(_fixture_articles()),
          str(summary["skipped_duplicate_count"]))


def check_same_cluster(state_path: Path) -> None:
    state = watch_state.load_state(state_path)
    repeated = [_article(
        "fx_hdec_smr_followup",
        "현대건설 SMR 원전 EPC 계약 후속 분석",
        "조선비즈",
        "같은 SMR EPC 프로젝트 후속 기사로 새 위험 유형은 없다.",
        "fx_hdec_smr_followup")]
    result = urgent_signals.evaluate_articles(repeated, state)
    statuses = _statuses(result)
    classes = _classes(result)
    check("C same-cluster new article marked repeated_cluster",
          statuses.get("fx_hdec_smr_followup") == urgent_signals.SEEN_REPEATED_CLUSTER,
          str(statuses.get("fx_hdec_smr_followup")))
    check("C same-cluster article is monitor_only, not fresh urgent",
          classes.get("fx_hdec_smr_followup") == urgent_signals.URGENCY_MONITOR_ONLY,
          str(classes.get("fx_hdec_smr_followup")))
    check("C same-cluster urgent candidate count zero",
          result["summary"]["urgent_candidate_count"] == 0)


def check_new_severe_risk(state_path: Path) -> None:
    state = watch_state.load_state(state_path)
    severe = [_article(
        "fx_hdec_severe_new_source",
        "현대건설, 중대재해 사고 관련 압수수색 진행",
        "뉴시스",
        "동일 리스크 클러스터지만 압수수색까지 확대된 중대재해 사고다.",
        "fx_hdec_severe_new_source")]
    result = urgent_signals.evaluate_articles(severe, state)
    classes = _classes(result)
    check("D new severe HDEC risk becomes send_candidate",
          classes.get("fx_hdec_severe_new_source")
          == urgent_signals.URGENCY_SEND_CANDIDATE,
          str(classes.get("fx_hdec_severe_new_source")))
    check("D new severe risk remains review-required",
          _all_review_only(result))


def check_source_contract() -> None:
    cli = WATCH_CLI.read_text(encoding="utf-8")
    module = (ROOT / "app" / "urgent_signals.py").read_text(encoding="utf-8")
    combined = cli + "\n" + module
    sender_calls = (
        "import send_telegram", "from scripts import send_telegram",
        "send_telegram.main", "api.telegram.org", "TELEGRAM_BOT_TOKEN",
    )
    check("watch path does not import/call Telegram sender",
          not any(token in combined for token in sender_calls))
    check("watch queue defaults send_allowed false",
          '"send_allowed": False' in module)
    check("watch queue defaults review_required true",
          '"review_required": True' in module)


def run_existing_gate(path: Path, label: str, timeout: int) -> None:
    proc = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "APP_MODE": "mock"},
        timeout=timeout,
    )
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip()[-400:]
    check(label, proc.returncode == 0, tail)


def main() -> int:
    print(f"== verify_watch_urgent_signals @ {ROOT} ==")
    before = _db_state()
    with tempfile.TemporaryDirectory(prefix="hdec_watch_state_") as tmp:
        state_path = Path(tmp) / "watch_state.json"
        os.environ["WATCH_STATE_PATH"] = str(state_path)
        check_first_run(state_path)
        check_second_run(state_path)
        check_same_cluster(state_path)
        check_new_severe_risk(state_path)
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        check("E temp watch state written only under temp path",
              str(state_path).startswith(tmp))
        state_text = json.dumps(saved, ensure_ascii=False)
        check("E watch state stores no Telegram secrets/tokens",
              "TELEGRAM" not in state_text and "telegram" not in state_text)

    check_source_contract()
    run_existing_gate(HUMAN_GATE, "F existing human review gate still passes", 900)
    run_existing_gate(STATIC_GATE, "G daily/static report gate still passes", 1200)

    after = _db_state()
    check("E repo radar.db untouched", before == after,
          f"before={before} after={after}")

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for failure in _failures:
            print(f"  - {failure}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
