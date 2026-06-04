"""
wsgi — Gunicorn entry point for the labeler web portal.

Exposes a module-level `app` object so Railway / Gunicorn can start with:
    gunicorn "src.modules.labeler.wsgi:app" --workers 2 --bind 0.0.0.0:$PORT

The existing create_app() signature requires (index_path, labels_dir).  Both
are resolved from environment variables so the process carries zero hard-coded
paths and behaves identically in local dev and on Railway (spec: web-deployment /
Environment Configuration, ADR-4 dev bypass).
"""
from __future__ import annotations

import os
from pathlib import Path

from src.modules.labeler.server import create_app

# ---------------------------------------------------------------------------
# Resolve paths from environment (no hard-coded defaults in production)
# ---------------------------------------------------------------------------

_labels_dir = Path(os.environ.get("LABELS_DIR", "data/labels")).resolve()
_index_path = _labels_dir / "crops" / "index.jsonl"

# ---------------------------------------------------------------------------
# Module-level app — Gunicorn imports this attribute directly
# ---------------------------------------------------------------------------

app = create_app(index_path=_index_path, labels_dir=_labels_dir)
