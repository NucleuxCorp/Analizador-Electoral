"""Tests for Supabase Storage URL helpers in review.images."""
from __future__ import annotations

import os
from unittest.mock import patch

from src.modules.review import images


class TestStoragePublicUrl:
    def test_storage_public_url_webp_default(self):
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
                "01_001_001_01_001/e14c_p01.webp"
            )

    def test_storage_public_url_jpeg_legacy(self):
        env = {
            "SUPABASE_URL": "https://proj.supabase.co",
            "TRANSVERSAL_STORAGE_BUCKET": "bunker-e14",
            "TRANSVERSAL_DATASET": "E14D_conflictivas",
        }
        with patch.dict(os.environ, env, clear=False):
            url = images.storage_public_url("05_001_001_01_001", "e14d", 2, ext="jpg")
            assert url.endswith("/E14D_conflictivas/05_001_001_01_001/e14d_p02.jpg")

    def test_storage_public_urls_dual_format(self):
        env = {
            "SUPABASE_URL": "https://proj.supabase.co",
            "TRANSVERSAL_STORAGE_BUCKET": "bunker-e14",
            "TRANSVERSAL_DATASET": "E14C_conflictivas",
        }
        with patch.dict(os.environ, env, clear=False):
            urls = images.storage_public_urls("01_001_001_01_001", "e14c", 1)
            assert urls["webp"].endswith("e14c_p01.webp")
            assert urls["jpg"].endswith("e14c_p01.jpg")

    def test_storage_public_url_without_bucket(self):
        with patch.dict(os.environ, {}, clear=True):
            assert images.storage_public_base() is None
            assert images.storage_public_url("01_001_001_01_001", "e14c", 1) is None

    def test_page_content_type(self):
        assert images.page_content_type("webp") == "image/webp"
        assert images.page_content_type("jpg") == "image/jpeg"