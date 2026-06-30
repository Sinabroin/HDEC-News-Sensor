"""P0-B1/P0-B2 — Telegram mock daily digest 빌더.

P0-B2부터 다이제스트 데이터는 scripts/build_executive_brief.py(공유 briefing
레이어)에서 가져오고, 이 파일은 Telegram용 한국어 텍스트 조립만 담당한다.

- 네트워크 호출 0건, 비밀값 접근 0건 (Telegram 발송은 send_telegram.py 소관).
- 저장소의 radar.db는 절대 건드리지 않는다 — brief 빌더가 tmp 디렉터리의
  일회용 DB에 mock 파이프라인을 새로 돌린다.
- APP_MODE=mock 고정.

사용법:
    python3 scripts/build_telegram_digest.py            # 메시지 출력
    python3 scripts/build_telegram_digest.py --dry-run  # 메시지 + 요약(stderr)
    python3 scripts/build_telegram_digest.py --json     # 기계 검증용 JSON
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
for _path in (ROOT, SCRIPTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from build_executive_brief import build_brief_via_mock_pipeline  # noqa: E402
from app.executive_digest import (  # noqa: E402
    build_executive_digest,
    render_telegram,
)

HEADER = "HDEC Executive Radar"
# 메시지 길이 예산. send_telegram.py의 MAX_MESSAGE_LEN보다 항상 작거나 같아야 한다
# (verify_telegram_digest.py / verify_executive_brief.py가 이 관계를 검사한다).
MESSAGE_BUDGET = 3000
TITLE_MAX = 70
REASON_MAX = 90
DIGEST_THEMES_MAX = 5
DIGEST_CATEGORIES_MAX = 5
_KST = timezone(timedelta(hours=9))


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fmt_kst(iso) -> str:
    """ISO 타임스탬프(UTC/KST 무관)를 KST 벽시계 'YYYY-MM-DD HH:MM'로 표시한다.
    임원 알림에 +00:00 같은 raw offset을 노출하지 않기 위한 표시 전용 변환 (P0-D1.5)."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return str(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M")


def _display(text) -> str:
    out = "" if text is None else str(text)
    for old, new in (
        ("현대건설 직접 관련", "현대건설 연관"),
        ("현대건설 직접 영향", "현대건설 연관"),
        ("현대건설 직접", "현대건설 연관"),
        ("검토 필요", "중요 신호"),
        ("추적 필요", "계속 관찰"),
    ):
        out = out.replace(old, new)
    return out


def _html(text) -> str:
    return escape(_display(text), quote=True)


def _is_http(url) -> bool:
    return bool(url) and str(url).startswith(("http://", "https://"))


def _title_html(entry: dict, limit: int = TITLE_MAX) -> str:
    title = _clip(entry.get("title") or "", limit)
    url = entry.get("url") or ""
    if _is_http(url):
        return f'<a href="{escape(str(url), quote=True)}">{_html(title)}</a>'
    return _html(title)


def _join_budgeted(lines: list[str]) -> str:
    message = "\n".join(lines)
    if len(message) <= MESSAGE_BUDGET:
        return message
    kept = []
    for line in lines:
        candidate = "\n".join(kept + [line])
        if len(candidate) > MESSAGE_BUDGET - 2:
            break
        kept.append(line)
    if not kept:
        return "…"
    return "\n".join(kept + ["…"])


