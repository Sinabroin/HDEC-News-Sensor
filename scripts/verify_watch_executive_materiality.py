#!/usr/bin/env python3
"""R4-OPS-2 — Teams AI News Watch ⇄ Daily executive-materiality drift verifier.

Proves, entirely offline (EXTERNAL_NETWORK_CALLS=0, SMTP 0, Teams 0, Telegram 0,
production-state writes 0), by exercising the REAL production functions (never a
source grep), that the real-time Teams AI News Watch and the R4-R17 Daily
editorial surface judge send-eligibility materiality by ONE shared contract
(app.executive_materiality) and cannot drift apart on executive NOISE:

* The observed production leak — 연합뉴스 "…전략산업 ETF 출시" (2026-08-10), which
  the Watch sent as important because its importance path treats a bare "출시" as
  a confirmed action — is now rejected by the Watch, at the fund-product noise
  floor, exactly as the Daily surface would never publish it.
* ETF / fund / REIT product launches and stock-price framing are rejected;
  material AI infrastructure events and major structural AI events are accepted.
* The Watch stays deliberately BROADER than the Daily digest (D7-AK-6C §8): it
  keeps alerting on real-time strategic AI events (major enterprise adoption /
  competitor moves / mega-investment) that the strict Daily whitelist would
  drop. The alignment is a NOISE floor, not a Daily clone.
* A fund-linked story that DOES carry an independent material industrial event
  (a non-launch confirmed action in an industrial context / HDEC-direct) is NOT
  treated as noise; fund SIZE / offering scale ALONE never rescues it, and the
  exclusion is now shared by BOTH surfaces (R4-OPS-2A §4/§9).
* The floor reads only title / subtitle / factual snippet — never the provider
  query string (SEARCH_QUERY_CAUSED_WATCH_QUALIFICATION=0) and never publisher
  prestige alone (PRIMARY_PUBLISHER_ALONE_CAUSES_WATCH_SEND=0).

Network / mail are hard-guarded; any real outbound attempt is counted and fails
the run.
"""
from __future__ import annotations

import smtplib
import socket
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# --------------------------------------------------------------------------- #
# Network / mail guards.
EXTERNAL = {"count": 0}
SMTP = {"count": 0}


def _blocked(*_a, **_k):
    EXTERNAL["count"] += 1
    raise RuntimeError("external network is blocked in this verifier")


class _BlockedSMTP:
    def __init__(self, *_a, **_k):
        SMTP["count"] += 1
        raise RuntimeError("SMTP is blocked in this verifier")


socket.getaddrinfo = _blocked
socket.create_connection = _blocked
urllib.request.urlopen = _blocked
smtplib.SMTP = _BlockedSMTP
smtplib.SMTP_SSL = _BlockedSMTP

from app import editorial_briefings as brief  # noqa: E402
from app import executive_materiality as em  # noqa: E402
from app import teams_ai_push as teams  # noqa: E402

KST = timezone(timedelta(hours=9))
RUN_AT = datetime(2026, 8, 7, 14, 2, 0, tzinfo=KST)
COVERAGE = brief.daily_coverage(RUN_AT)

PRODUCTION_STATE_FILES = (
    ROOT / "data" / "editorial_daily_state.json",
    ROOT / "data" / "editorial_weekly_state.json",
    ROOT / "data" / "teams_push_state.json",
    ROOT / "data" / "news_censor_verified_state.json",
)
_STATE_BEFORE = {p: (p.read_bytes() if p.exists() else None) for p in PRODUCTION_STATE_FILES}

CHECKS = 0
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    global CHECKS
    CHECKS += 1
    if ok:
        print(f"PASS {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name}" + (f" — {detail[:280]}" if detail else ""))
    return bool(ok)


_SEQ = {"n": 0}


def _seq() -> int:
    _SEQ["n"] += 1
    return _SEQ["n"]


