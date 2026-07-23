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
import re
import uuid
from collections.abc import Collection
from datetime import datetime, timezone, timedelta
from collections.abc import Collection
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
ZERO_VARIANTS = {"*", "-", ".", "+", "o", "O", "x", "X", "/", "//", "///"}
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

    Only status='confirmed' crops are eligible: these are references shown to
    build the reviewer's confidence, and a still-pending crop is itself an
    unverified guess — showing one as "supporting evidence" is circular and
    misleading. Confirmed crops are also independent, already-resolved queue
    items, so this excludes anything a reviewer would still need to validate.
    """
    if not pdf_path or not label_ocr or label_ocr in ("", "?"):
        return []
    client = _client()
    q = (
        client.table("crops")
        .select("crop_id")
        .eq("pdf_path", pdf_path)
        .eq("label_ocr", label_ocr)
        .eq("status", "confirmed")
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
    _mk = re.search(r'E14_PRE_(\d+_\d+_\d+_\d+_\d+_\d+)_\d+', pdf_path or '')
    mesa_key = _mk.group(1) if _mk else None
    _client().table("reports").insert({
        "crop_id": crop_id,
        "pdf_path": pdf_path or None,
        "report_type": report_type,
        "digit_original": digit_original,
        "digit_corrected": digit_corrected,
        "digit": digit,
        "notes": notes,
        "annotator": annotator_uuid,
        "mesa_key": mesa_key,
    }).execute()


def check_mesa_already_reported(pdf_path: str, annotator_uuid: str) -> bool:
    """Return True if this annotator already filed a mesa report for this pdf_path.

    Fail-open on any error: returns False so the server pre-check never blocks
    a valid submission. The DB partial unique index remains the authoritative
    dedup layer.

    Args:
        pdf_path:       PDF path of the acta being checked.
        annotator_uuid: UUID string of the annotator.

    Returns:
        True if a mesa report row exists for (pdf_path, annotator), else False.
    """
    try:
        resp = (
            _client()
            .table("reports")
            .select("id")
            .eq("pdf_path", pdf_path)
            .eq("annotator", str(uuid.UUID(annotator_uuid)))
            .eq("report_type", "mesa")
            .limit(1)
            .execute()
        )
        return bool(resp.data)
    except Exception:
        return False


_E14_TYPE_LABELS = {
    "E14C": "E14C Claveros",
    "E14D": "E14D Delegados",
    "E14T": "E14T Transmisión",
}
_E14_TYPE_ORDER = ["E14C", "E14D", "E14T"]


def _derive_e14_type(pdf_path: str | None) -> str:
    p = (pdf_path or "").lower()
    if "e14c" in p:
        return "E14C"
    if "e14d" in p:
        return "E14D"
    if "e14t" in p:
        return "E14T"
    return "E14C"


def get_mesa_reports(limit: int = 200) -> list[dict]:
    """Fetch consolidated reports grouped by mesa, with nested reports grouped by E14 type.

    Each row has: mesa_key, total_reports, enmiendas, otros, mesa_reports,
    annotators, last_report_at, reports_by_type (dict E14C/D/T → list),
    first_crop_id (for Ver acta link at mesa level).
    Returns [] on any error.
    """
    try:
        response = (
            _client()
            .table("mesa_reports_view")
            .select("*")
            .order("total_reports", desc=True)
            .limit(limit)
            .execute()
        )
        rows = response.data or []
    except Exception:
        return []

    for row in rows:
        reports = row.get("reports") or []
        by_type: dict[str, list] = {}
        for rep in reports:
            e14 = _derive_e14_type(rep.get("pdf_path"))
            rep["e14_type"] = e14
            rep["e14_label"] = _E14_TYPE_LABELS.get(e14, e14)
            by_type.setdefault(e14, []).append(rep)
        # Ordered dict: E14C first, then D, then T
        row["reports_by_type"] = {k: by_type[k] for k in _E14_TYPE_ORDER if k in by_type}
        row["first_crop_id"] = reports[0].get("crop_id") if reports else None

    return rows


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
#
# 1800s (30min), not 300s: a cache miss pays for a full 122k-row pagination
# of mesa_results, which measured 30-40s on this Supabase instance (2026-07-19
# — full-table scans here are I/O-bound regardless of query shape, see
# MESAS_SIN_E14C). These are aggregate homepage stats, not real-time data;
# a longer TTL trades staleness (up to 30min old) for making that 30-40s
# cold-cache hit rare instead of guaranteed once every 5 minutes.
_mesa_stats_cache: dict = {}
_MESA_STATS_TTL = 1800  # seconds

# Total universe of mesas for the Colombia 2026 presidential election.
# Verified against data/allMviewGetProgressByCorporations.json:
# allMviewGetProgressByCorporations.nodes[0].expected = 122020.
TOTAL_UNIVERSE: int = 122_020

# E14C PDFs on disk (E:/e14_segunda/E14C) after dedup cleanup — audited 2026-07-04.
# Limiting factor for mesas_all_three (E14T/E14D both have 122,019).
MESAS_ALL_THREE: int = 118_543

# Mesas with sources.e14c.status != "ok" (no-descargado/extraction_error),
# counted 2026-07-19 from the same local data/cross_mesa_validation_*.jsonl
# files that were uploaded to mesa_results (source of truth for source_status).
# A precomputed constant, not a live COUNT: `count(*) WHERE
# source_status->>'e14c' <> 'ok'` (and even the inverse `= 'ok'` equality)
# forces a full Postgres Seq Scan on this Supabase instance regardless of
# indexing — 'ok' matches ~97% of rows so a seq scan is the genuinely
# correct query plan, but scanning the full 122k-row/249MB table here takes
# 10-14s and routinely hits the statement_timeout. Refresh this value after
# re-running scripts/upload_mesa_results.py against a full dataset.
MESAS_SIN_E14C: int = 3_688

# Zeros dict returned by get_public_stats() on any exception (fail-closed).
_PUBLIC_STATS_ZEROS: dict = {
    "mesas_all_three": MESAS_ALL_THREE,
    "mesas_analyzed": 0,
    "mesas_remaining": TOTAL_UNIVERSE,
    "total_anomalias": 0,
    "total_universe": TOTAL_UNIVERSE,
    "mesas_sin_e14c": MESAS_SIN_E14C,
}

# All valid overall_status values (5-level taxonomy, design D3).
# Defined at module level to avoid tuple reconstruction on every stats call.
_MESA_STATUSES: tuple[str, ...] = (
    "clean", "known_anomaly", "warning", "discrepancy", "needs_review_large_delta"
)


def get_mesa_results(
    dept: str | None = None,
    status: str | None = None,
    mpio: str | None = None,
    zona: str | None = None,
    puesto: str | None = None,
    mesa_key: str | None = None,
    source_missing: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> list[dict]:
    """
    Fetch a paginated slice of mesa_results rows.

    Applies optional dept, overall_status, mpio, zona, puesto, exact
    mesa_key, and/or source_missing filters. Pagination is zero-based via
    PostgREST .range(offset, offset+per_page-1). Returns [] on any exception
    (fail-closed).

    Args:
        dept:     Two-digit department code to filter on, or None for all.
        status:   overall_status value to filter on, or None for all.
        mpio:     Municipio code to filter on (drill-down level 1), or None.
        zona:     Zona code to filter on (drill-down level 2), or None.
        puesto:   Puesto de votación code to filter on (drill-down level 2),
                  or None.
        mesa_key: Exact mesa_key to filter on (mesa detail lookup,
                  mesa-findings-consolidation Phase 3), or None.
        source_missing: One of "any", "e14c", "e14t", "e14d", or None. Filters
                  on source_status->>{source}.neq.ok. Rows with source_status
                  IS NULL (pre-backfill) do NOT match — they read as
                  "unknown", not "missing".
        page:     1-based page number (page=1 → offset 0).
        per_page: Number of rows per page. 50 for the admin route (design D7),
                  10 for the public Level-3 mesa drill-down page.

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
        if mpio is not None:
            query = query.eq("mpio", mpio)
        if zona is not None:
            query = query.eq("zona", zona)
        if puesto is not None:
            query = query.eq("puesto", puesto)
        if mesa_key is not None:
            query = query.eq("mesa_key", mesa_key)
        if source_missing == "any":
            query = query.or_(
                "source_status->>e14c.neq.ok,"
                "source_status->>e14t.neq.ok,"
                "source_status->>e14d.neq.ok"
            )
        elif source_missing in ("e14c", "e14t", "e14d"):
            query = query.neq(f"source_status->>{source_missing}", "ok")
        response = query.range(offset, offset + per_page - 1).execute()
        return response.data or []
    except Exception as exc:
        logger.warning("get_mesa_results failed: %s", exc)
        return []


