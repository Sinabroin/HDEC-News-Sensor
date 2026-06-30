"""Channel-neutral executive digest model and renderers.

The module owns the short briefing language shared by Telegram, email, and
Teams channel email.  It does not read environment variables, credentials, or
the network.  Callers provide the already-selected digest data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from html import escape
from urllib.parse import urlparse

# 공개 GitHub Pages 산출물 URL (비밀값 아님 — repo `vars`로 노출되는 공개 주소).
# 운영자가 REPORT_URL/DASHBOARD_URL env로 재정의하지 않으면 이메일/Teams 본문 CTA는
# 이 정식 공개 주소를 가리킨다. 이 모듈은 env를 읽지 않으므로(채널 중립·env-free),
# 호출자(send_email_alert.py)가 기존 send_telegram 계약으로 해석한 URL을 주입하고,
# 미설정이면 아래 fallback이 적용된다 (D7-AD-K).
DEFAULT_DASHBOARD_URL = (
    "https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor/daily/dashboard-latest.html"
)
DEFAULT_REPORT_URL = (
    "https://guides.playground-aidesignlab.co.kr/HDEC-News-Sensor/daily/latest.html"
)


@dataclass(frozen=True)
class ExecutiveLink:
    label: str
    title: str
    url: str


@dataclass(frozen=True)
class ExecutiveDigest:
    headline: str
    situation: str
    hdec_angle: str
    watch: str
    links: tuple[ExecutiveLink, ...]
    subject_topic: str
    date_kst: str
    brief_time: str
    news_data_mode: str
    dashboard_url: str
    report_url: str
    new_issues: tuple[ExecutiveLink, ...]

    def to_dict(self) -> dict:
        return asdict(self)


_SIGNAL_GROUPS = (
    ("hdec_signals", "현대건설 연관"),
    ("top_signals", "AI 관련"),
    ("risk_signals", "리스크·규제"),
    ("biz_signals", "수주·해외"),
)


def _signals(data: dict) -> list[dict]:
    out: list[dict] = []
    for key, _label in _SIGNAL_GROUPS:
        out.extend(s for s in (data.get(key) or []) if isinstance(s, dict))
    return out


def _signal_text(signals: list[dict]) -> str:
    return " ".join(
        f"{s.get('title') or ''} {s.get('category_label') or ''} "
        f"{s.get('topic') or ''} {s.get('reason') or ''}"
        for s in signals
    ).lower()


def _has_http_url(value: object) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _clip(text: object, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _pick_links(data: dict, limit: int = 3) -> tuple[ExecutiveLink, ...]:
    """Choose 1-3 evidence links without repeating an article or title."""

    picked: list[ExecutiveLink] = []
    seen: set[str] = set()
    remaining: list[tuple[str, dict]] = []
    for key, label in _SIGNAL_GROUPS:
        candidates = [signal for signal in (data.get(key) or []) if isinstance(signal, dict)]
        for index, signal in enumerate(candidates):
            url = str(signal.get("url") or "").strip()
            title = _clip(signal.get("title"), 78)
            dedup_key = str(signal.get("article_id") or url or title)
            if not title or not _has_http_url(url) or dedup_key in seen:
                continue
            if index > 0:
                remaining.append((label, signal))
                continue
            seen.add(dedup_key)
            picked.append(ExecutiveLink(label=label, title=title, url=url))
            if len(picked) >= limit:
                return tuple(picked)

    for label, signal in remaining:
        url = str(signal.get("url") or "").strip()
        title = _clip(signal.get("title"), 78)
        dedup_key = str(signal.get("article_id") or url or title)
        if not title or not _has_http_url(url) or dedup_key in seen:
            continue
        seen.add(dedup_key)
        picked.append(ExecutiveLink(label=label, title=title, url=url))
        if len(picked) >= limit:
            break
    return tuple(picked)


# radar_section → 신규 이슈 분류 태그 (category_label이 없을 때만 사용하는 fallback).
_SECTION_TAG = {
    "ai": "AI", "hdec_direct": "현대건설", "risk_regulation": "리스크",
    "business": "수주", "order_environment": "수주", "macro": "거시",
    "competitor": "경쟁", "supply_chain": "공급망",
}


def _issue_tag(item: dict) -> str:
    """신규 이슈의 짧은 분류 라벨 — 파이프라인의 category_label에서 파생한다(가짜 분류 생성 금지)."""
    label = " ".join(str(item.get("category_label") or "").split())
    if label:
        head = label.split("·")[0].split("/")[0].strip()
        return _clip(head or label, 12)
    return _SECTION_TAG.get(str(item.get("radar_section") or ""), "신규")


def _pick_new_issues(data: dict, limit: int = 5) -> tuple[ExecutiveLink, ...]:
    """오늘의 신규 이슈 — brief의 top_new_issues를 [분류] 제목 형태로 최대 5건.

    데이터를 새로 만들지 않고 기존 selected new-issue 풀(top_new_issues)만 쓴다."""
    out: list[ExecutiveLink] = []
    seen: set[str] = set()
    for item in (data.get("top_new_issues") or []):
        if not isinstance(item, dict):
            continue
        title = _clip(item.get("title"), 80)
        if not title:
            continue
        key = str(item.get("article_id") or item.get("url") or title)
        if key in seen:
            continue
        seen.add(key)
        url = str(item.get("url") or "").strip()
        out.append(ExecutiveLink(
            label=_issue_tag(item),
            title=title,
            url=url if _has_http_url(url) else "",
        ))
        if len(out) >= limit:
            break
    return tuple(out)


def build_executive_digest(
    data: dict,
    brief_time: str = "07:00",
    dashboard_url: str = "",
    report_url: str = "",
) -> ExecutiveDigest:
    """Convert selected brief signals into a four-sentence, conclusion-first brief.

    dashboard_url/report_url은 호출자가 기존 REPORT_URL/DASHBOARD_URL 계약으로 해석해
    주입한다. 비거나 http(s)가 아니면 공개 fallback 상수를 쓴다 (이 모듈은 env를 읽지
    않는다)."""

    all_signals = _signals(data)
    text = _signal_text(all_signals)
    has_hdec = bool(data.get("hdec_signals"))
    has_ai = any(
        token in text
        for token in ("ai ", "ai·", "ai데이터", "인공지능", "데이터센터", "전력 인프라", "smr")
    )
    has_overseas = bool(data.get("biz_signals")) or any(
        token in text for token in ("해외", "중동", "수주", "발주", "epc", "환율", "유가")
    )
    has_risk = bool(data.get("risk_signals")) or any(
        token in text for token in ("안전", "중대재해", "규제", "벌점", "제재", "입찰제한")
    )

    if has_ai and has_risk:
        headline = "AI 인프라 수주 기회와 안전·규제 리스크를 함께 봐야 합니다."
        subject_topic = "AI 인프라·안전 규제"
    elif has_ai:
        headline = "AI 데이터센터·전력 인프라 수주 신호가 강해졌습니다."
        subject_topic = "AI 데이터센터·전력"
    elif has_risk:
        headline = "안전·규제 리스크를 우선 점검해야 합니다."
        subject_topic = "안전·규제 리스크"
    elif has_hdec:
        headline = "현대건설 직접 영향 신호를 우선 확인해야 합니다."
        subject_topic = "현대건설 주요 신호"
    else:
        fallback = _clip(data.get("executive_one_liner") or "주요 사업 신호를 확인해야 합니다.", 72)
        headline = fallback.rstrip(" .。") + ("입니다." if not fallback.endswith(("다", "요")) else ".")
        subject_topic = "핵심 이슈"

    if has_ai and has_overseas:
        situation = (
            "데이터센터 전력 확보와 EPC 발주 확대가 이어지는 가운데, "
            "해외 사업은 유가·환율·조달 변수가 함께 움직이고 있습니다."
        )
    elif has_ai:
        situation = "데이터센터 전력 확보 경쟁이 전력·냉각·EPC 발주 확대로 이어지고 있습니다."
    elif has_overseas:
        situation = "해외 발주 환경에서 유가·환율·조달 조건이 동시에 변하고 있습니다."
    elif has_risk:
        situation = "현장 안전과 입찰·규제 조건에 영향을 줄 신호가 부각되고 있습니다."
    else:
        situation = "상위 신호의 사업 영향과 근거를 함께 확인할 시점입니다."

    if has_hdec and has_risk:
        hdec_angle = (
            "현대건설 관점에서는 관련 수주 가능성과 입찰 자격·현장 안전 리스크를 "
            "동시에 점검할 사안입니다."
        )
    elif has_hdec:
        hdec_angle = "현대건설 관점에서는 직접 수주 가능성과 실행 조건을 우선 확인할 사안입니다."
    elif has_ai:
        hdec_angle = (
            "현대건설 관점에서는 단순 IT 동향이 아니라 데이터센터 EPC와 "
            "전력 인프라 수주 환경 변화로 봐야 합니다."
        )
    elif has_overseas:
        hdec_angle = "현대건설 관점에서는 수주 조건과 원가·공기 영향을 함께 볼 사안입니다."
    else:
        hdec_angle = "현대건설 관점에서는 사업 영향과 대응 주체를 먼저 정리해야 합니다."

    watch_items: list[str] = []
    if has_ai:
        watch_items.append("데이터센터 투자·전력 병목")
    if has_overseas:
        watch_items.append("해외 발주 조건·원가")
    if has_risk:
        watch_items.append("안전 규제 변경")
    if not watch_items:
        watch_items.append("후속 발표와 사업 영향")
    watch = f"오늘은 {'、'.join(watch_items[:3])}을 우선 확인하면 됩니다.".replace("、", ", ")

    resolved_dashboard = dashboard_url if _has_http_url(dashboard_url) else DEFAULT_DASHBOARD_URL
    resolved_report = report_url if _has_http_url(report_url) else DEFAULT_REPORT_URL

    return ExecutiveDigest(
        headline=headline,
        situation=situation,
        hdec_angle=hdec_angle,
        watch=watch,
        links=_pick_links(data),
        subject_topic=subject_topic,
        date_kst=str(data.get("date_kst") or ""),
        brief_time=brief_time,
        news_data_mode=str(data.get("news_data_mode") or "mock"),
        dashboard_url=resolved_dashboard,
        report_url=resolved_report,
        new_issues=_pick_new_issues(data),
    )


def render_subject(digest: ExecutiveDigest) -> str:
    return f"[HDEC News Sensor] 오늘 {digest.brief_time} 브리프 — {digest.subject_topic}"


def _data_notice(digest: ExecutiveDigest) -> str:
    return "" if digest.news_data_mode == "live" else "※ 뉴스 mock 데이터 기반"


def render_telegram(digest: ExecutiveDigest) -> str:
    """Render the four briefing sentences plus at most three evidence links."""

    lines = [
        "HDEC Executive Radar",
        f"<b>[오늘 {escape(digest.brief_time)} 브리프]</b>",
        "",
        escape(digest.headline),
        "",
        escape(digest.situation),
        escape(digest.hdec_angle),
        "",
        escape(digest.watch),
    ]
    notice = _data_notice(digest)
    if notice:
        lines += ["", escape(notice)]
    if digest.links:
        lines += ["", "<b>핵심 링크</b>"]
        for link in digest.links:
            lines.append(
                f'· [{escape(link.label)}] '
                f'<a href="{escape(link.url, quote=True)}">{escape(link.title)}</a>'
            )
    lines += ["", "요약은 '대시보드 보기', 전체 근거·거시경제는 '상세 리포트 보기'에서 확인"]
    return "\n".join(lines)


def render_email_text(digest: ExecutiveDigest) -> str:
    """일반 이메일과 Teams 채널 이메일이 공유하는 plain text 본문.

    Teams는 HTML 버튼이 깨질 수 있으므로 plain text에도 '바로 보기' URL을 반드시 둔다
    (버튼 없이 URL fallback만으로 대시보드/리포트 접근 가능)."""

    lines = [
        f"[오늘 {digest.brief_time} 브리프]",
        "",
        digest.headline,
        "",
        "상황:",
        digest.situation,
        "",
        "현대건설 관점:",
        digest.hdec_angle,
        "",
        "오늘 확인:",
        digest.watch,
        "",
        "바로 보기:",
        f"- 요약 대시보드: {digest.dashboard_url}",
        f"- 전체 리포트: {digest.report_url}",
    ]
    new_urls = {issue.url for issue in digest.new_issues if issue.url}
    if digest.new_issues:
        lines += ["", "오늘의 신규 이슈"]
        for index, issue in enumerate(digest.new_issues, start=1):
            lines.append(f"{index}. [{issue.label}] {issue.title}")
            if issue.url:
                lines.append(f"   {issue.url}")
    links = [link for link in digest.links if link.url not in new_urls]
    if links:
        lines += ["", "핵심 링크:"]
        for index, link in enumerate(links, start=1):
            lines.append(f"{index}. [{link.label}] {link.title}")
            lines.append(f"   {link.url}")
    notice = _data_notice(digest)
    if notice:
        lines += ["", notice]
    return "\n".join(lines)


def _email_section(label: str, body: str) -> str:
    return (
        f'<p style="margin:18px 0 4px;color:#667085;font-size:12px;'
        f'font-weight:bold;letter-spacing:.02em">{escape(label)}</p>'
        f'<p style="margin:0;font-size:15px">{escape(body)}</p>'
    )


def _cta_button(url: str, label: str, primary: bool) -> str:
    """버튼 한 개. Gmail/Outlook에서 살아남는 inline style만 쓴다(테이블·외부 CSS 없음)."""
    background = "#1d4ed8" if primary else "#0f766e"
    return (
        f'<a href="{escape(url, quote=True)}" '
        'style="display:inline-block;margin:0 10px 10px 0;padding:12px 22px;'
        f'background:{background};color:#ffffff;text-decoration:none;'
        'border-radius:8px;font-size:14px;font-weight:bold">'
        f"{escape(label)}</a>"
    )


def render_email_html(digest: ExecutiveDigest) -> str:
    """임원용 단순 inline-style HTML. 외부 JS/CSS/이미지/첨부 없음.

    버튼이 깨져도 하단 plain URL fallback으로 대시보드/리포트에 접근할 수 있다."""

    headline_card = (
        '<div style="margin:0 0 4px;padding:16px 18px;background:#f1f5ff;'
        'border-left:4px solid #1d4ed8;border-radius:6px">'
        '<p style="margin:0;font-size:16px;font-weight:bold;color:#0b1f4d">'
        f"{escape(digest.headline)}</p></div>"
    )
    body_sections = (
        _email_section("현재 상황", digest.situation)
        + _email_section("현대건설 관점", digest.hdec_angle)
        + _email_section("오늘 확인할 항목", digest.watch)
    )
    buttons = (
        '<div style="margin:22px 0 8px">'
        + _cta_button(digest.dashboard_url, "요약 대시보드 보기", primary=True)
        + _cta_button(digest.report_url, "전체 리포트 보기", primary=False)
        + "</div>"
    )
    fallback = (
        '<p style="margin:0 0 2px;color:#667085;font-size:12px">'
        "버튼이 보이지 않으면 아래 주소를 직접 여세요.</p>"
        '<p style="margin:0;font-size:12px;color:#475467">요약 대시보드: '
        f'<a href="{escape(digest.dashboard_url, quote=True)}">{escape(digest.dashboard_url)}</a></p>'
        '<p style="margin:0 0 4px;font-size:12px;color:#475467">전체 리포트: '
        f'<a href="{escape(digest.report_url, quote=True)}">{escape(digest.report_url)}</a></p>'
    )
    new_urls = {issue.url for issue in digest.new_issues if issue.url}
    new_issues_html = ""
    if digest.new_issues:
        items = "".join(
            '<li style="margin:0 0 8px">'
            f'<span style="color:#667085">[{escape(issue.label)}]</span> '
            + (
                f'<a href="{escape(issue.url, quote=True)}">{escape(issue.title)}</a>'
                if issue.url
                else escape(issue.title)
            )
            + "</li>"
            for issue in digest.new_issues
        )
        new_issues_html = (
            '<h2 style="font-size:15px;margin:24px 0 10px">오늘의 신규 이슈</h2>'
            f'<ol style="margin:0;padding-left:22px">{items}</ol>'
        )
    links_html = ""
    visible_links = [link for link in digest.links if link.url not in new_urls]
    if visible_links:
        items = "".join(
            '<li style="margin:0 0 8px">'
            f'<span style="color:#667085">[{escape(link.label)}]</span> '
            f'<a href="{escape(link.url, quote=True)}">{escape(link.title)}</a>'
            "</li>"
            for link in visible_links
        )
        links_html = (
            '<h2 style="font-size:15px;margin:24px 0 10px">핵심 링크</h2>'
            f'<ol style="margin:0;padding-left:22px">{items}</ol>'
        )
    notice = _data_notice(digest)
    notice_html = (
        f'<p style="margin:18px 0 0;color:#667085;font-size:12px">{escape(notice)}</p>'
        if notice
        else ""
    )
    return (
        '<!doctype html><html lang="ko"><body '
        'style="font-family:Arial,Apple SD Gothic Neo,sans-serif;color:#172033;'
        'line-height:1.6;background:#ffffff">'
        '<main style="max-width:680px;margin:0 auto;padding:24px">'
        f'<h1 style="font-size:18px;margin:0 0 16px">오늘 {escape(digest.brief_time)} 브리프</h1>'
        f"{headline_card}{body_sections}{buttons}{fallback}{new_issues_html}{links_html}{notice_html}"
        "</main></body></html>"
    )
