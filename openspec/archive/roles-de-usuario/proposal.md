# Proposal: User Roles (roles-de-usuario)

## Intent

The labeling portal currently treats every authenticated user identically: any logged-in user can label digits, view crops, and (informally) interact with admin-only endpoints guarded only by a one-off `_is_admin_user()` check. As the platform onboards external validators, internal reviewers, and read-only observers, this single-tier model is unsafe and inflexible. We need a role-based access model so that admins can manage the system, validators label digits, reviewers audit full actas, and readers only consume statistics — enforced both in the backend (route guards) and in the UI (conditional rendering).

## Scope

### In Scope
- Four roles: `admin`, `validator`, `reviewer`, `reader` (default on first login: `validator`).
- `_get_user_role()` helper generalizing the existing `_is_admin_user()` logic against Supabase Auth `app_metadata.role`.
- `@require_role(role | [roles])` decorator replacing ad-hoc admin checks.
- Flask `before_request` hook that resolves `g.user_role` once per authenticated request (with short in-process TTL cache).
- Login POST: assign `validator` to `app_metadata` if no role set; redirect post-login according to role.
- `_inject_globals()` context processor exposes `user_role` to all templates; `home.html` and `label.html` render role-aware UI (admin link, role badge, hidden labeling controls for readers).
- Dev bypass (`LOCAL_DEV_BYPASS=1` or missing `SUPABASE_URL`) sets `g.user_role = "admin"`.
- Apply guards to existing routes per the role map (validator/reviewer/admin tiers).

### Out of Scope
- New `reviewer`-specific pages (full-acta review UI) — guards only; UI deferred.
- New `reader` dashboard — guards only; dashboard deferred.
- Role self-service UI for admins (assignment is via Supabase dashboard or admin CLI for now).
- Optional `user_roles` Postgres backup table — `app_metadata` is the single source of truth.
- Audit log of role changes.
- Per-departamento or per-corporacion scoping of roles.

## Capabilities

### New Capabilities
- `user-roles`: role taxonomy, role resolution from Supabase `app_metadata`, default-role assignment on first login, role-aware login redirect, in-process role cache.
- `route-authorization`: `@require_role` decorator, `before_request` role resolver, route → role map enforcement, dev-bypass role override.
- `role-aware-ui`: template `user_role` injection, conditional rendering in `home.html` and `label.html` (admin link, role badge, hide labeling actions for non-labelers).

### Modified Capabilities
- None (the existing labeler portal has no formal spec yet; auth currently only has `@require_auth` with no role concept).

## Approach

Approach B from exploration — `before_request` hook + dedicated `@require_role()` decorator:

1. Extend `auth.py` with `_get_user_role(user_id) -> str` (calls service-role `auth.admin.get_user_by_id`, reads `app_metadata["role"]`, defaults to `"validator"` on error/missing — **never** `"admin"`).
2. Add a 60-second in-process TTL cache keyed by `user_id` to absorb per-request Admin API latency.
3. Add `@require_role(roles)` decorator that depends on `g.user_role` being populated.
4. Register a `before_request` hook that, after `@require_auth` has set `g.user_id`, populates `g.user_role` exactly once per request (skipped for public routes).
5. Login POST: after successful sign-in, if `app_metadata.role` is absent, call `auth.admin.update_user_by_id` to set `validator` (idempotent). Then redirect by role (`admin` → `/admin/conflicts`, others → `/work` or `/`).
6. `_inject_globals()` adds `user_role` so templates can branch.
7. Replace inline `_is_admin_user()` calls with `@require_role("admin")`; keep `_is_admin_user()` as a thin wrapper for backward compatibility during transition.
8. Dev bypass sets `g.user_role = "admin"` so local development retains full access.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/modules/labeler/auth.py` | Modified | Add `_get_user_role()`, `@require_role()`, TTL cache, role constants. |
| `src/modules/labeler/server.py` | Modified | `before_request` hook, login role assignment + role-based redirect, replace `_is_admin_user()` with `@require_role`, extend `_inject_globals()`. |
| `src/modules/labeler/templates/home.html` | Modified | Conditional admin link, role badge, hide labeling CTA for `reader`. |
| `src/modules/labeler/templates/label.html` | Modified | Role badge in header, admin tools only for `admin`. |
| `src/modules/labeler/db.py` | Untouched | No schema changes (optional `user_roles` table is out of scope). |
| `scripts/supabase_schema.sql` | Untouched | No DDL changes. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Per-request Admin API latency degrades labeling throughput | Med | 60s in-process TTL cache keyed by `user_id`; invalidate on role change endpoint (future). |
| Existing users have no `role` in `app_metadata` and are locked out | Med | Login POST sets `validator` if absent; `_get_user_role()` defaults to `validator` on missing key. |
| `_get_user_role()` error path accidentally returns `admin` and escalates privilege | High impact / Low likelihood | Unit-test the error path; default is hard-coded `"validator"`; review fail-closed semantics in PR. |
| Dev bypass leaks to production and grants global admin | Low | Bypass only triggers when `LOCAL_DEV_BYPASS=1` AND `FLASK_ENV != "production"`; assert at startup. |
| Admin link disappears for legitimate admins because role read fails silently | Med | Log Admin API failures; surface a "role unknown" badge instead of silently downgrading UI. |
| Stacking with `reviewer`/`reader` guards for routes that don't yet exist creates dead code | Low | Apply guards only to existing routes in this change; defer reviewer/reader UI to follow-up. |

## Rollback Plan

1. Revert the PR (single commit revert acceptable — no DB schema changes).
2. Re-deploy: the system returns to the pre-change state where `_is_admin_user()` is the only role check and all authenticated users behave as `validator`-equivalent.
3. `app_metadata.role` values written during the change remain in Supabase but are harmless — the old code ignores them.
4. No data migration to undo. No cache to purge beyond a process restart.

## Dependencies

- Supabase Auth service-role key already configured (used by existing `_is_admin_user()`).
- Flask `g` and `before_request` (already in use).
- No new Python packages.

## Success Criteria

- [ ] `@require_role("admin")` blocks non-admins with 403 on `/admin/conflicts` and `/debug/sentry-test`.
- [ ] Labeling routes (`/work`, `/label`, `/skip`, `/back`, `/next`, `/mark-fraud`) accept `validator` and `admin`, reject `reviewer` and `reader`.
- [ ] Crop/PDF view routes accept `validator`, `reviewer`, `admin`; reject `reader`.
- [ ] First login of a user with no `app_metadata.role` results in `role=validator` persisted in Supabase.
- [ ] Post-login redirect routes admins to `/admin/conflicts` and others to `/work`.
- [ ] `home.html` and `label.html` render a visible role badge and hide controls the user cannot use.
- [ ] `_get_user_role()` returns `"validator"` (not `"admin"`) when the Admin API fails or `app_metadata.role` is missing — covered by an explicit test or diagnostic script.
- [ ] Average added latency per authenticated request is under 10 ms after warm cache (one Admin API call per user per 60 s).
- [ ] Dev bypass continues to grant full admin access locally without Supabase.