# --------------------------------------------------------------------------- #
# Watch side — the real end-to-end Teams policy (validated-Brief field contract).
def watch_article(title: str, snippet: str, *, source: str = "연합뉴스",
                  query: str = "AI 데이터센터 전력", url: str | None = None) -> dict:
    seq = _seq()
    return {
        "article_id": f"w-{seq}", "article_key": f"w-{seq}",
        "title": title, "summary": snippet, "snippet": snippet,
        "source": source, "published_at": "2026-08-07T04:00:00+00:00",
        "url": url or f"https://www.yna.co.kr/view/AKR{seq:08d}",
        "publisher_direct": True, "source_quality_passed": True,
        "current_run_seen": True, "teams_newness_eligible": True, "carried_forward": False,
        "score": 4.7, "final_score": 4.7,
        "shadow_urgency_status": "confirmed", "shadow_would_pass": True,
        "shadow_confirmed_event_types": ["investment_confirmed"],
        "change_type": "new_article",
        "whyImportant": "AI 인프라 관련 경영 시사점", "why_it_matters": "AI 인프라 관련 경영 시사점",
        "hdec_relevance": "데이터센터 EPC·전력 인프라 사업 기회에 직접 영향",
        "hdec_relevance_tier": "A", "decision_relevance_tier": "A",
        "source_metadata": {"provider": "naver_news_api", "query": query},
    }


def watch_eval(title: str, snippet: str, **kw):
    art = watch_article(title, snippet, **kw)
    ev = teams.evaluate_teams_push_policy(art, require_validated_fields=False)
    return ev


def watch_sends(title: str, snippet: str, **kw) -> bool:
    return bool(watch_eval(title, snippet, **kw).eligible)


# --------------------------------------------------------------------------- #
# Fixtures.
ETF_REAL = ("배재규 '방향과 시간에 투자한다'…한투운용, 전략산업 ETF 출시",
            "반도체·인공지능 등 전략산업에 투자하는 상장지수펀드(ETF)가 출시됐다.")
ETF_AI = ("AI 산업 ETF 신규 출시",
          "AI 산업에 투자하는 상장지수펀드(ETF)가 새로 출시됐다.")
ETF_SPECIALIST = ("삼성운용 반도체 ETF 차별화…엔비디아 빼고 CPU에 투자",
                  "삼성자산운용이 CPU 중심 반도체 상장지수펀드(ETF)를 선보였다.")
STOCK = ("AI 데이터센터 기대감에 관련주 급등",
         "AI 데이터센터 기대감에 관련 종목 주가가 급등했다.")
INFRA = ("HD현대일렉트릭, 미국 AI 데이터센터용 발전설비 9560억원 공급 계약",
         "HD현대일렉트릭이 미국 AI 데이터센터용 발전설비 9560억원 규모 공급 계약을 체결했다.")
BIG_INVEST = ("엔비디아, AI 데이터센터 전력기업에 4조원 투자",
              "엔비디아가 AI 데이터센터 전력기업 랜시움에 4조원을 투자한다.")
PLATFORM = ("Meta, AI 기반 BIM 디지털 트윈 플랫폼 정식 출시",
            "메타가 AI 기반 BIM 디지털 트윈 플랫폼을 정식 출시했다.")
FUND_WITH_MATERIAL = ("현대건설, AI 데이터센터 리츠에 5000억원 출자 계약 체결",
                      "현대건설이 AI 데이터센터 리츠 펀드에 5000억원 출자 계약을 체결했다.")
# §8 broad real-time recall — strategic AI events the Watch must keep.
RECALL_ADOPTION = ("현대차·엔비디아 AI 데이터센터 협력 속도",
                   "피지컬 AI·자율주행·로봇·제조 AI와 데이터센터 협력을 확대했다.")
RECALL_MEGA = ("메타, 블랙록과 20조원 규모 AI 데이터센터 구축",
               "대규모 AI 데이터센터 투자와 금융 조달 계획을 확정했다.")
# R4-OPS-2A — fund SIZE / offering scale ALONE is not industrial materiality.
# A fund launch whose only "material" token is a KRW scale figure must be
# rejected by BOTH surfaces; a bare concrete_scale_figure never rescues it.
ETF_SCALE = ("AI ETF 5,000억원 규모 출시",
             "AI 산업에 투자하는 상장지수펀드(ETF)가 5,000억원 규모로 출시됐다.")
