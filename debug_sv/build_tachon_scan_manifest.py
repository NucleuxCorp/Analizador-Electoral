"""Build a stratified 500-PDF manifest for tachon pattern scan experiments."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS_DIR = REPO_ROOT / "data" / "pdfs_e14c_segunda"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "analysis_segunda_vuelta" / "tachon_scan_500_manifest.json"
DEFAULT_VALIDATE_JSONL = REPO_ROOT / "data" / "analysis_segunda_vuelta" / "e14c_validate_results.jsonl"
ALLOCATION_FORMULA = "proportional_with_min_1"
DEPT_RE = re.compile(r"^(\d+)_")


class ManifestEntry(TypedDict):
    pdf: str
    dept: str
    stratum: str
    validate_holdout: bool


class Manifest(TypedDict):
    seed: int
    total_pdfs: int
    total_corpus: int
    departments: int
    allocation_formula: str
    created_at: str
    manifest_hash: str
    entries: list[ManifestEntry]


def parse_dept(filename: str) -> str:
    """Extract department prefix via ``^(\\d+)_``; raise ValueError if no match."""
    match = DEPT_RE.match(filename)
    if not match:
        raise ValueError(f"Cannot parse department from filename: {filename}")
    return match.group(1)


def format_stratum(dept: str) -> str:
    """Return ``dept_{NN}`` with numeric dept ids zero-padded to two digits."""
    if dept.isdigit():
        return f"dept_{int(dept):02d}"
    return f"dept_{dept}"


def _adjust_tiebreak_key(dept: str, alloc: dict[str, int], dept_counts: dict[str, int]) -> tuple[int, int, str]:
    return (alloc[dept], dept_counts[dept], dept)


def allocate_stratified(
    dept_counts: dict[str, int],
    *,
    target: int = 500,
    total_corpus: int,
    min_per_dept: int = 1,
) -> dict[str, int]:
    """Proportional allocation with per-dept floor; adjust to exactly ``target``."""
    if not dept_counts:
        raise ValueError("dept_counts must not be empty")
    if target <= 0:
        raise ValueError("target must be positive")
    if total_corpus <= 0:
        raise ValueError("total_corpus must be positive")

    alloc = {
        dept: max(min_per_dept, round(target * count / total_corpus))
        for dept, count in dept_counts.items()
        if count >= 1
    }

    diff = sum(alloc.values()) - target
    while diff > 0:
        candidates = [dept for dept in alloc if alloc[dept] > min_per_dept]
        if not candidates:
            raise ValueError(f"Cannot reduce allocation to target={target}")
        dept = max(candidates, key=lambda d: _adjust_tiebreak_key(d, alloc, dept_counts))
        alloc[dept] -= 1
        diff -= 1

    while diff < 0:
        dept = max(alloc, key=lambda d: _adjust_tiebreak_key(d, alloc, dept_counts))
        if alloc[dept] >= dept_counts[dept]:
            raise ValueError(f"Cannot increase allocation for dept={dept}: insufficient corpus")
        alloc[dept] += 1
        diff += 1

    if sum(alloc.values()) != target:
        raise ValueError(f"Allocation sum {sum(alloc.values())} != target {target}")
    return alloc


def to_manifest_pdf_path(pdf_path: Path, corpus_dir: Path) -> str:
    """Return a forward-slash manifest path relative to the repository root."""
    resolved = pdf_path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        corpus_resolved = corpus_dir.resolve()
        try:
            corpus_rel = corpus_resolved.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            corpus_rel = corpus_resolved.name
        return f"{corpus_rel}/{resolved.name}"


def compute_manifest_hash(entries: list[ManifestEntry]) -> str:
    """SHA-256 over canonical JSON of sorted pdf paths only, prefixed with ``sha256:``."""
    pdf_paths = sorted(entry["pdf"] for entry in entries)
    canonical = json.dumps(pdf_paths, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def load_validate_pdfs(validate_jsonl: Path | None) -> set[str]:
    """Load PDF paths from validate JSONL for holdout tagging."""
    if validate_jsonl is None or not validate_jsonl.exists():
        return set()

    pdfs: set[str] = set()
    with validate_jsonl.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            pdf = row.get("pdf")
            if pdf:
                pdfs.add(str(pdf).replace("\\", "/"))
    return pdfs


def build_manifest(
    pdf_dir: Path = DEFAULT_CORPUS_DIR,
    *,
    target: int = 500,
    seed: int = 42,
    validate_jsonl: Path | None = DEFAULT_VALIDATE_JSONL,
    out_path: Path = DEFAULT_OUTPUT,
    write_file: bool = True,
) -> Manifest:
    """Glob corpus, stratified sample, tag holdouts, hash, and optionally write manifest."""
    pdf_dir = pdf_dir.resolve()
    if not pdf_dir.is_dir():
        raise FileNotFoundError(f"Corpus directory not found: {pdf_dir}")

    dept_pdfs: dict[str, list[Path]] = {}
    for pdf_path in sorted(pdf_dir.glob("*.pdf")):
        dept = parse_dept(pdf_path.name)
        dept_pdfs.setdefault(dept, []).append(pdf_path)

    if not dept_pdfs:
        raise ValueError(f"No PDF files found in corpus directory: {pdf_dir}")

    dept_counts = {dept: len(paths) for dept, paths in dept_pdfs.items()}
    total_corpus = sum(dept_counts.values())
    allocation = allocate_stratified(
        dept_counts,
        target=target,
        total_corpus=total_corpus,
        min_per_dept=1,
    )

    validate_pdfs = load_validate_pdfs(validate_jsonl)
    rng = random.Random(seed)
    entries: list[ManifestEntry] = []

    for dept in sorted(allocation):
        pool = list(dept_pdfs[dept])
        rng.shuffle(pool)
        selected = pool[: allocation[dept]]
        if len(selected) < allocation[dept]:
            raise ValueError(
                f"Insufficient PDFs for dept={dept}: need {allocation[dept]}, have {len(pool)}"
            )
        for pdf_path in selected:
            rel_pdf = to_manifest_pdf_path(pdf_path, pdf_dir)
            entries.append(
                {
                    "pdf": rel_pdf,
                    "dept": dept,
                    "stratum": format_stratum(dept),
                    "validate_holdout": rel_pdf in validate_pdfs,
                }
            )

    manifest_hash = compute_manifest_hash(entries)
    manifest: Manifest = {
        "seed": seed,
        "total_pdfs": len(entries),
        "total_corpus": total_corpus,
        "departments": len(dept_counts),
        "allocation_formula": ALLOCATION_FORMULA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest_hash": manifest_hash,
        "entries": entries,
    }

    if write_file:
        out_path = out_path.resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, ensure_ascii=False)
            handle.write("\n")

    return manifest


def _print_allocation_table(manifest: Manifest, allocation: dict[str, int]) -> None:
    print(f"seed={manifest['seed']} target={manifest['total_pdfs']} corpus={manifest['total_corpus']}")
    print(f"departments={manifest['departments']} formula={manifest['allocation_formula']}")
    print("dept\talloc")
    for dept in sorted(allocation):
        print(f"{dept}\t{allocation[dept]}")
    print(f"manifest_hash={manifest['manifest_hash']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build stratified tachon scan PDF manifest.")
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--target", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--validate-jsonl", type=Path, default=DEFAULT_VALIDATE_JSONL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.target <= 0:
        print("error: --target must be positive", file=sys.stderr)
        return 2
    if args.seed < 0:
        print("error: --seed must be non-negative", file=sys.stderr)
        return 2

    corpus_dir = args.corpus_dir
    if not corpus_dir.is_absolute():
        corpus_dir = REPO_ROOT / corpus_dir
    output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    validate_jsonl = (
        None
        if args.validate_jsonl is None
        else (args.validate_jsonl if args.validate_jsonl.is_absolute() else REPO_ROOT / args.validate_jsonl)
    )

    try:
        if args.dry_run:
            pdf_dir = corpus_dir.resolve()
            if not pdf_dir.is_dir():
                print(f"error: corpus directory not found: {pdf_dir}", file=sys.stderr)
                return 1
            dept_counts: dict[str, int] = {}
            for pdf_path in pdf_dir.glob("*.pdf"):
                dept = parse_dept(pdf_path.name)
                dept_counts[dept] = dept_counts.get(dept, 0) + 1
            allocation = allocate_stratified(
                dept_counts,
                target=args.target,
                total_corpus=sum(dept_counts.values()),
            )
            entries_stub: list[ManifestEntry] = []
            manifest_stub: Manifest = {
                "seed": args.seed,
                "total_pdfs": args.target,
                "total_corpus": sum(dept_counts.values()),
                "departments": len(dept_counts),
                "allocation_formula": ALLOCATION_FORMULA,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "manifest_hash": compute_manifest_hash(entries_stub),
                "entries": entries_stub,
            }
            _print_allocation_table(manifest_stub, allocation)
            return 0

        manifest = build_manifest(
            corpus_dir,
            target=args.target,
            seed=args.seed,
            validate_jsonl=validate_jsonl,
            out_path=output,
            write_file=True,
        )
        print(
            f"Wrote manifest: {output} "
            f"(total_pdfs={manifest['total_pdfs']}, departments={manifest['departments']}, "
            f"hash={manifest['manifest_hash']})"
        )
        return 0
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())