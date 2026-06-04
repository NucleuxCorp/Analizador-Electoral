"""
tests/labeler/test_rpc.py — Integration tests for the assign_next_crop RPC.

These tests require a live Supabase project or local Postgres instance with
the schema from scripts/supabase_schema.sql and the function from
scripts/assign_next_crop.sql deployed.

All tests are marked with pytest.mark.integration and are SKIPPED automatically
when SUPABASE_URL or SUPABASE_ANON_KEY are not set in the environment.

Run with:
    pytest tests/labeler/test_rpc.py -m integration

Or set env vars and run the full suite:
    SUPABASE_URL=... SUPABASE_ANON_KEY=... pytest tests/labeler/test_rpc.py
"""
from __future__ import annotations

import os
import threading
import uuid
from typing import Optional

import pytest


# ---------------------------------------------------------------------------
# Skip marker: skip entire module if SUPABASE_URL is not set
# ---------------------------------------------------------------------------

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
_SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()

pytestmark = pytest.mark.integration

_SKIP_REASON = (
    "Integration tests require SUPABASE_URL and SUPABASE_ANON_KEY environment "
    "variables pointing to a Supabase test project or local Postgres instance."
)

_SUPABASE_AVAILABLE = bool(_SUPABASE_URL and _SUPABASE_KEY)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def supabase_client():
    """Return a live Supabase sync client (skips if env not set)."""
    if not _SUPABASE_AVAILABLE:
        pytest.skip(_SKIP_REASON)

    from supabase import create_client  # type: ignore[import]
    return create_client(_SUPABASE_URL, _SUPABASE_KEY)


@pytest.fixture
def isolated_crop(supabase_client):
    """
    Insert a fresh test crop row (status='pending', annotation_count=0)
    and clean it up after the test.

    Yields the crop_id.
    """
    crop_id = f"test-rpc-{uuid.uuid4().hex[:12]}"
    supabase_client.table("crops").insert({
        "crop_id": crop_id,
        "pdf_path": "test/fake.pdf",
        "field_name": "test_field",
        "digit_index": 0,
        "label_ocr": "5",
        "confidence": 0.9,
        "priority": 2,
        "annotation_count": 0,
        "status": "pending",
    }).execute()

    yield crop_id

    # Cleanup: remove crop, labels, assignments for this test crop
    supabase_client.table("assignments").delete().eq("crop_id", crop_id).execute()
    supabase_client.table("labels").delete().eq("crop_id", crop_id).execute()
    supabase_client.table("crops").delete().eq("crop_id", crop_id).execute()


# ---------------------------------------------------------------------------
# 6.4a  assign_next_crop — basic assignment
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_assign_next_crop_returns_crop_id(supabase_client, isolated_crop):
    """assign_next_crop must return the crop_id when a crop is available."""
    annotator_id = str(uuid.uuid4())

    response = supabase_client.rpc(
        "assign_next_crop",
        {"p_annotator_id": annotator_id},
    ).execute()

    # Clean up the assignment
    supabase_client.table("assignments").delete().eq("annotator_id", annotator_id).execute()

    assert response.data is not None
    # The returned crop_id must be a non-empty string
    assert isinstance(response.data, str)
    assert len(response.data) > 0


