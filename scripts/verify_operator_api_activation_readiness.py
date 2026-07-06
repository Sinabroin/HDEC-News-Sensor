#!/usr/bin/env python3
"""D7-AG-2 Operator API deployment readiness verifier (offline)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def main() -> int:
    api = text("app/operator_api.py")
    gateway = text("app/operator_gateway.py")
    config = text("app/config.py")
    template = text("templates/dashboard_preview.html")
    pyproject = text("pyproject.toml")

    check("Vercel minimal FastAPI entrypoint",
          'entrypoint = "app.operator_api:app"' in pyproject and "app = FastAPI(" in api)
    check("only operator router is included in deployment app",
          "app.include_router(router)" in api and "from app.main" not in api)
    for path in ("/api/operator/collect", "/api/operator/send", "/api/operator/send-teams"):
        check(f"POST {path}", f'@router.post("{path}")' in api)
    check("health smoke endpoint", '@router.get("/api/operator/health")' in api)
    check("CORS uses configured Pages origin + credentials for edge session",
          "config.OPERATOR_ALLOWED_ORIGINS" in api and "allow_credentials=True" in api)
    check("server-side authorization (edge identity allowlist + constant-time legacy secret)",
          "def authorize(" in gateway and "hmac.compare_digest" in gateway
          and "OPERATOR_ALLOWED_USERS" in config)
    check("minimum server env contract",
          all(name in config for name in (
              "GH_OPERATOR_TOKEN", "OPERATOR_ACCESS_MODE", "OPERATOR_REPO"
          )))
    check("workflow dispatch owned by server",
          "api.github.com" in gateway and 'method="POST"' in gateway)
    check("run lookup after 204 dispatch",
          "/runs?" in gateway and '"run_id"' in gateway and '"run_url"' in gateway)
    check("browser uses public base only",
          "fetch(base + path" in template and "api.github.com" not in template)
    check("link fallback removed",
          "GitHub Actions 열기" not in template and 'id="opActionLinks"' not in template)
    check("secret values absent from template",
          not re.search(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}", template))

    smoke = subprocess.run(
        [sys.executable, "scripts/smoke_operator_api_local.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    check("offline gateway smoke", smoke.returncode == 0, (smoke.stdout or smoke.stderr)[-300:])

    print()
    if failures:
        print(f"RESULT: FAIL ({len(failures)})")
        return 1
    print("RESULT: PASS — deployable minimal Operator API; production credentials remain server-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
