#!/usr/bin/env python3
"""D7-AD-X 뉴스 접근성/내부 reader 계약 검증 (완전 오프라인)."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import news_access  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "news_source_access_cases.json"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
DOC = ROOT / "docs" / "operations" / "D7ADX_NEWS_SOURCE_ACCESS_POLICY.md"

failures = []
passes = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passes
    if ok:
        passes += 1
        print(f"  PASS  {label}")
    else:
        failures.append(label)
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def main() -> int:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    results = {
        case["id"]: news_access.classify_link_access(
            case["original_url"],
            final_url=case.get("final_url"),
            status_code=case.get("status_code"),
            content_type=case.get("content_type"),
            body_sample=case.get("body_sample"),
        )
        for case in cases
    }
    warning = results["hdec_warning_redirect"]
    check("1. WARNING.jpg fixture → corp_blocked",
          news_access.detect_corp_warning_url(
              "https://www.hdec.kr/warning/WARNING.jpg")
          and warning["link_access_status"] == "corp_blocked",
          str(warning))

    articles = [{
        "id": "keep-me",
        "title": "정상 언론사 업무 기사",
        "source": "Example News",
        "url": cases[0]["original_url"],
        "final_url": cases[0]["final_url"],
        "status_code": 200,
        "snippet": "레이더 판단에 필요한 수집 요약",
    }]
    inventory = news_access.build_source_inventory(articles)
    scoring_src = (ROOT / "app" / "scoring.py").read_text(encoding="utf-8")
    radar_src = (ROOT / "app" / "radar.py").read_text(encoding="utf-8")
    check("2. corp_blocked는 기사 삭제/점수 사유가 아님",
          len(articles) == 1 and len(inventory) == 1
          and "link_access_status" not in scoring_src
          and "link_access_status" not in radar_src)

    template = TEMPLATE.read_text(encoding="utf-8")
    check("3. dashboard에 내부 reader/openArticleReader 존재",
          'id="articleReaderOv"' in template and "function openArticleReader" in template)
    check("4. 기본 CTA가 기사 보기",
          'onclick="return openArticleReader(this);">기사 보기</button>' in template)
    check("5. 외부 URL은 원문 사이트 보조 링크",
          "원문 사이트 ↗" in template and 'target="_blank"' in template
          and 'rel="noopener noreferrer"' in template)

    check("6. source inventory 문서 존재", DOC.exists())
    doc = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
    check("7. 문서에 정책 후보 3종 구분",
          all(value in doc for value in
              ("allow_candidate", "review_needed", "keep_blocked")))
    check("8. model/UI에 link_access_status 존재",
          "link_access_status" in template
          and "link_access_status" in
          (ROOT / "scripts" / "build_static_dashboard.py").read_text(encoding="utf-8"))

    briefing_src = (ROOT / "app" / "briefing.py").read_text(encoding="utf-8")
    check("9. 기사 중요도/라우팅과 링크 접근성 분리",
          "from app import news_access" not in scoring_src
          and "from app import news_access" not in radar_src
          and "link_access_status" not in briefing_src
          and "접근 상태는 기사 중요도" in template)

    # 값이 박힌 secret만 탐지한다. 보안 설명의 일반 단어 token/key는 실패 사유가 아니다.
    secret_patterns = (
        r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*[\"'][A-Za-z0-9_-]{16,}",
        r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b",
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    )
    check("10. HTML/JS에 hardcoded secret/API key/token 없음",
          not any(re.search(pattern, template) for pattern in secret_patterns))

    main_src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    proxy_patterns = (
        r"@app\.(?:get|post)\([\"'][^\"']*(?:proxy|fetch-url)",
        r"(?:url|target_url)\s*:\s*str.*urlopen",
    )
    check("11. arbitrary open proxy endpoint 없음",
          not any(re.search(pattern, main_src, re.S | re.I) for pattern in proxy_patterns)
          and "open proxy endpoint" in doc)

    access_src = (ROOT / "app" / "news_access.py").read_text(encoding="utf-8")
    forbidden_network = ("urlopen(", "requests.get(", "httpx.get(", "socket.")
    check("12. CI/offline에서 외부 네트워크 강제 없음",
          not any(value in access_src for value in forbidden_network))

    check("13. source/collection taxonomy 계약",
          news_access.SOURCE_TYPES == {
              "publisher", "portal", "rss", "api", "search", "licensed_db", "unknown"}
          and news_access.COLLECTION_METHODS == {
              "rss", "api", "search_result", "portal_result", "manual_report", "unknown"})
    check("14. corp_blocked publisher는 allow 후보이며 row 보존",
          inventory[0]["access_status"] == "corp_blocked"
          and inventory[0]["suggested_policy"] == "allow_candidate"
          and inventory[0]["sample_count"] == 1,
          str(inventory))

    # D7-AD-X 회귀 방지: 외부 '원문 사이트' href는 warning URL을 절대 쓰지 않고,
    # final_url(진단)보다 원본 URL을 우선한다. (상세 계약은
    # verify_article_link_regression_contract.py)
    choose = getattr(news_access, "choose_external_article_url", None)
    check("15. choose_external_article_url: warning 배제 + 원본 우선",
          callable(choose)
          and choose({"url": "https://www.hdec.kr/warning/WARNING.jpg"}) == ""
          and choose({"original_url": "https://pub.example/a",
                      "final_url": "https://www.hdec.kr/warning/WARNING.jpg"})
          == "https://pub.example/a"
          and choose({"url": "https://pub.example/u",
                      "final_url": "https://pub.example/f"}) == "https://pub.example/u")

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
