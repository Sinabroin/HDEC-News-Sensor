"""D7-P — 예약(스케줄) 라이브 다이제스트 발송 오케스트레이터 (GitHub Actions 전용).

이 스크립트는 D3I 사람 검토 게이트(scripts/send_telegram.py)를 대체하지 않고
그 '위에' 예약 자동 발송 전용 안전 계층을 한 겹 더 얹는다 (defense-in-depth):

  1) 기본은 발송하지 않는다 (dry-run / review). 후보 다이제스트만 출력한다.
  2) 실제 발송은 TELEGRAM_AUTO_SEND=1 (명시 opt-in)일 때만 시도한다. 이 플래그는
     운영자가 의도적으로 켜는 '예약 발송 승인'이며, 기본값(미설정)은 항상 발송 0건이다.
  3) 발송을 시도하더라도 — 이미 게시된 리포트 산출물(docs/daily/*.html)을 읽어
     아래 두 정직성 게이트를 통과해야만 send_telegram.py로 위임한다:
       · freshness: 생성 시각이 너무 오래됐으면(stale) 발송하지 않는다. 라이브 갱신이
         실패해 과거 리포트가 복원된 상황을 발송으로 위장하지 않기 위함.
       · live 정직성: news_data_mode != live 면 발송하지 않는다 (mock/데모를 라이브로
         보내지 않는다). 데모로 강제하려면 TELEGRAM_ALLOW_MOCK=1 을 명시해야 한다.
  4) 게이트를 통과하면 send_telegram.py(D3I)에 위임한다 — 실제 Telegram POST·토큰
     비노출·버튼 preflight는 전부 그 단일 진실원이 담당한다. 이 스크립트는 자격증명/
     토큰을 직접 읽거나 출력하지 않는다 (자식 프로세스 환경으로만 상속).

따라서 발송 조건은 (예약 opt-in) ∧ (freshness) ∧ (live) ∧ (D3I: send_mode=send +
승인 + 자격증명) 모두 참일 때뿐이다. 어느 하나라도 거짓이면 실제 발송 0건이며,
'발송됨'으로 위장하지 않는다 (rules.md §1/§4 보존).

generated_kst / latest_article_kst / news_data_mode 는 게시 산출물에서 읽어 로그에
노출한다 — 무엇을, 어느 시점 데이터로 보낼지 운영자가 검토할 수 있게.

사용법:
    python3 scripts/send_scheduled_telegram.py                       # dry-run (기본, 발송 0건)
    TELEGRAM_AUTO_SEND=1 python3 scripts/send_scheduled_telegram.py  # 게이트 통과 시 발송
    python3 scripts/send_scheduled_telegram.py --dry-run             # 강제 dry-run
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
SENDER = SCRIPTS_DIR / "send_telegram.py"

# ── 예약 발송 게이트 환경변수 ─────────────────────────────────────────────
# 미설정/빈 값/"1"이 아닌 값은 전부 '발송 안 함'(fail-closed)으로 처리한다.
AUTO_SEND_ENV = "TELEGRAM_AUTO_SEND"      # '1' 명시 opt-in일 때만 발송 시도
ALLOW_MOCK_ENV = "TELEGRAM_ALLOW_MOCK"    # '1'이면 mock/데모도 발송 허용 (기본 금지)
MAX_AGE_ENV = "TELEGRAM_MAX_AGE_HOURS"    # 생성 후 이 시간 초과면 stale (발송 거부)
NOW_ENV = "SCHEDULED_NOW"                 # 비교 기준 시각 ISO (검증/테스트 주입용)
TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_MAX_AGE_HOURS = 12.0

DEFAULT_REPORT = "docs/daily/latest.html"
DEFAULT_DASHBOARD = "docs/daily/dashboard-latest.html"

_KST = timezone(timedelta(hours=9))

EXIT_OK = 0        # dry-run 보류(기본 안전) 또는 위임 발송 성공
EXIT_ERROR = 1     # 하드 오류 (산출물 없음 / 위임 발송 실패 / 자격증명 없음)
EXIT_REFUSED = 3   # 발송 시도했으나 정직성 게이트가 거부 (stale / mock) — 시끄러운 비발송

# 빌더가 산출물에 심는 안정 계약 마커들 (HTML 렌더 세부와 무관하게 유지되는 표식).
_RE_NEWS_MODE = re.compile(r"<!--news-data-mode:([a-z_]+)-->")
_RE_PUBKST = re.compile(
    r'id="pubKst"[^>]*>\s*([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2})')
_RE_FOOTER_KST = re.compile(
    r"생성\s+([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2})\s*KST")
_RE_LATEST_KST = re.compile(r'"latest_article_kst"\s*:\s*"([^"]*)"')


def _is_true(env_name: str) -> bool:
    return os.environ.get(env_name, "").strip().lower() in TRUE_VALUES


def _coerce_now(override: str = "") -> datetime:
    """비교 기준 '현재 시각'을 tz-aware UTC로 돌려준다.

    override(또는 SCHEDULED_NOW env)가 ISO로 주어지면 그 시각을 쓴다 — 검증의 시간
    드리프트를 없애기 위함(D7-N과 동일 패턴). 없으면 실제 벽시계(UTC)."""
    raw = (override or os.environ.get(NOW_ENV, "")).strip()
    if raw:
        try:
            dt = datetime.fromisoformat(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _parse_kst(text: str):
    """'YYYY-MM-DD HH:MM' (KST 벽시계) → tz-aware datetime. 실패 시 None (가짜 시각 금지)."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return dt.replace(tzinfo=_KST)


