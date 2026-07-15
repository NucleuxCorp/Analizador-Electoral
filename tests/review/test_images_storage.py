"""Tests for Supabase Storage URL helpers in review.images."""
from __future__ import annotations

import os
from unittest.mock import patch

from src.modules.review import images


class TestStoragePublicUrl:
    def test_storage_public_url_when_configured(self):
        env = {
            "SUPABASE_URL": "https://proj.supabase.co",
            "TRANSVERSAL_STORAGE_BUCKET": "bunker-e14",
            "TRANSVERSAL_DATASET": "E14C_conflictivas",
        }
        with patch.dict(os.environ, env, clear=False):
            assert images.storage_public_base() == (
                "https://proj.supabase.co/storage/v1/object/public/bunker-e14/E14C_conflictivas"
            )
            url = images.storage_public_url("01_001_001_01_001", "e14c", 1)
            assert url == (
                "https://proj.supabase.co/storage/v1/object/public/bunker-e14/E14C_conflictivas/"
                "01_001_001_01_001/e14c_p01.jpg"
            )

    def test_storage_public_url_respects_dataset_env(self):
        env = {
            "SUPABASE_URL": "https://proj.supabase.co",
            "TRANSVERSAL_STORAGE_BUCKET": "bunker-e14",
            "TRANSVERSAL_DATASET": "E14D_conflictivas",
        }
        with patch.dict(os.environ, env, clear=False):
            assert images.storage_public_base().endswith("/E14D_conflictivas")
            url = images.storage_public_url("05_001_001_01_001", "e14d", 2)
            assert "/E14D_conflictivas/05_001_001_01_001/e14d_p02.jpg" in url

    def test_storage_public_url_without_bucket(self):
        with patch.dict(os.environ, {}, clear=True):
            assert images.storage_public_base() is None
            assert images.storage_public_url("01_001_001_01_001", "e14c", 1) is None