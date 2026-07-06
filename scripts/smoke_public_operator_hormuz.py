#!/usr/bin/env python3
"""Public D7-AG-3 post-deploy smoke: enabled Operator API + Hormuz transit indicator.

Read-only GET only. This script never sends an operator POST and cannot dispatch or send.
It PASSES only after the Operator API is deployed behind edge auth (operator_api_enabled=true);
before deployment it honestly FAILs the operator-enabled checks ('배포 미완료').
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

DEFAULT_DASHBOARD = (
    "https://sinabroin.github.io/HDEC-News-Sensor/daily/dashboard-latest.html"
)
ISLAND = re.compile(
    r'<script type="application/json" id="preview-model">(.*?)</script>', re.S
)
SECRET_SHAPES = (
    re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"https://[^\"'\s]*(?:webhook\.office\.com|powerautomate\.com)[^\"'\s]*", re.I),
)
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def get(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "HDEC-D7-AG-2-Smoke/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.getcode(), response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, OSError, ValueError):
        return 0, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dashboard-url", default=DEFAULT_DASHBOARD)
    args = parser.parse_args(argv)

    code, html = get(args.dashboard_url)
    check("public dashboard HTTP 200", code == 200, str(code))
    match = ISLAND.search(html)
    try:
        model = json.loads(match.group(1)) if match else {}
    except json.JSONDecodeError:
        model = {}
    base = str(model.get("operator_api_base") or "").rstrip("/")
    check("public model operator_api_enabled=true",
          model.get("operator_api_enabled") is True)
    check("public model has HTTPS Operator API base", base.startswith("https://"), base)
    check("actual 3-button UI",
          all(label in html for label in (
              "데이터 새로고침 실행", "텔레그램 전송 실행", "Teams 채널 전송 실행"
          ))
          and "fetch(base + path" in html
          and "GitHub Actions 열기" not in html)
    check("Hormuz fixed risk visible",
          'id="hormuzRiskWatch"' in html and "호르무즈 해협" in html
          and "핵심 리스크 감시" in html)
    hz = model.get("hormuz_transit") or {}
    check("Hormuz transit indicator visible (통과 선박량 · not article)",
          "호르무즈 해협 통과 선박량" in html and 'id="hormuzTransitCard"' in html
          and hz.get("data_mode") in {"live", "connected", "unavailable"})
    if hz.get("data_mode") in {"live", "connected"}:
        check("Hormuz transit is real (source + numeric count)",
              "PortWatch" in str(hz.get("source")) and isinstance(hz.get("n_total"), int))
    else:
        check("Hormuz transit honest when unavailable ('연동 미설정')",
              "연동 미설정" in html and hz.get("n_total") is None)
    check("Deal Watch standalone absent",
          '<section class="dealwatch"' not in html
          and 'id="dealWatchRows"' not in html
          and "renderDealWatch" not in html)
    check("public secret/token/webhook shapes 0",
          not any(pattern.search(html) for pattern in SECRET_SHAPES))
    check("privileged API URLs absent",
          "api.github.com" not in html
          and "api.telegram.org" not in html
          and "openapi.naver.com/v1/search/news.json" not in html)

    if base.startswith("https://"):
        health_code, health_body = get(base + "/api/operator/health")
        try:
            health = json.loads(health_body)
        except json.JSONDecodeError:
            health = {}
        check("Operator API health HTTP 200", health_code == 200, str(health_code))
        check("Operator API server credentials configured",
              health.get("operator_api_enabled") is True, repr(health))

    print()
    if failures:
        print(f"RESULT: FAIL ({len(failures)})")
        return 1
    print("RESULT: PASS — public Operator API enabled + Hormuz visible (read-only smoke)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
