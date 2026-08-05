#!/usr/bin/env python3
"""R4-R11 §4 — UTF-8 acceptance-artifact verifier.

Fails when any generated acceptance artifact (evidence HTML/JSON/text/logs,
Teams message text, evidence scripts) is not honest UTF-8:

* invalid UTF-8 byte sequences;
* U+FFFD replacement characters;
* known mojibake byte-decoding patterns:
  - the CP949-as-UTF-8 classic ``占쏙옙``;
  - UTF-8-bytes-read-as-CP949 double decoding (e.g. ``건설`` → ``嫄댁꽕``,
    ``게시`` → ``寃뚯떆``), detected by the strict roundtrip
    ``text.encode("cp949").decode("utf-8")`` yielding Hangul — real Korean
    text never survives that roundtrip.

Offline and read-only: network 0, SMTP 0, Teams 0, Telegram 0, state writes 0.

Usage: verify_utf8_acceptance.py --root <acceptance-dir> [--root <dir> ...]
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

TEXT_SUFFIXES = {
    ".css", ".csv", ".htm", ".html", ".js", ".json", ".log", ".md", ".py",
    ".svg", ".toml", ".txt", ".xml", ".yaml", ".yml",
}
MAX_BYTES = 32 * 1024 * 1024
_CP949_CLASSIC = "占쏙옙"
_NON_ASCII_RUN = re.compile(r"[^\x00-\x7f]{2,}")


def _is_hangul_syllable(char: str) -> bool:
    return "가" <= char <= "힣"


def double_decode_mojibake(text: str) -> str:
    """Return the first UTF-8-as-CP949 double-decoded run, or ""."""
    for match in _NON_ASCII_RUN.finditer(text):
        run = match.group(0)
        if any(unicodedata.category(char) == "Co" for char in run):
            continue
        try:
            recovered = run.encode("cp949").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if sum(1 for char in recovered if _is_hangul_syllable(char)) >= 2:
            return run
    return ""


def inspect_file(path: Path) -> list[str]:
    problems: list[str] = []
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        return [f"file exceeds {MAX_BYTES} bytes"]
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        return [f"invalid UTF-8 at byte {exc.start}: {raw[exc.start:exc.start + 8]!r}"]
    if "�" in text:
        index = text.index("�")
        problems.append(
            f"U+FFFD replacement character at offset {index}: "
            f"…{text[max(0, index - 12):index + 12]!r}…"
        )
    if _CP949_CLASSIC in text:
        problems.append("CP949-as-UTF-8 mojibake pattern 占쏙옙 present")
    double_decoded = double_decode_mojibake(text)
    if double_decoded:
        problems.append(
            f"UTF-8-as-CP949 double-decoded run detected: {double_decoded[:24]!r}"
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        action="append",
        type=Path,
        required=True,
        help="Directory (or single file) of acceptance artifacts to verify.",
    )
    args = parser.parse_args()

    scanned = 0
    failures: list[tuple[Path, str]] = []
    for root in args.root:
        if not root.exists():
            failures.append((root, "acceptance root does not exist"))
            continue
        files = [root] if root.is_file() else sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES
        )
        for path in files:
            scanned += 1
            for problem in inspect_file(path):
                failures.append((path, problem))

    for path, problem in failures:
        print(f"FAIL {path}: {problem}")
    print(f"utf8_files_scanned={scanned} utf8_failures={len(failures)}")
    print(
        "network_calls=0 smtp_attempts=0 teams_sends=0 telegram_sends=0 "
        "production_state_writes=0"
    )
    if failures or scanned == 0:
        if scanned == 0:
            print("FAIL no acceptance artifacts were scanned")
        print("ACCEPTANCE_ARTIFACT_UTF8=FAIL")
        print("RESULT=D7-AK-6E_R4R11_UTF8_ACCEPTANCE_FAIL")
        return 1
    print("ACCEPTANCE_ARTIFACT_UTF8=PASS")
    print("RESULT=D7-AK-6E_R4R11_UTF8_ACCEPTANCE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
