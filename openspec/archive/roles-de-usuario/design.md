# Design: User Roles (roles-de-usuario)

## Technical Approach

Implement a 4-tier role authorization layer on top of the existing `@require_auth` decorator. The single source of truth for a user's role is Supabase Auth `app_metadata.role`, read via the service-role admin API. A `before_request` hook populates `g.user_role` once per authenticated request (with a 60s in-process TTL cache), a new `@require_role()` decorator factory enforces the route map, and `_inject_globals()` exposes `user_role` to templates for conditional rendering. The existing `_is_admin_user()` is kept as a thin compatibility shim during the transition.

This sits cleanly inside the existing `LOCAL_DEV` vs `PRODUCTION` split: the dev bypass short-circuits to `g.user_role = "admin"`, and the cache is per-process (acceptable under Flask dev server and uWSGI workers).

## Architecture Decisions

| Decision | Choice | Rejected | Rationale |
|---|---|---|---|
| Role storage | Supabase `app_metadata.role` | Local Postgres `user_roles` table | `app_metadata` already exists; admin-only writes (service-role); no schema migration; consistent with existing `_is_admin_user()` |
| Resolution timing | `before_request` hook (one read per request, cached) | Decorator-time read on every protected route | Avoids N reads when chained decorators evaluate; single point of failure handling; predictable latency budget |
| Cache | In-process `dict[user_id, (role, expires_at)]`, 60s TTL, lazy eviction | Redis, `flask_caching`, no cache | No new dependency; warm cache eliminates Admin API latency from hot path; 60s window is acceptable for role changes that go through the Supabase dashboard |
| Decorator pattern | `@require_role(roles)` factory returning a decorator | Class-based permission objects, Flask-Principal | Matches existing `@require_auth` style; minimal surface area; pure stdlib `functools.wraps` |
| Failure mode | Default to `"validator"` on Admin API error or missing key | Default to `"admin"`; default to `None`/403 | Fail-closed on privilege (never escalate) but keep the portal usable for labeling; aligns with proposal risk mitigation |
| Compat for `_is_admin_user()` | Keep as wrapper around `get_user_role() == "admin"` | Delete in same PR | Reduces blast radius; `/label`'s `is_admin` resolution path stays one-line; future PR removes it |
| Dev bypass | `g.user_role = "admin"` when `LOCAL_DEV_BYPASS=1` OR `SUPABASE_URL` unset | Always require role lookup | Mirrors existing `@require_auth` bypass; protects local workflow; production guarded by env contract already enforced at startup |

## Data Flow

    HTTP Request
        │
        ▼
    @app.before_request _assign_request_id
        │
        ▼
    @app.before_request _check_maintenance_mode
        │
        ▼
    @app.before_request _resolve_user_role     ◄── NEW
        │   (only if g.user_id set by @require_auth path; else no-op)
        │
        │   role_cache[user_id] fresh? ── yes ──► g.user_role = cached
        │           │ no
        │           ▼
        │   auth.admin.get_user_by_id(user_id)
        │           │
        │           ├─ ok    → g.user_role = app_metadata.role or "validator"
        │           └─ error → g.user_role = "validator"  (FAIL-CLOSED)
        │   write back to role_cache with expires_at = now + 60s
        │
        ▼
    @require_auth → @require_role("admin"|"validator"|...) → view_func
        │                       │
        │                       └── if g.user_role not in allowed:
        │                               JSON: 403 {"error":"forbidden"}
        │                               HTML: redirect "/" or "/auth/login"
        ▼
    Response (after _inject_globals adds user_role to template ctx)

`@require_auth` runs first and populates `g.user_id`; only then does the `before_request` role resolver have something to look up. Public routes (`/`, `/auth/*`, `/status`, `/privacy`) never reach the resolver because they have no `g.user_id`; the resolver short-circuits when `g.user_id` is unset.

## File Changes

| File | Action | Description |
|---|---|---|
| `src/modules/labeler/auth.py` | Modify | Add `ROLE_ADMIN/VALIDATOR/REVIEWER/READER` constants, `_role_cache`, `_get_user_role(user_id)`, `require_role(*roles)` decorator factory, `resolve_user_role()` hook function |
| `src/modules/labeler/server.py` | Modify | Register `@app.before_request resolve_user_role`; replace `_is_admin_user()` call sites with `@require_role("admin")`; in `auth_login_post`, after successful sign-in, write `app_metadata.role = "validator"` if absent and redirect by role; extend `_inject_globals()` with `user_role`; keep `_is_admin_user()` as wrapper |
| `src/modules/labeler/templates/home.html` | Modify | Add role badge near `Sesión:`; conditional `/admin/conflicts` link when `user_role == "admin"`; hide "Empezar a etiquetar" CTA for `reader` |
| `src/modules/labeler/templates/label.html` | Modify | Role badge in top-right header strip (next to `user_email`); admin-only links (Conflicts) shown only for admins |

