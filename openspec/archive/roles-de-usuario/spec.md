# Spec: roles-de-usuario

> Change: `roles-de-usuario`
> Store: hybrid (Engram + openspec)
> Status: verified-and-archived

---

## Capability: user-roles

### Purpose

Define the role taxonomy, role resolution pipeline, default-role assignment on first login, and in-process TTL cache for role lookups.

---

### Requirement: Role Taxonomy

The system MUST recognise exactly four roles: `admin`, `validator`, `reviewer`, `reader`.
Roles MUST be stored as a string in Supabase Auth `app_metadata.role`.
No other role value is valid; unknown values MUST be treated as `validator` at runtime.

#### Scenario: Known role returned from app_metadata

- GIVEN a Supabase user whose `app_metadata.role` is `"reviewer"`
- WHEN `_get_user_role(user_id)` is called
- THEN the function returns `"reviewer"`

#### Scenario: Unknown role value falls back to validator

- GIVEN a Supabase user whose `app_metadata.role` is `"superuser"` (not in the taxonomy)
- WHEN `_get_user_role(user_id)` is called
- THEN the function returns `"validator"`

---

### Requirement: Role Resolution

`_get_user_role(user_id: str) -> str` in `src/modules/labeler/auth.py` MUST resolve the role by calling the Supabase Admin API (`auth.admin.get_user_by_id`), reading `app_metadata["role"]`, and returning it.
The function MUST return `"validator"` (never `"admin"`) when:
- `app_metadata` is absent or has no `role` key, OR
- the Admin API call raises any exception.

#### Scenario: app_metadata missing role key

- GIVEN a user with `app_metadata = {}` (no `role` key)
- WHEN `_get_user_role(user_id)` is called
- THEN the function returns `"validator"`

#### Scenario: Admin API raises exception

- GIVEN the Supabase Admin API raises a network or auth error
- WHEN `_get_user_role(user_id)` is called
- THEN the function returns `"validator"` without propagating the exception

#### Scenario: Error path never returns admin

- GIVEN any error condition (missing key, API failure, timeout)
- WHEN `_get_user_role(user_id)` is called
- THEN the return value is NOT `"admin"`

---

### Requirement: Role Cache

`_get_user_role` MUST cache the resolved role in process memory with a 60-second TTL, keyed by `user_id`.
Within the TTL window, subsequent calls for the same `user_id` MUST return the cached value without calling the Admin API.
After the TTL expires the cache entry MUST be evicted and the next call MUST re-fetch from Supabase.

#### Scenario: Cache hit within TTL

- GIVEN `_get_user_role("uid-123")` was called at T=0 and returned `"validator"`
- WHEN `_get_user_role("uid-123")` is called again at T=30s
- THEN the Admin API is NOT called a second time and `"validator"` is returned

#### Scenario: Cache miss after TTL

- GIVEN a cached entry for `"uid-123"` that was set at T=0
- WHEN `_get_user_role("uid-123")` is called at T=61s
- THEN the Admin API IS called and the freshly resolved role is returned

#### Scenario: Cache is user-scoped

- GIVEN `_get_user_role("uid-A")` was cached
- WHEN `_get_user_role("uid-B")` is called
- THEN `uid-B` is looked up independently from the Admin API

---

### Requirement: Default Role Assignment on First Login

During the login POST handler in `src/modules/labeler/server.py`, if the authenticated user has no `role` in `app_metadata`, the system MUST call `auth.admin.update_user_by_id` to set `app_metadata.role = "validator"` before redirecting.
This operation MUST be idempotent — if the role is already set, no update is made.

#### Scenario: First-time user gets validator role

- GIVEN a user whose `app_metadata` has no `role` field
- WHEN the user successfully completes the login POST
- THEN `auth.admin.update_user_by_id` is called with `app_metadata={"role": "validator"}`
- AND the user is redirected to `/work`

#### Scenario: Existing role is not overwritten

- GIVEN a user whose `app_metadata.role` is `"admin"`
- WHEN the user successfully completes the login POST
- THEN `auth.admin.update_user_by_id` is NOT called for role assignment
- AND the user is redirected to `/admin/conflicts`

---

## Capability: route-authorization

### Purpose