# ---------------------------------------------------------------------------
# 6.4b  assign_next_crop — concurrent callers, no collision
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_assign_next_crop_collision_free_concurrent(supabase_client, isolated_crop):
    """
    Two concurrent callers on the same last available crop must receive
    different assignments (no collision).

    Since SKIP LOCKED is used server-side, one caller gets the crop and the
    other either gets a different crop or NULL.  Neither gets an error and
    no duplicate (crop_id, annotator_id) pair is created.
    """
    crop_id = isolated_crop
    results: list[Optional[str]] = [None, None]
    errors: list[Optional[Exception]] = [None, None]

    annotator_1 = str(uuid.uuid4())
    annotator_2 = str(uuid.uuid4())

    def _call_rpc(index: int, annotator_id: str) -> None:
        try:
            # Each thread gets its own Supabase client to simulate true concurrency
            from supabase import create_client  # type: ignore[import]
            client = create_client(_SUPABASE_URL, _SUPABASE_KEY)
            resp = client.rpc(
                "assign_next_crop",
                {"p_annotator_id": annotator_id},
            ).execute()
            results[index] = resp.data
        except Exception as exc:
            errors[index] = exc

    t1 = threading.Thread(target=_call_rpc, args=(0, annotator_1))
    t2 = threading.Thread(target=_call_rpc, args=(1, annotator_2))

    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    # No exceptions from either thread
    assert errors[0] is None, f"Thread 1 raised: {errors[0]}"
    assert errors[1] is None, f"Thread 2 raised: {errors[1]}"

    # At most one of them should have received the isolated_crop
    # (The other should have received NULL or a different crop)
    results_with_data = [r for r in results if r is not None]
    # Both getting the same crop_id would be a collision — this must not happen
    if len(results_with_data) == 2:
        assert results[0] != results[1], (
            f"COLLISION: both annotators received the same crop_id={results[0]}"
        )

    # Cleanup
    supabase_client.table("assignments").delete().eq("annotator_id", annotator_1).execute()
    supabase_client.table("assignments").delete().eq("annotator_id", annotator_2).execute()


# ---------------------------------------------------------------------------
# 6.4c  assign_next_crop — same annotator cannot get same crop twice
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_assign_next_crop_same_annotator_no_duplicate(supabase_client, isolated_crop):
    """
    The same annotator calling assign_next_crop twice must not receive the
    same crop_id twice (invariant I1: annotator must not label same crop twice).
    """
    annotator_id = str(uuid.uuid4())

    resp1 = supabase_client.rpc(
        "assign_next_crop",
        {"p_annotator_id": annotator_id},
    ).execute()
    crop_1 = resp1.data

    resp2 = supabase_client.rpc(
        "assign_next_crop",
        {"p_annotator_id": annotator_id},
    ).execute()
    crop_2 = resp2.data

    # If both got a crop, they must be different
    if crop_1 and crop_2:
        assert crop_1 != crop_2, "Same annotator received the same crop_id on second call"

    # Cleanup
    supabase_client.table("assignments").delete().eq("annotator_id", annotator_id).execute()


# ---------------------------------------------------------------------------
# 6.4d  assign_next_crop — expired assignment is eligible for reassignment
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_assign_next_crop_expired_assignment_is_requeued(supabase_client, isolated_crop):
    """
    A crop with an expired assignment (expires_at in the past) must be
    eligible for reassignment by a new annotator.
    """
    from datetime import datetime, timedelta, timezone

    crop_id = isolated_crop
    original_annotator = str(uuid.uuid4())
    new_annotator = str(uuid.uuid4())

    # Insert an already-expired assignment directly (bypassing the RPC)
    expired_at = (
        datetime.now(tz=timezone.utc) - timedelta(hours=1)
    ).isoformat()
    supabase_client.table("assignments").insert({
        "crop_id": crop_id,
        "annotator_id": original_annotator,
        "assigned_at": (
            datetime.now(tz=timezone.utc) - timedelta(hours=2)
        ).isoformat(),
        "expires_at": expired_at,
    }).execute()

    # New annotator calls the RPC — must receive the crop (expired assignment ignored)
    resp = supabase_client.rpc(
        "assign_next_crop",
        {"p_annotator_id": new_annotator},
    ).execute()

    # Cleanup
    supabase_client.table("assignments").delete().eq("annotator_id", original_annotator).execute()
    supabase_client.table("assignments").delete().eq("annotator_id", new_annotator).execute()

    assert resp.data == crop_id, (
        f"Expected crop_id={crop_id} but got {resp.data!r}. "
        "Expired assignment may not be treated as inactive."
    )