def _digest_signal(entry: dict) -> dict:
    """brief의 시그널 entry를 다이제스트 JSON 호환 형태로 변환한다."""
    return {
        "rank": entry["rank"],
        "article_id": entry["article_id"],
        "title": entry["title"],
        "source": entry["source"],
        "topic": entry.get("topic"),
        "category_label": entry.get("category_label"),
        "radar_section": entry.get("radar_section"),
        "final_score": entry.get("final_score"),
        "score_band": entry.get("score_band"),
        "alert_grade": entry.get("alert_grade"),
        "action_label": entry.get("action_label"),
        "confidence": entry.get("confidence"),
        "reason": entry.get("one_line_reason") or entry.get("implication") or "",
        "risk_priority_score": entry.get("risk_priority_score"),
        "risk_radar_label": entry.get("risk_radar_label"),
        "spread_label": (entry.get("spread") or {}).get("label", "단독 신호"),
        # P0-C1.13: 현대건설 직접 그룹핑·공급사 후순위·재무 라우팅용 분류 메타 (재계산 없음).
        "hdec_bucket": entry.get("hdec_bucket"),
        "executive_section": entry.get("executive_section"),
        "supplier_only": bool(entry.get("supplier_only")),
        "is_finance": bool(entry.get("is_finance")),
        # P0-C1.14: 수주·해외 블록 우선순위 클래스 (발주/EPC/해외 > 공급사 단독).
        "order_class": entry.get("order_class"),
        "url": entry.get("url"),
    }


# 같은 회사/공급사가 Top 3를 도배하지 않도록 중복 키 추출용 (P0-C1.12).
_ENTITY_NAMES = ["현대건설", "현대엔지니어링", "삼성물산", "gs건설", "sk에코플랜트",
                 "dl이앤씨", "대우건설", "포스코이앤씨", "롯데건설",
                 "가온전선", "ls전선", "대한전선", "대원전선"]


def _entity_key(entry: dict) -> str:
    """중복 도배 방지 키 — 같은 회사/공급사(예: 가온전선 2건)는 같은 키로 묶어 하나만 남긴다.

    회사명이 없으면 기사별 고유 키(article_id)를 써서 서로 다른 AI 기사가 같은 테마라는
    이유로 과도하게 합쳐지지 않게 한다 (테마는 같아도 별개 신호는 살린다)."""
    title = (entry.get("title") or "").lower()
    for name in _ENTITY_NAMES:
        if name.lower() in title:
            return f"co:{name}"
    return f"id:{entry.get('article_id')}"


def _dedup_key(entry: dict) -> str:
    """AI Top·수주·해외 도배 방지 키 (P0-C1.14). 공급사 단독은 회사가 달라도(가온전선·솔루엠
    등) 하나의 '공급사 클래스'로 묶어, 서로 다른 전선·버스덕트 기사 2건이 Top을 차지하지
    못하게 한다 (클래스 단위 dedup). 그 외는 회사/기사 단위 키(_entity_key)를 그대로 쓴다."""
    if entry.get("supplier_only"):
        return "class:supplier-only"
    return _entity_key(entry)


def _pick_diverse(entries: list, limit: int, seen: set, key=_entity_key) -> list:
    """점수순 후보에서 같은 주체(key)가 겹치지 않게 limit개를 고른다 (Telegram Top 다양성).

    key는 중복 판정 함수다 — 기본은 회사/기사 단위(_entity_key), 공급사 클래스 단위로
    묶으려면 _dedup_key를 넘긴다 (P0-C1.14 AI Top 공급사 도배 방지)."""
    out = []
    for e in entries:
        k = key(e)
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
        if len(out) == limit:
            break
    return out


# 현대건설 직접 영향 Telegram 그룹 (P0-C1.13) — hdec_bucket → 임원 메모 라벨 (1:1 매핑).
# 1 리스크 · 2 전략(수주·DC·SMR·뉴에너지) · 3 운영(AI·하도급·계약) · 4 기술·조직(R&D) ·
# 5 재무(자금조달) · 6 기타. 우선순위 순으로 최대 3줄만 노출한다 (장문 헤드라인 나열 방지).
_HDEC_THEME_ORDER = [(1, "리스크"), (2, "전략"), (3, "운영"),
                     (4, "기술·조직"), (5, "재무"), (6, "기타")]
HDEC_BULLET_MAX = 3        # 현대건설 직접 블록 최대 줄 수
HDEC_GROUP_TITLE_MAX = 46  # 그룹 줄 안 제목 클립 (메모 가독성)


