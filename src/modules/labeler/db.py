"""
db — Supabase persistence layer for the labeler web portal.

Provides a thin synchronous wrapper around the supabase-py client.
All public functions operate on three tables: crops, labels, assignments.

Design references:
  ADR-2 (Postgres RPC assign_next_crop with SKIP LOCKED)
  ADR-3 (Supabase Storage bucket "crops" for image URLs)
  sdd/web-portal/spec (capabilities: shared-labeling-queue, inter-annotator-agreement)

Dev bypass (ADR-4):
  When SUPABASE_URL is unset the module-level `supabase` client is None.
  Call sites in server.py guard with the same env check used by auth.py,
  so db functions are never called in local dev mode.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger("labeler.db")

# ---------------------------------------------------------------------------
# Module-level Supabase client (sync create_client — ADR-2)
# ---------------------------------------------------------------------------

_supabase_url: str = os.environ.get("SUPABASE_URL", "").strip()

# Table operations prefer the service_role key when available, so the portal can
# read/write while RLS denies all direct anon/authenticated access (Option A
# server-trust model). Falls back to the anon key when no service key is set
# (e.g. RLS-off local dev / pilot before hardening). The service_role key is
# server-side only — never sent to the browser. User auth still uses the anon
# key via auth.init_supabase_client (a separate client).
_service_key: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
_anon_key: str = os.environ.get("SUPABASE_ANON_KEY", "").strip()
_supabase_key: str = _service_key or _anon_key

supabase: Any = None  # supabase.Client | None

if _supabase_url and _supabase_key:
    from supabase import create_client  # type: ignore[import]
    supabase = create_client(_supabase_url, _supabase_key)


def _client():
    """Return the module-level client, raising RuntimeError if not initialised."""
    if supabase is None:
        raise RuntimeError(
            "Supabase client is not initialised. "
            "Ensure SUPABASE_URL and SUPABASE_ANON_KEY are set before importing db."
        )
    return supabase


# ---------------------------------------------------------------------------
# Label normalisation for agreement / majority voting
# ---------------------------------------------------------------------------

# Annotators write zero as any of these glyphs. They are semantically identical
# and must NOT count as a disagreement (consistent with revalidate_actas.py).
ZERO_VARIANTS = {"*", "-", ".", "+", "o", "O", "/", "//", "///"}
SKIP_LABEL = "_skip"


def _normalize_label(value: str) -> str:
    """Collapse zero-variant glyphs to '0' so agreement compares meaning, not glyph."""
    v = (value or "").strip()
    return "0" if v in ZERO_VARIANTS else v


# ---------------------------------------------------------------------------
# 3.4  assign_next_crop
# ---------------------------------------------------------------------------

def assign_next_crop(annotator_id: str, vuelta: str = "primera") -> str | None:
    """
    Call the assign_next_crop_v2 Postgres RPC and return the assigned crop_id.

    Uses SELECT FOR UPDATE SKIP LOCKED (server-side) so concurrent callers
    never receive the same crop.  Returns None when no crop is available.

    Args:
        annotator_id: UUID string of the current annotator (from JWT sub claim).
        vuelta:       Voting round tag ('primera' or 'segunda'). Defaults to 'primera'.

    Returns:
        crop_id string, or None if the queue is empty for this annotator.
    """
    try:
        response = _client().rpc(
            "assign_next_crop_v2",
            {"p_annotator_id": annotator_id, "p_vuelta": vuelta},
        ).execute()
        # supabase-py v2: response.data is the scalar return value of the function
        return response.data or None
    except Exception as exc:
        err = str(exc).lower()
        if "function" in err and (
            "not found" in err or "does not exist" in err or "unknown" in err
        ):
            logger.warning(
                "assign_next_crop_v2 not found in Supabase; falling back to "
                "assign_next_crop. Deploy scripts/supabase_schema_v2.sql for "
                "vuelta-aware assignment. Error: %s",
                exc,
            )
            response = _client().rpc(
                "assign_next_crop",
                {"p_annotator_id": annotator_id},
            ).execute()
            return response.data or None
        raise


# ---------------------------------------------------------------------------
# 3.5  get_crop_details
# ---------------------------------------------------------------------------

def get_crop_details(crop_id: str) -> dict:
    """
    Fetch a single crop row from the crops table via PostgREST.

    Args:
        crop_id: Primary key of the crop to fetch.

    Returns:
        Dict with all crop columns.

    Raises:
        ValueError: if the crop_id does not exist.
    """
    response = (
        _client()
        .table("crops")
        .select("*")
        .eq("crop_id", crop_id)
        .single()
        .execute()
    )
    if not response.data:
        raise ValueError(f"Crop not found: {crop_id!r}")
    return response.data


# ---------------------------------------------------------------------------
# 3.6  write_label
# ---------------------------------------------------------------------------

def write_label(
    crop_id: str,
    annotator_id: str,
    label_human: str,
    amended: bool,
    is_admin: bool,
    vuelta: str = "primera",
) -> None:
    """
    Insert a labels row and increment crops.annotation_count atomically.

    Steps (two sequential PostgREST calls — supabase-py sync):
      1. INSERT INTO labels (crop_id, annotator_id, label_human, amended, is_admin_resolution, vuelta)
      2. UPDATE crops SET annotation_count = annotation_count + 1 WHERE crop_id = ?

    Args:
        crop_id:      The crop being annotated.
        annotator_id: UUID of the annotator.
        label_human:  The label value token submitted by the annotator.
        amended:      True if the annotator amended the OCR suggestion.
        is_admin:     True if this label is an admin conflict resolution.
        vuelta:       Voting round tag ('primera' or 'segunda'). Defaults to 'primera'.
    """
    _client().table("labels").insert(
        {
            "crop_id": crop_id,
            "annotator_id": annotator_id,
            "label_human": label_human,
            "amended": amended,
            "is_admin_resolution": is_admin,
            "vuelta": vuelta,
        }
    ).execute()

    # Increment annotation_count using a PostgREST RPC-style update.
    # supabase-py v2 does not support atomic increments via PostgREST directly,
    # so we read the current count and write count+1.  The race is acceptable
    # here because the business logic (evaluate_agreement) re-fetches both labels
    # and the assignment is exclusively held by this annotator via SKIP LOCKED.
    crop = get_crop_details(crop_id)
    new_count = (crop.get("annotation_count") or 0) + 1
    _client().table("crops").update(
        {"annotation_count": new_count}
    ).eq("crop_id", crop_id).execute()


# ---------------------------------------------------------------------------
# 3.7  evaluate_agreement
# ---------------------------------------------------------------------------

def evaluate_agreement(crop_id: str) -> None:
    """
    Read a crop's labels and set crops.confirmed_label / status.

    Adaptive redundancy (2 → 3):
      - Admin resolution present → admin value wins, status = 'confirmed'.
      - 2 real labels agree       → status = 'confirmed'.
      - 2 real labels disagree    → status = 'needs_third' (re-opens for a 3rd
                                    annotator; assignments are freed).
      - 3 real labels, a value has
        a 2/3 majority             → status = 'confirmed' (majority value).
      - 3 real labels all distinct → status = 'disputed' (admin review).

    Zero-variant glyphs (*, -, ., +, o, O) are normalised to '0' before voting
    so they never count as a disagreement. '_skip' labels are ignored entirely.

    Args:
        crop_id: The crop to evaluate.
    """
    response = (
        _client()
        .table("labels")
        .select("label_human, is_admin_resolution, annotator_id")
        .eq("crop_id", crop_id)
        .order("ts", desc=False)
        .execute()
    )
    labels: list[dict] = response.data or []

    # Admin resolution path: an admin label wins unconditionally.
    admin_labels = [lb for lb in labels if lb.get("is_admin_resolution")]
    if admin_labels:
        confirmed_value = admin_labels[-1]["label_human"]
        _client().table("crops").update(
            {"confirmed_label": confirmed_value, "status": "confirmed"}
        ).eq("crop_id", crop_id).execute()
        _client().table("assignments").delete().eq("crop_id", crop_id).execute()
        return

    # Real annotator labels (skip markers excluded), normalised for voting.
    real = [
        _normalize_label(lb["label_human"])
        for lb in labels
        if lb.get("label_human") != SKIP_LABEL
    ]

    if len(real) >= 3:
        # Majority vote over the first three independent labels.
        first_three = real[:3]
        winner, count = _majority(first_three)
        if count >= 2:
            _client().table("crops").update(
                {"confirmed_label": winner, "status": "confirmed"}
            ).eq("crop_id", crop_id).execute()
        else:
            # Three annotators, all disagree → admin review needed.
            _client().table("crops").update(
                {"status": "disputed"}
            ).eq("crop_id", crop_id).execute()
    elif len(real) == 2:
        if real[0] == real[1]:
            _client().table("crops").update(
                {"confirmed_label": real[0], "status": "confirmed"}
            ).eq("crop_id", crop_id).execute()
        else:
            # Disagreement → escalate to a third annotator instead of conflict.
            _client().table("crops").update(
                {"status": "needs_third"}
            ).eq("crop_id", crop_id).execute()

    # Free the leases of annotators who are done with this crop.
    _client().table("assignments").delete().eq("crop_id", crop_id).execute()


def _majority(values: list[str]) -> tuple[str, int]:
    """Return (most_common_value, its_count) for a small list of labels."""
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    winner = max(counts, key=counts.get)
    return winner, counts[winner]


# ---------------------------------------------------------------------------
# 3.8  release_expired_assignments
# ---------------------------------------------------------------------------

def release_expired_assignments() -> None:
    """
    Delete assignments where expires_at < now(), freeing locked crops.

    Called as a cheap side-effect on every GET / request so the queue never
    has permanently stale leases (design data-flow section).
    """
    # PostgREST does NOT evaluate "now()" as a Postgres expression in filter
    # values — it treats it as a literal string.  Pass the current UTC timestamp
    # as an ISO-8601 string so PostgREST can compare TIMESTAMPTZ correctly.
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    _client().table("assignments").delete().lt(
        "expires_at", now_iso
    ).execute()


# ---------------------------------------------------------------------------
# 3.9  get_storage_url
# ---------------------------------------------------------------------------

def get_concordancias(pdf_path: str, label_ocr: str, exclude_crop_id: str = "", limit: int = 8) -> list[str]:
    """
    Return subcell crop_ids from the same E14 (pdf_path) that share the same
    OCR prediction as the current crop. Only same-digit siblings are returned —
    no filler from other digit classes — so the reviewer sees how consistent
    the model is on this specific digit across the acta.
    """
    if not pdf_path or not label_ocr or label_ocr in ("", "?"):
        return []
    client = _client()
    q = (
        client.table("crops")
        .select("crop_id")
        .eq("pdf_path", pdf_path)
        .eq("label_ocr", label_ocr)
        .neq("digit_index", -1)
        .neq("crop_id", exclude_crop_id)
        .limit(limit)
        .execute()
    )
    return [r["crop_id"] for r in (q.data or [])]

def get_storage_url(crop_id: str) -> str:
    """
    Return the public Supabase Storage URL for a crop PNG in bucket 'crops'.

    Reads the actual storage_url column from the crops table (ADR-3),
    instead of fabricating a path from crop_id — because segunda-vuelta
    crops live at sv/batch1/{hash}/{filename}.png, not at {crop_id}.png.

    Args:
        crop_id: The crop identifier.

    Returns:
        Full HTTPS URL to the public image.

    Raises:
        ValueError: if the crop_id does not exist in the database.
    """
    resp = (
        _client()
        .table("crops")
        .select("storage_url")
        .eq("crop_id", crop_id)
        .single()
        .execute()
    )
    if not resp.data or not resp.data.get("storage_url"):
        raise ValueError(f"No storage_url for crop_id: {crop_id!r}")
    return resp.data["storage_url"]


# ---------------------------------------------------------------------------
# 3.9b  vuelta-aware list helpers (segunda-vuelta portal switch)
# ---------------------------------------------------------------------------

def list_crops_by_vuelta(client, vuelta: str) -> list[dict]:
    """Return all crops for a given vuelta ('primera' or 'segunda')."""
    response = client.table("crops").select("*").eq("vuelta", vuelta).execute()
    return response.data or []


def list_labels_by_vuelta(client, vuelta: str) -> list[dict]:
    """Return all labels for a given vuelta."""
    response = client.table("labels").select("*").eq("vuelta", vuelta).execute()
    return response.data or []


def list_assignments_by_vuelta(client, vuelta: str) -> list[dict]:
    """Return all assignments for a given vuelta."""
    response = client.table("assignments").select("*").eq("vuelta", vuelta).execute()
    return response.data or []


def create_label_with_vuelta(
    client,
    crop_id: str,
    label: str,
    user_id: str,
    vuelta: str,
) -> dict:
    """Insert a label row scoped to a vuelta and return the created record."""
    response = client.table("labels").insert(
        {
            "crop_id": crop_id,
            "annotator_id": user_id,
            "label_human": label,
            "vuelta": vuelta,
        }
    ).execute()
    data = response.data or []
    return data[0] if data else {}


# ---------------------------------------------------------------------------
# 3.10  get_conflict_crops
# ---------------------------------------------------------------------------

def get_conflict_crops() -> list[dict]:
    """
    Fetch all crops with status = 'disputed' for admin resolution.

    Returns:
        List of crop dicts ordered by priority ASC, crop_id ASC.
    """
    response = (
        _client()
        .table("crops")
        .select("*")
        .eq("status", "disputed")
        .order("priority", desc=False)
        .order("crop_id", desc=False)
        .execute()
    )
    return response.data or []


def record_fraud_mark(crop_id: str, pdf_path: str, reason: str, annotator: str) -> None:
    """Insert a fraud report into the fraud_marks table."""
    _client().table("fraud_marks").insert({
        "crop_id": crop_id or None,
        "pdf_path": pdf_path or None,
        "reason": reason,
        "annotator": annotator,
    }).execute()


def record_feedback(crop_id: str, pdf_path: str, message: str, annotator: str) -> None:
    """Insert a feedback report into the feedback_marks table."""
    _client().table("feedback_marks").insert({
        "crop_id": crop_id or None,
        "pdf_path": pdf_path or None,
        "message": message,
        "annotator": annotator,
    }).execute()


def get_fraud_marks(limit: int = 500) -> list[dict]:
    """Fetch fraud reports ordered by most recent first. Returns [] if table missing."""
    try:
        response = (
            _client()
            .table("fraud_marks")
            .select("*")
            .eq("hidden", False)
            .order("marked_at", desc=True)
            .limit(limit)
            .execute()
        )
        return response.data or []
    except Exception:
        return []


def get_feedback_marks(limit: int = 500) -> list[dict]:
    """Fetch feedback reports ordered by most recent first. Returns [] if table missing."""
    try:
        response = (
            _client()
            .table("feedback_marks")
            .select("*")
            .eq("hidden", False)
            .order("reported_at", desc=True)
            .limit(limit)
            .execute()
        )
        return response.data or []
    except Exception:
        return []


def get_amended_crops(limit: int = 500) -> list[dict]:
    """Fetch labels where amended=True, enriched with crop metadata.

    Returns a list of dicts merging label fields (label_human, annotator_id, ts)
    with crop fields (field_name, digit_index, pdf_path, label_ocr, storage_url).
    Returns [] on any error.
    """
    try:
        cli = _client()
        labels_resp = (
            cli.table("labels")
            .select("crop_id, label_human, annotator_id, ts")
            .eq("amended", True)
            .order("ts", desc=True)
            .limit(limit)
            .execute()
        )
        labels = labels_resp.data or []
        if not labels:
            return []

        crop_ids = list({lb["crop_id"] for lb in labels if lb.get("crop_id")})
        crops_resp = (
            cli.table("crops")
            .select("crop_id, field_name, digit_index, pdf_path, label_ocr, storage_url")
            .in_("crop_id", crop_ids)
            .execute()
        )
        crop_map = {r["crop_id"]: r for r in (crops_resp.data or [])}

        result = []
        for lb in labels:
            cid = lb.get("crop_id", "")
            crop = crop_map.get(cid, {})
            result.append({
                "crop_id": cid,
                "label_human": lb.get("label_human"),
                "annotator_id": lb.get("annotator_id"),
                "ts": lb.get("ts"),
                "field_name": crop.get("field_name"),
                "digit_index": crop.get("digit_index"),
                "pdf_path": crop.get("pdf_path"),
                "label_ocr": crop.get("label_ocr"),
                "storage_url": crop.get("storage_url"),
            })
        return result
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 3.11  get_global_stats
# ---------------------------------------------------------------------------

def get_global_stats(user_id: str = "") -> dict:
    """
    Returns global labeling progress plus the personal count for user_id.

    Changed to count unique E14 actas (pdf_path) instead of individual crops.

    Returns:
        {
            "global_labeled": int,  # distinct E14 with >= 1 confirmed crop
            "my_labeled":     int,  # labels submitted by user_id (0 if empty)
            "total":          int,  # total distinct E14 with subcells in system
        }
    """
    cli = _client()

    # Total: count distinct pdf_path
    try:
        r = cli.rpc("count_distinct_e14_total", {}).execute()
        total = r.data or 0
    except Exception:
        total_resp = cli.table("crops").select("pdf_path", count="exact").neq("digit_index", -1).execute()
        total = total_resp.count or 0

    # Labeled: count distinct pdf_path with status=confirmed
    try:
        r = cli.rpc("count_distinct_e14_confirmed", {}).execute()
        global_labeled = r.data or 0
    except Exception:
        # Fallback
        r2 = cli.table("crops").select("pdf_path", count="exact").eq("status", "confirmed").neq("digit_index", -1).execute()
        global_labeled = r2.count or 0

    my_labeled = 0
    if user_id:
        my_resp = (
            cli.table("labels")
            .select("id", count="exact")
            .eq("annotator_id", user_id)
            .execute()
        )
        my_labeled = my_resp.count or 0

    return {"global_labeled": global_labeled, "my_labeled": my_labeled, "total": total}


# ---------------------------------------------------------------------------
# 3.11b  reports — record_report, get_reports, retract_recent_marks
# ---------------------------------------------------------------------------

def record_report(
    crop_id: str,
    pdf_path: str | None,
    report_type: str,
    annotator: str,
    digit_original: str | None = None,
    digit_corrected: str | None = None,
    digit: str | None = None,
    notes: str | None = None,
) -> None:
    """Insert a structured anomaly report into the reports table.

    Args:
        crop_id:         Crop being reported.
        pdf_path:        Source PDF path (nullable).
        report_type:     'enmienda' or 'otro'.
        annotator:       UUID string of the reporting annotator.
        digit_original:  Original digit value (enmienda only).
        digit_corrected: Corrected digit value (enmienda only).
        digit:           Optional digit reference (otro only).
        notes:           Free-text note (required for otro, optional for enmienda).
    """
    # Cast annotator to canonical UUID string — raises ValueError for invalid input,
    # which surfaces to the caller as a data-integrity error rather than silent bad data.
    annotator_uuid = str(uuid.UUID(annotator))
    _client().table("reports").insert({
        "crop_id": crop_id,
        "pdf_path": pdf_path or None,
        "report_type": report_type,
        "digit_original": digit_original,
        "digit_corrected": digit_corrected,
        "digit": digit,
        "notes": notes,
        "annotator": annotator_uuid,
    }).execute()


def get_reports(limit: int = 500) -> list[dict]:
    """Fetch anomaly reports ordered by most recent first.

    Returns:
        List of report dicts. Returns [] if the table is missing or on any error.
    """
    try:
        response = (
            _client()
            .table("reports")
            .select("*")
            .eq("hidden", False)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return response.data or []
    except Exception:
        return []


_HIDE_ALLOWED_TABLES: frozenset[str] = frozenset({"fraud_marks", "feedback_marks", "reports"})


def hide_mark(table: str, record_id: int) -> None:
    """Soft-delete a record by setting hidden=True. Silently swallows errors."""
    if table not in _HIDE_ALLOWED_TABLES:
        raise ValueError(f"hide_mark: table {table!r} not in allowed set")
    try:
        _client().table(table).update({"hidden": True}).eq("id", record_id).execute()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("hide_mark(%s, %s) failed: %s", table, record_id, exc)


def retract_recent_marks(user_id: str, crop_id: str) -> None:
    """Best-effort DELETE across reports/fraud_marks/feedback_marks for (user, crop) < 3h.

    Silently swallows any error per table — a retraction failure MUST NOT block /back.
    Each table is deleted independently so one failure does not abort the others.

    Args:
        user_id: UUID string of the annotator (from JWT sub claim).
        crop_id: The crop being rolled back.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()

    # reports table — annotator column is UUID type
    try:
        annotator_uuid = str(uuid.UUID(user_id))
        (
            _client()
            .table("reports")
            .delete()
            .eq("annotator", annotator_uuid)
            .eq("crop_id", crop_id)
            .gt("created_at", cutoff)
            .execute()
        )
    except Exception as exc:
        logger.warning("retract_recent_marks reports failed: %s", exc)

    # fraud_marks table — annotator column is TEXT
    try:
        (
            _client()
            .table("fraud_marks")
            .delete()
            .eq("annotator", user_id)
            .eq("crop_id", crop_id)
            .gt("marked_at", cutoff)
            .execute()
        )
    except Exception as exc:
        logger.warning("retract_recent_marks fraud_marks failed: %s", exc)

    # feedback_marks table — annotator column is TEXT
    try:
        (
            _client()
            .table("feedback_marks")
            .delete()
            .eq("annotator", user_id)
            .eq("crop_id", crop_id)
            .gt("reported_at", cutoff)
            .execute()
        )
    except Exception as exc:
        logger.warning("retract_recent_marks feedback_marks failed: %s", exc)