def _resolve_path(arg_path: str) -> Path:
    p = Path(arg_path)
    return p if p.is_absolute() else (ROOT / p)


def _read_artifact(path: Path) -> dict:
    """게시 산출물(HTML)에서 정직성 메타를 추출한다. 파일이 없으면 exists=False."""
    meta = {"exists": False, "news_data_mode": "",
            "generated_kst": "", "latest_article_kst": ""}
    try:
        html = path.read_text(encoding="utf-8")
    except OSError:
        return meta
    meta["exists"] = True
    m = _RE_NEWS_MODE.search(html)
    if m:
        meta["news_data_mode"] = m.group(1)
    m = _RE_PUBKST.search(html) or _RE_FOOTER_KST.search(html)
    if m:
        meta["generated_kst"] = m.group(1)
    m = _RE_LATEST_KST.search(html)
    if m:
        meta["latest_article_kst"] = m.group(1)
    return meta


def load_meta(report_path: str, dashboard_path: str) -> dict:
    """리포트/대시보드 산출물에서 메타를 합친다 (대시보드 우선 — pubKst/latest가 깨끗)."""
    dash = _read_artifact(_resolve_path(dashboard_path))
    rep = _read_artifact(_resolve_path(report_path))
    return {
        "news_data_mode": dash["news_data_mode"] or rep["news_data_mode"],
        "generated_kst": dash["generated_kst"] or rep["generated_kst"],
        "latest_article_kst": dash["latest_article_kst"] or rep["latest_article_kst"],
        "dashboard_exists": dash["exists"],
        "report_exists": rep["exists"],
        "any_exists": dash["exists"] or rep["exists"],
    }