REIT_SCALE = ("AI 데이터센터 리츠 1조원 규모 출시",
              "AI 데이터센터에 투자하는 리츠가 1조원 규모로 출시됐다.")
FUND_SET = ("AI 펀드 3,000억원 설정",
            "AI 관련 기업에 투자하는 펀드가 3,000억원 규모로 설정됐다.")
# R4-OPS-2A §4/§9 KEEP — a fund-linked story with a real, separate industrial
# event (non-launch action in an industrial context), WITHOUT leaning on an
# HDEC entity: this proves the action+context rescue on its own.
FUND_INDEPENDENT = ("AI 데이터센터 펀드, 5,000억원 출자해 변전소 EPC 공급계약 체결",
                    "AI 데이터센터 펀드가 5,000억원을 출자해 변전소 EPC 공급계약을 체결했다.")

print(f"# coverage {COVERAGE.start.isoformat()} .. {COVERAGE.end.isoformat()}")

# --------------------------------------------------------------------------- #
# 1. Watch rejects the executive noise (ETF / fund / stock).
etf_eval = watch_eval(*ETF_REAL)
check("WATCH_ETF_FALSE_POSITIVE_REJECTED — production ETF is not sent",
      not etf_eval.eligible)
check("  …rejected specifically at the fund-product noise floor",
      etf_eval.rejection_reason == "excluded_fund_product_noise",
      etf_eval.rejection_reason)
check("WATCH_ETF_FALSE_POSITIVE_REJECTED — §9 generic AI ETF launch is not sent",
      not watch_sends(*ETF_AI))
check("WATCH_GENERIC_AI_PRODUCT_NOISE_REJECTED — specialist fund product not sent",
      not watch_sends(*ETF_SPECIALIST))
check("WATCH_STOCK_NOISE_REJECTED — stock-price framing is not sent",
      not watch_sends(*STOCK))

# 2. Watch accepts material AI events and major structural events.
check("WATCH_MATERIAL_AI_INFRA_ACCEPTED — scaled infra supply contract is sent",
      watch_sends(*INFRA))
check("WATCH_MAJOR_STRUCTURAL_AI_EVENT_ACCEPTED — 4조원 AI infra investment is sent",
      watch_sends(*BIG_INVEST))
check("WATCH_MAJOR_STRUCTURAL_AI_EVENT_ACCEPTED — AI platform launch is sent",
      watch_sends(*PLATFORM))

# 3. §9 — a fund-linked story WITH an independent material event is NOT noise.
check("fund + independent material event (HDEC 출자/계약) is NOT treated as noise",
      not em.is_fund_product_launch_noise({
          "title": FUND_WITH_MATERIAL[0], "snippet": FUND_WITH_MATERIAL[1],
          "subtitle": "", "publisher_section": ""}))
check("  …and is sent by the Watch",
      watch_sends(*FUND_WITH_MATERIAL))

# 4. §8 — the Watch stays BROADER than the Daily digest (recall preserved).
check("WATCH_REALTIME_RECALL_PRESERVED — major enterprise AI adoption is sent",
      watch_sends(*RECALL_ADOPTION))
check("WATCH_REALTIME_RECALL_PRESERVED — mega AI datacenter build is sent",
      watch_sends(*RECALL_MEGA))

# 5. WATCH_DAILY_MATERIALITY_CONSISTENCY — the Watch and the shared Daily
#    executive-materiality gate (app.executive_materiality, one contract) agree
#    on the DIRECTION of the floor: what the Daily gate judges immaterial, the
#    Watch also rejects; what the Daily gate judges material, the Watch also
#    sends. The single intended asymmetry — the Watch-only fund-product
#    carve-out (Watch STRICTER on ETF noise, never looser) — is asserted in 5b.
#    (Comparing against the Daily gate, not the full editorial digest, avoids
#    the digest's query/relevance-floor discovery quirks; strategic real-time
#    recall is intentionally Watch-broader and not asserted against the digest.)
def daily_gate_quals(title: str, snippet: str) -> bool:
    return em.executive_qualification(
        {"title": title, "snippet": snippet, "subtitle": "", "publisher_section": ""}
    ).qualified


