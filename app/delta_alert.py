"""Delta-first alert payload model and channel renderers (D7-AJ-2/3).

이 모듈은 '시간당 변동(delta) 알림'의 채널 중립 표현을 소유한다. 전체 일일 브리프
(app/executive_digest.py, 07:00 아침 브리프 언어)와는 별개의 계약이며, 서로 침범하지
않는다. 이 파일은 다음만 한다:

  · delta 아티팩트(JSON) 파싱/검증 — 스키마 위반은 fail-closed(예외).
  · 실제 KST 제목/기준시각 계약(고정 07:00 없음) — 단일 owner.
  · Telegram / 이메일(Teams 채널) / Teams Adaptive Card 렌더.

절대 하지 않는 것: 환경변수 읽기, 네트워크 호출, 자격증명/webhook URL 접근, 점수·등급·
분류 재계산. 아티팩트에 담긴 이미 확정된 결과를 '표시만' 한다. CTA URL(요약 대시보드/
전체 리포트)은 호출자가 기존 REPORT_URL/DASHBOARD_URL 계약으로 해석해 주입한다(이 모듈은
env를 읽지 않는다). 미주입이면 executive_digest의 공개 fallback 상수를 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape

# 공개 CTA fallback URL은 단일 owner(executive_digest)에서 재사용한다 — 중복 상수 금지.
from app.executive_digest import DEFAULT_DASHBOARD_URL, DEFAULT_REPORT_URL

SCHEMA_VERSION = 1
MAX_ARTICLES = 5
_KST = timezone(timedelta(hours=9))

# 아티팩트 source 라벨 — live 자동발송은 live-delta, mock/데모는 mock-delta, 검증은 test-delta.
LIVE_SOURCE = "live-delta"
MOCK_SOURCE = "mock-delta"
TEST_SOURCE = "test-delta"
VALID_SOURCES = frozenset({LIVE_SOURCE, MOCK_SOURCE, TEST_SOURCE})

_TITLE_MAX = 90
_RELEVANCE_MAX = 100
_SUMMARY_MAX = 140
_JUDGMENT_MAX = 140
_CARD_ACTION_TITLE_MAX = 44


class InvalidDeltaArtifact(ValueError):
    """delta 아티팩트가 스키마 계약을 위반함 — 발송하면 안 되는 상태(fail-closed)."""


@dataclass(frozen=True)
class DeltaArticle:
    article_key: str
    title: str
    published_at: str
    published_kst: str
    source: str
    category: str
    hdec_relevance: str
    summary: str
    url: str


@dataclass(frozen=True)
class DeltaAlert:
    schema_version: int
    generated_at: str
    generated_kst: str
    source: str
    alert_delta: bool
    changed_count: int
    new_candidate_count: int
    judgment: str
    articles: tuple[DeltaArticle, ...]
    dashboard_url: str
    report_url: str

    @property
    def sendable(self) -> bool:
        """실제 발송해도 되는 상태인가 — delta가 열려 있고 보여줄 기사가 있을 때만 True."""
        return bool(self.alert_delta and self.articles)


# ── 파싱/검증 ────────────────────────────────────────────────────────────────

def _clip(text: object, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _is_http(url: object) -> bool:
    return bool(url) and str(url).strip().lower().startswith(("http://", "https://"))


def _fmt_kst(iso: object) -> str:
    """ISO 타임스탬프를 KST 벽시계 'YYYY-MM-DD HH:MM'로 (raw offset 비노출)."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M")


def _hhmm(generated_kst: str) -> str:
    """'YYYY-MM-DD HH:MM' → 'HH:MM' (제목의 실제 발송 시각). 형식 위반이면 원문 그대로."""
    text = (generated_kst or "").strip()
    if len(text) >= 16 and text[10] == " ":
        return text[11:16]
    return text


def _parse_article(obj: object) -> DeltaArticle | None:
    if not isinstance(obj, dict):
        return None
    title = _clip(obj.get("title"), _TITLE_MAX)
    if not title:
        return None
    url = str(obj.get("url") or "").strip()
    if not _is_http(url):
        url = ""
    published_at = str(obj.get("published_at") or "").strip()
    published_kst = str(obj.get("published_kst") or "").strip() or _fmt_kst(published_at)
    article_key = str(
        obj.get("article_key") or obj.get("article_id") or url or title
    ).strip()
    return DeltaArticle(
        article_key=article_key,
        title=title,
        published_at=published_at,
        published_kst=published_kst,
        source=_clip(obj.get("source"), 60),
        category=_clip(obj.get("category") or obj.get("category_label"), 40),
        hdec_relevance=_clip(obj.get("hdec_relevance"), _RELEVANCE_MAX),
        summary=_clip(obj.get("summary"), _SUMMARY_MAX),
        url=url,
    )


