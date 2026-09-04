#!/usr/bin/env python3
"""Fail CI if ZAP passive JSON report contains High/Critical/Medium (with narrow allowlists)."""
from __future__ import annotations

import ipaddress
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


FAIL_RISKS = {"High", "Critical", "Medium"}
RFC1918 = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]
PRIVATE_IP_RE = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b"
)


def _is_rfc1918(text: str) -> bool:
    for match in PRIVATE_IP_RE.findall(text or ""):
        try:
            addr = ipaddress.ip_address(match)
        except ValueError:
            continue
        if any(addr in net for net in RFC1918):
            return True
    return False


def _sts_allowed(alert: dict) -> bool:
    name = (alert.get("name") or alert.get("alert") or "").lower()
    if "strict-transport-security" not in name and "sts header" not in name:
        return False
    for inst in alert.get("instances") or []:
        uri = inst.get("uri") or ""
        if urlparse(uri).scheme == "https":
            return False
        if urlparse(uri).scheme == "http":
            return True
    return False


def _private_ip_allowed(alert: dict) -> bool:
    """Allow RFC1918 only when evidence is hostname/Server/gateway header — not body."""
    name = (alert.get("name") or alert.get("alert") or "").lower()
    if "private ip" not in name:
        return False
    for inst in alert.get("instances") or []:
        evidence = (inst.get("evidence") or "") + "\n" + (inst.get("otherinfo") or "")
        param = (inst.get("param") or "").lower()
        # Body leaks fail the gate.
        if "body" in (inst.get("attack") or "").lower():
            return False
        # ZAP Private IP plugin often puts IP in evidence; if otherinfo mentions body, fail.
        other = (inst.get("otherinfo") or "").lower()
        if "response body" in other or "<html" in other or "{" in evidence:
            # JSON/HTML body evidence — fail
            if PRIVATE_IP_RE.search(evidence) and (
                "{" in evidence or "<" in evidence or "body" in other
            ):
                return False
        # Header / hostname style: short IP-only evidence is compose noise.
        if _is_rfc1918(evidence) and len(evidence.strip()) < 64 and "{" not in evidence:
            if param in {"", "server", "host", "hostname", "x-forwarded-for"}:
                continue
            # Default: allow short IP-only evidence from docker DNS if no body markers.
            if not any(ch in evidence for ch in "{<>"):
                continue
            return False
        if _is_rfc1918(evidence):
            continue
        return False
    return True


def main() -> int:
    report_path = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/zap/zap-passive.json")
    if not report_path.exists():
        print(f"[ZAP] Report missing: {report_path}", file=sys.stderr)
        return 2

    raw = report_path.read_text(encoding="utf-8")
    if "/api/billing/daraja" in raw or "/billing/daraja" in raw:
        print(
            "[ZAP] Report mentions Daraja callback URLs. "
            "ZAP must never request the Safaricom STK callback.",
            file=sys.stderr,
        )
        return 1

    data = json.loads(raw)
    sites = data.get("site") or []
    failures = []
    warnings = []
    info = []
    allowed = []

    for site in sites:
        for alert in site.get("alerts") or []:
            risk = (alert.get("riskdesc") or alert.get("risk") or "").split(" ")[0]
            name = alert.get("name") or alert.get("alert") or "unknown"
            count = alert.get("count") or len(alert.get("instances") or [])
            entry = f"{risk}: {name} (count={count})"

            if risk == "Medium" and _sts_allowed(alert):
                allowed.append(f"ALLOW {entry} [STS on http://]")
                continue
            if risk == "Low" and _private_ip_allowed(alert):
                allowed.append(f"ALLOW {entry} [RFC1918 header/host]")
                continue

            if risk in FAIL_RISKS:
                # Medium Private IP in body or CSRF/CSP fail.
                if "private ip" in name.lower() and not _private_ip_allowed(alert):
                    failures.append(entry + " [body/topology leak]")
                else:
                    failures.append(entry)
            elif risk == "Low":
                warnings.append(entry)
            else:
                info.append(entry)

    print(f"[ZAP] Parsed {report_path}")
    print(
        f"[ZAP] Failures={len(failures)} Allowed={len(allowed)} "
        f"Low={len(warnings)} Info={len(info)}"
    )
    for line in failures:
        print(f"  FAIL  {line}")
    for line in allowed:
        print(f"  OK    {line}")
    for line in warnings[:20]:
        print(f"  LOW   {line}")
    for line in info[:10]:
        print(f"  INFO  {line}")

    if failures:
        print("[ZAP] Passive scan FAILED (High/Critical/Medium on public JSON).", file=sys.stderr)
        return 1
    print("[ZAP] Passive scan gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
