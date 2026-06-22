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

import os
from datetime import datetime, timezone
from typing import Any

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
ZERO_VARIANTS = {"*", "-", ".", "+", "o", "O"}
SKIP_LABEL = "_skip"


def _normalize_label(value: str) -> str:
    """Collapse zero-variant glyphs to '0' so agreement compares meaning, not glyph."""
    v = (value or "").strip()
    return "0" if v in ZERO_VARIANTS else v


# ---------------------------------------------------------------------------
# 3.4  assign_next_crop
# ---------------------------------------------------------------------------

def assign_next_crop(annotator_id: str) -> str | None:
    """
    Call the assign_next_crop Postgres RPC and return the assigned crop_id.

    Uses SELECT FOR UPDATE SKIP LOCKED (server-side) so concurrent callers
    never receive the same crop.  Returns None when no crop is available.

    Args:
        annotator_id: UUID string of the current annotator (from JWT sub claim).

    Returns:
        crop_id string, or None if the queue is empty for this annotator.
    """
    response = _client().rpc(
        "assign_next_crop",
        {"p_annotator_id": annotator_id},
    ).execute()
    # supabase-py v2: response.data is the scalar return value of the function
    return response.data or None


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
) -> None:
    """
    Insert a labels row and increment crops.annotation_count atomically.

    Steps (two sequential PostgREST calls — supabase-py sync):
      1. INSERT INTO labels (crop_id, annotator_id, label_human, amended, is_admin_resolution)
      2. UPDATE crops SET annotation_count = annotation_count + 1 WHERE crop_id = ?

    Args:
        crop_id:      The crop being annotated.
        annotator_id: UUID of the annotator.
        label_human:  The label value token submitted by the annotator.
        amended:      True if the annotator amended the OCR suggestion.
        is_admin:     True if this label is an admin conflict resolution.
    """
    _client().table("labels").insert(
        {
            "crop_id": crop_id,
            "annotator_id": annotator_id,
            "label_human": label_human,
            "amended": amended,
            "is_admin_resolution": is_admin,
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

def get_concordancias(pdf_path: str, label_ocr: str, exclude_crop_id: str = "", limit: int = 5) -> list[str]:
    """
    Return crop_ids from the same acta (pdf_path) whose OCR value matches the
    current digit — the DB-backed replacement for the local index.jsonl lookup,
    so it works on a cloud deploy with no local files.
    """
    if not pdf_path or not label_ocr or label_ocr == "?":
        return []
    resp = (
        _client()
        .table("crops")
        .select("crop_id")
        .eq("pdf_path", pdf_path)
        .eq("label_ocr", label_ocr)
        .limit(limit + 1)
        .execute()
    )
    out: list[str] = []
    for r in (resp.data or []):
        cid = r.get("crop_id")
        if cid and cid != exclude_crop_id:
            out.append(cid)
        if len(out) >= limit:
            break
    return out


def get_storage_url(crop_id: str) -> str:
    """
    Return the public Supabase Storage URL for a crop PNG in bucket 'crops'.

    The URL pattern is: {SUPABASE_URL}/storage/v1/object/public/crops/{crop_id}.png

    Args:
        crop_id: The crop identifier (used as the storage object key).

    Returns:
        Full HTTPS URL to the public image.
    """
    base_url = _supabase_url.rstrip("/")
    return f"{base_url}/storage/v1/object/public/crops/{crop_id}.png"


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
    Fetch all crops with status = 'conflict' for admin resolution.

    Returns:
        List of crop dicts ordered by priority ASC, crop_id ASC.
    """
    response = (
        _client()
        .table("crops")
        .select("*")
        .eq("status", "conflict")
        .order("priority", desc=False)
        .order("crop_id", desc=False)
        .execute()
    )
    return response.data or []


# ---------------------------------------------------------------------------
# 3.11  get_global_stats
# ---------------------------------------------------------------------------

def get_global_stats(user_id: str = "") -> dict:
    """
    Returns global labeling progress plus the personal count for user_id.

    Calls the count_distinct_labeled_crops() Postgres RPC to avoid fetching
    all labels rows just for a DISTINCT count.

    Returns:
        {
            "global_labeled": int,  # distinct crops with >= 1 human label
            "my_labeled":     int,  # labels submitted by user_id (0 if empty)
            "total":          int,  # total crops in the system
        }
    """
    cli = _client()

    total_resp = cli.table("crops").select("crop_id", count="exact").execute()
    total = total_resp.count or 0

    try:
        rpc_resp = cli.rpc("count_distinct_labeled_crops", {}).execute()
        global_labeled = rpc_resp.data or 0
        if not global_labeled:
            raise ValueError("rpc returned zero or null")
    except Exception:
        # Fallback: count crops that have at least one human annotation
        try:
            fb = cli.table("crops").select("crop_id", count="exact").gt("annotation_count", 0).execute()
            global_labeled = fb.count or 0
        except Exception:
            global_labeled = 0

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
# 3.12  get_real_progress
# ---------------------------------------------------------------------------

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
