"""Channel-neutral executive digest model and renderers.

The module owns the short briefing language shared by Telegram, email, and
Teams channel email.  It does not read environment variables, credentials, or
the network.  Callers provide the already-selected digest data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from html import escape
from urllib.parse import urlparse


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


def build_executive_digest(data: dict, brief_time: str = "07:00") -> ExecutiveDigest:
    """Convert selected brief signals into a four-sentence, conclusion-first brief."""

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
    lines = [
        f"[오늘 {digest.brief_time} 브리프]",
        "",
        digest.headline,
        "",
        digest.situation,
        digest.hdec_angle,
        "",
        digest.watch,
    ]
    notice = _data_notice(digest)
    if notice:
        lines += ["", notice]
    if digest.links:
        lines += ["", "핵심 링크"]
        for index, link in enumerate(digest.links, start=1):
            lines.append(f"{index}. [{link.label}] {link.title}")
            lines.append(f"   {link.url}")
    return "\n".join(lines)


def render_email_html(digest: ExecutiveDigest) -> str:
    paragraphs = "".join(
        f'<p style="margin:0 0 12px">{escape(sentence)}</p>'
        for sentence in (
            digest.headline,
            digest.situation,
            digest.hdec_angle,
            digest.watch,
        )
    )
    notice = _data_notice(digest)
    notice_html = (
        f'<p style="margin:14px 0 0;color:#667085;font-size:12px">{escape(notice)}</p>'
        if notice
        else ""
    )
    links_html = ""
    if digest.links:
        items = "".join(
            '<li style="margin:0 0 8px">'
            f'<span style="color:#667085">[{escape(link.label)}]</span> '
            f'<a href="{escape(link.url, quote=True)}">{escape(link.title)}</a>'
            "</li>"
            for link in digest.links
        )
        links_html = (
            '<h2 style="font-size:15px;margin:20px 0 10px">핵심 링크</h2>'
            f'<ol style="margin:0;padding-left:22px">{items}</ol>'
        )
    return (
        '<!doctype html><html lang="ko"><body '
        'style="font-family:Arial,Apple SD Gothic Neo,sans-serif;color:#172033;line-height:1.55">'
        '<main style="max-width:680px;margin:0 auto;padding:20px">'
        f'<h1 style="font-size:18px;margin:0 0 20px">오늘 {escape(digest.brief_time)} 브리프</h1>'
        f"{paragraphs}{notice_html}{links_html}"
        "</main></body></html>"
    )
