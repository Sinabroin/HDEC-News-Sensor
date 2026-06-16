"""P0-C1.11 / P0-C1.12 — 라이브 기사 품질·의사결정 관련성 수동 점검 헬퍼 (읽기 전용).

라이브 brief를 마크다운 표로 떨어뜨려 운영자가 임원용 분류 품질을 눈으로 검토하게 한다.
P0-C1.12: 현대건설 직접 영향 / 수주·해외·발주 환경 후보 / 경쟁사·공급망 섹션과,
'제외됐지만 의사결정 관련성 높은 후보' 점검을 추가한다. '즉시 알림 후보'는 즉시 등급이
0건이면 상위 신호로 대체되므로 '상단 표시 후보'로 명시한다(추적 필요 행 오라벨 방지).
검증기(verify_*)와 달리 결정적 PASS/FAIL을 내지 않는다 — 운영자가 직접 보는 감사 표다.

안전 계약:
- 읽기 전용: 저장소 radar.db를 건드리지 않는다 (brief 빌더가 temp DB에 mock/live 파이프라인을
  새로 돌린다). DB 쓰기·commit·발송·네트워크 게시를 하지 않는다.
- --output(기본 임시 경로)에만 마크다운을 쓴다. 비밀값 접근 0건.
- dict/list/str가 섞여 와도 안전하게 셀 문자열로 변환한다 (표가 깨지지 않게).
- 라이브 네트워크는 운영자가 직접 NEWS_MODE=live로 돌릴 때만 쓴다 (mock 기본).
- 본문 전문을 싣지 않는다 — brief 파생 요약(제목/출처/점수/섹션)만 표기한다.

사용법:
    NEWS_MODE=live python3 scripts/audit_live_article_quality.py \
        --output /tmp/hdec_article_quality_audit.md
    python3 scripts/audit_live_article_quality.py            # mock (stdout)
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_executive_brief import build_brief_via_mock_pipeline  # noqa: E402


def _cell(value, limit: int = 80) -> str:
    """무엇이 와도(dict/list/str/None) 안전한 마크다운 셀 문자열로 변환한다."""
    if value is None:
        return "-"
    if isinstance(value, dict):
        value = value.get("label") or value.get("title") or ", ".join(
            f"{k}={v}" for k, v in list(value.items())[:3])
    elif isinstance(value, (list, tuple)):
        value = " / ".join(_cell(v, limit) for v in value[:3]) or "-"
    text = str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text or "-"


def _signal_rows(signals, risk: bool = False) -> list[str]:
    out = ["| # | 등급 | 중요도 | 출처 | 섹션 | 제목 |",
           "|---|---|---|---|---|---|"]
    if not signals:
        out.append("| _(없음)_ |  |  |  |  |  |")
        return out
    for s in signals:
        if not isinstance(s, dict):
            out.append(f"| ? |  |  |  |  | {_cell(s)} |")
            continue
        score = s.get("risk_priority_score") if risk else s.get("final_score")
        score_txt = "-" if score is None else f"{float(score):.1f}"
        src = s.get("display_source") or s.get("source")
        out.append(
            f"| {_cell(s.get('rank'), 4)} | {_cell(s.get('action_label') or s.get('alert_grade'), 14)} "
            f"| {score_txt} | {_cell(src, 24)} "
            f"| {_cell(s.get('radar_label') or s.get('radar_section'), 14)} "
            f"| {_cell(s.get('title'))} |")
    return out


def _suspicious_section(brief: dict) -> list[str]:
    """의심 행 — stock-hype/집계호스트/저품질-상위/현대건설-제외/리스크 오분류를 모은다.

    brief 빌더가 돌린 temp DB(라이브 수집분)를 그대로 읽어 전 기사 기준으로 점검한다
    (읽기 전용). 분류 로직은 순수 함수(article_quality/radar/source_quality)를 그대로 쓴다.
    """
    lines = ["", "## 의심 행 (수동 점검 대상)", ""]
    try:
        from app import (article_quality, db, decision_relevance,  # noqa: F401
                         insight, radar, source_quality)
        rows = db.fetch_articles_with_scores()
    except Exception as exc:  # noqa: BLE001 — 헬퍼는 어떤 경우에도 죽지 않는다
        lines.append(f"_전 기사 점검 생략 (DB 접근 불가: {_cell(exc)})_")
        return lines
    # 의사결정 관련성은 raw 제목/출처/스니펫으로만 판정한다 (생성된 사유/카테고리 라벨에
    # '현대건설'이 들어가 self-fulfilling 오탐을 내는 것을 막는다 — P0-C1.12 미션 주의).
    high_tiers = {decision_relevance.TIER_A, decision_relevance.TIER_A_MINUS,
                  decision_relevance.TIER_B_PLUS}

    # 카테고리/섹션 파생 (brief와 동일 로직 — 점수 재계산 없음)
    def _category(rid):
        try:
            d = db.fetch_article_detail(rid)
            impl = (((d or {}).get("insight")) or {}).get("hdec_implication") or ""
            inv = {t: k for k, t in insight.IMPLICATION_TEMPLATES.items()}
            return inv.get(impl.strip(), "general")
        except Exception:  # noqa: BLE001
            return "general"

    risk_kw = getattr(radar, "RISK_ACTION_STRONG", [])
    stockhype, aggregator, hdec_excluded = [], [], []
    risk_kw_not_risk, risk_no_action, decision_excluded = [], [], []
    ai_finance_misroute = []
    for r in rows:
        title = r.get("title") or ""
        source = r.get("source") or ""
        grade = r.get("alert_grade")
        aq = article_quality.assess(source, title)
        dr = decision_relevance.classify(r, _category(r["id"]))
        # 재무 하드 오버라이드(P0-C1.14)를 brief와 동일하게 적용 — raw가 재무 신호면 AI→거시.
        section = decision_relevance.override_radar_section(
            radar.classify_section(r, _category(r["id"])), dr)
        disp = source_quality.normalize_display_source(source)
        if aq["stock_hype"]:
            stockhype.append((title, source, grade, section))
        if disp != source:
            aggregator.append((title, source, disp, grade))
        if aq["hdec_direct"] and grade == "제외":
            hdec_excluded.append((title, source, grade))
        has_risk_kw = any(kw in title for kw in risk_kw)
        if has_risk_kw and section != radar.RISK:
            risk_kw_not_risk.append((title, section, grade))
        if section == radar.RISK and not has_risk_kw and not aq["hdec_enforcement"]:
            risk_no_action.append((title, grade))
        # 의사결정 관련성이 높은데(B+ 이상) 제외 등급이면 surface 후보 — 운영자 점검 대상.
        if grade == "제외" and dr["decision_relevance_tier"] in high_tiers:
            decision_excluded.append(
                (title, source, dr["decision_relevance_tier"],
                 dr["executive_label"]))
        # 재무 하드 오버라이드 검증 (P0-C1.14) — 오버라이드 후에도 재무·자금조달 신호가 AI
        # 섹션에 남았는지 본다. decision_relevance가 finance를 AI에서 빼므로 정상 동작 시 0건
        # (감사가 '알려진 미해결 오분류'를 받아들이지 않고 통과 섹션으로 둔다 — P0-C1.14).
        if section == radar.AI and dr.get("is_finance"):
            ai_finance_misroute.append(
                (title, source, dr["primary_executive_section"]))

    def _block(title, items, header, fmt):
        out = [f"### {title} ({len(items)}건)", ""]
        if not items:
            out.append("_해당 없음_")
            return out + [""]
        out.append(header)
        out.append("|" + "---|" * (header.count("|") - 1))
        out += [fmt(it) for it in items[:25]]
        if len(items) > 25:
            out.append(f"_…외 {len(items) - 25}건_")
        return out + [""]

    lines += _block(
        "stock-hype / 증권 리서치성 (핵심 섹션 제외 대상)", stockhype,
        "| 제목 | 출처 | 등급 | 섹션 |",
        lambda it: f"| {_cell(it[0])} | {_cell(it[1], 24)} | {_cell(it[2], 12)} | {_cell(it[3], 12)} |")
    lines += _block(
        "집계 호스트 출처 (표시 정규화 → 경유)", aggregator,
        "| 제목 | raw 출처 | 표시 출처 | 등급 |",
        lambda it: f"| {_cell(it[0])} | {_cell(it[1], 22)} | {_cell(it[2], 14)} | {_cell(it[3], 12)} |")
    lines += _block(
        "현대건설 직접 언급인데 제외됨 (승격 후보 점검)", hdec_excluded,
        "| 제목 | 출처 | 등급 |",
        lambda it: f"| {_cell(it[0])} | {_cell(it[1], 24)} | {_cell(it[2], 12)} |")
    lines += _block(
        "리스크 키워드 있으나 리스크·규제 분류 아님", risk_kw_not_risk,
        "| 제목 | 섹션 | 등급 |",
        lambda it: f"| {_cell(it[0])} | {_cell(it[1], 14)} | {_cell(it[2], 12)} |")
    lines += _block(
        "리스크·규제 분류인데 risk-action 키워드 없음", risk_no_action,
        "| 제목 | 등급 |",
        lambda it: f"| {_cell(it[0])} | {_cell(it[1], 12)} |")
    lines += _block(
        "제외됐지만 의사결정 관련성 높은 후보 (B+ 이상 — surface 점검)", decision_excluded,
        "| 제목 | 출처 | 의사결정 티어 | 섹션 |",
        lambda it: f"| {_cell(it[0])} | {_cell(it[1], 22)} | {_cell(it[2], 8)} "
                   f"| {_cell(it[3], 14)} |")
    lines += _block(
        "재무·자금조달인데 AI 섹션 잔류 (재무 하드 오버라이드 검증 · 정상=0건)",
        ai_finance_misroute,
        "| 제목 | 출처 | 의사결정 섹션 |",
        lambda it: f"| {_cell(it[0])} | {_cell(it[1], 22)} | {_cell(it[2], 16)} |")
    return lines


def build_markdown(brief: dict) -> str:
    counts = {k: brief.get(k) for k in (
        "total_articles", "total_signals", "immediate_count", "daily_count",
        "weekly_count", "excluded_count")}
    lines = [
        f"# HDEC 라이브 기사 품질 감사 — {_cell(brief.get('date_kst'))}",
        "",
        f"- 뉴스 모드: **{_cell(brief.get('news_data_mode'))}** "
        f"(fallback={_cell(brief.get('news_fallback_used'))})",
        f"- 생성: {_cell(brief.get('generated_at'))}",
        f"- 카운트: 수집·분석 {counts['total_articles']} · 신호 {counts['total_signals']} · "
        f"즉시 {counts['immediate_count']} · 검토 {counts['daily_count']} · "
        f"추적 {counts['weekly_count']} · 참고/제외 {counts['excluded_count']}",
        "",
        "> 운영자 수동 점검용 자동 표입니다. 라이브 Google News RSS는 주기적 쿼리/출처 "
        "튜닝이 계속 필요합니다 (이 표는 완벽을 주장하지 않습니다).",
        "",
        "## 현대건설 직접 영향",
        *_signal_rows(brief.get("hdec_direct_signals")),
        "",
        "## AI 관련",
        *_signal_rows(brief.get("ai_radar_signals")),
        "",
        "## 수주·해외·발주 환경 후보",
        *_signal_rows(brief.get("business_signals")),
        "",
        "## 리스크·규제 (리스크 우선도순)",
        *_signal_rows(brief.get("risk_regulation_signals"), risk=True),
        "",
        "## 경쟁사·공급망",
        *_signal_rows(brief.get("competitor_supply_signals")),
        "",
        "## 거시경제",
        *_signal_rows(brief.get("macro_economy_signals")),
        "",
        "## 신규 이슈 Top",
        *_signal_rows(brief.get("top_new_issues")),
        "",
        # P0-C1.12: 'top_immediate_signals'는 즉시 등급이 0건이면 상위 신호로 대체되므로
        # '즉시 알림 후보'로 라벨하면 추적 필요 행을 오라벨한다 → '상단 표시 후보'로 명시한다.
        "## 상단 표시 후보 (운영자 점검 · 즉시 등급만 '즉시 확인')",
        *_signal_rows(brief.get("top_immediate_signals")),
        "",
        "## 카테고리별 분포",
        "| 카테고리 | 총건수 | 즉시 |",
        "|---|---|---|",
    ]
    for sec in (brief.get("category_sections") or []):
        if not isinstance(sec, dict):
            continue
        lines.append(f"| {_cell(sec.get('category_label'), 28)} "
                     f"| {_cell(sec.get('total_count'), 6)} "
                     f"| {_cell(sec.get('instant_count'), 6)} |")
    lines += _suspicious_section(brief)
    lines += ["", "_끝._"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HDEC 라이브 기사 품질 감사 헬퍼 (읽기 전용, 발송 없음)")
    parser.add_argument("--output", metavar="PATH",
                        help="마크다운을 PATH에 쓴다 (생략 시 임시 파일 + stdout)")
    args = parser.parse_args(argv)

    brief = build_brief_via_mock_pipeline()  # NEWS_MODE에 따라 mock/live 수집 (temp DB)
    md = build_markdown(brief)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"audit written: {out} ({len(md)} chars) "
              f"news_data_mode={brief.get('news_data_mode')}")
    else:
        tmp = Path(tempfile.gettempdir()) / "hdec_article_quality_audit.md"
        tmp.write_text(md, encoding="utf-8")
        print(md)
        print(f"\n[audit written: {tmp}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
