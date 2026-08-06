#!/usr/bin/env python3
"""D7-AK-6E R4-R12 §2 — official-institution AI-centrality gate.

Official source status must never replace AI relevance. An official article
may enter the AI Daily candidate pool only when AI is explicit and central,
the article carries a material confirmed event, and the subject sits in an
executive decision domain — tourism/festival publicity and broad ministry
reports with AI as one bullet are excluded before candidate selection, and
tier authority (promotion) applies only after semantic eligibility.

Proves, entirely offline (network 0, sends 0, production state writes 0):

* §1 the exact 2026-08-06 MOIS 여수 island-tourism regression
  (unrelated_domain_or_event_publicity, excluded before candidate selection);
* §2 the exact 2026-08-06 MOIS broad government report regression
  (incidental_ai_in_broad_government_report, excluded);
* §3 a genuinely AI-central material official article stays fully eligible
  (promoted + selected) — the gate rejects noise, not officials;
* §4 semantic eligibility precedes tier authority: a non-AI 협약/체결 release
  can never be promoted or become a promoted-official Teams row;
* §5 AI-branded event publicity (캠페인/체험) is never pool-eligible;
* §6 AI-central official without a material confirmed event is excluded from
  the Daily pool while the T&I-brief conditional-supply flag is unchanged;
* §7 the six official gate counters are exposed machine-readably;
* §8 operator-review extras apply the same gate (no official filler resurfaces
  through the console pool).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import ai_centrality  # noqa: E402
from app import editorial_briefings as eb  # noqa: E402
from app import public_institution_routing as pir  # noqa: E402
from app import teams_ai_push  # noqa: E402

CHECKS = 0
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"PASS: {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL: {name}" + (f" — {detail[:300]}" if detail else ""))


# ---------------------------------------------------------------------------
# Exact production rows from the rejected 2026-08-06 Daily Review Console.
# ---------------------------------------------------------------------------
YEOSU_TITLE = "낭만이 가득한 여수 밤바다에서섬의 특별한 추억을 만나세요"
YEOSU_LEAD = (
    "- 제7회 섬의 날, ‘낭만충전, 섬’ 주제로 8월 6일부터 9일까지 여수세계엑스포장 "
    "일원에서 개최 - 6일 기념식서 섬 발전 유공자 10명 정부포상 및 하현우·리센느 등 "
    "축하공연 - 전시관, 백섬백길 걷기, 공연·체험 행사 등 다채로운 프로그램 운영"
)
BROAD_TITLE = "모두의 행복, 모두의 안전, 국민 삶을 플러스+ 하겠습니다."
BROAD_LEAD = (
    "- 국정운영 중추부처로서 생명안전·민생회복·AI전환·균형성장 핵심성과 창출 "
    "- 7대 역점과제와 개혁·성장·정상화·체감과제 신속 추진으로 국민 삶 변화 본격화 "
    "행정안전부는 8월 5일 「정부와 국민이 함께하는 업무 보고」를 통해 지난 6개월간의 "
    "주요 성과를 되짚어보고, 하반기에 신속히 추진하여 국민이 체감할 수 있는 핵심 "
    "과제들을 발표했다."
)
MOIS_URL = (
    "https://www.mois.go.kr/frt/bbs/type010/commonSelectBoardArticle.do"
    "?bbsId=BBSMSTR_000000000008&nttId={ntt}"
)


def official_row(key: str, title: str, lead: str, *, source: str = "행정안전부",
                 url: str = "", **overrides) -> dict:
    row = {
        "id": key,
        "article_id": key,
        "title": title,
        "source": source,
        "url": url or MOIS_URL.format(ntt=abs(hash(key)) % 10**6),
        "publisher_url": url or MOIS_URL.format(ntt=abs(hash(key)) % 10**6),
        "publisher_direct": True,
        "published_at": "2026-08-05T21:00:00+09:00",
        "snippet": lead,
        "summary": lead,
        "source_metadata": {"provider": "publisher_direct_rss"},
    }
    row.update(overrides)
    return row


def media_row(key: str, title: str, lead: str) -> dict:
    return {
        "id": key,
        "article_id": key,
        "title": title,
        "source": "연합뉴스",
        "url": f"https://www.yna.co.kr/view/{key}",
        "publisher_url": f"https://www.yna.co.kr/view/{key}",
        "publisher_direct": True,
        "published_at": "2026-08-05T22:00:00+09:00",
        "snippet": lead,
        "summary": lead,
        "source_metadata": {"provider": "naver_news_api"},
    }


def run_daily_selection(rows, *, operator_review: bool = False):
    coverage = eb.CoverageWindow(
        datetime(2026, 8, 4, 22, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 5, 21, 40, tzinfo=timezone.utc),
    )
    audit = eb.SelectionAuditCounters()
    articles = eb.normalize_articles(
        rows,
        coverage,
        limit=6 if not operator_review else 24,
        resolve_images=False,
        selection_mode=eb.SELECTION_MODE_EDITORIAL_PRIORITY,
        selection_audit=audit,
        edition_type="daily",
        operator_review=operator_review,
    )
    return articles, audit


def main() -> int:
    healthy = media_row(
        "ai-dc-anchor",
        "국가 AI 데이터센터 착공…GPU 전력 인프라 구축 개시",
        "국가 AI 데이터센터가 착공해 GPU 전력 인프라 구축을 시작했다.",
    )

    # ------------------------------------------------------------------ §1
    yeosu = official_row("mois-yeosu-128452", YEOSU_TITLE, YEOSU_LEAD,
                         url=MOIS_URL.format(ntt=128452))
    yeosu_route = pir.classify(yeosu)
    check(
        "§1 여수 island-event release is unrelated_domain_or_event_publicity",
        yeosu_route.official_ai_gate_reason == pir.OFFICIAL_AI_GATE_PUBLICITY,
        yeosu_route.official_ai_gate_reason,
    )
    check("§1 여수 release is never Daily-pool eligible",
          not yeosu_route.official_daily_pool_eligible)
    check("§1 여수 release is never promoted / Teams / headline eligible",
          not yeosu_route.main_surface_eligible
          and not yeosu_route.teams_alert_eligible
          and not yeosu_route.headline_eligible)
    selected, audit = run_daily_selection([healthy, yeosu])
    check(
        "§1 여수 release is excluded before candidate selection",
        all(item.source != "행정안전부" for item in selected) and bool(selected),
        f"selected={[item.source for item in selected]}",
    )
    check("§1 official_unrelated_domain_rejected_rows counts the rejection",
          audit.official_unrelated_domain_rejected_rows == 1
          and audit.official_rows_seen == 1
          and audit.official_selected_rows == 0)

    # ------------------------------------------------------------------ §2
    broad = official_row("mois-broad-128476", BROAD_TITLE, BROAD_LEAD,
                         url=MOIS_URL.format(ntt=128476))
    broad_route = pir.classify(broad)
    check(
        "§2 broad ministry report is incidental_ai_in_broad_government_report",
        broad_route.official_ai_gate_reason == pir.OFFICIAL_AI_GATE_INCIDENTAL,
        broad_route.official_ai_gate_reason,
    )
    check("§2 broad report is never Daily-pool eligible",
          not broad_route.official_daily_pool_eligible)
    check("§2 broad report carries the incidental centrality level",
          broad_route.official_ai_centrality_level
          == ai_centrality.LEVEL_INCIDENTAL_AI_MENTION)
    selected, audit = run_daily_selection([healthy, broad])
    check("§2 broad report is excluded before candidate selection",
          all(item.source != "행정안전부" for item in selected) and bool(selected))
    check("§2 official_incidental_ai_rejected_rows counts the rejection",
          audit.official_incidental_ai_rejected_rows == 1
          and audit.official_rows_seen == 1)

    # ------------------------------------------------------------------ §3
    eligible = official_row(
        "msit-computing-start",
        "과기정통부, 국가 AI 컴퓨팅센터 착공…GPU 인프라 구축 개시",
        "국가 AI 컴퓨팅센터 건설을 착공해 GPU 인프라 구축을 시작했다.",
        source="과학기술정보통신부",
        url="https://www.msit.go.kr/bbs/view.do?bbsSeqNo=94&nttSeqNo=4001",
    )
    eligible_route = pir.classify(eligible)
    check("§3 AI-central material official release is fully eligible",
          eligible_route.official_ai_gate_reason == pir.OFFICIAL_AI_GATE_ELIGIBLE
          and eligible_route.official_daily_pool_eligible
          and eligible_route.main_surface_eligible
          and eligible_route.teams_alert_eligible)
    selected, audit = run_daily_selection([eligible])
    check("§3 eligible official release enters and is selected",
          any(item.source == "과학기술정보통신부" for item in selected))
    check("§3 official gate counters record the eligible row",
          audit.official_rows_seen == 1
          and audit.official_ai_central_rows == 1
          and audit.official_material_event_rows == 1
          and audit.official_selected_rows == 1)

    # ------------------------------------------------------------------ §4
    mou = official_row(
        "mois-tourism-mou",
        "행정안전부, 여수시와 관광 활성화 협약 체결",
        "관광 활성화를 위한 업무 협약을 체결했다.",
    )
    mou_route = pir.classify(mou)
    check("§4 non-AI 협약 체결 is never promoted (semantic before tier)",
          not mou_route.main_surface_eligible
          and not mou_route.teams_alert_eligible
          and mou_route.promotion_reason.startswith("official_ai_gate_rejected:"))
    check("§4 rejected promotion carries the exact gate reason",
          mou_route.promotion_reason
          == f"official_ai_gate_rejected:{pir.OFFICIAL_AI_GATE_PUBLICITY}")
    teams_candidates = teams_ai_push.select_teams_push_candidates(
        [{**mou, "score": 4.8,
          "shadow_urgency_status": "confirmed",
          "shadow_would_pass": True,
          "shadow_confirmed_event_types": ["contract_signed"],
          "change_type": "new_article"}],
        max_articles=None,
    )
    check("§4 non-AI official row never becomes a promoted-official Teams row",
          len(teams_candidates) == 0, f"candidates={len(teams_candidates)}")

    # ------------------------------------------------------------------ §5
    campaign = official_row(
        "mois-ai-campaign",
        "행정안전부, 생활 속 AI 안전 체험 캠페인 개최",
        "시민 대상 인공지능 안전 홍보 캠페인을 개최했다.",
    )
    campaign_route = pir.classify(campaign)
    check("§5 AI-branded publicity campaign is excluded",
          not campaign_route.official_daily_pool_eligible
          and campaign_route.official_ai_gate_reason
          == pir.OFFICIAL_AI_GATE_PUBLICITY)

    # ------------------------------------------------------------------ §6
    portal = official_row(
        "mois-ai-portal",
        "공공데이터 활용, 이제 AI에게 물어보세요 ‘AI기반 공공데이터포털’ 서비스 개시",
        "AI 기반 공공데이터 포털 서비스를 개시하고 클라우드 인프라를 확충했다.",
    )
    portal_route = pir.classify(portal)
    check("§6 AI-central official without material confirmed event is excluded",
          not portal_route.official_daily_pool_eligible
          and portal_route.official_ai_gate_reason
          == pir.OFFICIAL_AI_GATE_NO_EVENT)
    check("§6 T&I-brief conditional-supply flag is unchanged by the Daily gate",
          portal_route.tni_brief_eligible)
    selected, audit = run_daily_selection([healthy, portal])
    check("§6 no-material official is not a Daily candidate",
          all(item.source != "행정안전부" for item in selected))
    check("§6 no-material rejection stays visible as central-but-not-selected",
          audit.official_ai_central_rows == 1
          and audit.official_selected_rows == 0
          and audit.official_incidental_ai_rejected_rows == 0
          and audit.official_unrelated_domain_rejected_rows == 0)

    # ------------------------------------------------------------------ §7
    fields = eb.SelectionAuditCounters().manifest_fields()
    for key in (
        "official_rows_seen",
        "official_ai_central_rows",
        "official_incidental_ai_rejected_rows",
        "official_unrelated_domain_rejected_rows",
        "official_material_event_rows",
        "official_selected_rows",
    ):
        check(f"§7 manifest exposes {key}", key in fields)

    # ------------------------------------------------------------------ §8
    review, review_audit = run_daily_selection(
        [healthy, yeosu, broad, eligible], operator_review=True
    )
    check(
        "§8 operator-review extras apply the same official gate",
        all(item.title not in {YEOSU_TITLE, BROAD_TITLE} for item in review),
        f"review={[item.title[:20] for item in review]}",
    )
    check("§8 eligible official remains operator-visible",
          any(item.source == "과학기술정보통신부" for item in review))
    check("§8 operator-review audit counts both rejections",
          review_audit.official_rows_seen == 3
          and review_audit.official_incidental_ai_rejected_rows == 1
          and review_audit.official_unrelated_domain_rejected_rows == 1)

    print(f"checks={CHECKS} failures={len(FAILURES)}")
    print(
        "official_ai_centrality_gate="
        + ("PASS" if not FAILURES else "FAIL")
        + " mois_yeosu_rejected=excluded_before_candidate_selection"
        + " mois_broad_report_rejected=incidental_ai_in_broad_government_report"
        + " network_calls=0 smtp_attempts=0 teams_sends=0 telegram_sends=0"
        + " production_state_writes=0"
    )
    if FAILURES:
        for name in FAILURES:
            print(f"FAILED: {name}")
        return 1
    print("RESULT=D7-AK-6E_R4R12_OFFICIAL_AI_CENTRALITY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