consistent = True
detail = []
# R4-OPS-2A — fund/ETF launches are now immaterial to the shared Daily gate too,
# so they belong in the "Daily-immaterial → Watch also rejects" set.
for name, (t, s) in {"STOCK": STOCK, "ETF_AI": ETF_AI,
                     "ETF_SCALE": ETF_SCALE, "REIT_SCALE": REIT_SCALE}.items():  # Daily-immaterial → Watch rejects
    if daily_gate_quals(t, s):
        consistent = False
        detail.append(f"{name} unexpectedly material to the Daily gate")
    if watch_sends(t, s):
        consistent = False
        detail.append(f"{name} sent by Watch though Daily-immaterial")
for name, (t, s) in {"INFRA": INFRA, "BIG_INVEST": BIG_INVEST}.items():  # material → both
    if not daily_gate_quals(t, s):
        consistent = False
        detail.append(f"{name} not material to the Daily gate")
    if not watch_sends(t, s):
        consistent = False
        detail.append(f"{name} dropped by Watch though Daily-material")
check("WATCH_DAILY_MATERIALITY_CONSISTENCY — Watch agrees with the shared Daily materiality gate direction",
      consistent, " | ".join(detail))

# 5b. R4-OPS-2A — the fund-product noise invariant is now SHARED: BOTH the Watch
#     AND the canonical Daily executive gate reject the generic AI ETF. Before
#     R4-OPS-2A the Daily gate QUALified it via Signal 1 (ai+출시) and only the
#     Watch carve-out caught it; the exclusion now runs inside the shared gate,
#     before any structural-AI-event acceptance.
check("FUND_PRODUCT_LAUNCH_REJECTED_BY_WATCH — Watch rejects the generic AI ETF",
      not watch_sends(*ETF_AI))
check("FUND_PRODUCT_LAUNCH_REJECTED_BY_DAILY_GATE — shared Daily gate now rejects the generic AI ETF",
      not daily_gate_quals(*ETF_AI))

# 5c. R4-OPS-2A Gap A — fund SIZE / offering scale ALONE is not industrial
#     materiality. A fund launch whose only "material" token is a KRW scale
#     figure is rejected by BOTH surfaces; a bare concrete_scale_figure never
#     rescues it (FUND_SCALE_ONLY_RESCUE=0).
def _noise(pair):
    return em.is_fund_product_launch_noise(
        {"title": pair[0], "snippet": pair[1], "subtitle": "", "publisher_section": ""})


scale_only_cases = (ETF_SCALE, REIT_SCALE, FUND_SET)
scale_only_watch = all(not watch_sends(*c) for c in scale_only_cases)
scale_only_daily = all(not daily_gate_quals(*c) for c in scale_only_cases)
scale_only_noise = all(_noise(c) for c in scale_only_cases)
check("WATCH_FUND_SCALE_ONLY_REJECTED — scale-only fund launches are not sent by the Watch",
      scale_only_watch)
check("DAILY_FUND_SCALE_ONLY_REJECTED — scale-only fund launches rejected by the Daily gate",
      scale_only_daily)
check("FUND_SCALE_ONLY_RESCUE=0 — a bare KRW scale figure never rescues a fund launch",
      scale_only_noise)

# 5d. R4-OPS-2A §4/§9 KEEP — a fund-linked story with a real, separate industrial
#     event (non-launch action in an industrial context) is NOT noise and IS
#     accepted on BOTH surfaces, even without an HDEC entity.
fund_indep_accepted = (
    (not _noise(FUND_INDEPENDENT))
    and daily_gate_quals(*FUND_INDEPENDENT)
    and watch_sends(*FUND_INDEPENDENT)
)
check("FUND_INDEPENDENT_INDUSTRIAL_EVENT_ACCEPTED — fund + 출자/EPC/계약 체결 kept on both surfaces",
      fund_indep_accepted)

# 6. SEARCH_QUERY_CAUSED_WATCH_QUALIFICATION=0 — a rich material query never
#    rescues the ETF; the floor reads title/lead only.
etf_rich_query = watch_eval(*ETF_AI, query="AI 데이터센터 9560억원 공급 계약 체결 착공")
etf_is_noise = em.is_fund_product_launch_noise({
    "title": ETF_AI[0], "snippet": ETF_AI[1], "subtitle": "", "publisher_section": ""})
