#!/usr/bin/env python3
"""Audit Supabase cloud state against local copies before segunda cutover.

Exit codes: 0 = clean, 1 = mismatch, 2 = unrecoverable error.
Mismatches are written as JSONL lines: {layer, kind, id, reason}.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.modules.labeler.db import _client

DEFAULT_OUTPUT = "primera_backup.jsonl"
DEFAULT_BUCKET = "crops"
DEFAULT_LABELS_DIR = "data/labels"
DEFAULT_VUELTA = "primera"
CHECKPOINT_NAME = ".audit_checkpoint"


def _load_jsonl(path: Path):
    rows = []
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return rows


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _load_checkpoint(path: Path):
    if path.exists():
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {}


def _save_checkpoint(path: Path, checkpoint):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(checkpoint, fh)


def _mm(layer: str, kind: str, id_: str, reason: str):
    return {"layer": layer, "kind": kind, "id": id_, "reason": reason}


def _check_storage(client, bucket: str, labels_dir: Path, verified: set):
    if "storage" in verified:
        return []
    try:
        cloud = {i.get("name", "") for i in client.storage.from_(bucket).list()}
        cloud.discard("")
    except Exception as exc:
        raise RuntimeError(f"storage list failed: {exc}") from exc

    crops_dir = labels_dir / "crops"
    local = {p.name for p in crops_dir.iterdir() if p.suffix == ".png"} if crops_dir.exists() else set()

    out = []
    for name in cloud - local:
        out.append(_mm("storage", "missing_in_local", name, "object in cloud but not local crops dir"))
    for name in local - cloud:
        out.append(_mm("storage", "missing_in_cloud", name, "png local but not in cloud storage"))
    return out


def _check_crops(client, vuelta: str, labels_dir: Path, verified: set):
    if "crops" in verified:
        return []
    try:
        resp = client.table("crops").select("crop_id").eq("vuelta", vuelta).execute()
        cloud = {r.get("crop_id", "") for r in resp.data or []}
        cloud.discard("")
    except Exception as exc:
        raise RuntimeError(f"crops query failed: {exc}") from exc

    local = {r.get("crop_id", "") for r in _load_jsonl(labels_dir / "crops" / "index.jsonl")}
    local.discard("")

    out = []
    for cid in cloud - local:
        out.append(_mm("crops", "missing_in_local", cid, "crop in cloud but not local index"))
    for cid in local - cloud:
        out.append(_mm("crops", "missing_in_cloud", cid, "crop local but not in cloud table"))
    return out


def _check_labels(client, vuelta: str, labels_dir: Path, output_path: Path, verified: set):
    if "labels" in verified:
        return []
    try:
        resp = client.table("labels").select("*").eq("vuelta", vuelta).execute()
        cloud = [r for r in resp.data or [] if r.get("crop_id")]
    except Exception as exc:
        raise RuntimeError(f"labels query failed: {exc}") from exc

    local = {(r.get("crop_id"), r.get("annotator_id")) for r in _load_jsonl(labels_dir / "labels" / "manifest.jsonl")}
    local.discard((None, None))

    out = []
    for row in cloud:
        key = (row.get("crop_id"), row.get("annotator_id"))
        if key not in local:
            out.append(_mm("labels", "missing_in_local", key[0], f"label from {key[1]} in cloud but not local manifest"))

    if out:
        _write_jsonl(output_path, out)
    return out


def _check_assignments(client, vuelta: str, verified: set):
    if "assignments" in verified:
        return []
    try:
        resp = client.table("assignments").select("*", count="exact").eq("vuelta", vuelta).execute()
        count = resp.count if resp.count is not None else len(resp.data or [])
    except Exception as exc:
        raise RuntimeError(f"assignments query failed: {exc}") from exc

    if count > 0:
        return [_mm("assignments", "count_mismatch", "assignments", f"{count} active assignments in cloud")]
    return []


def _run_audit(args):
    labels_dir = Path(args.labels_dir)
    output_path = Path(args.output)
    checkpoint_path = labels_dir / CHECKPOINT_NAME
    verified = set()

    if args.incremental:
        verified = set(_load_checkpoint(checkpoint_path).get("verified", []))

    if not labels_dir.exists():
        return 2, [_mm("audit", "fatal", "labels_dir", f"missing: {labels_dir}")]

    try:
        client = _client()
    except RuntimeError as exc:
        return 2, [_mm("audit", "fatal", "supabase", str(exc))]

    all_mismatches = []
    layers = [
        ("storage", lambda: _check_storage(client, args.bucket, labels_dir, verified)),
        ("crops", lambda: _check_crops(client, args.vuelta, labels_dir, verified)),
        ("labels", lambda: _check_labels(client, args.vuelta, labels_dir, output_path, verified)),
        ("assignments", lambda: _check_assignments(client, args.vuelta, verified)),
    ]

    for name, fn in layers:
        try:
            mismatches = fn()
        except RuntimeError as exc:
            return 2, [_mm("audit", "fatal", name, str(exc))]
        all_mismatches.extend(mismatches)

        if args.incremental and not mismatches:
            cp = _load_checkpoint(checkpoint_path)
            verified_list = list(cp.get("verified", []))
            if name not in verified_list:
                verified_list.append(name)
            cp["verified"] = verified_list
            _save_checkpoint(checkpoint_path, cp)

    return (1 if all_mismatches else 0), all_mismatches


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit Supabase cloud state against local copies.")
    parser.add_argument("--incremental", action="store_true", help="skip verified layers via .audit_checkpoint")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="mismatch JSONL path")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET, help="Storage bucket")
    parser.add_argument("--labels-dir", default=DEFAULT_LABELS_DIR, help="local labels dir")
    parser.add_argument("--vuelta", default=DEFAULT_VUELTA, help="vuelta tag")
    args = parser.parse_args(argv)

    code, mismatches = _run_audit(args)
    for m in mismatches:
        prefix = "FATAL" if code == 2 else "MISMATCH"
        print(f"{prefix} {m['layer']}/{m['kind']}/{m['id']}: {m['reason']}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
