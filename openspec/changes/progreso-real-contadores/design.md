# Design: Progreso Real — Contadores Compuestos y Mensaje Contextual

## Technical Approach

Replace the single `global_labeled` counter (which includes `_skip` labels and inflates perceived progress) with three distinct metrics — started, confirmed, total — and add `done_reason` to distinguish "personal queue exhausted" from "all work complete". The change touches 4 files across the data layer (db.py + SQL), controller layer (server.py), and presentation layer (label.html).

## Architecture Decisions

### Decision: New RPC `count_started_crops()` vs direct query

| Option | Tradeoff | Decision |
|--------|----------|----------|
| New RPC | Consistent with existing `count_distinct_labeled_crops()` pattern; avoids fetching all labels rows | **Chosen** |
| Direct PostgREST query | No SQL deployment needed; but pulls more data for DISTINCT count | Rejected |

**Rationale**: The existing `get_global_stats()` already uses an RPC with fallback — same pattern, same risk profile.

### Decision: Keep `get_global_stats()` alongside `get_real_progress()`

| Option | Tradeoff | Decision |
|--------|----------|----------|
| Replace entirely | Simpler; but breaks any external callers | Rejected |
| Add alongside | Backward compatible; `/status` endpoint still works | **Chosen** |

**Rationale**: `/status` and `/next` JSON both reference `global_labeled`. Deprecation is out of scope.

### Decision: Local mode `confirmed = labeled`

| Option | Tradeoff | Decision |
|--------|----------|----------|
| confirmed = labeled | Simple; local mode has no crops table with status | **Chosen** |
| Add local status tracking | Over-engineering for dev mode | Rejected |

## Data Flow

```
Browser (label.html)
  │
  ├── GET /work ──→ work_view() ──→ get_real_progress() ──→ Supabase
  │                     │                                      │
  │                     │  {started, confirmed, total}         │
  │                     └── render_template(label.html)        │
  │
  └── POST /label ──→ label_view() ──→ write_label()
        │                                    │
        └── GET /next ──→ next_view() ──→ get_real_progress()
                              │
                              └── JSON {started, confirmed, total, done_reason}

Supabase:
  count_started_crops()  ← SELECT COUNT(DISTINCT crop_id) FROM labels WHERE label_human != '_skip'
  SELECT COUNT(*) FROM crops WHERE status = 'confirmed'
  SELECT COUNT(*) FROM crops
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `scripts/supabase_schema.sql` | Modify | Add `count_started_crops()` RPC (after line 88) |
| `src/modules/labeler/db.py` | Modify | Add `get_real_progress()` function (~line 397) |
| `src/modules/labeler/server.py` | Modify | `work_view` (L871-939), `next_view` (L1152-1198), `next_view_local` (L1475-1502) |
| `src/modules/labeler/templates/label.html` | Modify | Progress bar (L291-328), `updateDOM()` (L974-988), done block (L291-295) |
| `src/modules/labeler/queue.py` | No change | `labeled` property already exists; server.py maps it to started/confirmed |

## Interfaces / Contracts

### `get_real_progress()` return type (db.py)

```python
def get_real_progress() -> dict:
    """Returns {"started": int, "confirmed": int, "total": int}"""
```

- `started`: `count_started_crops()` RPC → fallback `SELECT COUNT(DISTINCT crop_id) FROM labels WHERE label_human != '_skip'`
- `confirmed`: `SELECT COUNT(*) FROM crops WHERE status = 'confirmed'`
- `total`: `SELECT COUNT(*) FROM crops`

### JSON contract from `/next` (additions)

```json
{
  "done": false,
  "started": 400,
  "confirmed": 250,
  "total": 1000,
  "done_reason": null,
  "...existing fields..."
}
```

When `done: true`:
- `done_reason`: `"queue_exhausted"` | `"all_done"`
- `started`, `confirmed`, `total` still present

### Template variables (label.html additions)

New: `started`, `confirmed`, `pct` (computed), `done_reason`.
Removed from display: `my_labeled` (still passed but not shown in progress bar).

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | `get_real_progress()` returns correct dict | Mock Supabase client, verify RPC call + fallback |
| Integration | `work_view` passes `done_reason` correctly | Flask test client with mock db |
| E2E | Progress bar shows 3 counters, `updateDOM` refreshes them | Manual: label a crop, verify counters update without reload |
| SQL | `count_started_crops()` excludes `_skip` | Run RPC in Supabase SQL editor with test data |

## Migration / Rollout

No migration required. The new RPC is additive. Rollback is safe — remove the RPC, revert Python/HTML changes. No data schema changes.

## Open Questions

- [ ] Should `my_labeled` remain in the progress bar or be removed entirely? (Proposal says 3 metrics only)
- [ ] CSS: keep the thin/thick toggle or simplify to single layout?