Define the `@require_role` decorator, the `before_request` role resolver, the route → role map enforcement, and the dev-bypass behaviour.

---

### Requirement: require_role Decorator

`@require_role(roles: str | list[str])` MUST be defined in `src/modules/labeler/auth.py`.
When applied to a Flask route, it MUST check `g.user_role` against the allowed roles list.
If `g.user_role` is in the allowed roles, the route executes normally.
If `g.user_role` is NOT in the allowed roles, the decorator MUST abort with HTTP 403.
The decorator MUST depend on `g.user_role` being populated by the `before_request` hook — it MUST NOT call `_get_user_role` itself.

#### Scenario: Allowed role passes through

- GIVEN `g.user_role = "admin"` and a route decorated with `@require_role("admin")`
- WHEN the route is called
- THEN the route handler executes and returns its normal response

#### Scenario: Disallowed role receives 403

- GIVEN `g.user_role = "reader"` and a route decorated with `@require_role(["validator", "admin"])`
- WHEN the route is called
- THEN the response is HTTP 403

#### Scenario: Single string or list accepted

- GIVEN `@require_role("admin")` and `@require_role(["admin"])`
- WHEN applied to equivalent routes
- THEN both forms enforce the same access rule

---

### Requirement: before_request Role Resolver

A Flask `before_request` hook in `src/modules/labeler/server.py` MUST populate `g.user_role` once per authenticated request by calling `_get_user_role(g.user_id)`.
The hook MUST run only when `g.user_id` is already set (i.e., after `@require_auth` has executed for that request).
Public routes that do not set `g.user_id` MUST NOT trigger the role resolver.

#### Scenario: Authenticated request gets role populated

- GIVEN a request to an authenticated route where `g.user_id = "uid-123"` is set
- WHEN the `before_request` hook runs
- THEN `g.user_role` is set to the resolved role for `"uid-123"`

#### Scenario: Public route skips role resolution

- GIVEN a request to `/auth/login` (a public route)
- WHEN the `before_request` hook runs
- THEN `g.user_role` is NOT set and no Admin API call is made

---

### Requirement: Route Access Map

The system MUST enforce the following minimum-role requirements:

| Route group | Allowed roles |
|---|---|
| `/work`, `/label`, `/skip`, `/back`, `/next`, `/mark-fraud` | `validator`, `admin` |
| `/image/<crop_id>`, `/pdf` | `validator`, `reviewer`, `admin` |
| `/admin/conflicts`, `/debug/sentry-test` | `admin` |
| `/`, `/auth/*`, `/status`, `/privacy` | public (no role check) |

A user whose role does not meet the minimum requirement MUST receive HTTP 403.

#### Scenario: validator accesses labeling route

- GIVEN `g.user_role = "validator"`
- WHEN requesting `GET /work`
- THEN the response is 200 (or valid labeling content)

#### Scenario: reviewer blocked from labeling route

- GIVEN `g.user_role = "reviewer"`
- WHEN requesting `POST /label`
- THEN the response is HTTP 403

#### Scenario: reviewer accesses image route

- GIVEN `g.user_role = "reviewer"`
- WHEN requesting `GET /image/<crop_id>`
- THEN the response is 200

#### Scenario: reader blocked from image route

- GIVEN `g.user_role = "reader"`
- WHEN requesting `GET /image/<crop_id>`
- THEN the response is HTTP 403

#### Scenario: non-admin blocked from admin route

- GIVEN `g.user_role = "validator"`
- WHEN requesting `GET /admin/conflicts`
- THEN the response is HTTP 403

---

### Requirement: Dev Bypass Role

When the dev bypass is active (`LOCAL_DEV_BYPASS=1` or `SUPABASE_URL` unset), the system MUST set `g.user_role = "admin"` in addition to setting `g.user_id`.
The bypass MUST NOT activate when `FLASK_ENV=production`.

#### Scenario: Dev bypass grants admin role

- GIVEN `LOCAL_DEV_BYPASS=1` is set and `FLASK_ENV != "production"`
- WHEN any authenticated route is accessed
- THEN `g.user_role` is `"admin"` without any Supabase Admin API call

#### Scenario: Dev bypass does not activate in production

- GIVEN `LOCAL_DEV_BYPASS=1` AND `FLASK_ENV=production`
- WHEN any route is accessed
- THEN the normal auth + role resolution pipeline runs (bypass is skipped)

