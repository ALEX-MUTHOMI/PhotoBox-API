#!/usr/bin/env python3
"""Stepped DSA load harness: 100 / 500 / 1000 concurrent accepted GETs.

P99 is computed only on non-429 responses. 429s are counted separately so a
venue NAT storm is not mistaken for algorithm latency.

Usage (against a running API with pre-minted guest cookies):

  python scripts/scale/dsa_load.py \\
    --base-url https://api.example.test \\
    --gallery-id <uuid> \\
    --cookie-file cookies.txt \\
    --levels 100,500,1000

Cookie file: one Cookie header value per line (gallery_access=...; gallery_session=...).
Reuse photographer JWT / guest sessions — never mint 1000 SMTP consumes here.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class Sample:
    status: int
    latency_ms: float


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def _load_cookies(path: Path) -> list[str]:
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines:
        raise SystemExit(f"No cookies found in {path}")
    return lines


def _fetch(url: str, cookie: str, timeout: float) -> Sample:
    req = urllib.request.Request(url, headers={"Cookie": cookie, "Accept": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            exc.read()
        except Exception:
            pass
    except Exception:
        status = 0
    latency_ms = (time.perf_counter() - started) * 1000.0
    return Sample(status=status, latency_ms=latency_ms)


def run_level(
    *,
    url: str,
    cookies: list[str],
    concurrency: int,
    timeout: float,
) -> dict:
    samples: list[Sample] = []

    def worker(i: int) -> Sample:
        cookie = cookies[i % len(cookies)]
        return _fetch(url, cookie, timeout)

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(worker, i) for i in range(concurrency)]
        for fut in concurrent.futures.as_completed(futures):
            samples.append(fut.result())

    accepted = [s for s in samples if s.status and s.status < 400]
    throttled = [s for s in samples if s.status == 429]
    errors = [s for s in samples if s.status == 0 or (s.status >= 400 and s.status != 429)]
    latencies = sorted(s.latency_ms for s in accepted)
    return {
        "concurrency": concurrency,
        "total": len(samples),
        "accepted": len(accepted),
        "http_429": len(throttled),
        "errors": len(errors),
        "p50_ms_accepted": round(_percentile(latencies, 50), 2),
        "p95_ms_accepted": round(_percentile(latencies, 95), 2),
        "p99_ms_accepted": round(_percentile(latencies, 99), 2),
        "mean_ms_accepted": round(statistics.fmean(latencies), 2) if latencies else None,
    }


def parse_levels(raw: str) -> list[int]:
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="API origin, e.g. https://api.example.test")
    parser.add_argument("--gallery-id", required=True, help="Published gallery UUID")
    parser.add_argument(
        "--cookie-file",
        required=True,
        type=Path,
        help="File with pre-minted guest Cookie header values (one per line)",
    )
    parser.add_argument(
        "--levels",
        default="100,500,1000",
        help="Comma-separated concurrency steps (default: 100,500,1000)",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--path-template",
        default="/api/galleries/{gallery_id}/",
        help="GET path template; {gallery_id} is substituted",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    cookies = _load_cookies(args.cookie_file)
    base = args.base_url.rstrip("/")
    path = args.path_template.format(gallery_id=args.gallery_id)
    url = f"{base}{path}"
    levels = parse_levels(args.levels)

    report = {
        "url": url,
        "cookie_pool": len(cookies),
        "levels": [],
    }
    for level in levels:
        print(f"Running concurrency={level} ...", file=sys.stderr)
        report["levels"].append(
            run_level(url=url, cookies=cookies, concurrency=level, timeout=args.timeout)
        )

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