def _hdec_grouped_bullets(signals: list) -> tuple[list, set]:
    """현대건설 직접 신호를 implication(버킷)별로 묶어 임원 메모형 bullet ≤3줄로 만든다.

    같은 그룹은 한 줄에 대표 제목 최대 2건을 ' / '로 잇는다 (다섯 줄 헤드라인 나열이 아니라
    리스크/전략/운영처럼 묶어 보여준다). 노출한 신호의 article_id 집합을 함께 돌려줘
    [리스크·규제]가 같은 헤드라인을 반복하지 않게 한다 (중복 방지)."""
    groups: dict = {}
    for s in signals:
        bucket = s.get("hdec_bucket") or 6
        groups.setdefault(bucket, []).append(s)
    bullets, shown_ids = [], set()
    for bucket, label in _HDEC_THEME_ORDER:
        items = groups.get(bucket)
        if not items:
            continue
        titles, used = [], []
        for s in items[:2]:                     # 그룹당 대표 최대 2건
            title = _title_html(s, HDEC_GROUP_TITLE_MAX)
            if title:
                titles.append(title)
                used.append(s.get("article_id"))
        if not titles:
            continue
        bullets.append(f"· {_html(label)}: " + " / ".join(titles))
        shown_ids.update(used)
        if len(bullets) >= HDEC_BULLET_MAX:
            break
    return bullets, shown_ids