def count_mesa_results(
    dept: str | None = None,
    mpio: str | None = None,
    zona: str | None = None,
    puesto: str | None = None,
    status: str | None = None,
    source_missing: str | None = None,
) -> int:
    """
    Return the count of mesa_results rows matching the given filters.

    Used for Level-3 (mesa) pagination, 10 rows/page (design D7 drill-down).
    Returns 0 on any exception (fail-closed).

    Args:
        dept:     Two-digit department code to filter on, or None for all.
        mpio:     Municipio code to filter on, or None.
        zona:     Zona code to filter on, or None.
        puesto:   Puesto de votación code to filter on, or None.
        status:   overall_status value to filter on, or None for all.
        source_missing: One of "any", "e14c", "e14t", "e14d", or None. See
                  get_mesa_results() for the exact PostgREST semantics.

    Returns:
        Matching row count, or 0 on error.
    """
    try:
        query = _client().table("mesa_results").select("mesa_key", count="exact")
        if dept is not None:
            query = query.eq("dept", dept)
        if mpio is not None:
            query = query.eq("mpio", mpio)
        if zona is not None:
            query = query.eq("zona", zona)
        if puesto is not None:
            query = query.eq("puesto", puesto)
        if status is not None:
            query = query.eq("overall_status", status)
        if source_missing == "any":
            query = query.or_(
                "source_status->>e14c.neq.ok,"
                "source_status->>e14t.neq.ok,"
                "source_status->>e14d.neq.ok"
            )
        elif source_missing in ("e14c", "e14t", "e14d"):
            query = query.neq(f"source_status->>{source_missing}", "ok")
        response = query.execute()
        return response.count or 0
    except Exception as exc:
        logger.warning("count_mesa_results failed: %s", exc)
        return 0


def _fetch_mesa_results_batched(select_cols: str, dept: str | None = None) -> list[dict]:
    """
    Fetch all mesa_results rows for the given columns, paginating in 1000-row
    batches (Supabase enforces db-max-rows=1000 regardless of .limit()).

    Shared by _get_mesa_stats_uncached and _get_hierarchical_mesa_stats_uncached
    so both stay in sync on the pagination strategy.

    No ORDER BY: both callers only aggregate/count rows into dicts keyed by
    dept/mpio/puesto, so row order is irrelevant. An explicit
    `.order("mesa_key")` was here for OFFSET-pagination stability, but on
    this table size it makes Postgres sort (or scan a bloated index) on
    every page, and cost grows with offset until later pages hit the
    statement timeout entirely (confirmed 2026-07-19: with .order(), page 86
    at offset 86000 times out after ~9s; without it, all 123 pages complete
    in under a second each). This endpoint is a periodically-cached read
    (get_mesa_stats() TTL) against a table that isn't concurrently written
    during normal operation, so the small risk of a skipped/duplicated row
    under a mid-pagination write is an acceptable trade for not timing out.
    """
    _BATCH = 1000
    rows: list[dict] = []
    offset = 0
    while True:
        q = (
            _client().table("mesa_results")
            .select(select_cols)
            .range(offset, offset + _BATCH - 1)
        )
        if dept is not None:
            q = q.eq("dept", dept)
        batch = (q.execute().data) or []
        rows.extend(batch)
        if len(batch) < _BATCH:
            break
        offset += _BATCH
    return rows


