"""Run Bandit without echoing source snippets or literal secret-like values."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    _, bandit_args = parser.parse_known_args()

    command = [
        sys.executable,
        "-m",
        "bandit",
        "-f",
        "json",
        "-q",
        *bandit_args,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        print("bandit failed before producing parseable JSON output", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip().splitlines()[-1], file=sys.stderr)
        return result.returncode or 1

    issues = payload.get("results", [])
    print(f"bandit issues: {len(issues)}")
    for issue in issues:
        filename = issue.get("filename", "<unknown>")
        line_number = issue.get("line_number", "?")
        test_id = issue.get("test_id", "<unknown>")
        severity = issue.get("issue_severity", "<unknown>")
        confidence = issue.get("issue_confidence", "<unknown>")
        print(f"{filename}:{line_number} {test_id} severity={severity} confidence={confidence}")

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