def build_digest_data() -> dict:
    """공유 brief 레이어에서 다이제스트 구조체를 만든다 (P0-C1.12: 현대건설 직접 우선).

    상단 구성: 현대건설 직접 → AI 관련 → 수주·해외 → 리스크·규제. 같은 회사/주체가
    Top을 도배하지 않도록 다양성 dedup을 적용한다."""
    brief = build_brief_via_mock_pipeline()
    hdec = brief.get("hdec_direct_signals") or []
    ai = brief.get("ai_radar_signals") or []
    biz = brief.get("business_signals") or []
    risk = brief.get("risk_regulation_signals") or []
    top_immediate = brief.get("top_immediate_signals") or []

    # 현대건설 직접 — 그룹핑용으로 전체(briefing 캡 최대 5건)를 넘긴다. AI Top에서는 같은
    # 현대건설 주체가 다시 올라오지 않게 회사 키를 선점한다. 단 이 선점은 AI Top에만 쓴다 —
    # 수주·해외 블록까지 막으면 발주 신호가 통째로 사라지므로(P0-C1.14) 분리한다.
    # D3L visible single-use로 HDEC 직접 기사가 top_immediate에 먼저 배치될 수 있으므로,
    # Telegram 그룹핑 입력에는 즉시 후보의 HDEC 항목도 포함한다(리포트 surface 중복은 없음).
    hdec_candidates = list(hdec)
    seen_hdec_ids = {e.get("article_id") for e in hdec_candidates}
    for e in top_immediate:
        if (e.get("article_id") not in seen_hdec_ids
                and (e.get("executive_section") == "hdec_direct"
                     or (e.get("hdec_bucket") or 9) != 9)):
            hdec_candidates.append(e)
            seen_hdec_ids.add(e.get("article_id"))
    hdec_list = hdec_candidates[:5]
    ai_seen: set = {_entity_key(e) for e in hdec_list}
    # AI 관련 — 부품·전선 공급사 단독(가온전선 등)은 뒤로 정렬하고 클래스 단위로 묶어, 더 강한
    # AI/EPC/현대건설 신호가 있으면 공급사가 AI Top을 차지/도배하지 않게 한다 (P0-C1.13/14).
    ai_base = ai or brief.get("top_immediate_signals") or []
    ai_sorted = sorted(ai_base, key=lambda e: 1 if e.get("supplier_only") else 0)
    ai_top = _pick_diverse(ai_sorted, 3, ai_seen, key=_dedup_key)
    # 수주·해외 — 발주/EPC/DC/SMR·경쟁사 발주(0) > 해외·중동·재건 환경(1) > 현대건설 직접
    # 발주(2) > 공급사 단독(3) 순으로 정렬한다 (order_class). 회사 키 선점을 쓰지 않으므로
    # 현대건설 주체가 앞 섹션에 있어도 블록이 통째로 사라지지 않는다 — 같은 '기사' 중복만
    # format 단계에서 제외한다. 블록 내부에선 클래스 단위로 묶어 공급사/같은 회사 도배를 막는다.
    # 재무·자금조달 신호는 [현대건설 직접]/거시로 라우팅되므로 수주·해외에서 제외한다
    # (decision_relevance가 이미 발주 멤버십을 비우지만, 표시 단계에서도 한 번 더 막는다).
    biz_sorted = sorted(
        (e for e in biz if not e.get("is_finance")),
        key=lambda e: ((e.get("order_class") if e.get("order_class") is not None
                        else 9), -(e.get("final_score") or 0)))
    biz_candidates = _pick_diverse(biz_sorted, 4, set(), key=_dedup_key)
    # 리스크·규제 — 별도 풀(항상 노출). 최상위가 현대건설 직접에 이미 나오면 다음 리스크로
    # 대체할 수 있게 2건까지 확보한다 (헤드라인 중복 회피는 format 단계에서).
    risk_top = _pick_diverse(risk, 2, set())
    return {
        "header": HEADER,
        "mode": brief["mode"],
        "news_data_mode": brief.get("news_data_mode", "mock"),
        "macro_data_mode": brief.get("macro_data_mode", "unavailable"),
        "data_warning": brief.get("data_warning", ""),
        "date_kst": brief["date_kst"],
        "generated_at": brief["generated_at"],
        "executive_one_liner": brief["executive_one_liner"],
        "status_board": brief["status_board"],
        "hdec_signals": [_digest_signal(e) for e in hdec_list],
        "top_signals": [_digest_signal(e) for e in ai_top],
        "ai_first": bool(ai),
        "biz_signals": [_digest_signal(e) for e in biz_candidates],
        "risk_signals": [_digest_signal(e) for e in risk_top],
        "theme_rankings": brief["theme_rankings"][:DIGEST_THEMES_MAX],
        "category_counts": brief["category_counts"],
        "macro_snapshot": brief["macro_snapshot"],
        "operator_note": brief["operator_note"],
        "counts": brief["pipeline_counts"] or {},
        # 이메일/Teams '오늘의 신규 이슈' 섹션용 — 대시보드와 같은 top_new_issues 풀을
        # 그대로 전달한다(재계산·재선별 없음). Telegram 본문에는 노출하지 않는다.
        "top_new_issues": brief.get("top_new_issues") or [],
    }


def format_digest_message(data: dict) -> str:
    """Render the common conclusion-first executive brief for Telegram."""

    message = render_telegram(build_executive_digest(data))
    return _join_budgeted(message.splitlines())


def build_digest_message() -> str:
    """send_telegram.py가 사용하는 단일 진입점."""
    return format_digest_message(build_digest_data())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HDEC Executive Radar — Telegram mock daily digest 빌더 (발송 없음)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true",
                       help="메시지를 stdout에 출력하고 요약을 stderr에 남긴다")
    group.add_argument("--json", action="store_true",
                       help="기계 검증용 JSON을 출력한다")
    args = parser.parse_args(argv)

    data = build_digest_data()
    message = format_digest_message(data)

    if args.json:
        data["executive_digest"] = build_executive_digest(data).to_dict()
        data["message_chars"] = len(message)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    print(message)
    if args.dry_run:
        print(f"[dry-run] signals={len(data['top_signals'])} "
              f"chars={len(message)} budget={MESSAGE_BUDGET} (발송 없음)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
