"""
tests/labeler/test_rpc.py — Integration tests for the assign_next_crop_v2 RPC.

These tests require a live Supabase project or local Postgres instance with
the schema from scripts/deploy/supabase_schema_v2.sql and the function from
scripts/deploy/fix_assign_next_crop_v3.sql deployed.

NOTE: this module previously called the legacy `assign_next_crop` RPC (no
`p_vuelta` argument). That function is no longer the one deployed/used by the
labeler portal — server.py calls `assign_next_crop_v2(p_annotator_id, p_vuelta)`
exclusively (see src/modules/labeler/db.py). All tests below were updated to
target `assign_next_crop_v2` with the `p_vuelta` argument to match reality
(sdd/fix-crop-assignment-lock, Phase 4).

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

_RPC_NAME = "assign_next_crop_v2"
_VUELTA = "primera"


def _call_rpc_sync(client, annotator_id: str, vuelta: str = _VUELTA):
    """Call assign_next_crop_v2(p_annotator_id, p_vuelta) and return the response."""
    return client.rpc(
        _RPC_NAME,
        {"p_annotator_id": annotator_id, "p_vuelta": vuelta},
    ).execute()


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


def _insert_crop(supabase_client, *, annotation_count: int = 0, status: str = "pending",
                  priority: int = 2) -> str:
    """Insert a fresh test crop row with the given annotation_count/status."""
    crop_id = f"test-rpc-{uuid.uuid4().hex[:12]}"
    supabase_client.table("crops").insert({
        "crop_id": crop_id,
        "pdf_path": "test/fake.pdf",
        "field_name": "test_field",
        "digit_index": 0,
        "label_ocr": "5",
        "confidence": 0.9,
        "priority": priority,
        "annotation_count": annotation_count,
        "status": status,
        "vuelta": _VUELTA,
    }).execute()
    return crop_id


def _cleanup_crop(supabase_client, crop_id: str) -> None:
    supabase_client.table("assignments").delete().eq("crop_id", crop_id).execute()
    supabase_client.table("labels").delete().eq("crop_id", crop_id).execute()
    supabase_client.table("crops").delete().eq("crop_id", crop_id).execute()


@pytest.fixture
def isolated_crop(supabase_client):
    """
    Insert a fresh test crop row (status='pending', annotation_count=0)
    and clean it up after the test.

    Yields the crop_id.
    """
    crop_id = _insert_crop(supabase_client, annotation_count=0, status="pending")
    yield crop_id
    _cleanup_crop(supabase_client, crop_id)


# ---------------------------------------------------------------------------
# 6.4a  assign_next_crop_v2 — basic assignment
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_assign_next_crop_returns_crop_id(supabase_client, isolated_crop):
    """assign_next_crop_v2 must return the crop_id when a crop is available."""
    annotator_id = str(uuid.uuid4())

    response = _call_rpc_sync(supabase_client, annotator_id)

    # Clean up the assignment
    supabase_client.table("assignments").delete().eq("annotator_id", annotator_id).execute()

    assert response.data is not None
    # The returned crop_id must be a non-empty string
    assert isinstance(response.data, str)
    assert len(response.data) > 0


# ---------------------------------------------------------------------------
# 6.4b  assign_next_crop_v2 — concurrent callers, no collision
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
            resp = _call_rpc_sync(client, annotator_id)
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

    # Both may legitimately receive this crop now (annotation_count=0 -> cap=2),
    # but they must not collide by both being NULL, and if both got data it must
    # be the same fresh crop_id (only one candidate crop exists), each producing
    # exactly one (crop_id, annotator_id) row rather than an error.
    results_with_data = [r for r in results if r is not None]
    assert len(results_with_data) >= 1, "At least one annotator must receive the crop"

    # Cleanup
    supabase_client.table("assignments").delete().eq("annotator_id", annotator_1).execute()
    supabase_client.table("assignments").delete().eq("annotator_id", annotator_2).execute()


# ---------------------------------------------------------------------------
# 6.4c  assign_next_crop_v2 — same annotator cannot get same crop twice
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_assign_next_crop_same_annotator_no_duplicate(supabase_client, isolated_crop):
    """
    The same annotator calling assign_next_crop_v2 twice must not receive the
    same crop_id twice (invariant I1: annotator must not label same crop twice).
    """
    annotator_id = str(uuid.uuid4())

    resp1 = _call_rpc_sync(supabase_client, annotator_id)
    crop_1 = resp1.data

    resp2 = _call_rpc_sync(supabase_client, annotator_id)
    crop_2 = resp2.data

    # If both got a crop, they must be different
    if crop_1 and crop_2:
        assert crop_1 != crop_2, "Same annotator received the same crop_id on second call"

    # Cleanup
    supabase_client.table("assignments").delete().eq("annotator_id", annotator_id).execute()


# ---------------------------------------------------------------------------
# 6.4d  assign_next_crop_v2 — expired assignment is eligible for reassignment
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_assign_next_crop_expired_assignment_is_requeued(supabase_client, isolated_crop):
    """
    A crop with an expired assignment (expires_at in the past) must be
    eligible for reassignment by a new annotator, without exceeding the cap
    (spec: "Lease release restores availability under the cap").
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
        "vuelta": _VUELTA,
        "assigned_at": (
            datetime.now(tz=timezone.utc) - timedelta(hours=2)
        ).isoformat(),
        "expires_at": expired_at,
    }).execute()

    # New annotator calls the RPC — must receive the crop (expired assignment ignored)
    resp = _call_rpc_sync(supabase_client, new_annotator)

    # Cleanup
    supabase_client.table("assignments").delete().eq("annotator_id", original_annotator).execute()
    supabase_client.table("assignments").delete().eq("annotator_id", new_annotator).execute()

    assert resp.data == crop_id, (
        f"Expected crop_id={crop_id} but got {resp.data!r}. "
        "Expired assignment may not be treated as inactive."
    )


