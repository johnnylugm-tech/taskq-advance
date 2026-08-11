# CONFIG_RECORDS.md - taskq-advance

> On-demand Lazy Load template.

## 1. Version Information
- Version: vharness-v4-20260811-score96-30-g7fdbac6
- Git Commit: 7fdbac6
- Release Date: 2026-08-11

## 2. Runtime Configuration
| Environment | Config |
|-------------|--------|
| Development | {{config}} |
| Production | {{config}} |

## 3. Dependency List
```
{{pip freeze / npm lock output}}
```

## 4. Environment Variables
| Variable | Type | Description |
|----------|------|-------------|
| {{VAR}} | secret | {{description}} |

## 5. Deployment Log
| Date | Version | Method | Executor |
|------|---------|--------|----------|
| 2026-08-11 | harness-v4-20260811-score96-30-g7fdbac6 | {{method}} | {{name}} |

## 6. Configuration Change Log
| Phase | Change | Rationale |
|-------|--------|----------|
| Phase 8 | {{change}} | {{reason}} |

## 7. Rollback SOP
**Trigger Condition**: {{condition}}
**Commands**:
```bash
{{rollback commands}}
```

## 8. Configuration Compliance
- [ ] Phase 7 risk mitigations implemented
- [ ] Monitoring thresholds configured
- [ ] Circuit breaker enabled

## Human Context (P8 append)

### Ownership per Config Item
| Item | Owner | Backup | Source-of-Truth |
|------|-------|--------|-----------------|
| `DATABASE_URL` | Platform team (DB) | SRE on-call | `taskq_api/config.py` env binding |
| `SECRET_KEY` / `JWT_SECRET` | Security team | Platform lead | Vault `secret/taskq/jwt` |
| `CELERY_BROKER_URL` / `REDIS_URL` | Platform team (Queue) | SRE on-call | `taskq_api/repository/session.py` |
| `OIDC_CLIENT_*` | Identity team | Auth squad | `taskq_api/service/auth.py` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Observability team | SRE on-call | sidecar config-map |
| Feature flags (`feature_*`) | Product owner | Release manager | `taskq_api/service/runner.py` |
| Migrations (`migrations/versions/`) | DB lead | Platform lead | git-protected branch |

### Secret Rotation Cadence
| Secret | Cadence | Procedure |
|--------|---------|-----------|
| `JWT_SECRET` | every 90 days | Vault rotate → staged rollout → invalidation grace 24h |
| `DATABASE_PASSWORD` | every 60 days | Vault rotate → blue/green DB failover verification |
| `OIDC_CLIENT_SECRET` | every 180 days | Identity team rotation via IdP console |
| Redis/queue TLS key | every 365 days | SRE scheduled job |
| Application API tokens | on-demand + 90 days max | request-ticket based |

### Access Audit Log Reference
- Production config access is recorded in Vault audit log (`audit/device` log stream).
- Application-level secret reads emit structured logs (`event=secret.read`) at `taskq_api/service/auth.py`.
- Quarterly access review owned by Security team; SOC receives weekly summary digest.
- Run `vault audit -path secret/taskq` to dump last-30-days for any P8 inspection.
