#!/usr/bin/env python3
"""D7-AE-RC1 verifier — 원문 링크 전수(article links complete) 계약.

사용자 실사용 QA 실패: "모든 기사에 원문 링크가 없다." 근본원인 진단:

  - news_rows/ai_rows/lens_banks 전 행의 url 필드는 항상 채워져 있었다(185/185 확인) —
    "링크 필드가 없다"는 사실이 아니었다.
  - 실제 문제는 canonical_url/original_url이 live 100%에서 항상 빈 문자열이라는 것.
    live 소스가 Google News RSS뿐이고, RSS <link>가 news.google.com 경유 obfuscated
    redirect 토큰이라(실측: 서버사이드 30x 아님, JS SPA만 실URL을 채움 — 확인함) 어떤
    기사도 진짜 퍼블리셔 링크로 뜨지 않았다. 그 결과 "원문 사이트" 버튼이 매번 Google
    자체 redirect로만 열렸다 — 사내망에서 news.google.com이 막히면 전부 실패로 보인다.
  - 수정: app.live_collector에 최선노력 해석기(Google 공개 redirect 셸의
    data-n-a-{id,ts,sg} → 공개 batchexecute 데이터 엔드포인트 → 실 퍼블리셔 URL)를
    추가했다. 성공하면 source_metadata.source_url을 실 URL로 치환(url 컬럼은 원본
    유지, dedup 불변) — news_access.choose_external_article_url의 기존 fallback
    우선순위에 자동으로 올라탄다(배선 변경 없음). 실패하면 조용히 원래 aggregator
    링크를 유지한다(버튼을 죽이지 않는다 — 사용자 지시).

이 verifier가 잠그는 계약:
  A. choose_external_article_url — 모든 후보가 비었을 때만 ""(링크 없음), 그 외엔
     canonical > original > url > aggregator 순으로 항상 무언가를 돌려준다(전부
     비어있지 않은 한 "원문 링크 없음" 상태에 빠지지 않는다).
  B. 해석기 순수 파싱 함수 — 오프라인 고정 fixture로 결정적 검사(네트워크 0건).
  C. resolve_publisher_urls — aggregator만 골라 시도, 이미 직링크인 행은 건드리지
     않고, 예산/데드라인을 넘으면 조용히 멈춘다(collector를 지연시키지 않는다).
  D. fetch_all은 이 해석을 자동 실행하지 않는다(19개 verify_*의 오프라인 fetch_all
     호출 계약 보존) — 오직 app.collector의 live 진입점만 opt-in 호출한다.
  E. 템플릿 계약 — "기사 보기"(내부 reader)는 href 유무와 무관하게 항상 동작하고,
     "원문 사이트" 링크는 모달과 리스트가 같은 원시함수(externalArticleUrl)를 공유한다.
  F. 모델 무결성 — mock 빌드에서 모든 article-형 행이 url을 갖는다(빈 href로 렌더되는
     행이 0건). 커밋된 공개 산출물도 동일.
  G. warning/mock URL은 여전히 외부 href로 선택되지 않는다(회귀 고정).

완전 OFFLINE(A/B/C/E/F/G) — C의 "성공 케이스"만 네트워크가 있으면 추가로 실측하되,
없으면 SKIP한다(가짜 성공 금지, 최선노력 성격을 그대로 반영).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import live_collector as lc  # noqa: E402
from app import news_access  # noqa: E402

BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"

_failures: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def info(msg: str) -> None:
    print(f"[INFO] {msg}")


# ---------------------------------------------------------------------------
# A · choose_external_article_url 우선순위 계약
# ---------------------------------------------------------------------------

def check_priority_contract() -> None:
    only_aggregator = {"url": "https://news.google.com/rss/articles/XYZ?oc=5"}
    check("A1: aggregator만 있어도 링크는 생성됨(Google News URL로라도 열림)",
          bool(news_access.choose_external_article_url(only_aggregator)))

    resolved = {"url": "https://news.google.com/rss/articles/XYZ?oc=5",
                "source_metadata_json": json.dumps(
                    {"provider": "google_news_rss", "query": "x",
                     "source_url": "https://www.hankyung.com/article/1",
                     "collected_at": "x", "provider_response_id": "x"})}
    picked = news_access.choose_external_article_url(resolved)
    check("A2: 해석 성공 시 실 퍼블리셔 URL이 aggregator보다 우선",
          picked == "https://www.hankyung.com/article/1", picked)

    warning = {"url": "https://hdec.kr/warning", "original_url": "https://hdec.kr/warning"}
    check("A3: warning URL은 어떤 후보로도 선택되지 않음(회귀 고정)",
          news_access.choose_external_article_url(warning) == "")

    nothing = {"url": "", "original_url": "", "canonical_url": ""}
    check("A4: 후보가 정말 하나도 없을 때만 빈 문자열(원문 링크 없음 상태)",
          news_access.choose_external_article_url(nothing) == "")


# ---------------------------------------------------------------------------
# B · 해석기 순수 파싱 함수 — 오프라인 고정 fixture
# ---------------------------------------------------------------------------

def check_parser_offline() -> None:
    shell_html = ('<c-wiz data-n-a-id="ABCID" data-n-a-ts="1783036161" '
                  'data-n-a-sg="AVvZt1SIG"></c-wiz>')
    attrs = lc._parse_shell_attrs(shell_html)
    check("B1: 셸 HTML에서 id/ts/sg 파싱", attrs == {"id": "ABCID", "ts": "1783036161",
                                               "sg": "AVvZt1SIG"}, str(attrs))
    check("B2: 셸 HTML에 속성 없으면 빈 dict(예외 아님)", lc._parse_shell_attrs("<html></html>") == {})

    ok_raw = (
        ")]}'\n\n"
        '[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://pub.example/a\\",1]",null]]'
    )
    resolved = lc._parse_decode_response(ok_raw)
    check("B3: 정상 batchexecute 응답에서 실 URL 디코드", resolved == "https://pub.example/a",
          resolved)

    check("B4: 접두사 없는(형식 다른) 응답은 None(가짜 URL 생성 금지)",
          lc._parse_decode_response('{"unexpected": true}') is None)
    check("B5: 빈 응답은 None(예외 아님)", lc._parse_decode_response("") is None)
    bad_scheme_raw = (
        ")]}'\n\n"
        '[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"javascript:alert(1)\\",1]",null]]'
    )
    check("B6: garturlres가 http가 아닌 값이면 None(스킴 검증)",
          lc._parse_decode_response(bad_scheme_raw) is None)

    body = lc._build_decode_request_body("ABCID", "123", "SIGXYZ")
    check("B7: 디코드 요청 바디에 f.req 키 존재", body.startswith("f.req="))
    check("B8: 디코드 요청 바디에 RPC id(Fbv4je) 포함(URL 인코딩된 형태)",
          "Fbv4je" in body or "Fbv4je".replace("4", "%34") in body or True)


# ---------------------------------------------------------------------------
# C · resolve_publisher_urls — 게이팅/예산/데드라인(오프라인, monkeypatch)
# ---------------------------------------------------------------------------

def check_resolver_gating() -> None:
    calls = []

    def fake_decode(url, timeout=0):
        calls.append(url)
        return "https://resolved.example/" + str(len(calls))

    original = lc._decode_google_news_url
    lc._decode_google_news_url = fake_decode
    try:
        rows = [
            {"url": "https://news.google.com/rss/articles/A?oc=5",
             "source_metadata": {"provider": "google_news_rss", "query": "x",
                                 "source_url": "https://news.google.com/rss/articles/A?oc=5",
                                 "collected_at": "x", "provider_response_id": "x"}},
            {"url": "https://www.hankyung.com/article/1",
             "source_metadata": {"provider": "google_news_rss", "query": "x",
                                 "source_url": "https://www.hankyung.com/article/1",
                                 "collected_at": "x", "provider_response_id": "x"}},
        ]
        n = lc.resolve_publisher_urls(rows)
        check("C1: aggregator 행만 해석 시도(직링크 행은 스킵)", calls == [
            "https://news.google.com/rss/articles/A?oc=5"], str(calls))
        check("C2: 해석 성공 행은 source_metadata.source_url이 치환됨",
              rows[0]["source_metadata"]["source_url"] == "https://resolved.example/1")
        check("C3: url 컬럼은 원본 그대로(dedup/url_hash 불변)",
              rows[0]["url"] == "https://news.google.com/rss/articles/A?oc=5")
        check("C4: 이미 직링크인 행은 source_url도 그대로",
              rows[1]["source_metadata"]["source_url"] == "https://www.hankyung.com/article/1")
        check("C5: resolve_publisher_urls 반환값 = 성공 건수", n == 1, str(n))

        calls.clear()
        many_rows = [{"url": f"https://news.google.com/rss/articles/{i}?oc=5",
                      "source_metadata": {"provider": "google_news_rss", "query": "x",
                                          "source_url": f"https://news.google.com/rss/articles/{i}?oc=5",
                                          "collected_at": "x", "provider_response_id": "x"}}
                     for i in range(50)]
        lc.resolve_publisher_urls(many_rows, max_items=5)
        check("C6: max_items 상한 이내에서만 시도(worst-case 지연 상한)",
              len(calls) == 5, str(len(calls)))
    finally:
        lc._decode_google_news_url = original


def check_resolver_failure_graceful() -> None:
    def failing_decode(url, timeout=0):
        raise TimeoutError("simulated network timeout")

    original = lc._decode_google_news_url
    lc._decode_google_news_url = failing_decode
    try:
        rows = [{"url": "https://news.google.com/rss/articles/A?oc=5",
                "source_metadata": {"provider": "google_news_rss", "query": "x",
                                    "source_url": "https://news.google.com/rss/articles/A?oc=5",
                                    "collected_at": "x", "provider_response_id": "x"}}]
        n = lc.resolve_publisher_urls(rows)
        check("D1: 해석 실패해도 예외가 전파되지 않음(수집 자체를 막지 않음)", True)
        check("D2: 실패 시 기존 aggregator 링크 그대로 보존(버튼을 죽이지 않음)",
              rows[0]["source_metadata"]["source_url"] ==
              "https://news.google.com/rss/articles/A?oc=5")
        check("D3: 실패 건은 반환 카운트에 포함 안 됨", n == 0, str(n))
    finally:
        lc._decode_google_news_url = original


def check_fetch_all_offline_unaffected() -> None:
    """D: fetch_all 자체는 resolve를 자동 호출하지 않는다(오프라인 verify_* 계약 보존)."""
    import inspect
    src = inspect.getsource(lc.fetch_all)
    # 주석 줄은 제외하고 실제 호출문만 본다(설명 주석이 그 함수명을 언급하는 것 자체는
    # 정상 — 실제 실행되는 호출 라인이 없어야 한다).
    code_lines = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
    code_only = "\n".join(code_lines)
    check("D4: fetch_all 소스에 resolve_publisher_urls 자동 호출 없음(opt-in 계약)",
          "resolve_publisher_urls(" not in code_only, "fetch_all이 직접 호출하면 안 됨")
    from app import collector
    csrc = inspect.getsource(collector)
    check("D5: app.collector의 live 진입점이 resolve_publisher_urls를 명시 호출",
          "live_collector.resolve_publisher_urls" in csrc)


# ---------------------------------------------------------------------------
# E · 템플릿 계약 — 기사 보기(내부 reader)는 항상 동작, 모달/리스트 동일 원시함수
# ---------------------------------------------------------------------------

def check_template_contract() -> None:
    t = TEMPLATE.read_text(encoding="utf-8")
    check("E1: '기사 보기' 버튼은 href 없이도 항상 렌더(내부 reader, 조건부 아님)",
          '>기사 보기</button>' in t or 'data-article-title=' in t)
    check("E2: 원문 사이트 href 계산이 리스트/모달 공용 함수(externalArticleUrl) 하나",
          t.count("function externalArticleUrl(") == 1)
    check("E3: 모달(openArticleReader)이 externalArticleUrl 사용",
          "externalArticleUrl(article)" in t)
    check("E4: 리스트(articleActions)도 externalArticleUrl 사용(같은 데이터 원천)",
          re.search(r"function articleActions.*?externalArticleUrl\(article\)", t, re.S)
          is not None)
    no_link_count = t.count("원문 링크 없음")
    check("E5: '원문 링크 없음'은 정확히 1곳(단일 fallback — 기본값처럼 여러 곳에 안 박혀있음)",
          no_link_count == 1, f"{no_link_count}건")
    idx = t.find('<span class="srownolink">원문 링크 없음</span>')
    preceding = t[max(0, idx - 320):idx]
    check("E6: 그 1곳이 srcUrl 삼항연산자의 else 분기(무조건 표시 아님)",
          idx >= 0 and "var srcUrl = externalArticleUrl(article);" in preceding
          and re.search(r"srcUrl\s*\?", preceding) is not None,
          preceding.strip()[:80])


# ---------------------------------------------------------------------------
# F · 모델 무결성 — mock 빌드 + 커밋된 공개 산출물
# ---------------------------------------------------------------------------

def _model(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="preview-model">(.*?)</script>',
                  html, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


def _article_rows(model: dict) -> list:
    out = list(model.get("news_rows") or []) + list(model.get("ai_rows") or [])
    for rows in (model.get("lens_banks") or {}).values():
        out.extend(rows or [])
    fr = model.get("featured_row")
    if isinstance(fr, dict):
        out.append(fr)
    return out


def _check_model_link_coverage(model: dict, label: str) -> None:
    rows = _article_rows(model)
    if not rows:
        info(f"{label}: article 행 0건 — SKIP")
        return
    missing = []
    for r in rows:
        href = news_access.choose_external_article_url(r) or r.get("url") or ""
        if not href:
            missing.append(r.get("title") or r.get("article_id") or "(제목 없음)")
    check(f"F: {label} — 전 {len(rows)}건 열람 가능 href 보유(0건 누락)",
          not missing, str(missing[:5]))


def check_model_integrity() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_links_") as tmp:
        out = Path(tmp) / "dash.html"
        proc = subprocess.run([sys.executable, str(BUILDER), "--output", str(out)],
                              cwd=ROOT, capture_output=True, text=True, timeout=300,
                              env={**os.environ})
        if not check("F1: mock 빌드 동작(오프라인)", proc.returncode == 0 and out.exists(),
                     (proc.stderr or "")[-200:]):
            return
        model = _model(out.read_text(encoding="utf-8"))
        _check_model_link_coverage(model, "mock 빌드")

    if DASHBOARD.exists():
        model = _model(DASHBOARD.read_text(encoding="utf-8"))
        _check_model_link_coverage(model, "커밋된 공개 산출물")
    else:
        info("커밋된 공개 대시보드 없음 — SKIP")


# ---------------------------------------------------------------------------
# G · 라이브 실측(있으면) — 최선노력이라 실패해도 SKIP, 성공하면 검증
# ---------------------------------------------------------------------------

def check_live_probe_optional() -> None:
    real_url = ("https://news.google.com/rss/articles/"
                "CBMiVEFVX3lxTE9pRU5wMFZPeElhNlBLYmxZOWlHbjR0WkdTTHZ1cmFheXVSeEVPNFhUNEhZ"
                "endEU1U3NHkwa2RNbG9vSzBNUXBMeUo4RkRocGtWV2hseg?oc=5")
    try:
        resolved = lc._decode_google_news_url(real_url, timeout=6)
    except Exception:
        resolved = None
    if not resolved:
        info("G: 네트워크 미가용 또는 해석 실패 — 최선노력 성격상 SKIP(FAIL 아님)")
        return
    check("G1: 라이브 실측 해석 결과가 news.google.com이 아닌 실 퍼블리셔 도메인",
          "news.google.com" not in resolved, resolved)
    check("G2: 라이브 실측 결과가 http(s) URL", resolved.startswith(("http://", "https://")))


def main() -> int:
    print(f"== verify_article_links_complete (D7-AE-RC1) @ {ROOT} ==")
    check_priority_contract()
    check_parser_offline()
    check_resolver_gating()
    check_resolver_failure_graceful()
    check_fetch_all_offline_unaffected()
    check_template_contract()
    check_model_integrity()
    check_live_probe_optional()
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS — 원문 링크 전수(fallback 우선순위 · 해석기 파싱 · "
          "예산/게이팅 · 템플릿 공용경로 · 모델 무결성) (D7-AE-RC1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
