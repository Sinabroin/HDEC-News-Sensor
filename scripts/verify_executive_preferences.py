#!/usr/bin/env python3
"""D6-C verifier — executive Telegram preference foundation.

Runs offline. It proves the preference store is personal-only foundation and
does not affect global sensing catalogs or live Telegram send behavior.
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import executive_preferences as prefs  # noqa: E402
from app import topic_profiles  # noqa: E402

STORE = ROOT / "data" / "executive_preferences.json"
SENDER = ROOT / "scripts" / "send_telegram.py"
DIGEST = ROOT / "scripts" / "build_telegram_digest.py"
WORKFLOW = ROOT / ".github" / "workflows" / "telegram-notify.yml"
LATEST = ROOT / "docs" / "daily" / "latest.html"
OPERATOR = ROOT / "docs" / "daily" / "operator-latest.html"

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _topic_catalog_snapshot() -> dict:
    return {
        "topics": [p.id for p in topic_profiles.all_topic_profiles()],
        "topic_queries": topic_profiles.iter_topic_queries(),
        "business_lenses": [p.id for p in topic_profiles.all_business_lenses()],
        "org_units": [t.id for t in topic_profiles.all_org_unit_tags()],
        "execution_scopes": [t.id for t in topic_profiles.all_execution_scope_tags()],
    }


def check_store_file() -> None:
    check("preference store exists", STORE.exists(), str(STORE.relative_to(ROOT)))
    if not STORE.exists():
        return
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except ValueError as exc:
        check("preference store parses as JSON", False, str(exc))
        return
    check("store schema version is 1", data.get("version") == 1)
    check("store has recipients list", isinstance(data.get("recipients"), list))
    check("committed store contains no real recipients", data.get("recipients") == [])


def check_default_shape() -> None:
    default = prefs.default_preference(" 12345 ", now="2026-06-24T00:00:00Z")
    check("default chat_id normalized", default["chat_id"] == "12345")
    check("default user_label empty", default["user_label"] == "")
    check("default delivery_mode all", default["delivery_mode"] == "all")
    check("default timestamps present",
          default["created_at"] == "2026-06-24T00:00:00Z"
          and default["updated_at"] == "2026-06-24T00:00:00Z")
    expected_keys = {"topic_profiles", "business_lenses", "org_units", "execution_scopes"}
    lens = default.get("lens_preferences")
    check("default lens_preferences has expected keys",
          isinstance(lens, dict) and set(lens) == expected_keys)
    check("default lens_preferences are empty lists",
          all(lens[key] == [] for key in expected_keys))


def check_unknown_chat_safe_default() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_pref_") as tmp:
        path = Path(tmp) / "prefs.json"
        got = prefs.get_preference("unknown-chat", path=path)
        check("unknown chat_id returns safe default", got["chat_id"] == "unknown-chat"
              and got["delivery_mode"] == "all"
              and all(not values for values in got["lens_preferences"].values()))
        check("get_preference does not create store file", not path.exists())


def check_upsert_and_sanitization() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_pref_") as tmp:
        path = Path(tmp) / "prefs.json"
        updated = prefs.upsert_preference("42", {
            "user_label": " CFO ",
            "delivery_mode": "priority_only",
            "lens_preferences": {
                "topic_profiles": ["hdec_direct", "not-a-topic", "hdec_direct"],
                "business_lenses": ["plant", "missing"],
                "org_units": ["finance_accounting"],
                "execution_scopes": ["overseas_site", ""],
            },
        }, path=path)
        check("upsert writes store file", path.exists())
        check("upsert trims user_label", updated["user_label"] == "CFO")
        check("upsert preserves allowed delivery_mode",
              updated["delivery_mode"] == "priority_only")
        lens = updated["lens_preferences"]
        check("upsert keeps only known topic ids", lens["topic_profiles"] == ["hdec_direct"])
        check("upsert keeps only known business lens ids", lens["business_lenses"] == ["plant"])
        check("upsert keeps known org/scope ids",
              lens["org_units"] == ["finance_accounting"]
              and lens["execution_scopes"] == ["overseas_site"])

        loaded = prefs.load_preferences(path)
        check("load_preferences returns chat_id-keyed map", list(loaded) == ["42"])
        bad_mode = prefs.upsert_preference("42", {"delivery_mode": "send_now"}, path=path)
        check("unknown delivery_mode resets to all", bad_mode["delivery_mode"] == "all")


def check_malformed_file_fails_safe() -> None:
    with tempfile.TemporaryDirectory(prefix="hdec_pref_") as tmp:
        path = Path(tmp) / "prefs.json"
        path.write_text("{not-json", encoding="utf-8")
        check("malformed store loads as empty", prefs.load_preferences(path) == {})
        default = prefs.get_preference("77", path=path)
        check("malformed store get_preference returns default",
              default["chat_id"] == "77" and default["delivery_mode"] == "all")
        updated = prefs.upsert_preference("77", {"user_label": "COO"}, path=path)
        data = json.loads(path.read_text(encoding="utf-8"))
        check("malformed store is safely reset on upsert",
              updated["user_label"] == "COO"
              and data.get("version") == 1
              and len(data.get("recipients") or []) == 1)


def check_catalogs_unchanged() -> None:
    before = _topic_catalog_snapshot()
    with tempfile.TemporaryDirectory(prefix="hdec_pref_") as tmp:
        path = Path(tmp) / "prefs.json"
        prefs.upsert_preference("500", {
            "lens_preferences": {
                "topic_profiles": ["hdec_direct"],
                "business_lenses": ["new_energy"],
                "org_units": ["strategy_planning"],
                "execution_scopes": ["domestic_site"],
            },
        }, path=path)
        prefs.load_preferences(path)
        prefs.get_preference("missing", path=path)
    after = _topic_catalog_snapshot()
    check("preference operations do not alter topic profile catalog",
          before["topics"] == after["topics"]
          and before["topic_queries"] == after["topic_queries"])
    check("preference operations do not alter business lens catalog",
          before["business_lenses"] == after["business_lenses"])
    check("preference operations do not alter org/scope catalogs",
          before["org_units"] == after["org_units"]
          and before["execution_scopes"] == after["execution_scopes"])


def check_no_send_path_changes() -> None:
    pref_import = "executive_preferences"
    sender_src = _read(SENDER)
    digest_src = _read(DIGEST)
    workflow_src = _read(WORKFLOW)
    check("send_telegram.py does not import preferences", pref_import not in sender_src)
    check("build_telegram_digest.py does not import preferences", pref_import not in digest_src)
    check("telegram workflow does not read preference store", pref_import not in workflow_src)
    guard_idx = sender_src.find("if not will_send")
    post_idx = sender_src.find("urlopen")
    approved_idx = sender_src.find("Send status: approved")
    check("Telegram POST remains behind review gate",
          0 <= guard_idx < approved_idx < post_idx,
          f"guard={guard_idx}, approved={approved_idx}, post={post_idx}")


def check_no_secrets_or_settings_command() -> None:
    src = _read(ROOT / "app" / "executive_preferences.py")
    combined = src + "\n" + _read(STORE)
    secret_needles = (
        "os.environ", "_load_dotenv", ".env", "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_IDS", "NAVER_CLIENT_SECRET", "OPENAI_API_KEY",
    )
    check("preference module/store do not read secrets/env",
          not any(needle in combined for needle in secret_needles))
    check("no /settings command implemented",
          "/settings" not in combined and "settings command" not in combined.lower())
    token_shape = re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{20,}")
    check("preference module/store contain no Telegram token shape",
          not token_shape.search(combined))


def check_git_protected_reports_unchanged() -> None:
    latest = _read(LATEST)
    check("latest.html remains full report, not dashboard export",
          "Executive Daily Brief" in latest and "dashboard-export:summary" not in latest
          and 'id="preview-model"' not in latest)
    proc = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", "docs/daily/operator-latest.html"],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    check("operator report unchanged from HEAD", proc.returncode == 0,
          f"rc={proc.returncode}")


def main() -> int:
    print(f"== verify_executive_preferences @ {ROOT} ==")
    check_store_file()
    check_default_shape()
    check_unknown_chat_safe_default()
    check_upsert_and_sanitization()
    check_malformed_file_fails_safe()
    check_catalogs_unchanged()
    check_no_send_path_changes()
    check_no_secrets_or_settings_command()
    check_git_protected_reports_unchanged()

    if _failures:
        print(f"\nRESULT: FAIL ({len(_failures)} failed)")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