def _get_mesa_stats_uncached(dept: str | None = None) -> dict:
    """
    Fetch overall_status distribution from mesa_results, grouped by dept.

    Returns a dict of the shape:
        {
            "01": {"clean": N, "known_anomaly": N, "warning": N,
                   "discrepancy": N, "needs_review_large_delta": N, "total": N},
            ...
            "_global": {"clean": N, ..., "total": N},
        }

    Internal helper — callers should use get_mesa_stats() for the cached
    version. Exposed at module level so tests can bypass the cache.

    Returns {} on any exception (fail-closed).
    """
    try:
        rows = _fetch_mesa_results_batched("dept, overall_status", dept=dept)

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


def get_public_stats() -> dict:
    """
    Return aggregate transparency counters for the public home page.

    Reuses the ``_mesa_stats_cache`` with key ``"public_stats"`` and the same
    ``_MESA_STATS_TTL`` (5-minute) TTL as ``get_mesa_stats()``.

    The function issues up to two COUNT-only Supabase queries per cache
    period: one via ``get_mesa_stats()`` (for ``mesas_analyzed`` /
    ``total_anomalias``) and one via ``count_mesa_results(source_missing=
    "e14c")`` (for ``mesas_sin_e14c``, national scope — includes dept
    88/exterior). ``mesas_all_three`` is a hardcoded disk-audit constant, not
    a query.

    Returns:
        Dict with keys: ``mesas_all_three``, ``mesas_analyzed``,
        ``mesas_remaining``, ``total_anomalias``, ``total_universe``,
        ``mesas_sin_e14c``.
        Returns ``_PUBLIC_STATS_ZEROS`` on any exception — never re-raises.
    """
    cache_key = "public_stats"
    now = time.monotonic()
    entry = _mesa_stats_cache.get(cache_key)
    if entry is not None and (now - entry["ts"]) < _MESA_STATS_TTL:
        return entry["data"]

    try:
        # --- mesas_analyzed and total_anomalias from get_mesa_stats() ---
        mesa_stats = get_mesa_stats()
        global_counts = mesa_stats.get("_global", {})
        mesas_analyzed: int = global_counts.get("total", 0)
        total_anomalias: int = (
            global_counts.get("known_anomaly", 0)
            + global_counts.get("warning", 0)
            + global_counts.get("discrepancy", 0)
            + global_counts.get("needs_review_large_delta", 0)
        )

        # mesas_all_three: known constant from disk audit (2026-07-04).
        mesas_all_three: int = MESAS_ALL_THREE

        # mesas_sin_e14c: precomputed constant (see MESAS_SIN_E14C) — same
        # pattern as mesas_all_three. A live COUNT/pagination that touches
        # source_status across all 122k rows was timing out regardless of
        # approach on this Supabase instance (see MESAS_SIN_E14C docstring).
        mesas_sin_e14c: int = MESAS_SIN_E14C

        result = {
            "mesas_all_three": mesas_all_three,
            "mesas_analyzed": mesas_analyzed,
            "mesas_remaining": max(0, TOTAL_UNIVERSE - mesas_analyzed),
            "total_anomalias": total_anomalias,
            "total_universe": TOTAL_UNIVERSE,
            "mesas_sin_e14c": mesas_sin_e14c,
        }
        _mesa_stats_cache[cache_key] = {"data": result, "ts": now}
        return result

    except Exception as exc:
        logger.warning("get_public_stats failed: %s", exc)
        return dict(_PUBLIC_STATS_ZEROS)


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


# ---------------------------------------------------------------------------
# mesa-semaphore Slice 2 — review-state aggregator
# ---------------------------------------------------------------------------

# Separate TTL cache for the review semaphore (same 5-min TTL as mesa stats).
_review_semaphore_cache: dict = {}
_REVIEW_SEMAPHORE_TTL = 300  # seconds


def _classify_revisada_result(crops_for_mesa: list[dict]) -> str:
    """
    Classify a REVISADA mesa as mesa_limpia, posible_fraude, or sin_datos.

    Logic (design D6):
      - sum confirmed_label of all crops where field_name starts with 'candidato_'
      - compare to confirmed_label of the crop where field_name == 'total_urna'
      - if sum == total → 'mesa_limpia'
      - if sum != total → 'posible_fraude'
      - if any operand is non-numeric or missing → 'sin_datos'
    """
    candidato_labels = [
        c.get("confirmed_label")
        for c in crops_for_mesa
        if (c.get("field_name") or "").startswith("candidato_")
    ]
    total_urna_label = next(
        (c.get("confirmed_label") for c in crops_for_mesa if c.get("field_name") == "total_urna"),
        None,
    )
    if not candidato_labels:
        return "sin_datos"
    try:
        suma = sum(int(x) for x in candidato_labels if x is not None)
        total = int(total_urna_label)
    except (TypeError, ValueError):
        return "sin_datos"
    return "mesa_limpia" if suma == total else "posible_fraude"