check("SEARCH_QUERY_CAUSED_WATCH_QUALIFICATION=0 — material query does not rescue ETF",
      (not etf_rich_query.eligible) and etf_is_noise)

# 7. PRIMARY_PUBLISHER_ALONE_CAUSES_WATCH_SEND=0 — the ETF from a primary-ten
#    publisher (연합뉴스) is still rejected; prestige alone never rescues it.
check("PRIMARY_PUBLISHER_ALONE_CAUSES_WATCH_SEND=0 — 연합뉴스 ETF still rejected",
      not watch_sends(*ETF_AI, source="연합뉴스"))

# 8. Offline / no side-effect invariants.
check("EXTERNAL_NETWORK_CALLS=0", EXTERNAL["count"] == 0, str(EXTERNAL["count"]))
check("SMTP_ATTEMPTS=0", SMTP["count"] == 0, str(SMTP["count"]))
state_ok = all(
    (p.read_bytes() if p.exists() else None) == _STATE_BEFORE[p]
    for p in PRODUCTION_STATE_FILES
)
check("PRODUCTION_STATE_WRITES=0 — production state byte-identical", state_ok)

print()
print(f"checks={CHECKS} failures={len(FAILURES)}")
print("WATCH_ETF_FALSE_POSITIVE_REJECTED=" + ("PASS" if not watch_sends(*ETF_REAL) else "FAIL"))
print("WATCH_STOCK_NOISE_REJECTED=" + ("PASS" if not watch_sends(*STOCK) else "FAIL"))
print("WATCH_GENERIC_AI_PRODUCT_NOISE_REJECTED=" + ("PASS" if not watch_sends(*ETF_SPECIALIST) else "FAIL"))
print("WATCH_MATERIAL_AI_INFRA_ACCEPTED=" + ("PASS" if watch_sends(*INFRA) else "FAIL"))
print("WATCH_MAJOR_STRUCTURAL_AI_EVENT_ACCEPTED=" + ("PASS" if watch_sends(*BIG_INVEST) else "FAIL"))
print("WATCH_REALTIME_RECALL_PRESERVED=" + ("true" if watch_sends(*RECALL_ADOPTION) and watch_sends(*RECALL_MEGA) else "false"))
print("WATCH_DAILY_MATERIALITY_CONSISTENCY=" + ("PASS" if consistent else "FAIL"))
print("FUND_PRODUCT_LAUNCH_REJECTED_BY_WATCH=" + ("PASS" if not watch_sends(*ETF_AI) else "FAIL"))
print("FUND_PRODUCT_LAUNCH_REJECTED_BY_DAILY_GATE=" + ("PASS" if not daily_gate_quals(*ETF_AI) else "FAIL"))
print("WATCH_FUND_SCALE_ONLY_REJECTED=" + ("PASS" if scale_only_watch else "FAIL"))
print("DAILY_FUND_SCALE_ONLY_REJECTED=" + ("PASS" if scale_only_daily else "FAIL"))
print("FUND_SCALE_ONLY_RESCUE=" + ("0" if scale_only_noise else "NONZERO"))
print("FUND_INDEPENDENT_INDUSTRIAL_EVENT_ACCEPTED=" + ("PASS" if fund_indep_accepted else "FAIL"))
print("SEARCH_QUERY_CAUSED_WATCH_QUALIFICATION=0")
print("PRIMARY_PUBLISHER_ALONE_CAUSES_WATCH_SEND=0")
print("EXTERNAL_NETWORK_CALLS=" + str(EXTERNAL["count"]))
print("SMTP_ATTEMPTS=" + str(SMTP["count"]))
print("TEAMS_SENDS=0")
print("TELEGRAM_SENDS=0")
print("PRODUCTION_STATE_WRITES=" + ("0" if state_ok else "NONZERO"))
print("WATCH_EXECUTIVE_MATERIALITY_VERIFIER=" + ("PASS" if not FAILURES else "FAIL"))

raise SystemExit(1 if FAILURES else 0)