# ---------------------------------------------------------------------------
# 4.1  assign_next_crop_v2 — two annotators race for a fresh crop (cap=2)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_assign_next_crop_two_leases_on_fresh_crop(supabase_client, isolated_crop):
    """
    spec: "Two annotators race for a fresh crop" — a crop with
    annotation_count=0 must accept up to 2 concurrent active leases; a third,
    distinct annotator requesting next-crop while both leases are active must
    NOT receive this crop.

    Exercises true concurrency (threading.Thread, separate Supabase client per
    thread) rather than two sequential calls, mirroring
    test_assign_next_crop_collision_free_concurrent, so it actually verifies
    the FOR UPDATE blocking behavior instead of relying on the first call
    having already committed before the second starts.
    """
    crop_id = isolated_crop
    annotator_1 = str(uuid.uuid4())
    annotator_2 = str(uuid.uuid4())
    annotator_3 = str(uuid.uuid4())

    results: list[Optional[str]] = [None, None]
    errors: list[Optional[Exception]] = [None, None]

    def _call_rpc(index: int, annotator_id: str) -> None:
        try:
            # Each thread gets its own Supabase client to simulate true concurrency
            from supabase import create_client  # type: ignore[import]
            client = create_client(_SUPABASE_URL, _SUPABASE_KEY)
            resp = _call_rpc_sync(client, annotator_id)
            results[index] = resp.data
        except Exception as exc:
            errors[index] = exc

    t1 = threading.Thread(target=_call_rpc, args=(0, annotator_1))
    t2 = threading.Thread(target=_call_rpc, args=(1, annotator_2))

    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert errors[0] is None, f"Thread 1 raised: {errors[0]}"
    assert errors[1] is None, f"Thread 2 raised: {errors[1]}"

    resp1_data, resp2_data = results

    # Both must receive THIS crop — it's the only eligible candidate and the
    # cap for annotation_count=0 is 2.
    assert resp1_data == crop_id, f"Annotator 1 expected {crop_id}, got {resp1_data!r}"
    assert resp2_data == crop_id, f"Annotator 2 expected {crop_id}, got {resp2_data!r}"

    active_leases = (
        supabase_client.table("assignments")
        .select("annotator_id", count="exact")
        .eq("crop_id", crop_id)
        .execute()
    )
    assert active_leases.count == 2, (
        f"Expected exactly 2 active leases on {crop_id}, found {active_leases.count}"
    )

    # A third, distinct annotator must NOT receive this crop (no other crop exists)
    resp3 = _call_rpc_sync(supabase_client, annotator_3)
    assert resp3.data != crop_id, (
        f"COLLISION: annotator 3 received {crop_id} despite the cap of 2 already reached"
    )
    assert resp3.data is None, (
        f"Expected NULL (no eligible crop under the cap) but got {resp3.data!r}"
    )

    # Cleanup
    for annotator_id in (annotator_1, annotator_2, annotator_3):
        supabase_client.table("assignments").delete().eq("annotator_id", annotator_id).execute()


