"""Per-method isolated tachon pattern scan over a stratified PDF manifest."""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, TypedDict

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.modules.analyzer.tachon_detector import (  # noqa: E402
    detect_density,
    detect_double_writing,
    detect_noise,
    detect_tachon,
)

MethodName = Literal["TACHON", "DOBLE_ESCRITURA", "DENSIDAD_ALTA", "ZONA_SUCIA", "all"]

METHOD_NAMES: list[str] = ["TACHON", "DOBLE_ESCRITURA", "DENSIDAD_ALTA", "ZONA_SUCIA"]
METHODS: dict[str, tuple[Callable[[np.ndarray], float], float]] = {
    "TACHON": (detect_tachon, 0.45),
    "DOBLE_ESCRITURA": (detect_double_writing, 0.50),
    "DENSIDAD_ALTA": (detect_density, 0.60),
    "ZONA_SUCIA": (detect_noise, 0.50),
}
COMBINED_WEIGHTS = {"tachon": 0.40, "double": 0.30, "density": 0.20, "noise": 0.10}
COMBINED_THRESHOLD = 0.45

DEFAULT_JSONL = REPO_ROOT / "data" / "analysis_segunda_vuelta" / "tachon_method_scan_500.jsonl"
DEFAULT_RUN_ID = "tachon-pattern-scan-500pdf"
HISTOGRAM_BINS = [round(i * 0.1, 1) for i in range(11)]
SCORE_KEYS = ["tachon_score", "double_score", "density_score", "noise_score"]

PREREQ_FILES = [
    REPO_ROOT / "debug_sv" / "e14_worker.py",
    REPO_ROOT / "debug_sv" / "subcell_tachon.py",
    REPO_ROOT / "debug_sv" / "grid_detector_v2.py",
    REPO_ROOT / "debug_sv" / "candidate_subcells.py",
]


class Manifest(TypedDict):
    seed: int
    total_pdfs: int
    total_corpus: int
    departments: int
    allocation_formula: str
    created_at: str
    manifest_hash: str
    entries: list[dict[str, Any]]


def check_prerequisites() -> bool:
    """Return True when all D2 worker prerequisite files exist."""
    return all(path.is_file() for path in PREREQ_FILES)


def ensure_prerequisites() -> None:
    """Exit with code 1 when D2 worker files are missing."""
    missing = [str(path) for path in PREREQ_FILES if not path.is_file()]
    if missing:
        print(
            "error: D2 worker prerequisites missing. Merge PR #1 (feat/d2-field-groups-tachon) first.",
            file=sys.stderr,
        )
        for path in missing:
            print(f"  missing: {path}", file=sys.stderr)
        raise SystemExit(1)


def _scan_mode_label(scan_mode: MethodName) -> str:
    return "all_methods" if scan_mode == "all" else scan_mode


def _round_score(value: float) -> float:
    return round(float(value), 3)


def _neutral_analysis(scan_mode: MethodName) -> dict[str, Any]:
    return {
        "scores": {
            "tachon_score": 0.0,
            "double_score": 0.0,
            "density_score": 0.0,
            "noise_score": 0.0,
            "combined_score": 0.0,
        },
        "flags_by_method": {name: False for name in METHOD_NAMES},
        "is_suspicious_combined": False,
        "scan_mode": _scan_mode_label(scan_mode),
    }


def analyze_subcell_isolated(
    crop_bgr: np.ndarray,
    *,
    scan_mode: MethodName = "all",
    has_ink: bool = True,
) -> dict[str, Any]:
    """Run all four detectors in isolation (not the combined production gate)."""
    if not has_ink or crop_bgr.size == 0:
        return _neutral_analysis(scan_mode)

    raw_scores = {
        "tachon_score": detect_tachon(crop_bgr),
        "double_score": detect_double_writing(crop_bgr),
        "density_score": detect_density(crop_bgr),
        "noise_score": detect_noise(crop_bgr),
    }
    scores = {key: _round_score(value) for key, value in raw_scores.items()}
    scores["combined_score"] = _round_score(
        scores["tachon_score"] * COMBINED_WEIGHTS["tachon"]
        + scores["double_score"] * COMBINED_WEIGHTS["double"]
        + scores["density_score"] * COMBINED_WEIGHTS["density"]
        + scores["noise_score"] * COMBINED_WEIGHTS["noise"]
    )

    flags_by_method = {
        name: scores[_score_key(name)] >= threshold
        for name, (_, threshold) in METHODS.items()
    }

    return {
        "scores": scores,
        "flags_by_method": flags_by_method,
        "is_suspicious_combined": scores["combined_score"] >= COMBINED_THRESHOLD,
        "scan_mode": _scan_mode_label(scan_mode),
    }


