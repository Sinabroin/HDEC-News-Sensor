# D7-AG-5 — Operator API 컨테이너 엔트리포인트 (HTTPS 배포용 · 플랫폼 무관).
# 공개 정적 대시보드의 운영자 버튼 3개가 호출하는 서버측 실행 게이트웨이만 노출한다.
# 전체 radar 앱/DB는 담지 않는다 — operator_api 는 config·operator_gateway 만 import 하므로
# 운영 실행 route(collect/send/send-teams/health)만 배포된다(공격 표면 최소화).
# 비밀값(GH_OPERATOR_TOKEN 등)은 런타임 env 로만 주입하고 이미지/CMD 에 굽지 않는다.
# 프로덕션 운영자 신원 보호는 이 컨테이너 앞단(Cloudflare Access / 사내 SSO / Vercel Protection)이
# 담당하고, 서버는 신원·Origin·레이트리밋으로 fail-closed 인가한다(app/operator_gateway.py).
FROM python:3.12-slim

WORKDIR /srv

# 런타임 의존성은 fastapi + uvicorn 뿐이다(requirements.txt).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 운영 실행에 필요한 leaf 모듈만 담는다(수집기/DB/발송/insight 로직은 포함하지 않는다).
COPY app/__init__.py app/config.py app/operator_gateway.py app/operator_api.py ./app/

EXPOSE 8000

# 대다수 PaaS 가 주입하는 $PORT 를 존중한다. 비밀값은 CMD 에 넣지 않는다(env 로만 주입).
CMD ["sh", "-c", "uvicorn app.operator_api:app --host 0.0.0.0 --port ${PORT:-8000}"]