def _build_digest_preview() -> str:
    """후보 다이제스트 메시지를 만든다 (dry-run 미리보기). APP_MODE=mock에서는 오프라인,
    NEWS_MODE=live(네트워크 가용)에서는 라이브로 동작한다. 토큰/발송 0건."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from build_telegram_digest import build_digest_message
    return build_digest_message()


def _delegate_to_sender() -> int:
    """게이트 통과 후 send_telegram.py(D3I)에 실제 발송을 위임한다 — 단일 진실원.

    예약 컨텍스트 승인으로 TELEGRAM_SEND_MODE=send + REVIEW_APPROVED=true 를 자식
    환경에 주입한다. 토큰/chat id는 부모 환경에서 그대로 상속될 뿐, 이 스크립트가
    읽거나 출력하지 않는다. 먼저 --preflight(버튼 타겟 검증)를 돌리고, 통과해야 실제
    발송 단계로 넘어간다. 자식 출력(발송 집계 등, 토큰 비노출)은 그대로 흘려보낸다.
    """
    child = dict(os.environ)
    child["TELEGRAM_SEND_MODE"] = "send"
    child["REVIEW_APPROVED"] = "true"
    pre = subprocess.run([sys.executable, str(SENDER), "--preflight"],
                         env=child, cwd=str(ROOT))
    if pre.returncode != 0:
        print("AUTO-SEND 중단: 버튼 preflight 실패 — 발송하지 않음", file=sys.stderr)
        return pre.returncode or EXIT_ERROR
    sent = subprocess.run([sys.executable, str(SENDER)], env=child, cwd=str(ROOT))
    return sent.returncode


def _resolve_max_age(cli_value) -> float:
    if cli_value is not None:
        return cli_value
    try:
        return float(os.environ.get(MAX_AGE_ENV, "").strip() or DEFAULT_MAX_AGE_HOURS)
    except ValueError:
        return DEFAULT_MAX_AGE_HOURS


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="HDEC Executive Radar — 예약 라이브 다이제스트 발송 (기본 dry-run)")
    parser.add_argument("--report", default=DEFAULT_REPORT,
                        help="게시된 전체 리포트 HTML 경로")
    parser.add_argument("--dashboard", default=DEFAULT_DASHBOARD,
                        help="게시된 요약 대시보드 HTML 경로")
    parser.add_argument("--now", default="",
                        help="비교 기준 시각 ISO (테스트용; 기본은 실제 벽시계 UTC)")
    parser.add_argument("--max-age-hours", type=float, default=None,
                        help=f"생성 후 이 시간 초과면 발송 거부 "
                             f"(기본 {DEFAULT_MAX_AGE_HOURS:g}h, env {MAX_AGE_ENV})")
    parser.add_argument("--dry-run", action="store_true",
                        help="TELEGRAM_AUTO_SEND과 무관하게 강제 dry-run (발송 0건)")
    args = parser.parse_args(argv)

    auto_send = _is_true(AUTO_SEND_ENV) and not args.dry_run
    allow_mock = _is_true(ALLOW_MOCK_ENV)
    now = _coerce_now(args.now)
    max_age = _resolve_max_age(args.max_age_hours)

    meta = load_meta(args.report, args.dashboard)
    mode = meta["news_data_mode"] or "(unknown)"
    generated_kst = meta["generated_kst"] or "(unknown)"
    latest_kst = meta["latest_article_kst"] or "(unavailable)"
    gen_dt = _parse_kst(meta["generated_kst"])
    age_hours = (now - gen_dt).total_seconds() / 3600.0 if gen_dt is not None else None
    age_str = f"{age_hours:.1f}" if age_hours is not None else "(unknown)"

    print("== 예약 라이브 다이제스트 (D7-P) ==")
    print(f"news_data_mode: {mode}")
    print(f"generated_kst: {generated_kst}")
    print(f"latest_article_kst: {latest_kst}")
    print(f"artifact_age_hours: {age_str}")
    print(f"auto_send_requested: {'true' if auto_send else 'false'} "
          f"({AUTO_SEND_ENV}={'set' if _is_true(AUTO_SEND_ENV) else 'unset'})")
    print(f"allow_mock: {'true' if allow_mock else 'false'} · max_age_hours: {max_age:g}")

    # ── 기본: dry-run (발송 0건) ──────────────────────────────────────────
    if not auto_send:
        print(f"Send status: dry-run — 예약 발송 비활성 ({AUTO_SEND_ENV}=1 일 때만 발송) · "
              "발송하지 않음")
        try:
            message = _build_digest_preview()
        except Exception as exc:  # 미리보기 빌드 실패는 발송과 무관 — 막지 않는다.
            print(f"(미리보기 다이제스트 빌드 건너뜀: {type(exc).__name__})", file=sys.stderr)
        else:
            recipients = bool(os.environ.get("TELEGRAM_CHAT_IDS", "").strip())
            print(f"recipients_configured: {'true' if recipients else 'false'}")
            print("--- 후보 다이제스트 (검토용, 발송 안 됨) ---")
            print(message)
        return EXIT_OK

    # ── 발송 시도 (TELEGRAM_AUTO_SEND=1) — 정직성 게이트를 차례로 통과해야 함 ──────
    if not meta["any_exists"]:
        print("AUTO-SEND 거부: 게시 산출물을 찾을 수 없음 — 발송하지 않음 "
              f"(report={args.report}, dashboard={args.dashboard})", file=sys.stderr)
        return EXIT_ERROR

    if gen_dt is None:
        print("AUTO-SEND 거부: 생성 시각(generated_kst)을 읽을 수 없어 stale 판별 불가 · "
              "발송하지 않음", file=sys.stderr)
        return EXIT_REFUSED

    if age_hours is not None and age_hours > max_age:
        print(f"AUTO-SEND 거부: 산출물이 오래됨(stale) — 생성 후 {age_str}h > 허용 {max_age:g}h · "
              "발송하지 않음 (라이브 갱신 실패로 과거 리포트가 복원된 것일 수 있음)",
              file=sys.stderr)
        return EXIT_REFUSED

    if meta["news_data_mode"] != "live" and not allow_mock:
        print(f"AUTO-SEND 거부: news_data_mode={mode} (live 아님) — mock/데모를 라이브로 "
              f"발송하지 않음 · 발송하지 않음 (데모로 강제하려면 {ALLOW_MOCK_ENV}=1)",
              file=sys.stderr)
        return EXIT_REFUSED

    label = "live" if meta["news_data_mode"] == "live" else f"{mode}+ALLOW_MOCK"
    print(f"AUTO-SEND: 정직성 게이트 통과 ({label}, age {age_str}h) — "
          "send_telegram.py(D3I)에 위임")
    return _delegate_to_sender()


if __name__ == "__main__":
    raise SystemExit(main())
