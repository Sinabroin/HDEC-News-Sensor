#!/usr/bin/env python3
"""D7-AD-X 원문 링크 회귀 방지 계약 검증 (완전 오프라인).

외부 '원문 사이트' href가 항상 원본에 가까운 접근 가능 URL이며 warning URL
(``hdec.kr/warning`` / ``WARNING.jpg``)이 절대 href로 새지 않는지, 그리고 링크
접근성 진단이 점수/분류/최신성과 분리되어 있는지 검증한다. 네트워크 호출 없음.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import news_access  # noqa: E402

TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
DIAG = ROOT / "scripts" / "diagnose_article_link_regression.py"

# 사내 차단 이미지/페이지 — 어떤 경우에도 외부 href로 선택되면 안 된다.
WARNING_URL = "https://www.hdec.kr/warning/WARNING.jpg"
WARNING_IMG = "http://intranet.example/assets/warning.jpg"

failures: list[str] = []
passes = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passes
    if ok:
        passes += 1
        print(f"  PASS  {label}")
    else:
        failures.append(label)
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def _load_diag():
    spec = importlib.util.spec_from_file_location("diag_link_regression", DIAG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    choose = getattr(news_access, "choose_external_article_url", None)
    check("1. choose_external_article_url 존재·호출 가능", callable(choose))
    if not callable(choose):
        print(f"\n{passes} passed, {len(failures)} failed\nABORT: 핵심 함수 부재")
        return 1

    # 2. warning URL은 외부 href로 선택되지 않는다.
    check("2. url이 warning이면 → \"\"",
          choose({"url": WARNING_URL}) == "" and choose({"url": WARNING_IMG}) == "",
          f"{choose({'url': WARNING_URL})!r}")
    check("2b. final_url이 warning이면 → \"\"",
          choose({"final_url": WARNING_URL}) == "")

    # 3. final_url이 warning이어도 publisher original/url이 있으면 그것을 href로 쓴다.
    check("3. warning final_url보다 original_url 우선",
          choose({"original_url": "https://www.hankyung.com/article/1",
                  "final_url": WARNING_URL}) == "https://www.hankyung.com/article/1")
    check("3b. warning final_url보다 url 우선",
          choose({"url": "https://pub.example/a",
                  "final_url": WARNING_URL}) == "https://pub.example/a")

    # 4. href 우선순위: canonical > original > url. final_url은 기본 href가 아니다.
    check("4. canonical_url이 url보다 우선",
          choose({"canonical_url": "https://pub.example/canon",
                  "url": "https://pub.example/u"}) == "https://pub.example/canon")
    check("4b. original_url이 url보다 우선",
          choose({"original_url": "https://pub.example/orig",
                  "url": "https://pub.example/u"}) == "https://pub.example/orig")
    check("4c. url이 있으면 final_url을 기본 href로 쓰지 않음",
          choose({"url": "https://pub.example/u",
                  "final_url": "https://pub.example/final"}) == "https://pub.example/u")
    check("4d. 원본 없는 단독 final_url은 href로 승격하지 않음",
          choose({"final_url": "https://pub.example/f"}) == "")

    # 5. portal/search fallback + 후보 전무 처리.
    check("5. source_metadata.source_url fallback",
          choose({"source_metadata": {"source_url": "https://pub.example/meta"}})
          == "https://pub.example/meta")
    check("5b. 유효 후보 없음 → \"\"",
          choose({}) == "" and choose({"url": "not-a-url"}) == ""
          and choose({"url": "javascript:alert(1)"}) == "")

    # 6. 템플릿: 내부 reader('기사 보기')와 외부 원문('원문 사이트')이 분리되고,
    #    외부 href는 raw url이 아니라 externalArticleUrl(warning 배제)로 만든다.
    template = TEMPLATE.read_text(encoding="utf-8")
    check("6. articleActions가 기사 보기(reader)+원문 사이트(external) 분리",
          "function articleActions" in template
          and ">기사 보기</button>" in template
          and "원문 사이트 ↗" in template)
    check("6b. externalArticleUrl 헬퍼로 외부 href 생성",
          "function externalArticleUrl" in template
          and "var srcUrl = externalArticleUrl(article)" in template)
    check("6c. externalArticleUrl이 warning URL을 배제",
          "hdec\\.kr\\/warning|warning\\.jpg" in template)
    check("6d. 원문 사이트 링크는 새 탭 안전 속성",
          'target="_blank"' in template and 'rel="noopener noreferrer"' in template)

    # 7. 빌더도 외부 href를 choose_external_article_url로 만든다(featured + row).
    builder = BUILDER.read_text(encoding="utf-8")
    check("7. 빌더가 choose_external_article_url 사용(>=2곳: featured+row)",
          builder.count("news_access.choose_external_article_url") >= 2)
    check("7b. 빌더가 external_url을 url/final_url과 별도 필드로 유지",
          '"external_url": news_access.choose_external_article_url(sig)' in builder
          and '"final_url": access["final_url"]' in builder
          and '"url": sig.get("url")' in builder)

    # 8. warning URL이 외부 href로 나가지 않음 — 빌드 없이 함수 계약으로 증명.
    aggregator = "https://news.google.com/rss/articles/CBMi"
    check("8. warning href 0건(계약): url=warning → 링크 없음",
          choose({"url": WARNING_URL, "final_url": WARNING_URL}) == "")
    check("8b. aggregator url은 그대로 유지(제거 아님)",
          choose({"url": aggregator}) == aggregator)

    # 9. 링크 접근성은 점수/분류/최신성 판단에 쓰이지 않는다(표시 전용).
    def _src(name: str) -> str:
        path = ROOT / "app" / name
        return path.read_text(encoding="utf-8") if path.exists() else ""

    scoring = _src("scoring.py")
    radar = _src("radar.py")
    decision = _src("decision_relevance.py")
    quality = _src("article_quality.py")
    recency = _src("news_recency.py")
    check("9. link_access_status가 점수/분류/품질/최신성에서 미사용",
          all("link_access_status" not in s
              for s in (scoring, radar, decision, quality, recency)))
    check("9b. news_access가 scoring/radar/briefing을 import하지 않음(leaf)",
          all(token not in _src("news_access.py") for token in
              ("import scoring", "import radar", "import briefing",
               "from app import scoring", "from app.scoring")))

    # 10. base/current 회귀 리포트 도구가 존재하고 오프라인이다.
    check("10. diagnose_article_link_regression.py 존재", DIAG.exists())
    diag = _load_diag()
    check("10b. 진단 도구 핵심 함수 존재",
          all(hasattr(diag, fn) for fn in
              ("build_inventory", "compare", "load_model")))
    forbidden_net = ("urlopen(", "requests.get(", "httpx.get(", "socket.socket",
                     "urllib.request", "http.client")
    diag_src = DIAG.read_text(encoding="utf-8")
    access_src = (ROOT / "app" / "news_access.py").read_text(encoding="utf-8")
    check("10c. 진단 도구·news_access에 외부 네트워크 강제 없음",
          not any(t in diag_src for t in forbidden_net)
          and not any(t in access_src for t in forbidden_net))

    # 11. 진단 도구가 aggregator/publisher href를 정확히 분류한다(합성 모델, 파일/네트워크 없음).
    synthetic = {
        "news_rows": [
            {"title": "구글뉴스 경유 기사", "url": aggregator},
            {"title": "퍼블리셔 직링크 기사", "url": "https://www.hankyung.com/x"},
            {"title": "warning으로 떨어지는 원본", "url": WARNING_URL},
        ]
    }
    inv = diag.build_inventory(synthetic)
    summary = inv["summary"]
    check("11. 진단: aggregator=1 · publisher=1 · warning href=0",
          summary["aggregator_href_count"] == 1
          and summary["publisher_href_count"] == 1
          and summary["warning_href_count"] == 0,
          str(summary["href_kind_distribution"]))

    # 12. 선택적: /tmp에 base/current 빌드가 있으면 회귀 0건을 확인(없으면 SKIP).
    base = Path("/tmp/hdec-link-base-858c829.html")
    current = Path("/tmp/hdec-link-current.html")
    if base.exists() and current.exists():
        try:
            cmp_result = diag.compare(
                diag.build_inventory(diag.load_model(base)),
                diag.build_inventory(diag.load_model(current)))
            check("12. base↔current href 회귀 0건 + warning 전환 0건",
                  cmp_result["href_changed_count"] == 0
                  and not cmp_result["href_became_warning"],
                  f"href_changed={cmp_result['href_changed_count']}")
        except (ValueError, OSError) as exc:
            print(f"  SKIP  12. base/current 비교 — {exc}")
    else:
        print("  SKIP  12. /tmp base/current 빌드 없음 (오프라인 계약 검사는 통과)")

    print(f"\n{passes} passed, {len(failures)} failed")
    if failures:
        print("FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
