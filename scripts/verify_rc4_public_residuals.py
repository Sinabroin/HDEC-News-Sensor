#!/usr/bin/env python3
"""D7-AE-RC4 public raw HTML and evidence-only business-lens contract.

Offline verifier. It builds public and operator fixtures in temporary paths, then
checks the committed public dashboard. No network, DB mutation, send, or deploy.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_static_dashboard.py"
TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
DASHBOARD = ROOT / "docs" / "daily" / "dashboard-latest.html"

FORBIDDEN_RAW = (
    "기상 데이터 소스 미연동",
    "NON-PRODUCTION PREVIEW",
    "데모/mock 데이터",
    "데모 데이터",
)
OPERATOR_LABELS = (
    "데이터 새로고침 실행",
    "텔레그램 전송 실행",
    "Teams 채널 전송 실행",
)
BUSINESS_LENSES = (
    "civil_infrastructure", "building_housing", "plant", "new_energy",
    "development_business", "global_business", "safety_quality",
)
WEATHER_UNAVAILABLE = "기상 데이터 미수신 — 공개 예보 API 응답 없음. 값을 만들지 않습니다."

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


def _model(html: str) -> dict:
    match = re.search(
        r'<script type="application/json" id="preview-model">(.*?)</script>',
        html,
        re.S,
    )
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except ValueError:
        return {}


def _rows(model: dict) -> list[dict]:
    rows = list(model.get("news_rows") or []) + list(model.get("ai_rows") or [])
    if model.get("featured_row"):
        rows.append(model["featured_row"])
    for bank in (model.get("lens_banks") or {}).values():
        rows.extend(bank or [])
    unique = {}
    for row in rows:
        key = (row.get("article_id") or "", row.get("title") or "", row.get("url") or "")
        unique[key] = row
    return list(unique.values())


def check_public(html: str, label: str) -> None:
    hits = [token for token in FORBIDDEN_RAW if token in html]
    check(f"R1[{label}]: public raw 실패 문자열 0건", not hits, str(hits))
    check(f"R2[{label}]: public operator JS는 미연결 fail-closed 분기 포함",
          'id="opctl-js"' in html and 'setStatus("Operator API 미연결"' in html
          and "setBtns(true)" in html)
    check(
        f"R3[{label}]: 실행 버튼 3개 visible + 링크 fallback 없음",
        'id="opActionLinks"' not in html and 'id="opApiControls"' in html
        and all(label_text in html for label_text in (
            "데이터 새로고침 실행", "텔레그램 전송 실행", "Teams 채널 전송 실행"
        )),
    )
    model = _model(html)
    check(f"R4[{label}]: preview-model JSON 파싱", bool(model))

    weather_mode = model.get("weather_data_mode")
    if weather_mode == "unavailable":
        check(
            f"W1[{label}]: unavailable 문구 단일 계약",
            WEATHER_UNAVAILABLE in html
            and model.get("weather_unavailable_reason") == WEATHER_UNAVAILABLE,
        )
        check(
            f"W2[{label}]: unavailable 기상값 미생성",
            not (model.get("weather_rows") or []),
        )
    elif weather_mode == "live":
        check(
            f"W1[{label}]: live 기상 source/as_of 존재",
            bool(model.get("weather_source")) and bool(model.get("weather_updated_at"))
            and bool(model.get("weather_rows")),
        )
    else:
        check(f"W1[{label}]: weather mode 유효", False, str(weather_mode))

    rows = _rows(model)
    check(f"L1[{label}]: 검증할 기사 행 존재", bool(rows), str(len(rows)))
    missing_reasons = []
    invalid_reasons = []
    fanout_rows = []
    for row in rows:
        lenses = set(row.get("lens") or [])
        reasons = row.get("lens_reasons") or {}
        business = sorted(lenses.intersection(BUSINESS_LENSES))
        if len(business) > 2:
            fanout_rows.append((row.get("title"), business))
        for lens in business:
            reason = reasons.get(lens)
            if not reason:
                missing_reasons.append((lens, row.get("title")))
                continue
            reason_text = " ".join(reason) if isinstance(reason, list) else str(reason)
            if any(bad in reason_text for bad in ("분류 폴백", "AI 밸류체인", "워치리스트 현장")):
                invalid_reasons.append((lens, row.get("title"), reason_text))
    check(
        f"L2[{label}]: 모든 사업 렌즈에 lens_reasons 존재",
        not missing_reasons,
        str(missing_reasons[:2]),
    )
    check(
        f"L3[{label}]: category/value-chain/site 폴백 사업 근거 0건",
        not invalid_reasons,
        str(invalid_reasons[:2]),
    )
    check(
        f"L4[{label}]: 기사당 사업 렌즈 2개 이하",
        not fanout_rows,
        str(fanout_rows[:2]),
    )
    counts = {
        lens: sum(lens in (row.get("lens") or []) for row in rows)
        for lens in BUSINESS_LENSES
    }
    count_values = list(counts.values())
    all_major_capped = bool(count_values) and all(value == 10 for value in count_values)
    check(
        f"L5[{label}]: 주요 사업 렌즈 일괄 10건 fan-out 아님",
        not all_major_capped and len(set(count_values)) > 1,
        str(counts),
    )


def _run_build(output: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "APP_MODE": "mock", "NEWS_MODE": "mock", "PYTHONHASHSEED": "0"}
    return subprocess.run(
        [sys.executable, str(BUILDER), "--output", str(output), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def check_build_contracts() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_rc4_") as tmp:
        public_out = Path(tmp) / "public.html"
        proc = _run_build(public_out)
        if check(
            "B1: fresh public mock build 성공",
            proc.returncode == 0 and public_out.exists(),
            (proc.stderr or "")[-300:],
        ):
            check_public(public_out.read_text(encoding="utf-8"), "fresh-public")

        operator_out = Path(tmp) / "operator.html"
        proc = _run_build(
            operator_out,
            "--operator-api-base",
            "https://operator.example.invalid",
        )
        if check(
            "B2: operator build 성공",
            proc.returncode == 0 and operator_out.exists(),
            (proc.stderr or "")[-300:],
        ):
            operator = operator_out.read_text(encoding="utf-8")
            check(
                "B3: operator build는 실행 UI/JS 유지",
                'id="opctl-js"' in operator
                and all(token in operator for token in OPERATOR_LABELS),
            )
            check(
                "B4: operator API base가 JSON island에만 주입",
                _model(operator).get("operator_api_base")
                == "https://operator.example.invalid",
            )


def check_direct_lens_contract() -> None:
    spec = importlib.util.spec_from_file_location("rc4_dashboard_builder", BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    injected = {
        "title": "OpenAI Broadcom custom AI chip partnership",
        "snippet": "새 AI 칩 공급 계약",
        "source": "Example",
        "category": "dc_power",
        "category_label": "중동 플랜트 New Energy 안전 품질 개발사업",
        "radar_section": "business_overseas",
    }
    lenses, reasons = module._lens_for_with_reasons(injected)
    check(
        "D1: category/section/value-chain만으로 사업 렌즈 주입 안 함",
        not set(lenses).intersection(BUSINESS_LENSES),
        f"lenses={lenses} reasons={reasons}",
    )
    safety = {
        "title": "현대건설 현장 중대재해 발생…고용부 특별감독 착수",
        "snippet": "",
        "source": "연합뉴스",
    }
    lenses, reasons = module._lens_for_with_reasons(safety)
    check(
        "D2: 강한 안전 근거는 safety_quality + reason",
        "safety_quality" in lenses and bool(reasons.get("safety_quality")),
        f"lenses={lenses} reasons={reasons}",
    )
    weak_global = {
        "title": "중동 문화 행사 관람객 증가",
        "snippet": "지역 축제 소식",
        "source": "Example",
    }
    lenses, _ = module._lens_for_with_reasons(weak_global)
    check("D3: 해외 마커만으로 global_business 불가", "global_business" not in lenses)
    strong_global = {
        "title": "중동 인프라 프로젝트 발주 재개",
        "snippet": "해외 건설 수주 시장 회복",
        "source": "연합뉴스",
    }
    lenses, reasons = module._lens_for_with_reasons(strong_global)
    check(
        "D4: 해외 마커+사업 맥락이면 global_business만 + reason",
        "global_business" in lenses and "civil_infrastructure" not in lenses
        and bool(reasons.get("global_business")),
        f"lenses={lenses}",
    )


def check_partition_contract() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    keys = re.findall(
        r'\{\s*key:\s*"(all|new|order|finance|policy|competitor|brand|global_press|etc)"'
        r',\s*label:',
        template,
    )
    expected = {
        "all", "new", "order", "finance", "policy",
        "competitor", "brand", "global_press", "etc",
    }
    check("P1: 2차 레이블 정의가 all+8개 partition", set(keys) == expected, str(keys))
    check(
        "P2: exclusive rowSecondaryKey + 0건 pill 숨김 + 합산 계약",
        "function rowSecondaryKey" in template
        and "0건 pill은 렌더하지 않는다" in template
        and "전체 = 하위 레이블 합산" in template,
    )


def main() -> int:
    print(f"== verify_rc4_public_residuals @ {ROOT} ==")
    check_build_contracts()
    check_direct_lens_contract()
    check_partition_contract()
    if check("C1: committed dashboard 존재", DASHBOARD.exists()):
        check_public(DASHBOARD.read_text(encoding="utf-8"), "committed")
    print()
    if _failures:
        print(f"RESULT: FAIL ({len(_failures)} 항목)")
        for failure in _failures:
            print(f"  - {failure}")
        return 1
    print("RESULT: PASS — RC4 public raw residuals / operator split / weather / lens evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