def parse_delta_alert(obj: object, *, dashboard_url: str = "", report_url: str = "") -> DeltaAlert:
    """검증된 dict를 DeltaAlert로 변환한다. 계약 위반은 InvalidDeltaArtifact(fail-closed).

    dashboard_url/report_url은 호출자가 기존 REPORT_URL/DASHBOARD_URL 계약으로 해석해
    주입한다. 비거나 http(s)가 아니면 공개 fallback 상수를 쓴다(이 모듈은 env를 읽지 않음).
    """
    if not isinstance(obj, dict):
        raise InvalidDeltaArtifact("artifact root is not an object")
    if obj.get("schema_version") != SCHEMA_VERSION:
        raise InvalidDeltaArtifact(f"unsupported schema_version: {obj.get('schema_version')!r}")
    source = str(obj.get("source") or "").strip()
    if source not in VALID_SOURCES:
        raise InvalidDeltaArtifact(f"unknown source: {source!r}")
    if not isinstance(obj.get("alert_delta"), bool):
        raise InvalidDeltaArtifact("alert_delta must be a boolean")
    raw_articles = obj.get("articles")
    if not isinstance(raw_articles, list):
        raise InvalidDeltaArtifact("articles must be a list")

    generated_kst = str(obj.get("generated_kst") or "").strip()
    generated_at = str(obj.get("generated_at") or "").strip()
    if not generated_kst and generated_at:
        generated_kst = _fmt_kst(generated_at)
    if not generated_kst:
        raise InvalidDeltaArtifact("generated_kst/generated_at is missing")

    seen: set[str] = set()
    articles: list[DeltaArticle] = []
    for item in raw_articles:
        parsed = _parse_article(item)
        if parsed is None or parsed.article_key in seen:
            continue
        seen.add(parsed.article_key)
        articles.append(parsed)
        if len(articles) >= MAX_ARTICLES:
            break

    def _cnt(key: str, default: int) -> int:
        try:
            return max(0, int(obj.get(key)))
        except (TypeError, ValueError):
            return default

    new_candidate_count = _cnt("new_candidate_count", len(articles))
    changed_count = _cnt("changed_count", new_candidate_count)

    return DeltaAlert(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at,
        generated_kst=generated_kst,
        source=source,
        alert_delta=bool(obj.get("alert_delta")),
        changed_count=changed_count,
        new_candidate_count=new_candidate_count,
        judgment=_clip(obj.get("judgment"), _JUDGMENT_MAX),
        articles=tuple(articles),
        dashboard_url=dashboard_url if _is_http(dashboard_url) else DEFAULT_DASHBOARD_URL,
        report_url=report_url if _is_http(report_url) else DEFAULT_REPORT_URL,
    )


def load_delta_alert(path, *, dashboard_url: str = "", report_url: str = "") -> DeltaAlert:
    """파일 경로에서 delta 아티팩트를 읽어 검증한다. 없거나 깨졌으면 fail-closed(예외)."""
    import json
    from pathlib import Path

    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise InvalidDeltaArtifact(f"artifact file unreadable: {type(exc).__name__}") from exc
    try:
        obj = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise InvalidDeltaArtifact("artifact is not valid JSON") from exc
    return parse_delta_alert(obj, dashboard_url=dashboard_url, report_url=report_url)


# ── 실제 KST 제목/기준시각 계약 (단일 owner · 고정 07:00 없음) ─────────────────

def _count_for_title(alert: DeltaAlert) -> int:
    return alert.new_candidate_count or len(alert.articles)


def title_text(alert: DeltaAlert) -> str:
    """'12:32 핵심 변동 — 신규 3건' — 실제 발송 시각(KST) + 신규 변동 건수."""
    return f"{_hhmm(alert.generated_kst)} 핵심 변동 — 신규 {_count_for_title(alert)}건"


def render_subject(alert: DeltaAlert) -> str:
    """이메일/Teams 제목 — '[HDEC News Sensor] 12:32 핵심 변동 — 신규 3건'."""
    return f"[HDEC News Sensor] {title_text(alert)}"


def first_line(alert: DeltaAlert) -> str:
    """본문 첫 줄 — '2026-07-14 12:32 KST 기준 변동 브리프'."""
    return f"{alert.generated_kst} KST 기준 변동 브리프"


def _judgment_line(alert: DeltaAlert) -> str:
    if alert.judgment:
        return alert.judgment
    return "새로 감지된 변동을 우선 확인하세요."


