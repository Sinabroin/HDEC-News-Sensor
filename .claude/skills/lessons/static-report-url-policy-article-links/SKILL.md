---
name: static-report-url-policy-article-links
description: Apply when the static report (docs/daily/latest.html) or any generated HTML must include clickable original-article links while still banning external resources. The old P0-B5 verifier forbade ALL http/https in the HTML; live news needs real article href links. Allow http(s) only inside href, keep everything else blocked.
---

## 발견 (P0-C1)

P0-B5의 `verify_static_report.py`는 "외부 리소스 없음 = HTML에 http/https 0건"으로
정적 페이지를 보장했다. 그런데 P0-C1에서 실뉴스 기사로 연결되는 **원문 링크(href)**가
반드시 필요해지면서 이 하드룰이 정면 충돌했다. http/https를 전부 막으면 클릭 가능한
원문 링크를 넣을 수 없고, 그냥 허용하면 외부 script/css/img/iframe/CDN까지 열린다.

## 원인

"외부 리소스 0건"과 "기사 원문 링크"는 둘 다 정당한 요구지만, URL 존재 여부만으로는
구분되지 않는다. 구분 기준은 **컨텍스트(어떤 속성/태그에 쓰였는가)**다.

## 재발 방지 규칙

- URL 정책은 "http/https 0건"이 아니라 **"href 속성값만 외부 URL 허용, 그 외 0건"**으로
  검사한다: `href="..."` 값을 먼저 제거한 뒤 남는 http/https를 위반으로 잡는다
  (`_url_policy_violations`). 외부 태그(`<script`/`<link `/`<img`/`<iframe`/`<object`/
  `<embed`)와 `@import`/`url(http`/`src=http`도 별도로 차단한다.
- 외부 `href` 앵커는 반드시 `target="_blank"` + `rel="noopener noreferrer"`를 갖는다
  (`_external_anchor_safety`로 검사).
- 랜딩 페이지(`docs/index.html`)는 기사 링크가 없으므로 기존 "http/https 0건" 하드룰을
  그대로 유지한다 — 정책 완화는 기사 링크가 필요한 리포트 본문에만 적용한다.
- 공개 게시하는 커밋 스냅샷은 **mock**으로 유지한다(재현/검증 가능). 실뉴스 링크가 있는
  리포트는 `NEWS_MODE=live`로 운영자가 직접 생성한다.