def _get_review_semaphore_uncached(dept: str | None = None) -> dict:
    """
    Call the get_review_semaphore_by_dept RPC and return classified results.

    Return shape:
        {
            "<mesa_key_mr>": {
                "dept": "01",
                "en_revision": bool,
                "revisada": bool,
                "revisada_result": "mesa_limpia" | "posible_fraude" | "sin_datos" | None,
            },
            ...
        }

    Returns {} on any exception (fail-closed).
    """
    try:
        params: dict = {}
        if dept is not None:
            params["p_dept"] = dept
        response = _client().rpc("get_review_semaphore_by_dept", params).execute()
        rows: list[dict] = response.data or []

        result: dict[str, dict] = {}
        for row in rows:
            mesa_mr = row.get("mesa_key_mr")
            if not mesa_mr:
                continue
            ann_sum = int(row.get("annotation_count_sum") or 0)
            prio_total = int(row.get("priority_total") or 0)
            prio_confirmed = int(row.get("priority_confirmed") or 0)

            en_revision = ann_sum >= 1 and not (prio_total > 0 and prio_confirmed == prio_total)
            revisada = prio_total > 0 and prio_confirmed == prio_total

            if revisada:
                candidato_sum = int(row.get("candidato_sum") or 0)
                total_urna_val = row.get("total_urna_val")
                if total_urna_val is not None:
                    revisada_result = "mesa_limpia" if candidato_sum == int(total_urna_val) else "posible_fraude"
                else:
                    revisada_result = "sin_datos"
            else:
                revisada_result = None

            result[mesa_mr] = {
                "dept": row.get("dept", mesa_mr[:2]),
                "en_revision": en_revision,
                "revisada": revisada,
                "revisada_result": revisada_result,
            }
        return result
    except Exception as exc:
        logger.warning("_get_review_semaphore_uncached failed: %s", exc)
        return {}


def get_mesa_semaphore_stats(dept: str | None = None) -> dict:
    """
    Return per-department 🟡/🟢 review state counts, with a 5-minute TTL cache.

    Return shape:
        {
            "01": {"en_revision": N, "revisada": N},
            ...
            "_global": {"en_revision": N, "revisada": N},
        }

    Crops with mesa_key_mr IS NULL are excluded (handled by the RPC).
    Returns {} on any exception (fail-closed).
    """
    cache_key = dept
    now = time.monotonic()
    entry = _review_semaphore_cache.get(cache_key)
    if entry is not None and (now - entry["ts"]) < _REVIEW_SEMAPHORE_TTL:
        return entry["data"]

    mesa_data = _get_review_semaphore_uncached(dept=dept)
    if not mesa_data:
        _review_semaphore_cache[cache_key] = {"data": {}, "ts": now}
        return {}

    # Aggregate per-dept counts
    per_dept: dict[str, dict[str, int]] = {}
    for mesa_mr, info in mesa_data.items():
        d = info.get("dept", mesa_mr[:2])
        if d not in per_dept:
            per_dept[d] = {"en_revision": 0, "revisada": 0, "limpia_count": 0, "fraude_count": 0}
        if info["revisada"]:
            per_dept[d]["revisada"] += 1
            result_str = info.get("revisada_result")
            if result_str == "mesa_limpia":
                per_dept[d]["limpia_count"] += 1
            elif result_str == "posible_fraude":
                per_dept[d]["fraude_count"] += 1
        elif info["en_revision"]:
            per_dept[d]["en_revision"] += 1

    global_en_revision = sum(v["en_revision"] for v in per_dept.values())
    global_revisada = sum(v["revisada"] for v in per_dept.values())
    global_limpia = sum(v["limpia_count"] for v in per_dept.values())
    global_fraude = sum(v["fraude_count"] for v in per_dept.values())
    result = {**per_dept, "_global": {
        "en_revision": global_en_revision,
        "revisada": global_revisada,
        "limpia_count": global_limpia,
        "fraude_count": global_fraude,
    }}

    _review_semaphore_cache[cache_key] = {"data": result, "ts": now}
    return result


# ---------------------------------------------------------------------------
# 3.14  transversal_review_decisions — visual review panel CRUD
# ---------------------------------------------------------------------------

_TRANSVERSAL_FIELDS = frozenset({"VOTANTES", "URNA", "SUMA_TOTAL"})
_TRANSVERSAL_SOURCES = frozenset({"e14c", "e14d", "e14t"})
_TRANSVERSAL_DECISIONS = frozenset({"accepted", "rejected"})
_TRANSVERSAL_REPORT_TYPES = frozenset({"campos_vacios", "enmienda", "otro"})
TRANSVERSAL_DECISION_EDIT_HOURS = 3