# ---------------------------------------------------------------------------
# 3.12  get_real_progress
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 3.13  mesa_results — paginated accessor + stats with TTL cache
# ---------------------------------------------------------------------------

# Module-level TTL cache for get_mesa_stats.
# Structure: { cache_key: {"data": dict, "ts": float} }
# Cache key is the dept param (or None for global).
_mesa_stats_cache: dict = {}
_MESA_STATS_TTL = 300  # seconds

# All valid overall_status values (5-level taxonomy, design D3).
# Defined at module level to avoid tuple reconstruction on every stats call.
_MESA_STATUSES: tuple[str, ...] = (
    "clean", "known_anomaly", "warning", "discrepancy", "critical"
)


def get_mesa_results(
    dept: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> list[dict]:
    """
    Fetch a paginated slice of mesa_results rows.

    Applies optional dept and/or overall_status filters. Pagination is
    zero-based via PostgREST .range(offset, offset+per_page-1). Returns []
    on any exception (fail-closed).

    Args:
        dept:     Two-digit department code to filter on, or None for all.
        status:   overall_status value to filter on, or None for all.
        page:     1-based page number (page=1 → offset 0).
        per_page: Number of rows per page. Hard-coded at 50 in the admin route
                  (design D7), but kept flexible here for testing.

    Returns:
        List of mesa_results row dicts.
    """
    try:
        offset = (page - 1) * per_page
        query = _client().table("mesa_results").select("*")
        if dept is not None:
            query = query.eq("dept", dept)
        if status is not None:
            query = query.eq("overall_status", status)
        response = query.range(offset, offset + per_page - 1).execute()
        return response.data or []
    except Exception as exc:
        logger.warning("get_mesa_results failed: %s", exc)
        return []


def _get_mesa_stats_uncached(dept: str | None = None) -> dict:
    """
    Fetch overall_status distribution from mesa_results, grouped by dept.

    Returns a dict of the shape:
        {
            "01": {"clean": N, "known_anomaly": N, "warning": N,
                   "discrepancy": N, "critical": N, "total": N},
            ...
            "_global": {"clean": N, ..., "total": N},
        }

    Internal helper — callers should use get_mesa_stats() for the cached
    version. Exposed at module level so tests can bypass the cache.

    Returns {} on any exception (fail-closed).
    """
    try:
        # Supabase enforces db-max-rows=1000 regardless of .limit(); paginate.
        _BATCH = 1000
        rows: list[dict] = []
        offset = 0
        while True:
            q = (
                _client().table("mesa_results")
                .select("dept, overall_status")
                .range(offset, offset + _BATCH - 1)
            )
            if dept is not None:
                q = q.eq("dept", dept)
            batch = (q.execute().data) or []
            rows.extend(batch)
            if len(batch) < _BATCH:
                break
            offset += _BATCH

        # Aggregate per-dept counts
        per_dept: dict[str, dict[str, int]] = {}
        for row in rows:
            d = row.get("dept", "unknown")
            s = row.get("overall_status", "unknown")
            if d not in per_dept:
                per_dept[d] = {st: 0 for st in _MESA_STATUSES}
                per_dept[d]["total"] = 0
            if s in per_dept[d]:
                per_dept[d][s] += 1
            per_dept[d]["total"] += 1

        # Build _global rollup
        global_counts: dict[str, int] = {st: 0 for st in _MESA_STATUSES}
        global_counts["total"] = 0
        for dept_counts in per_dept.values():
            for st in _MESA_STATUSES:
                global_counts[st] += dept_counts.get(st, 0)
            global_counts["total"] += dept_counts["total"]

        return {**per_dept, "_global": global_counts}
    except Exception as exc:
        logger.warning("_get_mesa_stats_uncached failed: %s", exc)
        return {}


def get_mesa_stats(dept: str | None = None) -> dict:
    """
    Return overall_status distribution from mesa_results, with a 5-min TTL cache.

    Cache key is the dept parameter (None means global). On a cache miss the
    function delegates to _get_mesa_stats_uncached() and stores the result.
    Returns {} on any exception (fail-closed).

    Args:
        dept: Two-digit department code to scope the stats, or None for all.

    Returns:
        Stats dict (see _get_mesa_stats_uncached for shape).
    """
    cache_key = dept  # None is a valid dict key
    now = time.monotonic()
    entry = _mesa_stats_cache.get(cache_key)
    if entry is not None and (now - entry["ts"]) < _MESA_STATS_TTL:
        return entry["data"]

    data = _get_mesa_stats_uncached(dept=dept)
    _mesa_stats_cache[cache_key] = {"data": data, "ts": now}
    return data


def get_real_progress() -> dict:
    """
    Returns real progress metrics excluding _skip labels.

    Returns:
        {"started": int, "confirmed": int, "total": int}
    """
    cli = _client()
    total_resp = cli.table("crops").select("crop_id", count="exact").execute()
    total = total_resp.count or 0

    confirmed = 0
    try:
        conf_resp = cli.table("crops").select("crop_id", count="exact").eq("status", "confirmed").execute()
        confirmed = conf_resp.count or 0
    except Exception:
        pass

    started = 0
    try:
        rpc_resp = cli.rpc("count_started_crops", {}).execute()
        started = rpc_resp.data or 0
        if not started:
            raise ValueError("rpc returned zero or null")
    except Exception:
        try:
            fb = cli.table("labels").select("crop_id", count="exact").neq("label_human", "_skip").execute()
            started = fb.count or 0
        except Exception:
            started = 0

    return {"started": started, "confirmed": confirmed, "total": total}
