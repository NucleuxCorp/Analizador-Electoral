"""Cell Crop Pipeline — CLI for classify, extract, and upload subcommands.

Usage:
    python scripts/cell_crop_pipeline.py classify [--dept CODE ...] [--types ...] [--corp CODE]
    python scripts/cell_crop_pipeline.py extract [--workers N] [--limit N]
    python scripts/cell_crop_pipeline.py upload --state {discrepante,divergente} [--max-upload N]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Field list (canonical order from spec)
# ---------------------------------------------------------------------------

CANONICAL_FIELDS = [
    "VOTANTES",
    "URNA",
    "INCINER",
    "C1_CEPEDA",
    "C2_ABELARDO",
    "BLANCO",
    "NULOS",
    "NO_MARCADOS",
    "SUMA_TOTAL",
]

# ---------------------------------------------------------------------------
# classify subcommand
# ---------------------------------------------------------------------------


def cmd_classify(args: argparse.Namespace) -> int:
    from src.modules.crop.concordance import classify_concordance, compute_jsd, compute_is_outlier
    from src.modules.crop.corp_lookup import build_corp_lookup, lookup_corp
    from src.modules.crop.manifest import load_manifest, append_records, make_crop_id

    data_dir = Path("data")
    manifest_path = Path(args.manifest)

    # Discover validation files for the requested depts
    if args.dept:
        dept_codes = list(args.dept)
        input_files = [data_dir / f"cross_mesa_validation_{d}.jsonl" for d in dept_codes]
    else:
        input_files = sorted(data_dir.glob("cross_mesa_validation_*.jsonl"))

    if not input_files:
        print("classify: no cross_mesa_validation_*.jsonl files found in data/", file=sys.stderr)
        return 1

    # Build corp lookup
    url_paths = []
    for name in ("e14c_urls.jsonl", "e14c_sv_urls.jsonl"):
        p = data_dir / name
        if p.exists():
            url_paths.append(p)
    corp_lookup_dict = build_corp_lookup(url_paths) if url_paths else {}

    # Load existing manifest to check for duplicates
    existing_manifest = load_manifest(manifest_path)
    existing_tuples: set[tuple] = {
        (
            r.get("mesa_key", ""),
            r.get("field_name", ""),
            r.get("subcell_pos"),
            r.get("e14_type", ""),
            r.get("crop_type", ""),
        )
        for r in existing_manifest.values()
    }

    types_filter = set(args.types) if args.types else {"e14c", "e14t", "e14d"}
    include_armonico = args.include_armonico
    corp_override = args.corp  # may be None

    total_written = 0
    total_skipped = 0

    for input_file in input_files:
        if not input_file.exists():
            print(f"classify: warning — file not found: {input_file}", file=sys.stderr)
            continue

        print(f"classify: processing {input_file.name}", file=sys.stderr)
        new_records: list[dict] = []

        with input_file.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue

                dept  = str(row.get("dept",   "")).zfill(2)
                mpio  = str(row.get("mpio",   "")).zfill(3)
                zona  = str(row.get("zona",   "")).zfill(2)
                puesto = str(row.get("puesto", "")).zfill(2)
                mesa  = str(row.get("mesa",   ""))
                if not (dept and mpio and zona and puesto and mesa):
                    continue

                corp_key = (dept, mpio, zona, puesto, mesa)
                corp = lookup_corp(corp_key, corp_lookup_dict, override=corp_override or "02")
                mesa_key = f"{mesa}{puesto}{zona}{mpio}{dept}{corp}"

                # Available types: check sources.{t}.status — any status other than
                # "not_available" / "error" means the type extracted data for this mesa.
                sources_data = row.get("sources", {})
                available_types = [
                    t for t in ("e14c", "e14t", "e14d")
                    if t in types_filter
                    and sources_data.get(t, {}).get("status") not in ("not_available", "error", None)
                ]
                if not available_types:
                    continue

                # Field-level integer values per source (used for fullcell concordance).
                src_field_values = {
                    t: sources_data.get(t, {}).get("fields", {})
                    for t in ("e14c", "e14t", "e14d")
                }

                # Subcell-level values come from congruencia.fields.{FIELD}.subcells[i].values.
                fields_data = row.get("congruencia", {}).get("fields", {})

                now = datetime.now(timezone.utc).isoformat()

                for field_name in CANONICAL_FIELDS:
                    field_data = fields_data.get(field_name, {})
                    subcells = field_data.get("subcells", [])

                    # Full-field values for fullcell crop concordance classification.
                    full_values = [
                        src_field_values["e14c"].get(field_name),
                        src_field_values["e14t"].get(field_name),
                        src_field_values["e14d"].get(field_name),
                    ]
                    full_state = classify_concordance(full_values)

                    # Subcell records: one per (subcell_pos, e14_type).
                    # Classification uses the cross-source values at that position.
                    # Values are stored as strings in the JSONL — convert to int|None.
                    for subcell_pos in range(3):
                        sc = subcells[subcell_pos] if subcell_pos < len(subcells) else {}
                        raw = sc.get("values", [None, None, None])
                        values = [int(v) if v is not None and str(v).lstrip("-").isdigit() else None for v in raw]
                        state = classify_concordance(values)

                        if state == "armonico" and not include_armonico:
                            continue

                        for e14_type in available_types:
                            crop_type = "subcell"
                            tuple_key = (mesa_key, field_name, subcell_pos, e14_type, crop_type)
                            if tuple_key in existing_tuples:
                                total_skipped += 1
                                continue

                            crop_id = make_crop_id(mesa_key, field_name, subcell_pos, e14_type, crop_type)
                            record = {
                                "crop_id": crop_id,
                                "mesa_key": mesa_key,
                                "dept": dept,
                                "mpio": mpio,
                                "zona": zona,
                                "puesto": puesto,
                                "mesa": mesa,
                                "corp": corp,
                                "field_name": field_name,
                                "subcell_pos": subcell_pos,
                                "e14_type": e14_type,
                                "crop_type": crop_type,
                                "concordance_state": state,
                                "is_outlier": compute_is_outlier(values, e14_type, state),
                                "values": values,
                                "jsd_e14c_e14t": None,
                                "jsd_e14c_e14d": None,
                                "jsd_e14t_e14d": None,
                                "local_path": None,
                                "supabase_uploaded": False,
                                "created_at": now,
                            }
                            new_records.append(record)
                            existing_tuples.add(tuple_key)

                    # Fullcell record: one per (field_name, e14_type).
                    if full_state == "armonico" and not include_armonico:
                        continue

                    for e14_type in available_types:
                        crop_type = "fullcell"
                        tuple_key = (mesa_key, field_name, None, e14_type, crop_type)
                        if tuple_key in existing_tuples:
                            total_skipped += 1
                            continue

                        crop_id = make_crop_id(mesa_key, field_name, None, e14_type, crop_type)
                        record = {
                            "crop_id": crop_id,
                            "mesa_key": mesa_key,
                            "dept": dept,
                            "mpio": mpio,
                            "zona": zona,
                            "puesto": puesto,
                            "mesa": mesa,
                            "corp": corp,
                            "field_name": field_name,
                            "subcell_pos": None,
                            "e14_type": e14_type,
                            "crop_type": "fullcell",
                            "concordance_state": full_state,
                            "is_outlier": compute_is_outlier(full_values, e14_type, full_state),
                            "values": full_values,
                            "jsd_e14c_e14t": None,
                            "jsd_e14c_e14d": None,
                            "jsd_e14t_e14d": None,
                            "local_path": None,
                            "supabase_uploaded": False,
                            "created_at": now,
                        }
                        new_records.append(record)
                        existing_tuples.add(tuple_key)

        if new_records:
            append_records(manifest_path, new_records)
            total_written += len(new_records)
            print(f"classify: wrote {len(new_records)} records from {input_file.name}", file=sys.stderr)

    print(
        f"classify: done — {total_written} new records, {total_skipped} skipped (already in manifest)",
        file=sys.stderr,
    )
    return 0


# ---------------------------------------------------------------------------
# extract subcommand
# ---------------------------------------------------------------------------


def cmd_extract(args: argparse.Namespace) -> int:
    from src.modules.crop.extractor import run_extract

    manifest_path = Path(args.manifest)
    index_path = Path(args.index)
    errors_path = Path(args.errors)
    workers = args.workers
    limit = args.limit  # may be None

    if not manifest_path.exists():
        print(f"extract: manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    report = run_extract(
        manifest_path=manifest_path,
        index_path=index_path,
        workers=workers,
        errors_path=errors_path,
        limit=limit,
    )

    print(
        f"extract: done — processed={report.processed}, skipped={report.skipped}, errors={report.errors}",
        file=sys.stderr,
    )
    return 0 if report.errors == 0 else 1


# ---------------------------------------------------------------------------
# upload subcommand
# ---------------------------------------------------------------------------


def cmd_upload(args: argparse.Namespace) -> int:
    from src.modules.crop.uploader import upload_state

    manifest_path = Path(args.manifest)
    state = args.state
    max_upload = args.max_upload  # may be None
    concurrent = args.concurrent
    bucket = args.bucket

    if not manifest_path.exists():
        print(f"upload: manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    report = asyncio.run(
        upload_state(
            manifest_path=manifest_path,
            state=state,
            max_upload=max_upload,
            concurrent=concurrent,
            bucket=bucket,
        )
    )

    print(
        f"upload: done — uploaded={report.uploaded}, skipped={report.skipped}, errors={report.errors}",
        file=sys.stderr,
    )
    return 0 if report.errors == 0 else 1


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cell_crop_pipeline.py",
        description="Cell Crop Pipeline — classify, extract, and upload crop records.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # classify
    p_classify = sub.add_parser(
        "classify",
        help="Read cross_mesa_validation files and write crop_manifest.jsonl records.",
    )
    p_classify.add_argument(
        "--dept", metavar="CODE", action="append", dest="dept",
        help="Process only this department (repeatable). Default: all available.",
    )
    p_classify.add_argument(
        "--types", metavar="TYPE", nargs="+", choices=["e14c", "e14t", "e14d"],
        help="E14 types to include (default: all three).",
    )
    p_classify.add_argument(
        "--include-armonico", action="store_true",
        help="Include 'armonico' records (excluded by default).",
    )
    p_classify.add_argument(
        "--corp", metavar="CODE",
        help="Override corp code for all records (default: look up from URL JSONLs, fallback '02').",
    )
    p_classify.add_argument(
        "--manifest", metavar="PATH", default="data/crop_manifest.jsonl",
        help="Path to output manifest (default: data/crop_manifest.jsonl).",
    )
    p_classify.set_defaults(func=cmd_classify)

    # extract
    p_extract = sub.add_parser(
        "extract",
        help="Extract PNG crops for pending manifest records.",
    )
    p_extract.add_argument(
        "--workers", type=int, default=4, metavar="N",
        help="Number of parallel worker processes (default: 4).",
    )
    p_extract.add_argument(
        "--manifest", metavar="PATH", default="data/crop_manifest.jsonl",
        help="Path to manifest (default: data/crop_manifest.jsonl).",
    )
    p_extract.add_argument(
        "--index", metavar="PATH", default="data/cross_mesa_index.jsonl",
        help="Path to cross_mesa_index.jsonl (default: data/cross_mesa_index.jsonl).",
    )
    p_extract.add_argument(
        "--errors", metavar="PATH", default="data/crop_extract_errors.jsonl",
        help="Path to error log (default: data/crop_extract_errors.jsonl).",
    )
    p_extract.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Limit number of (mesa, type) groups to process (smoke test).",
    )
    p_extract.set_defaults(func=cmd_extract)

    # upload
    p_upload = sub.add_parser(
        "upload",
        help="Upload pending crops to Supabase Storage.",
    )
    p_upload.add_argument(
        "--state", required=True, choices=["discrepante", "divergente"],
        help="Concordance state to upload (REQUIRED).",
    )
    p_upload.add_argument(
        "--max-upload", type=int, default=None, metavar="N",
        help="Maximum number of records to upload (default: unlimited).",
    )
    p_upload.add_argument(
        "--concurrent", type=int, default=5, metavar="N",
        help="Maximum concurrent uploads (default: 5).",
    )
    p_upload.add_argument(
        "--manifest", metavar="PATH", default="data/crop_manifest.jsonl",
        help="Path to manifest (default: data/crop_manifest.jsonl).",
    )
    p_upload.add_argument(
        "--bucket", metavar="NAME", default="crops",
        help="Supabase Storage bucket name (default: crops).",
    )
    p_upload.set_defaults(func=cmd_upload)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
