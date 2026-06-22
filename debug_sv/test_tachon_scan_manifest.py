"""Tests for stratified tachon scan manifest builder (T-M01–T-M05)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_tachon_scan_manifest import (  # noqa: E402
    allocate_stratified,
    build_manifest,
    compute_manifest_hash,
    parse_dept,
    to_manifest_pdf_path,
)


def _make_synthetic_corpus(root: Path, dept_counts: dict[str, int]) -> Path:
    corpus = root / "corpus"
    corpus.mkdir(parents=True)
    for dept, count in dept_counts.items():
        for idx in range(count):
            filename = f"{dept}_synthetic_{idx:04d}.pdf"
            (corpus / filename).write_bytes(b"%PDF-1.4\n")
    return corpus


@pytest.fixture
def synthetic_33_dept_corpus(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    """33 departments with skewed counts large enough for target=500 sampling."""
    dept_counts: dict[str, int] = {}
    for dept_num in range(1, 34):
        dept = f"{dept_num:02d}"
        if dept_num <= 3:
            dept_counts[dept] = 200
        elif dept_num <= 10:
            dept_counts[dept] = 80
        else:
            dept_counts[dept] = 20
    corpus = _make_synthetic_corpus(tmp_path, dept_counts)
    return corpus, dept_counts


def test_manifest_exactly_500_all_depts(synthetic_33_dept_corpus: tuple[Path, dict[str, int]], tmp_path: Path):
    """T-M01: manifest has exactly 500 PDFs and every department is represented."""
    corpus, dept_counts = synthetic_33_dept_corpus
    out_path = tmp_path / "manifest.json"

    manifest = build_manifest(
        corpus,
        target=500,
        seed=42,
        validate_jsonl=None,
        out_path=out_path,
        write_file=True,
    )

    assert manifest["total_pdfs"] == 500
    assert manifest["seed"] == 42
    assert manifest["departments"] == 33
    assert manifest["allocation_formula"] == "proportional_with_min_1"
    assert len(manifest["entries"]) == 500

    entry_depts = {entry["dept"] for entry in manifest["entries"]}
    assert entry_depts == set(dept_counts)
    for entry in manifest["entries"]:
        assert entry["dept"] == parse_dept(Path(entry["pdf"]).name)
        assert entry["stratum"] == f"dept_{int(entry['dept']):02d}"


def test_manifest_reproducible_seed_42(synthetic_33_dept_corpus: tuple[Path, dict[str, int]], tmp_path: Path):
    """T-M02: two runs with seed=42 produce identical hash and pdf lists."""
    corpus, _ = synthetic_33_dept_corpus

    manifest_a = build_manifest(
        corpus,
        target=500,
        seed=42,
        validate_jsonl=None,
        out_path=tmp_path / "manifest_a.json",
        write_file=False,
    )
    manifest_b = build_manifest(
        corpus,
        target=500,
        seed=42,
        validate_jsonl=None,
        out_path=tmp_path / "manifest_b.json",
        write_file=False,
    )

    pdfs_a = sorted(entry["pdf"] for entry in manifest_a["entries"])
    pdfs_b = sorted(entry["pdf"] for entry in manifest_b["entries"])

    assert manifest_a["manifest_hash"] == manifest_b["manifest_hash"]
    assert pdfs_a == pdfs_b


def test_allocation_proportional_min_one():
    """T-M03: tiny depts get floor=1; large depts proportional; sum equals target."""
    dept_counts = {
        "01": 12000,
        "16": 7000,
        "31": 5000,
        "68": 50,
        "72": 30,
    }
    total_corpus = sum(dept_counts.values())
    allocation = allocate_stratified(dept_counts, target=500, total_corpus=total_corpus)

    assert sum(allocation.values()) == 500
    assert allocation["68"] == 1
    assert allocation["72"] == 1
    assert allocation["01"] == round(500 * dept_counts["01"] / total_corpus)
    assert allocation["16"] == round(500 * dept_counts["16"] / total_corpus)
    assert allocation["31"] == round(500 * dept_counts["31"] / total_corpus)


def test_validate_holdout_tagging(tmp_path: Path):
    """T-M04: PDFs present in validate JSONL are tagged validate_holdout=true."""
    dept_counts = {"01": 5, "16": 5}
    corpus = _make_synthetic_corpus(tmp_path, dept_counts)

    holdout_pdf = to_manifest_pdf_path(corpus / "01_synthetic_0000.pdf", corpus)
    non_holdout_pdf = to_manifest_pdf_path(corpus / "16_synthetic_0000.pdf", corpus)
    validate_jsonl = tmp_path / "validate.jsonl"
    validate_jsonl.write_text(
        json.dumps({"pdf": holdout_pdf, "dept": "01"}) + "\n",
        encoding="utf-8",
    )

    manifest = build_manifest(
        corpus,
        target=10,
        seed=42,
        validate_jsonl=validate_jsonl,
        out_path=tmp_path / "manifest.json",
        write_file=False,
    )

    by_pdf = {entry["pdf"]: entry for entry in manifest["entries"]}
    assert by_pdf[holdout_pdf]["validate_holdout"] is True
    assert by_pdf[non_holdout_pdf]["validate_holdout"] is False


def test_manifest_hash_canonical():
    """T-M05: hash is stable and depends only on sorted pdf paths."""
    entries_a = [
        {"pdf": "data/pdfs_e14c_segunda/16_b.pdf", "dept": "16", "stratum": "dept_16", "validate_holdout": False},
        {"pdf": "data/pdfs_e14c_segunda/01_a.pdf", "dept": "01", "stratum": "dept_01", "validate_holdout": True},
    ]
    entries_b = [
        {"pdf": "data/pdfs_e14c_segunda/01_a.pdf", "dept": "01", "stratum": "dept_01", "validate_holdout": False},
        {"pdf": "data/pdfs_e14c_segunda/16_b.pdf", "dept": "16", "stratum": "dept_16", "validate_holdout": True},
    ]

    hash_a = compute_manifest_hash(entries_a)
    hash_b = compute_manifest_hash(entries_b)

    assert hash_a == hash_b
    assert hash_a.startswith("sha256:")
    assert len(hash_a) == len("sha256:") + 64

    expected_paths = sorted(entry["pdf"] for entry in entries_a)
    expected_canonical = json.dumps(expected_paths, separators=(",", ":"), ensure_ascii=False)
    assert hash_a == compute_manifest_hash(
        [{"pdf": p, "dept": "01", "stratum": "dept_01", "validate_holdout": False} for p in expected_paths]
    )
    assert expected_canonical == '["data/pdfs_e14c_segunda/01_a.pdf","data/pdfs_e14c_segunda/16_b.pdf"]'


def test_parse_dept_raises_on_invalid_name():
    with pytest.raises(ValueError, match="Cannot parse department"):
        parse_dept("invalid_name.pdf")