No DB schema change. No new dependencies. `db.py` untouched.

## Interfaces / Contracts

```python
# auth.py — new public API

ROLE_ADMIN     = "admin"
ROLE_VALIDATOR = "validator"
ROLE_REVIEWER  = "reviewer"
ROLE_READER    = "reader"
_VALID_ROLES   = frozenset({ROLE_ADMIN, ROLE_VALIDATOR, ROLE_REVIEWER, ROLE_READER})

_ROLE_CACHE_TTL = 60.0
_role_cache: dict[str, tuple[str, float]] = {}  # user_id -> (role, expires_at)

def _get_user_role(user_id: str) -> str:
    """Resolve role from Supabase app_metadata. Cached 60s. Fail-closed to validator."""

def resolve_user_role() -> None:
    """Flask before_request hook. Populates g.user_role exactly once per request.
    Dev bypass → 'admin'. Missing g.user_id → no-op (public route)."""

def require_role(*roles: str) -> Callable:
    """Decorator factory. 403 JSON or redirect when g.user_role not in roles."""
```

### Route → Role Map (enforced)

| Route | Decorator stack |
|---|---|
| `/work`, `/label`, `/skip`, `/back`, `/next`, `/mark-fraud` | `@require_auth` + `@require_role("validator","admin")` |
| `/image/<crop_id>`, `/pdf` | `@require_auth` + `@require_role("validator","admin","reviewer")` |
| `/admin/conflicts`, `/debug/sentry-test` | `@require_auth` + `@require_role("admin")` |
| `/`, `/auth/*`, `/status`, `/privacy` | unchanged (public) |

### 403 response format

- HTML request (`Accept: text/html`) → `redirect("/", 302)` (do NOT redirect to `/auth/login` — user is authenticated, just unauthorized).
- JSON / API request → `jsonify({"error": "forbidden"}), 403`.

### Cache eviction

Lazy: each `_get_user_role()` call compares `expires_at` to `time.monotonic()` and discards the entry if stale. No background sweep. Memory bound is `|active_users|` — acceptable for the labeling portal (<10k concurrent). Worker isolation (uWSGI) means each worker has its own cache; 60s TTL absorbs the cross-worker inconsistency window. No lock needed: Python dict assignment is atomic under the GIL and stale reads are tolerable.

## Testing Strategy

No automated test suite exists in this project. Validation follows the existing `diagnostic*.py` pattern.

| Layer | What to Test | Approach |
|---|---|---|
| Unit | `_get_user_role()` returns `"validator"` on Admin API exception; returns `"validator"` when `app_metadata.role` missing; returns the actual role when present; cache hit returns without API call | `diagnostic_roles.py` with monkey-patched `db._client()` |
| Integration | `@require_role("admin")` returns 403 JSON for non-admin; returns 302 redirect for HTML; passes through for admin | Flask test client driven from `diagnostic_roles.py` |
| Manual | Login as a user with no role → check Supabase dashboard shows `app_metadata.role = "validator"` and post-login lands on `/work` | One-off smoke check before merging |
| Manual | Dev bypass — start without `SUPABASE_URL`, hit `/admin/conflicts` → passes; with `LOCAL_DEV_BYPASS=1` + `SUPABASE_URL` set → passes | Two-shell verification |

## Migration / Rollout

No data migration. Existing users get `app_metadata.role = "validator"` written idempotently on next login. Users who never log in again keep working (resolver defaults to `"validator"` on missing key). Roll out behind no feature flag — fail-closed semantics make this safe. Rollback = revert PR; harmless `app_metadata.role` values stay in Supabase.

## Open Questions

- [ ] Should `_is_admin_user()` be deleted in this PR or in a follow-up? Recommendation: keep as wrapper here, delete in the next PR once all callers are confirmed migrated.
- [ ] Cache invalidation on explicit role change (admin endpoint to promote/demote) is out of scope per proposal; 60s TTL is the only invalidation. Confirm acceptable.
