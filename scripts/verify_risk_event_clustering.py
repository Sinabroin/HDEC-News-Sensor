"""D3O verifier — risk event clustering and excluded direct-risk audit.

Deterministic, no live network, temp DB only.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RADAR_DB = ROOT / "radar.db"
sys.path.insert(0, str(ROOT))

FIX = [
    {"id": "gtx_1", "source": "서울신문",
     "title": "서울시, GTX 삼성역 철근 누락 현대건설에 벌점…공공수주 입찰 제한",
     "snippet": "서울시가 GTX 삼성역 철근 누락과 관련해 현대건설 벌점 및 공공수주 입찰 제한 가능성을 통보했다."},
    {"id": "gtx_2", "source": "v.daum.net",
     "title": "[단독] 철근누락 현대건설 벌점 2점 통보…선분양·공공수주 경고등",
     "snippet": "현대건설 GTX 삼성역 현장의 철근 누락과 벌점 2점 통보가 선분양과 공공수주 리스크로 번졌다."},
    {"id": "gtx_3", "source": "오늘경제",
     "title": "[심층] 현대건설, GTX 삼성역 철근 178톤 누락 파장…안전불감증",
     "snippet": "GTX 삼성역 철근 누락 파장이 안전불감증과 품질관리 리스크로 확산되고 있다."},
    {"id": "ain_dubai", "source": "v.daum.net",
     "title": "현대건설, 아인 두바이 공방…하자·공기지연 책임 상호 이견",
     "snippet": "아인 두바이 프로젝트 하자와 공기지연 책임을 두고 손배 분쟁 가능성이 제기됐다."},
    {"id": "policy_ai_dc", "source": "전자신문",
     "title": "특별법 시행 앞둔 AI 데이터센터…건설사 전력망·냉각 솔루션 경쟁",
     "snippet": "AI 데이터센터 특별법 시행을 앞두고 건설사들이 전력망과 냉각 솔루션 대응에 나섰다."},
    {"id": "stock_noise", "source": "한국경제",
     "title": "AI 전력난 수혜주 급등…건설 관련주 투자포인트",
     "snippet": "AI 전력난 수혜주와 건설 관련주 투자포인트를 점검했다."},
    {"id": "bad_sales", "source": "서울경제",
     "title": "서울시, 현대건설 힐스테이트 분양 일정 공개",
     "snippet": "청약 일정과 분양 정보를 공개했다."},
    {"id": "bad_forum", "source": "국토일보",
     "title": "국토부, 스마트건설 기술 포럼 개최",
     "snippet": "건설사와 스마트건설 기술 방향을 논의했다."},
    {"id": "bad_gtxc", "source": "연합뉴스",
     "title": "GTX-C 입찰 제도 개선안 발표",
     "snippet": "GTX-C 노선 입찰 제도 개선안이 논의됐다."},
    {"id": "bad_gtxa", "source": "뉴스1",
     "title": "GTX-A 개통 앞두고 입찰 잡음 논란",
     "snippet": "GTX-A 개통과 입찰 잡음 관련 보도가 이어졌다."},
    {"id": "severe_supervision", "source": "서울경제",
     "title": "현대건설 현장 중대재해…고용부 특별감독 착수",
     "snippet": "현대건설 건설현장에서 중대재해가 발생해 고용노동부가 특별감독에 착수했다."},
]
for i, item in enumerate(FIX):
    item.setdefault("published_at", f"2026-06-19T{9 + i:02d}:00:00+09:00")
    item.setdefault("url", f"https://d3o.test/{item['id']}")

REQUIRED_EVENT_FIELDS = {
    "event_key",
    "event_title",
    "event_type",
    "severity",
    "impact_axes",
    "source_count",
    "sources",
    "article_count",
    "supporting_articles",
    "has_gateway_source",
    "has_major_source",
    "has_direct_hdec",
    "needs_operator_confirmation",
    "send_allowed",
    "review_required",
}

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _db_state():
    if not RADAR_DB.exists():
        return None
    stat = RADAR_DB.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _run_pipeline_sim() -> dict | None:
    code = (
        "import os, sys, json, tempfile\n"
        "d=tempfile.mkdtemp()\n"
        "os.environ['DB_PATH']=os.path.join(d,'t.db')\n"
        "os.environ['APP_MODE']='mock'; os.environ['NEWS_MODE']='live'\n"
        "sys.path.insert(0,'.')\n"
        "FIX=" + json.dumps(FIX, ensure_ascii=False) + "\n"
        "from app import db, collector, scoring, insight, briefing, live_collector as lc\n"
        "from app import decision_relevance, radar\n"
        "lc.fetch_all=lambda *a,**k:[dict(x) for x in FIX]\n"
        "db.init_db(); collector.run(); scoring.score_all(); insight.generate_all()\n"
        "b=briefing.build_brief()\n"
        "rows={r['id']:r for r in db.fetch_articles_with_scores()}\n"
        "details={rid:db.fetch_article_detail(rid) for rid in rows}\n"
        "def cat(rid):\n"
        "    from app import insight as ins\n"
        "    impl=(((details.get(rid) or {}).get('insight')) or {}).get('hdec_implication') or ''\n"
        "    inv={t:k for k,t in ins.IMPLICATION_TEMPLATES.items()}\n"
        "    return inv.get(impl.strip(),'general')\n"
        "decisions={rid:decision_relevance.classify(row, cat(rid)) for rid,row in rows.items()}\n"
        "radars={rid:decision_relevance.override_radar_section(radar.classify_section(row, cat(rid)), decisions[rid]) for rid,row in rows.items()}\n"
        "profiles={rid:briefing._top_exposure_profile(row, decisions[rid]) for rid,row in rows.items()}\n"
        "out={'mode':b.get('news_data_mode'), 'events':b.get('risk_event_clusters') or [],\n"
        "     'risk_ids':[e.get('article_id') for e in (b.get('risk_regulation_signals') or [])],\n"
        "     'radars':radars, 'profiles':profiles,\n"
        "     'review_excluded':[a.get('article_id') for a in ((b.get('review_excluded_evidence') or {}).get('items') or [])]}\n"
        "print(json.dumps(out, ensure_ascii=False))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, cwd=ROOT, timeout=240)
    if proc.returncode != 0:
        check("fixture pipeline 실행", False, (proc.stderr or "").strip()[-900:])
        return None
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("fixture pipeline 출력 파싱", False, (proc.stdout or "")[-700:])
        return None


def _event_by_key(events: list[dict], key: str) -> dict | None:
    return next((e for e in events if e.get("event_key") == key), None)


def _event_with_article(events: list[dict], article_id: str) -> dict | None:
    for ev in events:
        ids = {a.get("article_id") for a in (ev.get("supporting_articles") or [])}
        if article_id in ids:
            return ev
    return None


def _support_ids(ev: dict | None) -> set[str]:
    if not ev:
        return set()
    return {a.get("article_id") for a in (ev.get("supporting_articles") or [])}


def _check_event_contract(events: list[dict]) -> None:
    check("risk_event_clusters exists in brief JSON",
          isinstance(events, list) and bool(events), f"{len(events)}건")
    missing = {
        ev.get("event_key") or f"idx-{idx}": sorted(REQUIRED_EVENT_FIELDS - set(ev))
        for idx, ev in enumerate(events)
        if REQUIRED_EVENT_FIELDS - set(ev)
    }
    check("each event has required fields", not missing, str(missing))
    check("no event has send_allowed true",
          all(ev.get("send_allowed") is False for ev in events))
    check("no event lacks review_required",
          all(ev.get("review_required") is True for ev in events))


def _check_gtx(events: list[dict]) -> None:
    gtx = _event_by_key(events, "hdec_risk_gtx_samseong_rebar_penalty")
    gtx_count = sum(1 for ev in events
                    if ev.get("event_key") == "hdec_risk_gtx_samseong_rebar_penalty")
    check("A: GTX/철근/벌점 is one cluster, not three", gtx_count == 1,
          f"count={gtx_count}")
    if not check("A: GTX event cluster exists", bool(gtx)):
        return
    ids = _support_ids(gtx)
    check("A: GTX event has >=3 supporting articles",
          (gtx.get("article_count") or 0) >= 3 or len(ids) >= 3,
          f"article_count={gtx.get('article_count')} support={sorted(ids)}")
    check("A: GTX event type is bid/quality risk",
          gtx.get("event_type") in {"bid_restriction", "quality_defect"},
          str(gtx.get("event_type")))
    axes = set(gtx.get("impact_axes") or [])
    check("A: GTX impact axes include public order and quality",
          {"공공수주", "품질관리"} <= axes, str(gtx.get("impact_axes")))
    check("A: GTX review_required true and send_allowed false",
          gtx.get("review_required") is True and gtx.get("send_allowed") is False)
    check("A: excluded/low-priority GTX evidence can be represented",
          "gtx_3" in ids or (gtx.get("excluded_support_count") or 0) > 0,
          f"support={sorted(ids)} excluded={gtx.get('excluded_support_count')}")
    bad_ids = {"bad_gtxc", "bad_gtxa"} & ids
    check("A: unrelated GTX-A/C articles absent from Samsung Station event",
          not bad_ids, f"bad_support={sorted(bad_ids)}")


def _check_ain_and_policy(events: list[dict]) -> None:
    ain = _event_by_key(events, "hdec_risk_ain_dubai_dispute_defect_delay")
    if check("B: Ain Dubai event exists", bool(ain)):
        check("B: Ain Dubai type is dispute/quality",
              ain.get("event_type") in {"legal_dispute", "quality_defect"},
              str(ain.get("event_type")))
        check("B: Ain Dubai has overseas project impact",
              "해외 프로젝트" in (ain.get("impact_axes") or []),
              str(ain.get("impact_axes")))
        check("B: gateway-only event needs operator confirmation",
              ain.get("has_gateway_source") is True
              and ain.get("needs_operator_confirmation") is True,
              str({k: ain.get(k) for k in ("has_gateway_source", "needs_operator_confirmation")}))

    policy = _event_with_article(events, "policy_ai_dc")
    check("C: generic AI data center policy without concrete risk action has no risk event",
          policy is None, str(policy.get("event_key") if policy else "none"))


def _check_noise_and_supervision(sim: dict, events: list[dict]) -> None:
    stock = _event_with_article(events, "stock_noise")
    check("D: stock/noise has no risk event", stock is None,
          str(stock.get("event_key") if stock else "none"))
    for article_id in ("bad_sales", "bad_forum", "bad_gtxc", "bad_gtxa"):
        ev = _event_with_article(events, article_id)
        check(f"D: review counterexample {article_id} has no risk event",
              ev is None, str(ev.get("event_key") if ev else "none"))

    severe = _event_by_key(events, "construction_severe_accident_supervision")
    if check("E: 특별감독 severe safety event exists", bool(severe)):
        check("E: severe event type is safety_severe",
              severe.get("event_type") == "safety_severe",
              str(severe.get("event_type")))
        check("E: severe event review_required true",
              severe.get("review_required") is True)
    flags = ((sim.get("profiles") or {}).get("severe_supervision") or {}).get("top_exposure_flags") or []
    check("E: 특별감독 not treated as sports/coach",
          "sports_context" not in flags, str(flags))


def _check_direct_counterexample_keys() -> None:
    from app import risk_events

    by_id = {item["id"]: item for item in FIX}
    expected_none = ("bad_sales", "bad_forum", "bad_gtxc", "bad_gtxa")
    for article_id in expected_none:
        key = risk_events.event_key_for_row(by_id[article_id])
        check(f"direct key: {article_id} emits no event key", key is None, str(key))

    good_key = risk_events.event_key_for_row(by_id["gtx_1"])
    check("direct key: Samsung Station GTX fixture still maps to known event",
          good_key == "hdec_risk_gtx_samseong_rebar_penalty", str(good_key))


def main() -> int:
    print(f"== verify_risk_event_clustering @ {ROOT} ==")
    before = _db_state()
    _check_direct_counterexample_keys()
    sim = _run_pipeline_sim()
    events = (sim or {}).get("events") or []
    _check_event_contract(events)
    _check_gtx(events)
    _check_ain_and_policy(events)
    if sim:
        _check_noise_and_supervision(sim, events)
    after = _db_state()
    check("repo radar.db untouched", before == after, f"before={before} after={after}")
    if _failures:
        print("\nRESULT: FAIL")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
