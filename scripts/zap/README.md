# Kenya Pixieset TDD + OWASP ZAP

## TDD contracts (Docker)

```bash
docker compose run --rm test kenya
```

## Newman (Kenya hand-authored collection)

Never includes Daraja. Polls health before running.

```bash
bash scripts/ci/newman.sh
```

## OWASP ZAP passive scan

Passive-only Automation Framework plan (no active attacks). Probes Kenya public
routes against **app-dast** (`DEBUG=False`, `PHOTBOX_DAST=1`); gates on
**High/Critical/Medium** (STS-missing allowed only on plaintext `http://`).

**Daraja is out of scope.** Do not GET/POST `/api/billing/daraja/`. The
automation context excludes those paths; `gate_alerts.py` fails if a report
mentions them. Never point ZAP at a live Safaricom callback.

```bash
bash scripts/ci/zap_passive.sh
```

Reports: `artifacts/zap/zap-passive.json`, `artifacts/zap/zap-passive.html`

Optional CI: `RUN_ZAP_PASSIVE=1 bash scripts/ci/security.sh`
