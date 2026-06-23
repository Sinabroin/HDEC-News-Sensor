#!/usr/bin/env python3
"""D6-A/B — Executive summary dashboard static export.

`/dashboard-preview` is served from `templates/dashboard_preview.html`. This builder
reuses that checked-in template and publishes the same self-contained dashboard shell
to `docs/daily/dashboard-latest.html` for public Pages links.

It does not read secrets, call the network, touch radar.db, or copy any design artifact.
The preview's data honesty labels remain intact: demo/mock values stay marked as demo,
unavailable market data stays unavailable, and the full daily report remains
`docs/daily/latest.html`.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_TEMPLATE = ROOT / "templates" / "dashboard_preview.html"
DEFAULT_OUTPUT = "docs/daily/dashboard-latest.html"
EXPORT_TITLE = "HDEC Executive Radar — 요약 대시보드"
EXPORT_MARKER = "dashboard-export:summary"


def render_dashboard_html() -> str:
    """Return the standalone dashboard HTML used for the public summary link."""
    try:
        html = SOURCE_TEMPLATE.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: dashboard template missing: {SOURCE_TEMPLATE}", file=sys.stderr)
        raise SystemExit(1) from exc

    html = html.replace(
        "<title>HDEC Executive Radar — 대시보드 미리보기 (Preview)</title>",
        f"<title>{EXPORT_TITLE}</title>",
        1,
    )
    if EXPORT_MARKER not in html:
        html = html.replace(
            "<!DOCTYPE html>\n",
            "<!DOCTYPE html>\n"
            f"<!--{EXPORT_MARKER} source=templates/dashboard_preview.html "
            "target=docs/daily/dashboard-latest.html-->\n",
            1,
        )
    return html


def dashboard_metadata(html: str) -> dict:
    """Machine-readable metadata without embedding the HTML body."""
    return {
        "title": EXPORT_TITLE,
        "source_template": str(SOURCE_TEMPLATE.relative_to(ROOT)),
        "default_output": DEFAULT_OUTPUT,
        "html_chars": len(html),
        "has_export_marker": EXPORT_MARKER in html,
        "has_preview_model": 'id="preview-model"' in html,
        "has_data_honesty_labels": (
            "데모 데이터" in html
            and "현재 체결값 아님" in html
            and "미연동" in html
        ),
    }


def format_summary(html: str) -> str:
    meta = dashboard_metadata(html)
    return "\n".join([
        "== HDEC Executive Radar — Summary Dashboard Export ==",
        f"[source] {meta['source_template']}",
        f"[default_output] {meta['default_output']}",
        f"[html_chars] {meta['html_chars']}",
        "[contract] standalone preview structure · demo/missing-data labels preserved",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="HDEC Executive Radar — 정적 요약 대시보드 export 빌더")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true",
                       help="export 요약을 출력한다 (파일 쓰기 없음)")
    group.add_argument("--json", action="store_true",
                       help="기계 검증용 메타데이터 JSON을 출력한다")
    group.add_argument("--output", metavar="PATH",
                       help=f"HTML 파일을 PATH에 생성한다 (예: {DEFAULT_OUTPUT})")
    args = parser.parse_args(argv)

    html = render_dashboard_html()

    if args.json:
        print(json.dumps(dashboard_metadata(html), ensure_ascii=False, indent=2))
        return 0

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        print(f"dashboard written: {out_path} ({len(html)} chars)")
        return 0

    print(format_summary(html))
    if args.dry_run:
        print(f"[dry-run] html_chars={len(html)} (쓰기 없음)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
