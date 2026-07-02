# D7-AD-X 뉴스 원문 접근 및 내부 reader 정책

## 1. 현재 문제

사내망에서 외부 뉴스의 원문 링크를 열면 기사 대신
`https://www.hdec.kr/warning/WARNING.jpg` 또는 HDEC warning 페이지로 이동하는 사례가
있다. 일반 언론사·포털 기사도 유해성 때문이 아니라 업무시간 정책, URL 분류 오탐,
광고·추적 redirect, aggregator 경유 방식 때문에 차단될 수 있다.

이 문제는 레이더가 수집한 기사의 중요도와 별개다. 원문 접근이 막혀도 기사 row,
제목·요약·출처·발행시각·수집시각·판단근거를 삭제하거나 점수에서 감점하지 않는다.

## 2. 차단 분류

보안 검토에서는 다음 원인을 구분한다.

- 유해사이트 차단: malware, phishing, 성인, 도박 등 명시적 보안 category
- 업무시간 정책 차단: 정상 사이트지만 시간대 또는 업무 category 정책으로 제한
- 포털/언론사 오분류: publisher 또는 portal이 유해 category로 잘못 분류
- 광고/추적 URL 차단: click/track URL이나 광고 domain이 중간 경로에 포함
- aggregator redirect 차단: Google News, Daum, Naver 등 경유 URL 처리 중 제한
- 실제 unreachable: timeout, DNS/HTTP 오류, 삭제된 기사

`app/news_access.py`는 이미 관측된 original/final URL, status code, content type,
body sample만 분류한다. 빌드와 CI에서 외부 접속을 수행하지 않으며 proxy/VPN/bypass
링크를 만들지 않는다.

## 3. Source inventory 계약

`build_source_inventory(articles)` 결과는 domain별로 다음 필드를 보존한다.

| 필드 | 용도 |
|---|---|
| domain / source | 원 출처 식별 |
| original_url / final_url | 요청 URL과 최종 redirect 비교 |
| access_status | unknown, ok, corp_blocked, redirected, timeout, error |
| blocked_reason | warning signature 또는 관측된 block policy |
| sample_count | 같은 domain의 점검 표본 수 |
| source_type | publisher, portal, rss, api, search, licensed_db, unknown |
| collection_method | rss, api, search_result, portal_result, manual_report, unknown |
| suggested_policy | allow_candidate, review_needed, keep_blocked, unknown |

분류 원칙은 다음과 같다.

- `allow_candidate`: 정상 publisher/RSS/API/licensed DB가 사내 warning으로 redirect된
  사례. 허용 확정이 아니라 보안팀 검토 후보이다.
- `review_needed`: portal/search/aggregator redirect, 광고·추적 URL, timeout/HTTP 오류처럼
  원인 확인이 필요한 사례이다.
- `keep_blocked`: malware/phishing 등 명시적 유해 category 또는 warning endpoint 자체를
  원문으로 제출한 사례이다.
- `unknown`: 접근 관측이나 보안 category 근거가 아직 없다.

## 4. 보안팀 점검 항목

각 표본에 대해 아래 항목을 access log와 함께 확인한다.

1. source domain과 original URL
2. URL category 및 분류 공급자
3. final redirect chain과 HDEC warning 최종 URL
4. 적용된 block policy name과 업무시간 조건
5. business justification: `executive news radar`
6. allow candidate 여부와 승인 범위
7. fulltext 허용 여부
8. metadata only 허용 여부

검토 결과는 원문 URL, 점검시각, final URL, status, policy name을 포함해 보존한다.
인증 header, cookie, API key, token은 inventory나 정적 HTML에 저장하지 않는다.

## 5. 권장 접근 정책

- 사용자 브라우저 전체 허용보다 뉴스 레이더의 승인된 수집 경로 또는 내부 reader/gateway에
  범위를 한정한다.
- 언론사 domain은 `allow_candidate` inventory를 기반으로 보안팀 승인 후 관리한다.
- 내부 reader는 저장된 제목·snippet·출처·시각·판단근거를 표시한다. 저작권 또는 계약상
  권한 없이 기사 fulltext를 저장하지 않는다.
- metadata-only 허용과 fulltext 허용을 분리한다. licensed DB는 계약 범위를 별도 확인한다.
- source provenance와 접근성 점검 log를 보존한다.
- arbitrary URL을 가져오는 open proxy endpoint는 만들지 않는다. 서버 측 fetch가 필요해질
  경우 승인된 domain 목록, redirect 재검증, 사설 IP 차단, 응답 크기·시간 제한을 별도
  보안 설계로 승인받는다.

## 6. 내부 reader UX

기본 CTA `기사 보기`는 dashboard 내부 reader를 연다. reader는 제목, 출처, 발행·수집시각,
요약/snippet, 레이더 판단근거, 왜 중요한가, 관련 렌즈, 원문 URL, source domain/type,
collection method, `link_access_status`와 access note를 표시한다.

`원문 사이트`는 새 탭으로 여는 보조 CTA다. `corp_blocked`이면 “외부 원문은 사내망
정책상 제한될 수 있음”을 표시하되 내부 reader와 기사 신호는 유지한다. 접근성 판정은
즉시확인/AI/리스크 라우팅과 독립이다.