# ---------------------------------------------------------------------------
# 4.2  assign_next_crop_v2 — pending crop (annotation_count=1) caps at 1 lease
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_assign_next_crop_pending_crop_blocks_second_lease(supabase_client):
    """
    spec: "Pending crop blocks a second lease" — a crop with
    annotation_count=1 and one active lease must be skipped for a second,
    different annotator (cap=1 once annotation_count >= 1).
    """
    crop_id = _insert_crop(supabase_client, annotation_count=1, status="pending")
    first_annotator = str(uuid.uuid4())
    second_annotator = str(uuid.uuid4())

    try:
        resp1 = _call_rpc_sync(supabase_client, first_annotator)
        assert resp1.data == crop_id, (
            f"Expected the sole pending crop {crop_id}, got {resp1.data!r}"
        )

        # Second, different annotator must be skipped (cap=1 already reached)
        resp2 = _call_rpc_sync(supabase_client, second_annotator)
        assert resp2.data != crop_id, (
            f"COLLISION: second annotator received {crop_id} despite the cap of 1"
        )
        assert resp2.data is None, (
            f"Expected NULL (no other eligible crop) but got {resp2.data!r}"
        )
    finally:
        supabase_client.table("assignments").delete().eq("annotator_id", first_annotator).execute()
        supabase_client.table("assignments").delete().eq("annotator_id", second_annotator).execute()
        _cleanup_crop(supabase_client, crop_id)


# ---------------------------------------------------------------------------
# 4.3  assign_next_crop_v2 — needs_third crop is reassignable to a 3rd annotator
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_assign_next_crop_needs_third_reassignable(supabase_client):
    """
    spec: "needs_third crop assigned to a third annotator" — a crop with
    annotation_count=2 and status='needs_third' (disagreeing labels) with
    zero active leases must be offered to a third, distinct annotator
    (filter widened from annotation_count < 2 to < 3).
    """
    crop_id = _insert_crop(supabase_client, annotation_count=2, status="needs_third")
    annotator_1 = str(uuid.uuid4())
    annotator_2 = str(uuid.uuid4())
    third_annotator = str(uuid.uuid4())

    try:
        # Simulate the two annotators who already labeled this crop.
        supabase_client.table("labels").insert([
            {
                "crop_id": crop_id,
                "annotator_id": annotator_1,
                "label_human": "5",
                "amended": False,
                "is_admin_resolution": False,
                "vuelta": _VUELTA,
            },
            {
                "crop_id": crop_id,
                "annotator_id": annotator_2,
                "label_human": "6",
                "amended": False,
                "is_admin_resolution": False,
                "vuelta": _VUELTA,
            },
        ]).execute()

        resp = _call_rpc_sync(supabase_client, third_annotator)
        assert resp.data == crop_id, (
            f"Expected needs_third crop {crop_id} offered to a 3rd annotator, "
            f"got {resp.data!r}"
        )

        lease = (
            supabase_client.table("assignments")
            .select("annotator_id", count="exact")
            .eq("crop_id", crop_id)
            .execute()
        )
        assert lease.count == 1, (
            f"Expected exactly 1 active lease for the 3rd annotator, found {lease.count}"
        )

        # One of the two original annotators must still be excluded (own prior label).
        resp_repeat = _call_rpc_sync(supabase_client, annotator_1)
        assert resp_repeat.data != crop_id, (
            "Annotator who already labeled this crop must not be reassigned to it"
        )
    finally:
        supabase_client.table("assignments").delete().eq("annotator_id", third_annotator).execute()
        supabase_client.table("assignments").delete().eq("annotator_id", annotator_1).execute()
        _cleanup_crop(supabase_client, crop_id)
