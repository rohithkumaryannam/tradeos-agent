#!/usr/bin/env python3
"""Fail when tracked source files contain credentials or credentialed URLs."""
from __future__ import annotations
import re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOW = {".env.example", "scripts/check_secrets.py"}
PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|api[_-]?secret|password|bot[_-]?token)\s*[=:]\s*['\"](?!your_)[A-Za-z0-9/+_.-]{16,}['\"]"),
    re.compile(r"(?i)(mongodb(?:\+srv)?|https?|socks5?)://[^\s:/]+:[^\s/@]+@"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)

def main() -> int:
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    hits = []
    for name in filter(None, names):
        if name in ALLOW:
            continue
        path = ROOT / name
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if any(pattern.search(line) for pattern in PATTERNS):
                hits.append(f"{name}:{number}")
    if hits:
        print("Potential secrets in tracked files:\n" + "\n".join(hits))
        return 1
    print(f"Secret scan passed ({len(names)} tracked files; values not printed).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
