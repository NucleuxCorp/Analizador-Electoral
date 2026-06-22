"""Worker module for parallel E-14C batch analysis (Windows-safe spawn)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
VOTE_FIELDS = ("C1_CEPEDA", "C2_ABELARDO", "BLANCO", "NULOS", "NO_MARCADOS")

_grid = None
_cand = None
_engine = None
_out_dir = None


def _ensure_modules():
    global _grid, _cand, _engine, _out_dir
    if _engine is not None:
        return

    sys.path.insert(0, str(ROOT))

    spec_grid = importlib.util.spec_from_file_location(
        "grid_v2", ROOT / "debug_sv" / "grid_detector_v2.py"
    )
    grid = importlib.util.module_from_spec(spec_grid)
    spec_grid.loader.exec_module(grid)

    spec_cand = importlib.util.spec_from_file_location(
        "candidate_subcells", ROOT / "debug_sv" / "candidate_subcells.py"
    )
    cand = importlib.util.module_from_spec(spec_cand)
    spec_cand.loader.exec_module(cand)

    _mute_logging()
    _grid = grid
    _cand = cand
    _engine = grid.SegmentedEngine()
    _mute_logging()
    _out_dir = ROOT / "data" / "analysis_segunda_vuelta"


def _mute_logging() -> None:
    import logging
    logging.disable(logging.CRITICAL)
    for name in ("src.modules.analyzer.ocr_engines", "src.utils", ""):
        log = logging.getLogger(name)
        log.handlers.clear()
        log.propagate = False
        log.setLevel(logging.CRITICAL)


def init_worker() -> None:
    _mute_logging()
    _ensure_modules()


def _analyze_primary(pdf: Path) -> dict:
    from debug_sv.subcell_tachon import build_subcell_payload

    grid = _grid
    engine = _engine

    img = grid.render_page(pdf)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    v_lines = grid.detect_gray_v_lines(gray)
    if len(v_lines) < 4:
        v_lines = grid.SUBCELL_X_FALLBACK

    h_lines_raw = grid.detect_gray_h_lines(gray)
    page_h = gray.shape[0]
    h_lines = grid.process_h_lines(h_lines_raw, page_height=page_h)
    grid_rows = grid.build_grid(v_lines, h_lines)
    labeled_rows = grid.label_rows_by_structure(grid_rows, page_h=page_h)

    rows = []
    fields: dict[str, int | None] = {}
    primary_row_subcells: dict[str, list[dict]] = {}

    for row in labeled_rows:
        y_mid = row["y_mid"]
        label = row["label"]
        inks = [
            grid.cell_has_ink(gray, c["x1"], c["y1"], c["x2"], c["y2"])
            for c in row["cells"]
        ]
        known = grid.is_known_label(label)
        if sum(inks) == 0 and not known:
            continue

        digits, confs, subcells = [], [], []
        for j, cell in enumerate(row["cells"]):
            if not inks[j]:
                digits.append("0")
                confs.append(0.0)
            else:
                ch, conf = grid.ocr_cell(
                    engine, img, cell["x1"], cell["y1"], cell["x2"], cell["y2"]
                )
                digits.append(ch)
                confs.append(conf)
            subcells.append(
                build_subcell_payload(
                    {**cell, "idx": j},
                    digit=digits[-1],
                    confidence=round(confs[-1], 3),
                    has_ink=inks[j],
                    img=img,
                )
            )

        raw = "".join(digits)
        value = int(raw) if raw.isdigit() else None

        rows.append({
            "label": label,
            "value": value,
            "digits": digits,
            "confidences": [round(c, 3) for c in confs],
            "y_mid": y_mid,
        })
        if known and value is not None:
            fields[label] = value
        if known:
            primary_row_subcells[label] = subcells

    struct_warnings = grid.validate_labeled_rows(labeled_rows, fields)

    return {
        "fields": fields,
        "rows": rows,
        "structure_warnings": struct_warnings,
        "rows_accepted": len(rows),
        "v_lines": v_lines,
        "h_lines_raw_count": len(h_lines_raw),
        "h_lines_filtered_count": len(h_lines),
        "grid_rows_count": len(grid_rows),
        "method": "primary",
        "_builder_inputs": {
            "labeled_rows": labeled_rows,
            "primary_row_subcells": primary_row_subcells,
        },
    }


def _row_bounds(row: dict) -> tuple[int, int, int]:
    top = row.get("top", row.get("y_top", 0))
    bot = row.get("bot", row.get("y_bot", 0))
    y_mid = row.get("y_mid", (top + bot) // 2)
    return top, bot, y_mid


def _block_key_for_label(label: str) -> str | None:
    from debug_sv.grid_detector_v2 import (
        LABELS_BLOCK_A,
        LABELS_BLOCK_B,
        LABELS_BLOCK_C,
    )

    if label in LABELS_BLOCK_A:
        return "block_a"
    if label in LABELS_BLOCK_B:
        return "block_b"
    if label in LABELS_BLOCK_C:
        return "block_c"
    return None


def _build_tachon_summary(field_groups: dict) -> dict | None:
    analyzed = 0
    suspicious = 0
    flags_by_label: dict[str, set[str]] = {}

    for block in field_groups.values():
        for label, entry in block.get("fields", {}).items():
            for sub in entry.get("subcells", []):
                if sub.get("has_ink"):
                    analyzed += 1
                tachon = sub.get("tachon", {})
                if tachon.get("is_suspicious"):
                    suspicious += 1
                for flag in tachon.get("flags", []):
                    flags_by_label.setdefault(label, set()).add(flag)

    if analyzed == 0:
        return None

    return {
        "analyzed_subcells": analyzed,
        "suspicious_subcells": suspicious,
        "flags_by_label": {k: sorted(v) for k, v in flags_by_label.items()},
    }


def _build_field_groups(
    *,
    labeled_rows: list[dict],
    primary_row_subcells: dict[str, list[dict]],
    fallback_candidates: list[dict] | None,
    flat_fields: dict[str, int | None],
) -> tuple[dict, dict | None]:
    from debug_sv.grid_detector_v2 import (
        LABELS_BLOCK_A,
        LABELS_BLOCK_B,
        LABELS_BLOCK_C,
        is_known_label,
    )

    field_groups = {
        "block_a": {"labels": list(LABELS_BLOCK_A), "fields": {}},
        "block_b": {"labels": list(LABELS_BLOCK_B), "fields": {}},
        "block_c": {"labels": list(LABELS_BLOCK_C), "fields": {}},
    }

    fallback_by_label = {
        c["label"]: c for c in (fallback_candidates or [])
    }
    labeled_by_label = {
        r["label"]: r
        for r in labeled_rows
        if not r.get("label", "").startswith("UNK@")
        and is_known_label(r.get("label", ""))
    }

    all_labels = list(LABELS_BLOCK_A) + list(LABELS_BLOCK_B) + list(LABELS_BLOCK_C)

    for label in all_labels:
        if label not in labeled_by_label and label not in fallback_by_label:
            continue
        if label not in flat_fields or flat_fields[label] is None:
            continue

        block_key = _block_key_for_label(label)
        if block_key is None:
            continue

        entry: dict | None = None

        if label in LABELS_BLOCK_B and label in fallback_by_label:
            cand = fallback_by_label[label]
            y_top = cand.get("y_top", cand.get("top", 0))
            y_bot = cand.get("y_bot", cand.get("bot", 0))
            row_meta = labeled_by_label.get(label, {})
            entry = {
                "value": flat_fields[label],
                "y_mid": (y_top + y_bot) // 2,
                "y_top": y_top,
                "y_bot": y_bot,
                "label_source": row_meta.get("label_source", "structure"),
                "v_lines_row": cand["v_lines_row"],
                "subcells": cand.get("subcells", []),
            }
        elif label in labeled_by_label:
            row = labeled_by_label[label]
            top, bot, y_mid = _row_bounds(row)
            entry = {
                "value": flat_fields[label],
                "y_mid": y_mid,
                "y_top": top,
                "y_bot": bot,
                "label_source": row.get("label_source", "structure"),
                "subcells": primary_row_subcells.get(label, []),
            }

        if entry is not None:
            field_groups[block_key]["fields"][label] = entry

    return field_groups, _build_tachon_summary(field_groups)


def _merge_fallback(record: dict, fallback: dict) -> dict:
    merged = dict(record)
    merged["method"] = "primary+fallback"
    merged["fallback_candidates"] = fallback.get("candidates", [])

    fields = dict(record.get("fields", {}))
    for c in fallback.get("candidates", []):
        label = c["label"]
        if c.get("value") is not None:
            fields[label] = c["value"]

    merged["fields"] = fields
    return merged


def _assess(record: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    fields = record.get("fields", {})

    for label in ("C1_CEPEDA", "C2_ABELARDO"):
        if label not in fields or fields[label] is None:
            reasons.append(f"missing_{label.lower()}")

    if "URNA" not in fields or fields["URNA"] is None:
        reasons.append("missing_urna")

    if record.get("rows_accepted", 0) < 7:
        reasons.append("few_rows")

    vote_sum = sum(fields.get(k, 0) or 0 for k in VOTE_FIELDS)
    urna = fields.get("URNA")
    if urna is not None and vote_sum != urna:
        reasons.append(f"arithmetic_mismatch(sum={vote_sum},urna={urna})")

    if record.get("method") == "primary+fallback":
        reasons.append("used_candidate_fallback")

    for c in record.get("fallback_candidates", []):
        for conf in c.get("confidences", []):
            if 0 < conf < 0.5:
                reasons.append("low_confidence_candidate")
                break

    ts = record.get("tachon_summary")
    if ts and ts.get("suspicious_subcells", 0) > 0:
        reasons.append("tachon_suspicious")

    seen: set[str] = set()
    unique = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            unique.append(r)

    is_critical = any(
        u.startswith("missing_") or u.startswith("arithmetic_") for u in unique
    )
    return is_critical, unique


def process_pdf_task(pdf_rel: str, save_suspicious_crops: bool) -> dict:
    _ensure_modules()
    pdf = ROOT / pdf_rel.replace("/", "\\") if "/" in pdf_rel else ROOT / pdf_rel

    try:
        primary = _analyze_primary(pdf)
        builder_inputs = primary.pop(
            "_builder_inputs",
            {"labeled_rows": [], "primary_row_subcells": {}},
        )
        fields = primary["fields"]

        has_c1 = "C1_CEPEDA" in fields and fields["C1_CEPEDA"] is not None
        has_c2 = "C2_ABELARDO" in fields and fields["C2_ABELARDO"] is not None

        record = {
            "pdf": pdf_rel.replace("\\", "/"),
            "dept": pdf.name.split("_")[0],
            **primary,
        }

        if not (has_c1 and has_c2):
            debug_dir = None
            if save_suspicious_crops:
                debug_dir = _out_dir / "suspicious_crops" / pdf.stem
            fallback = _cand.extract_candidates(pdf, _engine, save_debug_dir=debug_dir)
            record = _merge_fallback(record, fallback)
            record["fallback_triggered"] = True
        else:
            record["fallback_triggered"] = False

        field_groups, tachon_summary = _build_field_groups(
            labeled_rows=builder_inputs["labeled_rows"],
            primary_row_subcells=builder_inputs["primary_row_subcells"],
            fallback_candidates=record.get("fallback_candidates"),
            flat_fields=record["fields"],
        )
        record["field_groups"] = field_groups
        if tachon_summary is not None:
            record["tachon_summary"] = tachon_summary

        suspicious, reasons = _assess(record)
        record["is_suspicious"] = suspicious
        record["suspicious_reasons"] = reasons
        return record

    except Exception as e:
        return {
            "pdf": pdf_rel.replace("\\", "/"),
            "error": str(e),
            "is_suspicious": True,
            "suspicious_reasons": ["processing_error"],
        }