def _meta_line(article: DeltaArticle) -> str:
    """'게시시각 · 출처/분류' — 있는 조각만 ' · '로 잇는다."""
    parts = [p for p in (article.published_kst, article.source or article.category) if p]
    return " · ".join(parts)


# ── Telegram (HTML parse mode) ────────────────────────────────────────────────

def render_telegram(alert: DeltaAlert) -> str:
    """변동 우선(delta-first) Telegram 본문. 최신순 최대 5건 · HTML escape.

    요약 대시보드/전체 리포트는 send_telegram이 inline 버튼으로 붙이므로 본문에는 raw URL을
    넣지 않는다(포인터 문장만). 기사별 '원문 링크'는 제목 앵커로 제공한다."""
    lines = [
        "🔔 HDEC Executive Radar",
        f"<b>{escape(title_text(alert))}</b>",
        escape(first_line(alert)),
        "",
        "<b>새로 감지된 핵심 변동</b>",
    ]
    for index, article in enumerate(alert.articles, start=1):
        if article.url:
            head = (f'{index}. <a href="{escape(article.url, quote=True)}">'
                    f"{escape(article.title)}</a>")
        else:
            head = f"{index}. {escape(article.title)}"
        lines.append(head)
        meta = _meta_line(article)
        if meta:
            lines.append(f"   {escape(meta)}")
        if article.hdec_relevance:
            lines.append(f"   ↳ {escape(article.hdec_relevance)}")
    lines += ["", f"오늘의 판단: {escape(_judgment_line(alert))}"]
    lines += ["", "요약은 '대시보드 보기', 전체 근거·거시경제는 '상세 리포트 보기'에서 확인"]
    return "\n".join(lines)


# ── 이메일 / Teams 채널 이메일 ────────────────────────────────────────────────

def render_email_text(alert: DeltaAlert) -> str:
    """plain text 본문 — 버튼이 깨져도 접근 가능하게 CTA URL을 세로로 명시한다.

    라벨↔URL을 각 줄로 분리해 '요약 대시보드 보기전체 리포트 보기'처럼 붙는 결합을 원천 차단한다."""
    lines = [title_text(alert), "", first_line(alert), "", "새로 감지된 핵심 변동"]
    for index, article in enumerate(alert.articles, start=1):
        lines.append(f"{index}. [{article.category or '변동'}] {article.title}")
        meta = _meta_line(article)
        if meta:
            lines.append(f"   {meta}")
        if article.hdec_relevance:
            lines.append(f"   현대건설 관련성: {article.hdec_relevance}")
        if article.url:
            lines.append(f"   원문: {article.url}")
    lines += ["", f"오늘의 판단: {_judgment_line(alert)}"]
    lines += [
        "",
        "바로 보기",
        f"- 요약 대시보드: {alert.dashboard_url}",
        f"- 전체 리포트: {alert.report_url}",
    ]
    return "\n".join(lines)


def _cta_link(url: str, label: str, primary: bool) -> str:
    """세로 block CTA 링크 한 줄 (display:block · 전체폭 · 아래 여백).

    가로 inline-block 버튼은 Teams/Outlook이 배경·간격을 떼어내며 라벨이 서로 붙는다
    ('요약 대시보드 보기전체 리포트 보기'). block으로 쌓고 margin으로 간격을 줘 문서 흐름
    순서를 보존한다. 외부 CSS/JS/이미지/테이블은 쓰지 않는다(sanitizer-safe)."""
    background = "#1d4ed8" if primary else "#0f766e"
    return (
        f'<a href="{escape(url, quote=True)}" '
        'style="display:block;margin:0 0 10px;padding:13px 18px;'
        f'background:{background};color:#ffffff;text-decoration:none;'
        'border-radius:8px;font-size:15px;font-weight:bold;text-align:center">'
        f"{escape(label)}</a>"
    )


def _article_li(article: DeltaArticle) -> str:
    title_html = (
        f'<a href="{escape(article.url, quote=True)}">{escape(article.title)}</a>'
        if article.url else escape(article.title)
    )
    parts = [
        f'<span style="color:#667085">[{escape(article.category or "변동")}]</span> {title_html}'
    ]
    meta = _meta_line(article)
    if meta:
        parts.append(f'<div style="color:#667085;font-size:13px;margin:2px 0 0">'
                     f"{escape(meta)}</div>")
    if article.hdec_relevance:
        parts.append(f'<div style="font-size:13px;margin:2px 0 0">현대건설 관련성: '
                     f"{escape(article.hdec_relevance)}</div>")
    return f'<li style="margin:0 0 12px">{"".join(parts)}</li>'


