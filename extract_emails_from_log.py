#!/usr/bin/env python3
"""Trích email từ log quét (dòng dạng `[123] user: email@domain`)."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

LINE_RE = re.compile(r"\[\d+\]\s+\S+:\s+(\S+@\S+)\s*$")


def extract(path: Path) -> list[str]:
    seen: set[str] = set()
    emails: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE_RE.search(line)
        if not m:
            continue
        email = m.group(1)
        if email not in seen:
            seen.add(email)
            emails.append(email)
    return emails


def main() -> int:
    parser = argparse.ArgumentParser(description="Trích email từ file log quét GitHub.")
    parser.add_argument("log_file", help="File log (terminal output)")
    parser.add_argument(
        "-o",
        "--output",
        default="eddiejaoude_follower_emails.txt",
        help="File output comma-separated",
    )
    args = parser.parse_args()

    emails = extract(Path(args.log_file))
    Path(args.output).write_text(",".join(emails), encoding="utf-8")
    print(f"{len(emails)} email → {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