---

### Requirement: is_admin_user Backward Compatibility

The existing `_is_admin_user()` function in `src/modules/labeler/auth.py` MUST be kept as a thin wrapper that delegates to `_get_user_role` during the transition period.
All direct route-level uses of `_is_admin_user()` in `server.py` MUST be replaced with `@require_role("admin")`.

#### Scenario: Inline admin checks replaced

- GIVEN a route that previously called `_is_admin_user()` inline
- WHEN the change is applied
- THEN the route is decorated with `@require_role("admin")` instead

---

## Capability: login-flow

### Purpose

Define the post-login role assignment and role-based redirect behaviour.

---

### Requirement: Role-Based Post-Login Redirect

After a successful login POST, the system MUST redirect the user based on their resolved role:
- `admin` → `/admin/conflicts`
- `reader` → `/` (public home — they cannot access `/work`)
- all other roles (`validator`, `reviewer`) → `/work`

#### Scenario: Admin redirected to conflicts

- GIVEN a user with `app_metadata.role = "admin"` logs in successfully
- WHEN the login POST handler completes
- THEN the response is a redirect to `/admin/conflicts`

#### Scenario: Validator redirected to work

- GIVEN a user with `app_metadata.role = "validator"` logs in successfully
- WHEN the login POST handler completes
- THEN the response is a redirect to `/work`

#### Scenario: Reader redirected to home

- GIVEN a user with `app_metadata.role = "reader"` logs in successfully
- WHEN the login POST handler completes
- THEN the response is a redirect to `/` (public home, not `/work`)

---

## Capability: role-aware-ui

### Purpose

Define template injection and conditional rendering rules so the UI reflects the current user's role without exposing inaccessible actions.

---

### Requirement: Template Role Injection

`_inject_globals()` in `src/modules/labeler/server.py` MUST add `user_role` (string) to the template context for every rendered response.
If `g.user_role` is not set (unauthenticated context), `user_role` MUST default to `""` (empty string).

#### Scenario: Authenticated template receives user_role

- GIVEN an authenticated request where `g.user_role = "reviewer"`
- WHEN any template is rendered
- THEN the template variable `user_role` equals `"reviewer"`

#### Scenario: Unauthenticated template receives empty string

- GIVEN a public route with no `g.user_role`
- WHEN a template is rendered
- THEN `user_role` equals `""`

---

### Requirement: home.html Conditional Rendering

`home.html` MUST conditionally render UI elements based on `user_role`:
- Admin navigation link MUST be visible only when `user_role == "admin"`.
- Labeling call-to-action (link or button to `/work`) MUST be hidden when `user_role == "reader"`.
- A role badge MUST be visible for all authenticated users displaying their current role.

#### Scenario: Admin sees admin link

- GIVEN `user_role = "admin"`
- WHEN `home.html` is rendered
- THEN the admin navigation link is present in the HTML

#### Scenario: Non-admin does not see admin link

- GIVEN `user_role = "validator"`
- WHEN `home.html` is rendered
- THEN the admin navigation link is absent from the HTML

#### Scenario: Reader does not see labeling CTA

- GIVEN `user_role = "reader"`
- WHEN `home.html` is rendered
- THEN the link or button pointing to `/work` is absent

#### Scenario: Role badge visible to authenticated users

- GIVEN any `user_role` value (non-empty)
- WHEN `home.html` is rendered
- THEN a role badge element displaying the role string is present

---

### Requirement: label.html Conditional Rendering

`label.html` MUST display a role badge in the header for all authenticated users.
Admin-only tools (if any admin UI elements exist in the labeling view) MUST be conditionally rendered for `user_role == "admin"` only.

#### Scenario: Role badge in label header

- GIVEN any authenticated user accessing the label view
- WHEN `label.html` is rendered
- THEN a role badge element is present in the page header

#### Scenario: Admin tools visible only to admin

- GIVEN `user_role = "validator"`
- WHEN `label.html` is rendered
- THEN admin-only tool elements are absent from the HTML

#### Scenario: Admin sees admin tools

- GIVEN `user_role = "admin"`
- WHEN `label.html` is rendered
- THEN admin-only tool elements are present in the HTML
