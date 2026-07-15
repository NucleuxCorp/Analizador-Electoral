"""Transversal review datasets — one lab + Storage prefix per E14 conflictiva queue."""
from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_LAB_ROOT = _ROOT / "Laboratorio/analisis_transversal"


def lab_root() -> Path:
    """Lab output root. Override with TRANSVERSAL_LAB_ROOT (e.g. E: external disk)."""
    env = os.environ.get("TRANSVERSAL_LAB_ROOT", "").strip()
    return Path(env) if env else _DEFAULT_LAB_ROOT


def lab_dataset_dir(name: str | None = None) -> Path:
    cfg = dataset_config(name)
    return lab_root() / cfg["lab_subdir"]

# Canonical dataset keys (also TRANSVERSAL_DATASET env value).
DATASETS: dict[str, dict[str, str]] = {
    "E14C_conflictivas": {
        "lab_subdir": "E14C_conflictivas_pendientes",
        "storage_prefix": "E14C_conflictivas",
        "export_slug": "transversal_review_E14C_conflictivas",
        "primary_source": "e14c",
        "label": "E14C — claveros (oficial)",
    },
    "E14D_conflictivas": {
        "lab_subdir": "E14D_conflictivas_pendientes",
        "storage_prefix": "E14D_conflictivas",
        "export_slug": "transversal_review_E14D_conflictivas",
        "primary_source": "e14d",
        "label": "E14D — delegados",
    },
    "E14T_conflictivas": {
        "lab_subdir": "E14T_conflictivas_pendientes",
        "storage_prefix": "E14T_conflictivas",
        "export_slug": "transversal_review_E14T_conflictivas",
        "primary_source": "e14t",
        "label": "E14T — transmisión",
    },
}

DEFAULT_DATASET = "E14C_conflictivas"

# Legacy export slugs → canonical dataset key
_EXPORT_SLUG_TO_DATASET = {v["export_slug"]: k for k, v in DATASETS.items()}


def normalize_dataset(name: str | None = None) -> str:
    """Resolve env/CLI value to canonical dataset key."""
    raw = (name or os.environ.get("TRANSVERSAL_DATASET") or DEFAULT_DATASET).strip()
    if raw in DATASETS:
        return raw
    if raw in _EXPORT_SLUG_TO_DATASET:
        return _EXPORT_SLUG_TO_DATASET[raw]
    known = ", ".join(sorted(DATASETS))
    raise ValueError(f"Unknown TRANSVERSAL_DATASET {raw!r}. Expected one of: {known}")


def dataset_config(name: str | None = None) -> dict[str, str]:
    key = normalize_dataset(name)
    return {"key": key, **DATASETS[key]}


def lab_mesas_path(name: str | None = None) -> Path:
    return lab_dataset_dir(name) / "mesas"


def storage_object_prefix(name: str | None = None) -> str:
    """Folder inside bucket: e.g. E14C_conflictivas/01_001_.../e14c_p01.webp (jpg legacy supported)."""
    return dataset_config(name)["storage_prefix"]


def export_slug(name: str | None = None) -> str:
    return dataset_config(name)["export_slug"]