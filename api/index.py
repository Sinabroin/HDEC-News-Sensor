"""Vercel Python 서버리스 진입점 — 배포용 Operator API(ASGI `app`)를 재노출한다.

D7-AG-5B — 운영자 API를 Vercel에 배포하기 위한 얇은 어댑터다. Vercel은 pyproject.toml의
[tool.vercel]을 읽지 않으므로, 여기서 app/operator_api.py의 ASGI `app`을 그대로 노출하고
vercel.json이 모든 요청 경로를 이 함수로 rewrite한다(FastAPI 내부 라우터가 실제
/api/operator/* 경로를 처리). 비밀값(GH_OPERATOR_TOKEN 등)은 Vercel 프로젝트 환경변수로만
주입하며 이 파일/응답/로그에 절대 싣지 않는다. 이 어댑터는 로직을 갖지 않는다(재노출만).
"""

import os
import sys

# 프로젝트 루트(app 패키지의 부모)를 import 경로에 보장한다 — Vercel 함수 실행 cwd에 무관하게
# `app.operator_api`가 해석되도록 한다(어댑터 전용 · 비즈니스 로직 없음).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.operator_api import app  # noqa: E402  (Vercel Python이 감지하는 ASGI 심볼)

__all__ = ["app"]