def _score_key(method_name: str) -> str:
    mapping = {
        "TACHON": "tachon_score",
        "DOBLE_ESCRITURA": "double_score",
        "DENSIDAD_ALTA": "density_score",
        "ZONA_SUCIA": "noise_score",
    }
    return mapping[method_name]


def _json_safe_record(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize record fields to native Python JSON types."""
    safe = dict(record)
    safe["has_ink"] = bool(record.get("has_ink", False))
    safe["subcell_idx"] = int(record.get("subcell_idx", 0))
    safe["is_suspicious_combined"] = bool(record.get("is_suspicious_combined", False))
    safe["scores"] = {key: _round_score(value) for key, value in record.get("scores", {}).items()}
    safe["flags_by_method"] = {
        key: bool(value) for key, value in record.get("flags_by_method", {}).items()
    }
    if "digit" in record:
        safe["digit"] = None if record["digit"] is None else str(record["digit"])
    if "confidence" in record:
        conf = record["confidence"]
        safe["confidence"] = None if conf is None else _round_score(conf)
    return safe


def validate_jsonl_record(record: dict[str, Any]) -> list[str]:
    """Validate a single JSONL record against the scan schema."""
    errors: list[str] = []
    required_top = [
        "run_id",
        "pdf",
        "dept",
        "block",
        "label",
        "subcell_idx",
        "has_ink",
        "scores",
        "flags_by_method",
        "is_suspicious_combined",
        "scan_mode",
    ]
    for key in required_top:
        if key not in record:
            errors.append(f"missing field: {key}")

    scores = record.get("scores", {})
    for key in [*SCORE_KEYS, "combined_score"]:
        if key not in scores:
            errors.append(f"missing scores.{key}")
        elif not isinstance(scores[key], (int, float)):
            errors.append(f"invalid scores.{key} type")

    flags = record.get("flags_by_method", {})
    for method in METHOD_NAMES:
        if method not in flags:
            errors.append(f"missing flags_by_method.{method}")
        elif not isinstance(flags[method], bool):
            errors.append(f"invalid flags_by_method.{method} type")

    if record.get("has_ink") and "double_score" not in scores:
        errors.append("ink subcell missing scores.double_score")

    return errors


def build_subcell_record(
    *,
    pdf: str,
    dept: str,
    block: str,
    label: str,
    subcell: dict[str, Any],
    analysis: dict[str, Any],
    run_id: str,
    skip_ocr: bool,
) -> dict[str, Any]:
    """Build a flat JSONL record for one subcell."""
    record = {
        "run_id": run_id,
        "pdf": pdf.replace("\\", "/"),
        "dept": dept,
        "block": block,
        "label": label,
        "subcell_idx": int(subcell.get("idx", 0)),
        "has_ink": bool(subcell.get("has_ink", False)),
        **analysis,
    }
    if skip_ocr:
        record["digit"] = None
        record["confidence"] = None
    else:
        record["digit"] = str(subcell.get("digit", "0"))
        record["confidence"] = _round_score(float(subcell.get("confidence", 0.0)))
    return _json_safe_record(record)


def _extract_subcells_from_pdf(pdf_path: Path, *, skip_ocr: bool) -> list[dict[str, Any]]:
    """Reuse D2 extraction pipeline without combined-gate enrichment."""
    from debug_sv.candidate_subcells import crop_cell
    from debug_sv import e14_worker

    e14_worker._ensure_modules()
    grid = e14_worker._grid

    original_ocr = None
    if skip_ocr:
        original_ocr = grid.ocr_cell

        def _fast_ocr(*_args, **_kwargs):
            return "0", 0.0

        grid.ocr_cell = _fast_ocr

    try:
        primary = e14_worker._analyze_primary(pdf_path)
        builder_inputs = primary.pop(
            "_builder_inputs",
            {"labeled_rows": [], "primary_row_subcells": {}},
        )
        fields = primary["fields"]

        has_c1 = "C1_CEPEDA" in fields and fields["C1_CEPEDA"] is not None
        has_c2 = "C2_ABELARDO" in fields and fields["C2_ABELARDO"] is not None

        fallback_candidates: list[dict[str, Any]] | None = None
        if not (has_c1 and has_c2):
            fallback = e14_worker._cand.extract_candidates(pdf_path, e14_worker._engine)
            fallback_candidates = fallback.get("candidates", [])
            merged_fields = dict(fields)
            for candidate in fallback_candidates:
                label = candidate["label"]
                if candidate.get("value") is not None:
                    merged_fields[label] = candidate["value"]
            fields = merged_fields

        field_groups, _ = e14_worker._build_field_groups(
            labeled_rows=builder_inputs["labeled_rows"],
            primary_row_subcells=builder_inputs["primary_row_subcells"],
            fallback_candidates=fallback_candidates,
            flat_fields=fields,
        )

        img = grid.render_page(pdf_path)
        extracted: list[dict[str, Any]] = []
        for block_id, block in field_groups.items():
            for label, field in block.get("fields", {}).items():
                for subcell in field.get("subcells", []):
                    crop = crop_cell(img, subcell, pad=grid.CELL_PAD)
                    analysis = analyze_subcell_isolated(
                        crop,
                        scan_mode="all",
                        has_ink=bool(subcell.get("has_ink", False)),
                    )
                    extracted.append(
                        {
                            "block": block_id,
                            "label": label,
                            "subcell": subcell,
                            "analysis": analysis,
                        }
                    )
        return extracted
    finally:
        if skip_ocr and original_ocr is not None:
            grid.ocr_cell = original_ocr


def process_pdf_for_scan(
    pdf_path: str,
    *,
    scan_mode: MethodName = "all",
    skip_ocr: bool = False,
    run_id: str = DEFAULT_RUN_ID,
) -> list[dict[str, Any]]:
    """Single-PDF worker: extract subcells and score with isolated detectors."""
    pdf = REPO_ROOT / pdf_path if not Path(pdf_path).is_absolute() else Path(pdf_path)
    dept = pdf.name.split("_")[0]
    rel_pdf = pdf_path.replace("\\", "/")

    subcells = _extract_subcells_from_pdf(pdf, skip_ocr=skip_ocr)
    records: list[dict[str, Any]] = []
    for item in subcells:
        analysis = dict(item["analysis"])
        analysis["scan_mode"] = _scan_mode_label(scan_mode)
        records.append(
            build_subcell_record(
                pdf=rel_pdf,
                dept=dept,
                block=item["block"],
                label=item["label"],
                subcell=item["subcell"],
                analysis=analysis,
                run_id=run_id,
                skip_ocr=skip_ocr,
            )
        )
    return records


def _worker_task(args: tuple[str, MethodName, bool, str]) -> tuple[str, list[dict[str, Any]] | None, str | None]:
    pdf_path, scan_mode, skip_ocr, run_id = args
    try:
        return pdf_path, process_pdf_for_scan(pdf_path, scan_mode=scan_mode, skip_ocr=skip_ocr, run_id=run_id), None
    except Exception as exc:
        return pdf_path, None, str(exc)


def load_manifest(manifest_path: Path) -> Manifest:
    with manifest_path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if "entries" not in manifest or not manifest["entries"]:
        raise ValueError(f"Invalid manifest (no entries): {manifest_path}")
    return manifest


def build_completion_metadata(
    *,
    manifest: Manifest,
    scan_mode: MethodName,
    workers: int,
    skip_ocr: bool,
    pdfs_requested: int,
    pdfs_ok: int,
    pdfs_error: int,
    subcell_records: int,
    elapsed_s: float,
    errors: list[dict[str, str]],
    run_id: str,
) -> dict[str, Any]:
    error_rate = pdfs_error / pdfs_requested if pdfs_requested else 0.0
    return {
        "run_id": run_id,
        "manifest_hash": manifest["manifest_hash"],
        "scan_mode": _scan_mode_label(scan_mode),
        "workers": workers,
        "skip_ocr": skip_ocr,
        "pdfs_requested": pdfs_requested,
        "pdfs_processed": pdfs_ok,
        "pdfs_ok": pdfs_ok,
        "pdfs_errors": pdfs_error,
        "pdfs_error": pdfs_error,
        "error_rate": round(error_rate, 6),
        "subcell_records": subcell_records,
        "elapsed_s": round(elapsed_s, 3),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "errors": errors,
    }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return _round_score(ordered[idx])


def _histogram_for_scores(values: list[float]) -> dict[str, Any]:
    counts = [0] * (len(HISTOGRAM_BINS) - 1)
    for value in values:
        clamped = min(max(float(value), 0.0), 1.0)
        if clamped >= 1.0:
            bucket = len(counts) - 1
        else:
            bucket = int(clamped * 10)
        counts[bucket] += 1
    return {
        "bins": HISTOGRAM_BINS,
        "counts": counts,
        "p50": _percentile(values, 50),
        "p90": _percentile(values, 90),
        "p99": _percentile(values, 99),
        "max": _round_score(max(values) if values else 0.0),
        "n": len(values),
    }


def _read_jsonl_records(jsonl_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with jsonl_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def build_aggregates(
    jsonl_path: Path,
    *,
    manifest: Manifest,
    scan_mode: MethodName,
    elapsed_s: float,
    workers: int,
    errors: list[dict[str, str]],
    run_id: str,
    skip_ocr: bool = True,
    pdfs_requested: int | None = None,
    pdfs_ok: int | None = None,
    pdfs_error: int | None = None,
) -> dict[str, Any]:
    """Compute summary, co-occurrence, histograms, and completion metadata."""
    records = _read_jsonl_records(jsonl_path)
    ink_records = [r for r in records if r.get("has_ink")]

    per_method_flag_rates: dict[str, dict[str, Any]] = {}
    for method in METHOD_NAMES:
        flagged = sum(1 for r in ink_records if r.get("flags_by_method", {}).get(method))
        total = len(ink_records)
        per_method_flag_rates[method] = {
            "flagged": flagged,
            "total_ink": total,
            "rate": round(flagged / total, 6) if total else 0.0,
        }
    combined_flagged = sum(1 for r in ink_records if r.get("is_suspicious_combined"))
    per_method_flag_rates["COMBINED"] = {
        "flagged": combined_flagged,
        "total_ink": len(ink_records),
        "rate": round(combined_flagged / len(ink_records), 6) if ink_records else 0.0,
    }

    per_dept: dict[str, dict[str, Any]] = {}
    dept_pdfs: dict[str, set[str]] = {}
    for record in records:
        dept = record["dept"]
        dept_pdfs.setdefault(dept, set()).add(record["pdf"])
    for dept, pdfs in sorted(dept_pdfs.items()):
        dept_ink = [r for r in ink_records if r["dept"] == dept]
        tachon_flagged = sum(1 for r in dept_ink if r["flags_by_method"].get("TACHON"))
        zona_flagged = sum(1 for r in dept_ink if r["flags_by_method"].get("ZONA_SUCIA"))
        per_dept[dept] = {
            "pdfs": len(pdfs),
            "tachon_rate": round(tachon_flagged / len(dept_ink), 6) if dept_ink else 0.0,
            "zona_sucia_rate": round(zona_flagged / len(dept_ink), 6) if dept_ink else 0.0,
        }

    per_label: dict[str, dict[str, float]] = {}
    for record in ink_records:
        label = record["label"]
        bucket = per_label.setdefault(label, {"ink": 0, "tachon": 0, "zona": 0})
        bucket["ink"] += 1
        if record["flags_by_method"].get("TACHON"):
            bucket["tachon"] += 1
        if record["flags_by_method"].get("ZONA_SUCIA"):
            bucket["zona"] += 1
    per_label_rates = {
        label: {
            "tachon_rate": round(stats["tachon"] / stats["ink"], 6) if stats["ink"] else 0.0,
            "zona_sucia_rate": round(stats["zona"] / stats["ink"], 6) if stats["ink"] else 0.0,
        }
        for label, stats in sorted(per_label.items())
    }

    matrix = [[0 for _ in METHOD_NAMES] for _ in METHOD_NAMES]
    joint_counts: dict[str, int] = {}
    for record in ink_records:
        flags = record.get("flags_by_method", {})
        active = [name for name in METHOD_NAMES if flags.get(name)]
        for i, left in enumerate(METHOD_NAMES):
            for j, right in enumerate(METHOD_NAMES):
                if flags.get(left) and flags.get(right):
                    matrix[i][j] += 1
        if len(active) >= 2:
            for i in range(len(active)):
                for j in range(i + 1, len(active)):
                    pair = "+".join(sorted([active[i], active[j]]))
                    joint_counts[pair] = joint_counts.get(pair, 0) + 1

    method_index = {name: idx for idx, name in enumerate(METHOD_NAMES)}
    conditional: dict[str, float] = {}
    for given in METHOD_NAMES:
        given_count = per_method_flag_rates[given]["flagged"]
        if not given_count:
            continue
        gi = method_index[given]
        for target in METHOD_NAMES:
            ti = method_index[target]
            conditional[f"P_{target}_given_{given}"] = round(matrix[gi][ti] / given_count, 6)

    histograms = {
        score_key: _histogram_for_scores([r["scores"][score_key] for r in ink_records])
        for score_key in SCORE_KEYS
    }

    validate_holdout_count = sum(
        1 for entry in manifest.get("entries", []) if entry.get("validate_holdout")
    )

    requested = pdfs_requested if pdfs_requested is not None else manifest.get("total_pdfs", 0)
    ok = pdfs_ok if pdfs_ok is not None else max(0, requested - len(errors))
    err_count = pdfs_error if pdfs_error is not None else len(errors)

    completion = build_completion_metadata(
        manifest=manifest,
        scan_mode=scan_mode,
        workers=workers,
        skip_ocr=skip_ocr,
        pdfs_requested=requested,
        pdfs_ok=ok,
        pdfs_error=err_count,
        subcell_records=len(records),
        elapsed_s=elapsed_s,
        errors=errors,
        run_id=run_id,
    )

    summary = {
        "run_id": run_id,
        "manifest_hash": manifest["manifest_hash"],
        "ink_subcells": len(ink_records),
        "pdf_count": len({r["pdf"] for r in records}),
        "error_count": err_count,
        "per_method_flag_rates": per_method_flag_rates,
        "per_dept": per_dept,
        "per_label": per_label_rates,
        "validate_holdout_count": validate_holdout_count,
    }

    cooccurrence = {
        "methods": METHOD_NAMES,
        "ink_subcells": len(ink_records),
        "joint_counts": joint_counts,
        "matrix": matrix,
        "conditional": conditional,
    }

    return {
        "summary": summary,
        "cooccurrence": cooccurrence,
        "histograms": histograms,
        "completion": completion,
    }


def render_markdown_report(aggregates: dict[str, Any]) -> str:
    """Render the seven-section markdown report from aggregate artifacts."""
    summary = aggregates["summary"]
    cooc = aggregates["cooccurrence"]
    hist = aggregates["histograms"]
    completion = aggregates["completion"]

    national_tachon = summary["per_method_flag_rates"]["TACHON"]["rate"]
    dept_rows = []
    for dept, stats in sorted(summary["per_dept"].items(), key=lambda item: item[1]["tachon_rate"], reverse=True):
        delta = stats["tachon_rate"] - national_tachon
        dept_rows.append(
            f"| {dept} | {stats['pdfs']} | {stats['tachon_rate']:.3f} | {delta:+.3f} |"
        )

    method_rows = []
    for method in [*METHOD_NAMES, "COMBINED"]:
        stats = summary["per_method_flag_rates"][method]
        method_rows.append(
            f"| {method} | {stats['flagged']} | {stats['total_ink']} | {stats['rate']:.3f} |"
        )

    label_rows = []
    for label, stats in sorted(summary["per_label"].items()):
        label_rows.append(
            f"| {label} | {stats['tachon_rate']:.3f} | {stats['zona_sucia_rate']:.3f} |"
        )

    cooc_header = "| | " + " | ".join(cooc["methods"]) + " |"
    cooc_sep = "|---|" + "|".join(["---"] * len(cooc["methods"])) + "|"
    cooc_body = []
    for i, row_name in enumerate(cooc["methods"]):
        row_vals = " | ".join(str(cooc["matrix"][i][j]) for j in range(len(cooc["methods"])))
        cooc_body.append(f"| {row_name} | {row_vals} |")

    percentile_rows = []
    for score_key in SCORE_KEYS:
        stats = hist[score_key]
        percentile_rows.append(
            f"| {score_key} | {stats['p50']:.3f} | {stats['p90']:.3f} | {stats['p99']:.3f} | {stats['max']:.3f} |"
        )

    holdout = summary.get("validate_holdout_count", 0)
    lines = [
        "# Tachon Method Scan Report",
        "",
        "## 1. Run Metadata",
        "",
        f"- Run ID: `{completion['run_id']}`",
        f"- Completed: {completion['completed_at']}",
        f"- Workers: {completion['workers']}",
        f"- Elapsed (s): {completion['elapsed_s']}",
        f"- Manifest hash: `{completion['manifest_hash']}`",
        f"- Error rate: {completion['error_rate']:.4f} ({completion.get('pdfs_errors', completion.get('pdfs_error', 0))}/{completion['pdfs_requested']})",
        f"- Scan mode: {completion['scan_mode']}",
        "",
        "## 2. Per-Method Flag Rates",
        "",
        "| Method | Flagged | Ink Subcells | Rate |",
        "|---|---:|---:|---:|",
        *method_rows,
        "",
        "## 3. Top Departments by TACHON Rate",
        "",
        f"National TACHON baseline: **{national_tachon:.3f}**",
        "",
        "| Dept | PDFs | TACHON Rate | Delta vs National |",
        "|---|---:|---:|---:|",
        *dept_rows,
        "",
        "## 4. Per-Label Hit Rates",
        "",
        "| Label | TACHON Rate | ZONA_SUCIA Rate |",
        "|---|---:|---:|",
        *label_rows,
        "",
        "## 5. Co-occurrence Matrix",
        "",
        cooc_header,
        cooc_sep,
        *cooc_body,
        "",
        "## 6. Score Percentiles",
        "",
        "| Score | p50 | p90 | p99 | max |",
        "|---|---:|---:|---:|---:|",
        *percentile_rows,
        "",
        "## 7. Validate Baseline Note",
        "",
        (
            f"Validate holdout overlap: **{holdout}** manifest PDFs tagged "
            f"`validate_holdout=true`. Dept `01` validate JSONL lacks `double_score`; "
            "this scan records all four isolated scores for national calibration."
        ),
        "",
    ]
    return "\n".join(lines)


def write_aggregate_artifacts(aggregates: dict[str, Any], base_dir: Path) -> dict[str, Path]:
    """Write summary, co-occurrence, histograms, completion, and markdown report."""
    base_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": base_dir / "tachon_method_scan_500_summary.json",
        "cooccurrence": base_dir / "tachon_method_cooccurrence.json",
        "histograms": base_dir / "tachon_method_histograms.json",
        "completion": base_dir / "tachon_method_scan_500_complete.json",
        "report": base_dir / "tachon_method_scan_500_report.md",
    }
    with paths["summary"].open("w", encoding="utf-8") as handle:
        json.dump(aggregates["summary"], handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    with paths["cooccurrence"].open("w", encoding="utf-8") as handle:
        json.dump(aggregates["cooccurrence"], handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    with paths["histograms"].open("w", encoding="utf-8") as handle:
        json.dump(aggregates["histograms"], handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    with paths["completion"].open("w", encoding="utf-8") as handle:
        json.dump(aggregates["completion"], handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    paths["report"].write_text(render_markdown_report(aggregates), encoding="utf-8")
    return paths


def run_scan(
    manifest_path: Path,
    *,
    scan_mode: MethodName = "all",
    workers: int = 8,
    skip_ocr: bool = False,
    out_jsonl: Path = DEFAULT_JSONL,
    save_top_crops: bool = False,
    top_n_per_method: int = 50,
    run_id: str = DEFAULT_RUN_ID,
    limit: int | None = None,
) -> dict[str, Any]:
    """Load manifest, process PDFs in parallel, write JSONL and aggregate artifacts."""
    ensure_prerequisites()
    manifest = load_manifest(manifest_path)
    entries = manifest["entries"][:limit] if limit else manifest["entries"]

    out_jsonl = out_jsonl if out_jsonl.is_absolute() else REPO_ROOT / out_jsonl
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    all_records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    pdfs_ok = 0

    tasks = [(entry["pdf"], scan_mode, skip_ocr, run_id) for entry in entries]
    if workers <= 1:
        results = [_worker_task(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_worker_task, task) for task in tasks]
            results = [future.result() for future in as_completed(futures)]

    with out_jsonl.open("w", encoding="utf-8") as handle:
        for pdf_path, records, error in results:
            if error:
                errors.append({"pdf": pdf_path, "error": error})
                continue
            pdfs_ok += 1
            for record in records or []:
                safe = _json_safe_record(record)
                handle.write(json.dumps(safe, ensure_ascii=False) + "\n")
                all_records.append(safe)

    elapsed_s = time.perf_counter() - started
    aggregates = build_aggregates(
        out_jsonl,
        manifest=manifest,
        scan_mode=scan_mode,
        elapsed_s=elapsed_s,
        workers=workers,
        errors=errors,
        run_id=run_id,
        skip_ocr=skip_ocr,
        pdfs_requested=len(entries),
        pdfs_ok=pdfs_ok,
        pdfs_error=len(errors),
    )
    write_aggregate_artifacts(aggregates, out_jsonl.parent)

    if save_top_crops:
        _save_top_crops(all_records, out_jsonl.parent / "tachon_scan_crops", top_n_per_method)

    return aggregates["completion"]


def _save_top_crops(records: list[dict[str, Any]], out_dir: Path, top_n: int) -> None:
    """Optional crop gallery placeholder — non-blocking for merge."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for method in METHOD_NAMES:
        method_dir = out_dir / method
        method_dir.mkdir(parents=True, exist_ok=True)
        score_key = _score_key(method)
        ranked = sorted(
            [r for r in records if r.get("has_ink")],
            key=lambda r: r["scores"].get(score_key, 0.0),
            reverse=True,
        )[:top_n]
        index_path = method_dir / "index.json"
        index_path.write_text(
            json.dumps(
                [
                    {
                        "pdf": r["pdf"],
                        "label": r["label"],
                        "subcell_idx": r["subcell_idx"],
                        "score": r["scores"].get(score_key, 0.0),
                    }
                    for r in ranked
                ],
                indent=2,
            ),
            encoding="utf-8",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run per-method isolated tachon pattern scan.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--method",
        choices=["TACHON", "DOBLE_ESCRITURA", "DENSIDAD_ALTA", "ZONA_SUCIA", "all"],
        default="all",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--output-jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--save-crops", action="store_true")
    parser.add_argument("--crops-per-method", type=int, default=50)
    parser.add_argument("--run-id", type=str, default=DEFAULT_RUN_ID)
    args = parser.parse_args(argv)

    if args.workers <= 0:
        print("error: --workers must be positive", file=sys.stderr)
        return 2

    manifest_path = args.manifest if args.manifest.is_absolute() else REPO_ROOT / args.manifest
    if not manifest_path.is_file():
        print(f"error: manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    try:
        ensure_prerequisites()
        completion = run_scan(
            manifest_path,
            scan_mode=args.method,
            workers=args.workers,
            skip_ocr=args.skip_ocr,
            out_jsonl=args.output_jsonl,
            save_top_crops=args.save_crops,
            top_n_per_method=args.crops_per_method,
            run_id=args.run_id,
            limit=args.limit,
        )
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    error_rate = completion.get("error_rate", 0.0)
    print(
        f"Scan complete: pdfs_ok={completion['pdfs_ok']} "
        f"pdfs_error={completion['pdfs_errors']} "
        f"subcells={completion['subcell_records']} "
        f"elapsed_s={completion['elapsed_s']}"
    )
    return 0 if error_rate < 0.01 else 2


if __name__ == "__main__":
    raise SystemExit(main())