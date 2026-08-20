# D7-AG-5 — Operator API 컨테이너 엔트리포인트 (HTTPS 배포용 · 플랫폼 무관).
# 공개 정적 대시보드의 운영자 버튼 3개가 호출하는 서버측 실행 게이트웨이만 노출한다.
# 전체 radar 앱/DB는 담지 않는다. 운영 실행 route와 인증된 editorial import가 직접 쓰는
# 도메인 leaf만 배포한다(공격 표면 최소화).
# 비밀값(GH_OPERATOR_TOKEN 등)은 런타임 env 로만 주입하고 이미지/CMD 에 굽지 않는다.
# 프로덕션 운영자 신원 보호는 이 컨테이너 앞단(Cloudflare Access / 사내 SSO / Vercel Protection)이
# 담당하고, 서버는 신원·Origin·레이트리밋으로 fail-closed 인가한다(app/operator_gateway.py).
FROM python:3.12-slim

WORKDIR /srv

# 런타임 의존성은 FastAPI/uvicorn과 기존 editorial raster validator(Pillow)뿐이다.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 운영 실행과 인증된 editorial import에 필요한 leaf만 담는다(DB/발송/insight는 제외).
COPY app/__init__.py app/config.py app/operator_auth.py app/operator_gateway.py \
     app/operator_api.py app/editorial_article_import.py app/editorial_briefings.py \
     app/editorial_review.py app/editorial_feedback.py app/editorial_operator_review.py app/public_urls.py \
     app/news_access.py app/news_coverage.py \
     app/source_quality.py ./app/

EXPOSE 8000

# 대다수 PaaS 가 주입하는 $PORT 를 존중한다. 비밀값은 CMD 에 넣지 않는다(env 로만 주입).
CMD ["sh", "-c", "uvicorn app.operator_api:app --host 0.0.0.0 --port ${PORT:-8000}"]