def render_email_html(alert: DeltaAlert) -> str:
    """단순 inline-style HTML(외부 JS/CSS/이미지/첨부 없음). 변동 기사 최상단 · 세로 CTA.

    버튼이 깨져도 하단 plain URL fallback으로 대시보드/리포트에 접근할 수 있다."""
    header = (
        f'<h1 style="font-size:18px;margin:0 0 4px">{escape(title_text(alert))}</h1>'
        f'<p style="margin:0 0 16px;color:#667085;font-size:13px">'
        f"{escape(first_line(alert))}</p>"
    )
    items = "".join(_article_li(article) for article in alert.articles)
    articles_html = (
        '<h2 style="font-size:15px;margin:0 0 10px">새로 감지된 핵심 변동</h2>'
        f'<ol style="margin:0;padding-left:22px">{items}</ol>'
    )
    judgment_html = (
        '<div style="margin:20px 0 6px;padding:14px 16px;background:#f1f5ff;'
        'border-left:4px solid #1d4ed8;border-radius:6px">'
        '<p style="margin:0;font-size:15px;font-weight:bold;color:#0b1f4d">'
        f"오늘의 판단: {escape(_judgment_line(alert))}</p></div>"
    )
    cta_block = (
        '<div style="margin:22px 0 10px">'
        + _cta_link(alert.dashboard_url, "요약 대시보드 보기", primary=True)
        + _cta_link(alert.report_url, "전체 리포트 보기", primary=False)
        + "</div>"
    )
    fallback = (
        '<p style="margin:0 0 2px;color:#667085;font-size:12px">'
        "버튼이 보이지 않으면 아래 주소를 직접 여세요.</p>"
        '<p style="margin:0;font-size:12px;color:#475467">요약 대시보드: '
        f'<a href="{escape(alert.dashboard_url, quote=True)}">{escape(alert.dashboard_url)}</a></p>'
        '<p style="margin:0 0 4px;font-size:12px;color:#475467">전체 리포트: '
        f'<a href="{escape(alert.report_url, quote=True)}">{escape(alert.report_url)}</a></p>'
    )
    return (
        '<!doctype html><html lang="ko"><body '
        'style="font-family:Arial,Apple SD Gothic Neo,sans-serif;color:#172033;'
        'line-height:1.6;background:#ffffff">'
        '<main style="max-width:680px;margin:0 auto;padding:24px">'
        f"{header}{articles_html}{judgment_html}{cta_block}{fallback}"
        "</main></body></html>"
    )


# ── Teams Adaptive Card (D7-AJ-3) ─────────────────────────────────────────────

def _text_block(text: str, **kwargs) -> dict:
    block = {"type": "TextBlock", "text": text, "wrap": True}
    block.update(kwargs)
    return block


def build_teams_card(alert: DeltaAlert) -> dict:
    """Teams Workflows(Power Automate) webhook용 Adaptive Card 페이로드.

    상단: HDEC EXECUTIVE RADAR · 실제 KST 시각 · 신규 N건. 본문: 최신 변동 기사 + 현대건설
    관련성 + 오늘의 판단. actions: 기사별 원문 · 요약 대시보드 · 전체 리포트(Action.OpenUrl).
    비밀값/webhook URL은 담기지 않는다(호출자가 전송 시에만 사용)."""
    body: list[dict] = [
        _text_block("HDEC EXECUTIVE RADAR", weight="Bolder", size="Medium"),
        _text_block(f"{alert.generated_kst} KST · 신규 {_count_for_title(alert)}건",
                    isSubtle=True, spacing="None"),
        _text_block("새로 감지된 핵심 변동", weight="Bolder", spacing="Medium"),
    ]
    for article in alert.articles:
        body.append(_text_block(article.title, weight="Bolder", spacing="Medium"))
        meta = _meta_line(article)
        prefix = f"[{article.category}] " if article.category else ""
        if prefix or meta:
            body.append(_text_block(f"{prefix}{meta}".strip(), isSubtle=True, spacing="None"))
        if article.hdec_relevance:
            body.append(_text_block(f"현대건설 관련성: {article.hdec_relevance}", spacing="None"))
    body.append(_text_block(f"오늘의 판단: {_judgment_line(alert)}",
                            weight="Bolder", spacing="Medium"))

    actions: list[dict] = []
    for article in alert.articles:
        if article.url:
            actions.append({
                "type": "Action.OpenUrl",
                "title": f"원문 보기 — {_clip(article.title, _CARD_ACTION_TITLE_MAX)}",
                "url": article.url,
            })
    actions.append({"type": "Action.OpenUrl", "title": "요약 대시보드 보기",
                    "url": alert.dashboard_url})
    actions.append({"type": "Action.OpenUrl", "title": "전체 리포트 보기",
                    "url": alert.report_url})

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                    "actions": actions,
                },
            }
        ],
    }