def get_mesa_raw_data(mesa_key: str) -> dict | None:
    """Return raw_data JSONB for one mesa_results row."""
    try:
        response = (
            _client()
            .table("mesa_results")
            .select("raw_data")
            .eq("mesa_key", mesa_key)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        return rows[0].get("raw_data")
    except Exception as exc:
        logger.warning("get_mesa_raw_data failed for %s: %s", mesa_key, exc)
        return None


def list_mesa_results_for_review(
    offset: int = 0,
    limit: int = 50,
    dept: str | None = None,
) -> list[dict]:
    """Paginated mesa_results rows with raw_data for transversal queue scan."""
    try:
        query = (
            _client()
            .table("mesa_results")
            .select("mesa_key, dept, mpio, zona, puesto, mesa, raw_data")
            .order("mesa_key")
        )
        if dept is not None:
            query = query.eq("dept", dept)
        response = query.range(offset, offset + limit - 1).execute()
        return response.data or []
    except Exception as exc:
        logger.warning("list_mesa_results_for_review failed: %s", exc)
        return []


def _parse_utc_ts(value: str) -> datetime:
    ts = value.replace("Z", "+00:00")
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def _empty_field_window() -> dict:
    return {
        "editable": True,
        "editable_until": None,
        "first_decision_at": None,
        "decision_count": 0,
    }


def _field_window_from_rows(rows: list[dict], *, now: datetime | None = None) -> dict:
    """Build one field's 3 h window from its decision rows (created_at)."""
    if not rows:
        return _empty_field_window()
    stamps = [_parse_utc_ts(r["created_at"]) for r in rows if r.get("created_at")]
    if not stamps:
        return _empty_field_window()
    first = min(stamps)
    until = first + timedelta(hours=TRANSVERSAL_DECISION_EDIT_HOURS)
    current = now or datetime.now(timezone.utc)
    return {
        "editable": current < until,
        "editable_until": until.isoformat(),
        "first_decision_at": first.isoformat(),
        "decision_count": len(rows),
    }


def get_transversal_decision_edit_window(mesa_key: str, field: str | None = None) -> dict:
    """Return edit window metadata scoped per field (3 h from first decision on that field).

    When ``field`` is set, returns that field's window dict (legacy single-window shape).
    When omitted, returns::

        {
          "scope": "field",
          "fields": { "VOTANTES": {...}, "URNA": {...}, "SUMA_TOTAL": {...} },
          "decision_count": <total slots decided on this mesa>,
        }
    """
    try:
        query = (
            _client()
            .table("transversal_review_decisions")
            .select("field, created_at")
            .eq("mesa_key", mesa_key)
        )
        if field is not None:
            query = query.eq("field", field)
        rows = (query.execute().data) or []
    except Exception as exc:
        logger.warning("get_transversal_decision_edit_window failed: %s", exc)
        if field is not None:
            return _empty_field_window()
        return {
            "scope": "field",
            "fields": {f: _empty_field_window() for f in _TRANSVERSAL_FIELDS},
            "decision_count": 0,
        }

    now = datetime.now(timezone.utc)
    if field is not None:
        return _field_window_from_rows(rows, now=now)

    by_field: dict[str, list[dict]] = {f: [] for f in _TRANSVERSAL_FIELDS}
    for row in rows:
        f = row.get("field")
        if f in by_field:
            by_field[f].append(row)

    fields = {
        f: _field_window_from_rows(by_field[f], now=now) for f in _TRANSVERSAL_FIELDS
    }
    return {
        "scope": "field",
        "fields": fields,
        "decision_count": sum(w["decision_count"] for w in fields.values()),
    }


def reopen_transversal_decisions(
    mesa_key: str,
    field: str | None = None,
) -> tuple[bool, str | None]:
    """Delete decisions so the reviewer can decide again.

    Scope is **per field**: pass ``field`` to clear one field (within its 3 h window).
    Without ``field``, clears every field that is still within its own window.
    """
    if field is not None:
        if field not in _TRANSVERSAL_FIELDS:
            return False, "invalid_field"
        window = get_transversal_decision_edit_window(mesa_key, field=field)
        if window["decision_count"] == 0:
            return False, "no_decisions"
        if not window["editable"]:
            return False, "edit_window_expired"
        try:
            (
                _client()
                .table("transversal_review_decisions")
                .delete()
                .eq("mesa_key", mesa_key)
                .eq("field", field)
                .execute()
            )
            return True, None
        except Exception as exc:
            logger.warning("reopen_transversal_decisions failed: %s", exc)
            return False, "db_error"

    # Reopen all fields still inside their individual windows.
    package = get_transversal_decision_edit_window(mesa_key)
    fields_meta = package.get("fields") or {}
    reopenable = [
        f for f, w in fields_meta.items()
        if w.get("decision_count", 0) > 0 and w.get("editable")
    ]
    if package.get("decision_count", 0) == 0:
        return False, "no_decisions"
    if not reopenable:
        return False, "edit_window_expired"
    try:
        for f in reopenable:
            (
                _client()
                .table("transversal_review_decisions")
                .delete()
                .eq("mesa_key", mesa_key)
                .eq("field", f)
                .execute()
            )
        return True, None
    except Exception as exc:
        logger.warning("reopen_transversal_decisions failed: %s", exc)
        return False, "db_error"


def upsert_transversal_decision(
    mesa_key: str,
    field: str,
    source: str,
    decision: str,
    reviewer_id: str,
    notes: str | None = None,
) -> bool:
    """Upsert one transversal review decision. Returns False on validation/DB error.

    Edit window is **per field** (3 h from the first decision on that field only).
    """
    if field not in _TRANSVERSAL_FIELDS:
        return False
    if source not in _TRANSVERSAL_SOURCES:
        return False
    if decision not in _TRANSVERSAL_DECISIONS:
        return False
    window = get_transversal_decision_edit_window(mesa_key, field=field)
    if window["decision_count"] > 0 and not window["editable"]:
        return False
    try:
        row = {
            "mesa_key": mesa_key,
            "field": field,
            "source": source,
            "decision": decision,
            "reviewer_id": reviewer_id,
            "notes": notes,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _client().table("transversal_review_decisions").upsert(
            row,
            on_conflict="mesa_key,field,source",
        ).execute()
        return True
    except Exception as exc:
        logger.warning("upsert_transversal_decision failed: %s", exc)
        return False


def _nest_transversal_rows(rows: list[dict]) -> dict:
    nested: dict[str, dict] = {}
    for row in rows:
        mk = row["mesa_key"]
        nested.setdefault(mk, {})
        nested[mk].setdefault(row["field"], {})
        nested[mk][row["field"]][row["source"]] = row["decision"]
    return nested


_DECIDED_SLOTS_CACHE: tuple[float, dict] | None = None
_PENDING_CACHE_TTL = 30.0


def _fetch_transversal_decision_rows(
    mesa_key: str | None = None,
    *,
    mesa_keys: Collection[str] | None = None,
) -> list[dict]:
    if mesa_keys is not None:
        if not mesa_keys:
            return []
        query = _client().table("transversal_review_decisions").select(
            "mesa_key, field, source, decision"
        )
        return (query.in_("mesa_key", list(mesa_keys)).execute().data) or []

    query = _client().table("transversal_review_decisions").select(
        "mesa_key, field, source, decision"
    )
    if mesa_key is not None:
        query = query.eq("mesa_key", mesa_key)
    return (query.execute().data) or []


def get_transversal_decisions(
    mesa_key: str | None = None,
    *,
    mesa_keys: Collection[str] | None = None,
) -> dict:
    """Return nested decisions: mesa → field → source → accepted|rejected."""
    try:
        rows = _fetch_transversal_decision_rows(mesa_key, mesa_keys=mesa_keys)
        return _nest_transversal_rows(rows)
    except Exception as exc:
        logger.warning("get_transversal_decisions failed: %s", exc)
        return {}


def get_transversal_decided_slots(*, force_reload: bool = False) -> dict:
    """Cached full decided-slot index for pending-queue reconciliation."""
    global _DECIDED_SLOTS_CACHE
    now = time.time()
    if (
        not force_reload
        and _DECIDED_SLOTS_CACHE is not None
        and now - _DECIDED_SLOTS_CACHE[0] < _PENDING_CACHE_TTL
    ):
        return _DECIDED_SLOTS_CACHE[1]

    nested = get_transversal_decisions()
    _DECIDED_SLOTS_CACHE = (now, nested)
    return nested


def clear_transversal_decided_slots_cache() -> None:
    global _DECIDED_SLOTS_CACHE
    _DECIDED_SLOTS_CACHE = None


def _serialize_transversal_report_row(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "source": row.get("source"),
        "report_type": row.get("report_type"),
        "notes": row.get("notes"),
        "fields": row.get("fields"),
        "annotator": row.get("annotator"),
        "created_at": row.get("created_at"),
    }


def list_transversal_reports(mesa_key: str) -> list[dict]:
    """Return reports for one mesa, newest first."""
    try:
        rows = (
            _client()
            .table("transversal_review_reports")
            .select("id, mesa_key, source, report_type, notes, fields, annotator, created_at")
            .eq("mesa_key", mesa_key)
            .order("created_at", desc=True)
            .execute()
            .data
        ) or []
        return [_serialize_transversal_report_row(r) for r in rows]
    except Exception as exc:
        logger.warning("list_transversal_reports failed: %s", exc)
        return []


def list_recent_transversal_reports(page: int = 1, per_page: int = 50) -> list[dict]:
    """Fetch a paginated, global listing of transversal_review_reports (all
    mesas, all sources), newest first — mirroring get_mesa_results()'s
    page/per_page/.range() pagination convention (design D7), but pointed at
    transversal_review_reports instead of mesa_results.

    Unlike list_transversal_reports(mesa_key), this reads across ALL mesas
    (no .eq() filter) — intended for the admin "Reportes de usuarios" queue.
    Pagination is 1-based via PostgREST .range(offset, offset+per_page-1).

    Args:
        page:     1-based page number (page=1 -> offset 0).
        per_page: Number of rows per page (default 50, mirrors the admin
                  mesa_results route convention).

    Returns:
        List of report row dicts, newest first, or [] on any error
        (fail-closed).
    """
    try:
        offset = (page - 1) * per_page
        rows = (
            _client()
            .table("transversal_review_reports")
            .select("id, mesa_key, source, report_type, notes, fields, annotator, created_at")
            .order("created_at", desc=True)
            .range(offset, offset + per_page - 1)
            .execute()
            .data
        ) or []
        return [
            {**_serialize_transversal_report_row(r), "mesa_key": r.get("mesa_key")}
            for r in rows
        ]
    except Exception as exc:
        logger.warning("list_recent_transversal_reports failed: %s", exc)
        return []


def count_recent_transversal_reports() -> int:
    """Return the total count of transversal_review_reports rows (no
    filters), mirroring count_mesa_results()'s count="exact" convention.

    Used to compute total pages for the admin "Reportes de usuarios" tab.
    Returns 0 on any exception (fail-closed).
    """
    try:
        response = (
            _client()
            .table("transversal_review_reports")
            .select("id", count="exact")
            .execute()
        )
        return response.count or 0
    except Exception as exc:
        logger.warning("count_recent_transversal_reports failed: %s", exc)
        return 0


def insert_transversal_reports(
    mesa_key: str,
    entries: list[dict],
    annotator: str,
) -> list[dict]:
    """Insert one or more structured reports for a mesa."""
    rows: list[dict] = []
    for entry in entries:
        source = entry.get("source")
        report_type = entry.get("report_type")
        notes = (entry.get("notes") or "").strip()
        if source not in _TRANSVERSAL_SOURCES:
            return []
        if report_type not in _TRANSVERSAL_REPORT_TYPES:
            return []
        if not notes:
            return []
        row = {
            "mesa_key": mesa_key,
            "source": source,
            "report_type": report_type,
            "notes": notes,
            "fields": entry.get("fields"),
            "annotator": annotator,
        }
        rows.append(row)
    if not rows:
        return []
    try:
        inserted = (
            _client()
            .table("transversal_review_reports")
            .insert(rows)
            .execute()
            .data
        ) or []
        return [_serialize_transversal_report_row(r) for r in inserted]
    except Exception as exc:
        logger.warning("insert_transversal_reports failed: %s", exc)
        return []


def delete_transversal_report(
    report_id: str,
    annotator: str,
    *,
    allow_any: bool = False,
) -> bool:
    """Delete a report when owned by annotator (or allow_any for admin)."""
    try:
        rows = (
            _client()
            .table("transversal_review_reports")
            .select("id, annotator")
            .eq("id", report_id)
            .limit(1)
            .execute()
            .data
        ) or []
        if not rows:
            return False
        owner = rows[0].get("annotator")
        if not allow_any and owner != annotator:
            return False
        (
            _client()
            .table("transversal_review_reports")
            .delete()
            .eq("id", report_id)
            .execute()
        )
        return True
    except Exception as exc:
        logger.warning("delete_transversal_report failed: %s", exc)
        return False


def get_transversal_reports_grouped() -> dict[str, list[dict]]:
    """All reports grouped by mesa_key for export."""
    try:
        rows = (
            _client()
            .table("transversal_review_reports")
            .select("id, mesa_key, source, report_type, notes, fields, annotator, created_at")
            .order("created_at")
            .execute()
            .data
        ) or []
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            mk = row["mesa_key"]
            grouped.setdefault(mk, []).append(_serialize_transversal_report_row(row))
        return grouped
    except Exception as exc:
        logger.warning("get_transversal_reports_grouped failed: %s", exc)
        return {}


def export_transversal_decisions(dataset: str | None = None) -> dict:
    """Full export envelope for download (spec §10)."""
    from src.modules.review.export import build_export_envelope

    nested = get_transversal_decisions()
    slug = dataset or os.environ.get(
        "TRANSVERSAL_DATASET",
        "transversal_review_E14C_conflictivas",
    )
    payload = build_export_envelope(nested, dataset=slug)
    payload["reports"] = get_transversal_reports_grouped()
    return payload


# ---------------------------------------------------------------------------
# Mesas hierarchical drill-down — Work Unit 1 / Phase 1 (DB Foundation)
# ---------------------------------------------------------------------------

# Separate TTL cache for the hierarchical stats (same 5-min TTL convention as
# _mesa_stats_cache / _review_semaphore_cache). Single global scan, so the
# cache key is a fixed constant rather than a dept param.
_hierarchical_stats_cache: dict = {}
_HIERARCHICAL_STATS_TTL = 300  # seconds
_HIERARCHICAL_STATS_CACHE_KEY = "hierarchical"

def _empty_hierarchical_bucket() -> dict:
    """A fresh per-bucket dict: the 5-status taxonomy + total + review counts."""
    bucket = {st: 0 for st in _MESA_STATUSES}
    bucket["total"] = 0
    bucket["en_revision"] = 0
    bucket["revisada"] = 0
    return bucket


def _accumulate_status(bucket: dict, status: str, count: int = 1) -> None:
    """Add `count` to bucket[status] (if it is a known status) and bucket['total']."""
    if status in bucket:
        bucket[status] += count
    bucket["total"] += count


def _hierarchical_coords_from_row(row: dict) -> tuple[str, str, str, str] | None:
    """Extract dept/mpio/zona/puesto from columns or mesa_key fallback."""
    dept = row.get("dept")
    mpio = row.get("mpio")
    zona = row.get("zona")
    puesto = row.get("puesto")
    if dept and mpio and zona and puesto:
        return str(dept), str(mpio), str(zona), str(puesto)
    mk = (row.get("mesa_key") or "").strip()
    parts = mk.split("_")
    if len(parts) >= 5:
        return parts[0], parts[1], parts[2], parts[3]
    return None


def _empty_hierarchical_shape() -> dict:
    """Fresh empty result (no shared nested dicts)."""
    return {
        "by_mpio": {},
        "by_puesto": {},
        "_global": _empty_hierarchical_bucket(),
        "review_by_mesa": {},
    }


def _fetch_hierarchical_rows_grouped() -> list[dict]:
    """
    Fetch one row per (dept, mpio, zona, puesto, overall_status) via the
    get_hierarchical_mesa_stats_grouped() RPC — Postgres does the GROUP BY
    instead of Python looping over every mesa, so this is a handful of
    paginated round trips (~28k grouped rows nationally) instead of one per
    mesa (100k+).

    PostgREST/Supabase hard-caps every response at 1000 rows regardless of
    the requested .range() — confirmed 2026-07-23: .range(0, 1999) still
    returned exactly 1000. An unranged call silently truncates to the first
    1000 grouped rows, which (depending on row order) can be entirely one
    department — this is exactly the bug that made /mesas show only
    ANTIOQUIA. Must paginate in 1000-row pages until a page comes back
    short.

    Falls back to the old per-mesa batched scan (scripts/deploy/
    add_hierarchical_mesa_stats_rpc.sql not yet applied on this environment,
    or the RPC call itself fails) — preserving the mesa_key-parse fallback
    for the column select — so the route keeps working either way, just
    slower. Raises only if BOTH paths fail — caller handles fail-closed.
    """
    try:
        _RPC_PAGE = 1000
        rows: list[dict] = []
        rpc_offset = 0
        while True:
            resp = (
                _client()
                .rpc("get_hierarchical_mesa_stats_grouped", {})
                .range(rpc_offset, rpc_offset + _RPC_PAGE - 1)
                .execute()
            )
            page = resp.data or []
            rows.extend(page)
            if len(page) < _RPC_PAGE:
                break
            rpc_offset += _RPC_PAGE
        return rows
    except Exception as exc:
        logger.warning(
            "get_hierarchical_mesa_stats_grouped RPC failed, falling back to "
            "per-mesa batched scan: %s", exc,
        )
        # Prefer the same slim column set as get_mesa_stats + geo coords.
        # Fall back to mesa_key parse if a wider select is rejected by PostgREST.
        try:
            rows = _fetch_mesa_results_batched(
                "mesa_key, dept, mpio, zona, puesto, overall_status"
            )
        except Exception as fetch_exc:
            logger.warning(
                "hierarchical select with coords failed, falling back to mesa_key: %s",
                fetch_exc,
            )
            rows = _fetch_mesa_results_batched("mesa_key, overall_status")

        grouped: dict[tuple, int] = {}
        for row in rows:
            coords = _hierarchical_coords_from_row(row)
            if coords is None:
                continue
            key = (*coords, row.get("overall_status", "unknown"))
            grouped[key] = grouped.get(key, 0) + 1
        return [
            {"dept": d, "mpio": m, "zona": z, "puesto": p, "overall_status": s, "n": n}
            for (d, m, z, p, s), n in grouped.items()
        ]


def _get_hierarchical_mesa_stats_uncached() -> dict:
    """
    Build the by_mpio / by_puesto / _global / review_by_mesa aggregation
    described by the mesas hierarchical drill-down design (D-Phase1).

    Does ONE grouped-aggregation round trip (get_hierarchical_mesa_stats_grouped
    RPC — see _fetch_hierarchical_rows_grouped) plus one call to the review
    semaphore RPC (via _get_review_semaphore_uncached) to merge in 🟡/🟢 review
    counts, parsed from mesa_key_mr segments (dept_mpio_zona_puesto_mesa).

    Only mpio/puesto combinations with >=1 analyzed mesa are present (no
    zero-row DIVIPOLE entries), since buckets are built strictly from
    mesa_results rows.

    Returns the empty shape on hard failure (fail-closed). Semaphore merge
    failures never discard status aggregation.
    """
    try:
        rows = _fetch_hierarchical_rows_grouped()
    except Exception as exc:
        logger.warning("_get_hierarchical_mesa_stats_uncached fetch failed: %s", exc)
        return _empty_hierarchical_shape()

    by_mpio: dict[str, dict] = {}
    by_puesto: dict[str, dict[str, dict]] = {}
    global_counts = _empty_hierarchical_bucket()

    for row in rows:
        dept = row.get("dept", "unknown")
        mpio = row.get("mpio", "unknown")
        zona = row.get("zona", "unknown")
        puesto = row.get("puesto", "unknown")
        status = row.get("overall_status", "unknown")
        n = row.get("n", 0)

        mpio_key = f"{dept}_{mpio}"
        if mpio_key not in by_mpio:
            bucket = _empty_hierarchical_bucket()
            bucket["dept"] = dept
            bucket["mpio"] = mpio
            by_mpio[mpio_key] = bucket
        _accumulate_status(by_mpio[mpio_key], status, n)

        puesto_bucket_map = by_puesto.setdefault(mpio_key, {})
        puesto_key = f"{zona}_{puesto}"
        if puesto_key not in puesto_bucket_map:
            bucket = _empty_hierarchical_bucket()
            bucket["dept"] = dept
            bucket["mpio"] = mpio
            bucket["zona"] = zona
            bucket["puesto"] = puesto
            puesto_bucket_map[puesto_key] = bucket
        _accumulate_status(puesto_bucket_map[puesto_key], status, n)

        _accumulate_status(global_counts, status, n)

    # Review counts are best-effort — never wipe status aggregation on RPC failure.
    review_by_mesa: dict[str, dict] = {}
    try:
        semaphore_data = _get_review_semaphore_uncached()
        for mesa_mr, info in semaphore_data.items():
            parts = mesa_mr.split("_")
            if len(parts) != 5:
                continue
            dept, mpio, zona, puesto, _mesa = parts

            review_by_mesa[mesa_mr] = {
                "en_revision": info.get("en_revision", False),
                "revisada": info.get("revisada", False),
                "revisada_result": info.get("revisada_result"),
            }

            mpio_key = f"{dept}_{mpio}"
            puesto_key = f"{zona}_{puesto}"
            if mpio_key not in by_mpio:
                continue
            targets = [global_counts, by_mpio[mpio_key]]
            if mpio_key in by_puesto and puesto_key in by_puesto[mpio_key]:
                targets.append(by_puesto[mpio_key][puesto_key])

            for bucket in targets:
                if info.get("revisada"):
                    bucket["revisada"] += 1
                elif info.get("en_revision"):
                    bucket["en_revision"] += 1
    except Exception as exc:
        logger.warning("hierarchical semaphore merge failed: %s", exc)

    return {
        "by_mpio": by_mpio,
        "by_puesto": by_puesto,
        "_global": global_counts,
        "review_by_mesa": review_by_mesa,
    }


def get_hierarchical_mesa_stats() -> dict:
    """
    Return the hierarchical mesa stats aggregation, with a 5-min TTL cache.

    Return shape:
        {
            "by_mpio":   {"{dept}_{mpio}": {..status counts.., "total": N,
                                             "en_revision": N, "revisada": N}},
            "by_puesto": {"{dept}_{mpio}": {"{zona}_{puesto}": {...same shape...}}},
            "_global":   {...same shape... national totals},
            "review_by_mesa": {"{dept}_{mpio}_{zona}_{puesto}_{mesa}":
                                {"en_revision": bool, "revisada": bool,
                                 "revisada_result": str | None}},
        }

    Delegates to _get_hierarchical_mesa_stats_uncached() on a cache miss.
    Successful non-empty results are cached; empty/fail-closed results are not
    (so a transient outage does not pin zeros for the full TTL).
    """
    now = time.monotonic()
    entry = _hierarchical_stats_cache.get(_HIERARCHICAL_STATS_CACHE_KEY)
    if entry is not None and (now - entry["ts"]) < _HIERARCHICAL_STATS_TTL:
        return entry["data"]

    try:
        data = _get_hierarchical_mesa_stats_uncached()
    except Exception as exc:
        logger.warning("get_hierarchical_mesa_stats failed: %s", exc)
        return _empty_hierarchical_shape()

    total = (data.get("_global") or {}).get("total", 0) or 0
    if total > 0 or data.get("by_mpio"):
        _hierarchical_stats_cache[_HIERARCHICAL_STATS_CACHE_KEY] = {
            "data": data,
            "ts": now,
        }
    return data


def mesa_result_exists(mesa_key: str) -> bool | None:
    """
    Return True if a row with this mesa_key exists in mesa_results,
    False if the lookup succeeded and found nothing, or None if the
    lookup itself failed (Supabase unreachable/erroring).

    Used as an anti-forgery guard before inserting a public mesa report
    (SDD: public-mesa-report, Phase 1) — a POST body can carry any
    mesa_key string, so it must be validated server-side against the
    real table before it is trusted for a write into the shared
    transversal_review_reports table.

    Fail-closed: None is never treated as "exists" by callers — but it
    is distinct from a confirmed False, so a lookup outage can be
    reported to the citizen as "try again" (503) instead of the
    misleading "that mesa doesn't exist" (400).
    """
    try:
        resp = (
            _client()
            .table("mesa_results")
            .select("mesa_key")
            .eq("mesa_key", mesa_key)
            .limit(1)
            .execute()
        )
        return bool(resp.data)
    except Exception as exc:
        logger.error("mesa_result_exists lookup failed: %s", exc)
        return None
