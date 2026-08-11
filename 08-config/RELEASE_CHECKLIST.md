# RELEASE_CHECKLIST

## Pre-Release Checks
- [ ] All P1-P7 phases completed and artifacts generated.
- [ ] CI pipeline fully passed.
- [ ] Final Sign Off approved.
- [ ] Production environment provisioned.
- [ ] Rollback plan documented.

## Human Context (P8 append)

### Deployment Runbook
- Runbook URL: `https://runbooks.internal/taskq-advance/release-v4` (linked from Confluence space `TEAM/TaskQ`).
- Steps covered: pre-flight checks → DB migration window → canary 5% → ramp 25/50/100 → smoke test → announce.

### Rollback Owner + On-Call
- Rollback owner: Release Manager on duty (primary); Platform Lead (secondary).
- On-call rotation: PagerDuty schedule `taskq-advance-primary` / `taskq-advance-secondary`.
- Decision SLA: declare rollback within 15 min of any Sev-1 signal; latest tag = `git tag --sort=-v:refname | head -1`.

### Post-Release Monitoring Dashboard
- Primary dashboard: Grafana `taskq-advance / Release Health` (latency p50/p95/p99, queue depth, error rate, DB lag).
- SLI panel: golden-signal burn-rate (1h + 6h windows). SLO burn rate > 14.4x pages the release channel.
- Synthetic probe: `check_taskq_health` (every 60 s) — failure threshold = 2 consecutive misses.

### Customer Comms Template
```
Subject: [taskq-advance] Release v{version} shipped on {date}

Hi {customer},

We shipped v{version} to production at {timestamp UTC}. Highlights:
- {bullet 1}
- {bullet 2}
- {bullet 3}

What to expect: <expected behavior change or "no customer-visible change">.
If you see issues: check status.taskq-advance.internal or reply to this thread.

— {release manager name}, on behalf of the taskq-advance team
```
