from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def repo_root() -> Path:
    cwd = Path.cwd().resolve()
    safe_cwd = cwd.as_posix()
    result = subprocess.run(
        ["git", "-c", f"safe.directory={safe_cwd}", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip()).resolve()


ROOT = repo_root()
SKIP_PARTS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "media",
    "media_root",
    "static",
    "static_root",
    "htmlcov",
    "coverage",
    "dist",
    "build",
}
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".sqlite3",
    ".db",
    ".pyc",
    ".pyo",
    ".woff",
    ".woff2",
    ".ttf",
}
SENSITIVE_KEY_RE = re.compile(
    r"(SECRET|PASSWORD|TOKEN|PRIVATE_KEY|API_KEY|ACCESS_KEY|WEBHOOK_SECRET|DSN|SALT)",
    re.IGNORECASE,
)
ASSIGNMENT_PATTERNS = (
    re.compile(r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.+?)\s*$"),
    re.compile(r"^\s*-\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.+?)\s*$"),
    re.compile(r"^\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<value>.+?)\s*$"),
)
PLACEHOLDER_MARKERS = (
    "change",
    "changeme",
    "example",
    "dummy",
    "placeholder",
    "test",
    "local",
    "stub",
    "sample",
    "ci-",
    "ci_",
    "not-a-real-secret",
    "your-",
    "your_",
    "insert-",
    "replace-",
    "webhook-secret",
    "email-password",
    "secret-key",
    "access-key",
)
SAFE_VALUE_FRAGMENTS = (
    "os.environ",
    "getenv",
    "env(",
    "${",
    "$",
    "secrets.",
    "vars.",
    "settings.",
    "config.",
    "self.",
    "request.",
    "serializer.",
    "validated_data",
)
SAFE_TEST_VALUE_FRAGMENTS = (
    "password",
    "token",
    "secret",
    "test",
    "example",
    "fixture",
    "user",
)


def git_tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line for line in result.stdout.splitlines() if line.strip()]


def should_skip(path: Path) -> bool:
    rel_parts = set(path.relative_to(ROOT).parts)
    if rel_parts & SKIP_PARTS:
        return True
    return path.suffix.lower() in SKIP_SUFFIXES


def read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None


def normalize_value(raw: str) -> str:
    value = raw.strip().strip("'\"")
    if " #" in value:
        value = value.split(" #", 1)[0].strip()
    return value


def is_safe_literal(rel: str, value: str) -> bool:
    lowered = value.lower()
    if not lowered:
        return True
    if lowered in {"true", "false", "0", "1", "none", "null", "[]", "{}"}:
        return True
    if any(fragment in value for fragment in SAFE_VALUE_FRAGMENTS):
        return True
    if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
        return True
    if rel.endswith(".env.example") or "docs" in Path(rel).parts:
        return True
    if "\\tests\\" in rel or "/tests/" in rel or rel.endswith("conftest.py"):
        if any(fragment in lowered for fragment in SAFE_TEST_VALUE_FRAGMENTS):
            return True
    if any(char in value for char in "()[]{}"):
        return True
    if value.startswith(("'", '"')) and value.endswith(("'", '"')):
        value = value[1:-1]
    if re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", value):
        return True
    # Avoid flagging ordinary short constants; the gate targets likely real
    # committed secrets, not all secret-handling variable names.
    if len(value) < 24:
        return True
    return False


def inspect_line(rel: str, line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    for pattern in ASSIGNMENT_PATTERNS:
        match = pattern.match(line)
        if not match:
            continue
        key = match.group("key")
        value = normalize_value(match.group("value"))
        if not SENSITIVE_KEY_RE.search(key):
            return None
        if key.lower().endswith("_setting"):
            return None
        if is_safe_literal(rel, value):
            return None
        return key, "SEC001"
    return None


def main() -> int:
    findings: list[tuple[str, str, str, str]] = []
    for path in git_tracked_files():
        if should_skip(path):
            continue
        text = read_text(path)
        if text is None:
            continue
        rel = os.fspath(path.relative_to(ROOT))
        for line in text.splitlines():
            finding = inspect_line(rel, line)
            if finding:
                variable, rule_id = finding
                findings.append((rel, rule_id, variable, "high"))

    for rel, rule_id, variable, severity in sorted(set(findings)):
        print(f"{rel}\t{rule_id}\t{variable}\t{severity}")

    if findings:
        print("Tracked likely real secret assignments found; values suppressed.", file=sys.stderr)
        return 1

    print("secret hygiene ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
