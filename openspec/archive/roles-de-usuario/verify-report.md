# Verification Report: roles-de-usuario

> Change: `roles-de-usuario`
> Store: hybrid (Engram + openspec)
> Verified: 2026-06-30
> Verdict: **PASS**

---

## Runtime Evidence

Diagnostic script `diagnostic_roles.py` executed: **6 PASS, 0 FAIL**

Additional runtime probes:
- Production mode suppresses dev bypass: PASS
- Public route (no user_id) skips role resolution: PASS
- Expired TTL forces re-fetch (returns validator on API failure): PASS
- Unknown role "superuser" falls back to ROLE_VALIDATOR: PASS
- `_is_admin_user()` thin wrapper delegates correctly: PASS

---

## Task Completion (14/14 complete)

All 14 tasks verified complete in apply-progress artifact and confirmed by code inspection.

---

## Capability: user-roles

| Requirement | Status | Evidence |
|---|---|---|
| `_get_user_role()` returns "validator" on API exception | PASS | auth.py:172-173 + Scenario 4 runtime |
| `_get_user_role()` returns "validator" when `app_metadata.role` missing | PASS | auth.py:167 `get("role", ROLE_VALIDATOR)` |
| `_get_user_role()` NEVER returns "admin" on error | PASS | error path hardcodes ROLE_VALIDATOR; runtime probe confirmed |
| Unknown role values default to "validator" | PASS | auth.py:168-169; "superuser" → "validator" runtime confirmed |
| Cache hit within 60s does not call Admin API | PASS | auth.py:157-159; Scenario 5 runtime |
| Cache keyed per user_id | PASS | `_role_cache: dict[str, tuple[str, float]]` at auth.py:37 |

---

## Capability: route-authorization

| Requirement | Status | Evidence |
|---|---|---|
| `@require_role(ROLE_ADMIN)` on `/admin/conflicts` and `/debug/sentry-test` | PASS | server.py:1294-1295, 1306-1307 |
| `@require_role(ROLE_VALIDATOR, ROLE_ADMIN)` on `/work`, `/label`, `/skip`, `/back`, `/next`, `/mark-fraud` | PASS | server.py:997, 1099, 1170, 1317, 1396, 1474 |
| `@require_role(ROLE_VALIDATOR, ROLE_REVIEWER, ROLE_ADMIN)` on `/image/<crop_id>`, `/pdf` | PASS | server.py:1213-1214, 1442-1443 |
| Dev bypass: `g.user_role = "admin"` when `LOCAL_DEV_BYPASS=1` AND `FLASK_ENV != "production"` | PASS | auth.py:190-193; Scenario 1 runtime |
| Dev bypass does NOT activate when `FLASK_ENV=production` | PASS | auth.py:190 `if not is_production`; production probe confirmed |
| Public routes have no role guard | PASS | No `@require_role` on `/`, `/auth/*`, `/status`, `/privacy`; resolve_user_role no-ops when g.user_id unset |

---

## Capability: login-flow

| Requirement | Status | Evidence |
|---|---|---|
| Login POST assigns "validator" if `app_metadata.role` absent (idempotent) | PASS | server.py:939-946 `if "role" not in app_meta` guard |
| Login POST does NOT overwrite existing role | PASS | server.py:947-948 `else` branch skips update |
| Redirect: admin → `/admin/conflicts`, reader → `/`, others → `/work` | PASS | server.py:956-961 |

---

## Capability: role-aware-ui

| Requirement | Status | Evidence |
|---|---|---|
| `_inject_globals()` adds `user_role` (empty string when unset) | PASS | server.py:635 `getattr(g, "user_role", "")` |
| `home.html`: role badge for authenticated users | PASS | home.html:142 inside `{% if logged_in %}` |
| `home.html`: admin link only when `user_role == "admin"` | PASS | home.html:109-111 |
| `home.html`: CTA hidden for `user_role == "reader"` | PASS | home.html:106-108 `{% if user_role != "reader" %}` |
| `label.html`: role badge in header | PASS | label.html:305 inside `{% if user_email %}` header block |
| `label.html`: admin tools conditional on `user_role == "admin"` | PASS | label.html:308-310 |

---

## Issues

### CRITICAL
None.

### WARNING
None.

### SUGGESTION
`_is_admin_user(g.user_id)` at server.py:1131 is still called inside `/label` to set the `is_admin` flag for `write_label()`. This is intentional (data flag, not access control) and was explicitly retained in apply-progress notes. It could be replaced with `g.user_role == ROLE_ADMIN` for consistency since `g.user_role` is already populated at that point, but it is not blocking.

---

## Final Verdict: PASS

- 30/30 spec requirements: PASS
- 14/14 tasks: complete
- Runtime diagnostics: 6/6 PASS
- Additional runtime probes: 5/5 PASS
- CRITICAL issues: 0
- WARNING issues: 0
- SUGGESTION: 1 (non-blocking)

Ready for archive and merge.
