# Tasks: roles-de-usuario

> Change: `roles-de-usuario`
> Store: hybrid (Engram + openspec)
> Status: completed-and-archived

---

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | 210–270 |
| 400-line budget risk | Medium |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | N/A (single PR) |

**Rationale**: 4 files touched. `auth.py` gains ~80 lines (constants, cache dict, `_get_user_role`, `resolve_user_role`, `require_role`). `server.py` changes ~90 lines (before_request registration, login flow rewrite, 6 route decorator replacements, `_inject_globals` extension, `_is_admin_user` wrapper). Two HTML templates gain ~20–30 lines each. Total sits comfortably under 400; single PR is fine.

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|---|---|---|---|
| 1 | All four capabilities as a single cohesive PR | PR 1 | Sequential phases inside; atomic at PR boundary |

---

## Phase 1: Foundation — auth.py constants, cache, and core functions

- [x] 1.1 **Add role constants and cache dict to `src/modules/labeler/auth.py`.**
  Add `ROLE_ADMIN`, `ROLE_VALIDATOR`, `ROLE_REVIEWER`, `ROLE_READER`, `_VALID_ROLES`, `_ROLE_CACHE_TTL = 60.0`, and `_role_cache: dict[str, tuple[str, float]] = {}` after the existing imports section.

- [x] 1.2 **Implement `_get_user_role(user_id: str) -> str` in `src/modules/labeler/auth.py`.**
  Check `_role_cache` for a fresh entry (`expires_at > time.monotonic()`); on miss, call `client.auth.admin.get_user_by_id(user_id)`, read `app_metadata.get("role")`, validate against `_VALID_ROLES` (default `ROLE_VALIDATOR` on missing/invalid); catch all exceptions and return `ROLE_VALIDATOR`; write result to cache.

- [x] 1.3 **Implement `resolve_user_role() -> None` (before_request hook) in `src/modules/labeler/auth.py`.**
  If `LOCAL_DEV_BYPASS=1` (or `SUPABASE_URL` unset) AND `FLASK_ENV != "production"`, set `g.user_role = ROLE_ADMIN` and return. If `getattr(g, "user_id", None)` is falsy, return (no-op for public routes). Otherwise call `_get_user_role(g.user_id)` and assign to `g.user_role`.

- [x] 1.4 **Implement `require_role(*roles: str) -> Callable` decorator factory in `src/modules/labeler/auth.py`.**
  Factory wraps `view_func` with `functools.wraps`; inner decorator checks `g.user_role in roles`; on failure, if `_is_html_request()` → `redirect("/", 302)`, else → `jsonify({"error": "forbidden"}), 403`. Accepts both single strings and multiple positional args.

- [x] 1.5 **Update `_is_admin_user()` in `src/modules/labeler/server.py` to be a thin wrapper.**
  Replace the body with `return _get_user_role(user_id) == ROLE_ADMIN`; add import of `_get_user_role` and `ROLE_ADMIN` from `auth`.

---

## Phase 2: Core Wiring — server.py integration

- [x] 2.1 **Register `resolve_user_role` as a `before_request` hook in `create_app()` in `src/modules/labeler/server.py`.**
  Import `resolve_user_role` alongside `require_auth`. Add `@app.before_request` + `resolve_user_role` registration immediately after the existing `_check_maintenance_mode` registration. Production block only (inside `if _production_mode:`).

- [x] 2.2 **Add `user_role` to `_inject_globals()` template context in `src/modules/labeler/server.py`.**
  Extend the returned dict with `"user_role": getattr(g, "user_role", "")`.

- [x] 2.3 **Replace inline `_is_admin_user()` calls on `/admin/conflicts` and `/debug/sentry-test` with `@require_role("admin")` in `src/modules/labeler/server.py`.**
  Remove the `if not _is_admin_user(...)` guard inside each view body. Add `@require_role("admin")` decorator below `@require_auth` on both routes.

- [x] 2.4 **Add `@require_role` decorators to labeling and image routes in `src/modules/labeler/server.py`.**
  - `/work`, `/label`, `/skip`, `/back`, `/next`, `/mark-fraud` → `@require_role(ROLE_VALIDATOR, ROLE_ADMIN)`
  - `/image/<crop_id>`, `/pdf` → `@require_role(ROLE_VALIDATOR, ROLE_REVIEWER, ROLE_ADMIN)`
  Decorator is placed below `@require_auth` on each route.

- [x] 2.5 **Rewrite login flow in `auth_login_post()` in `src/modules/labeler/server.py`.**
  After successful `sign_in_with_password`: read `response.user.app_metadata`; if `"role"` absent, call `client.auth.admin.update_user_by_id(user_id, {"app_metadata": {"role": ROLE_VALIDATOR}})` and set `role = ROLE_VALIDATOR`; else `role = app_meta["role"]`. Prime cache: `_role_cache[user_id] = (role, time.monotonic() + _ROLE_CACHE_TTL)`. Redirect: `"/admin/conflicts"` for admin, `"/"` for reader, `"/work"` for all others.

---

## Phase 3: Template Updates

- [x] 3.1 **Add role badge to `src/modules/labeler/templates/home.html`.**
  Inside the `{% if logged_in %}` block, after `user_email`, add a role badge element: `<span class="role-badge">{{ user_role }}</span>` (or equivalent inline style). Badge is visible for any non-empty `user_role`.

- [x] 3.2 **Add conditional admin nav link to `src/modules/labeler/templates/home.html`.**
  Wrap a new link `<a href="/admin/conflicts">Admin</a>` (or equivalent nav element) in `{% if user_role == "admin" %}...{% endif %}`.

- [x] 3.3 **Hide labeling CTA for reader in `src/modules/labeler/templates/home.html`.**
  Find the "Empezar a etiquetar" button/link pointing to `/work`. Wrap it in `{% if user_role != "reader" %}...{% endif %}`.

- [x] 3.4 **Add role badge and conditional admin tools to `src/modules/labeler/templates/label.html`.**
  In the top-right header strip (next to `user_email`), add `<span class="role-badge">{{ user_role }}</span>`. Any admin-only UI elements (e.g., Conflicts link) MUST be wrapped in `{% if user_role == "admin" %}...{% endif %}`.

---

## Phase 4: Diagnostic Script

- [x] 4.1 **Create `diagnostic_roles.py` in the project root.**
  Script exercises `_get_user_role()` error paths with monkey-patched client (missing key, API exception, valid role, cache hit). Uses Flask test client to verify `@require_role("admin")` returns 403 JSON for non-admin and 302 HTML redirect for reader. Prints PASS/FAIL per scenario.

---

## Dependency Order

```
1.1 → 1.2 → 1.3 → 1.4   (sequential — each builds on prior)
1.5 depends on 1.2 (imports _get_user_role)
2.1 depends on 1.3
2.2 depends on 1.3 (g.user_role set by hook)
2.3 depends on 1.4 and 2.1
2.4 depends on 1.4 and 2.1
2.5 depends on 1.1 and 1.2
3.1–3.4 depend on 2.2 (user_role in template context)
4.1 depends on all Phase 1–3 tasks
```

Parallelism opportunity: 3.1–3.4 can all run in parallel once 2.2 is done.

## Implementation Status

All 14 tasks completed and verified (2026-06-30).
