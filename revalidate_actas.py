"""
revalidate_actas.py — Re-run arithmetic validation on actas using HUMAN labels.

Reads labeled digits from Supabase, reconstructs each numeric field per acta,
re-runs FormData.validate(), and compares against the original CNN flag:

  - Flag DISAPPEARS  → the original suspicion was an OCR misread (false positive)
  - Flag PERSISTS    → genuine discrepancy (jury error or fraud) — needs human review

Only actas with ALL their fields fully labeled are revalidated. Partial actas
are reported with their completion percentage.

Usage:
    python revalidate_actas.py                 # report only
    python revalidate_actas.py --min-complete 80   # revalidate actas >=80% labeled
    python revalidate_actas.py --output data/revalidation.jsonl
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# Load .env + repo root
_REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO))
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO / ".env")
except ImportError:
    pass

import os
from src.modules.analyzer.form_extractor import FormData

# Variants of zero that humans label
ZERO_VARIANTS = {"*", "-", ".", "+", "o", "O"}
SKIP_LABEL = "_skip"

# Map field_name → how it feeds into FormData
NIVELACION_FIELDS = {"total_votantes", "total_urna", "total_incinerados"}
TOTALS_FIELDS = {"votos_blanco", "votos_nulos", "votos_no_marcados", "suma_total"}


def _digit_value(label: str) -> str:
    """Convert a human label to a digit character. Zero variants → '0'."""
    label = (label or "").strip()
    if label in ZERO_VARIANTS:
        return "0"
    if label.isdigit():
        return label
    return ""  # unknown / unusable


def reconstruct_field(digits: dict[int, str]) -> int | None:
    """
    Build an integer from {digit_index: label_human}.
    digit_index == -1 means the whole cell was labeled at once (fallback).
    """
    if not digits:
        return None
    # Fallback: a single -1 entry holds the full number
    if -1 in digits:
        raw = "".join(c for c in digits[-1] if c.isdigit())
        return int(raw) if raw else None
    # Normal: concatenate by digit_index order
    parts = []
    for idx in sorted(k for k in digits if k >= 0):
        d = _digit_value(digits[idx])
        if d == "":
            return None  # unusable digit → field incomplete
        parts.append(d)
    if not parts:
        return None
    return int("".join(parts))


def build_formdata(pdf_path: str, fields: dict[str, dict[int, str]]) -> FormData:
    """Construct a FormData from reconstructed field values."""
    fd = FormData(pdf_path=pdf_path)
    candidatos: dict[int, int | None] = {}
    for field_name, digits in fields.items():
        value = reconstruct_field(digits)
        if field_name.startswith("candidato_"):
            try:
                n = int(field_name.split("_")[1])
                candidatos[n] = value
            except (IndexError, ValueError):
                pass
        elif field_name in NIVELACION_FIELDS or field_name in TOTALS_FIELDS:
            setattr(fd, field_name, value)
    # Build candidate list in order 1..13
    fd.votos_candidatos = [candidatos.get(i) for i in range(1, 14)]
    return fd


def fetch_labels(client):
    """
    Fetch all crops + their human labels from Supabase.
    Returns: {pdf_path: {field_name: {digit_index: label_human}}}, expected_counts
    """
    # Pull all crops (field structure) and labels (human values), paginated
    def _all_rows(table, cols):
        rows, start, page = [], 0, 1000
        while True:
            resp = client.table(table).select(cols).range(start, start + page - 1).execute()
            batch = resp.data or []
            rows.extend(batch)
            if len(batch) < page:
                break
            start += page
        return rows

    crops = _all_rows("crops", "crop_id, pdf_path, field_name, digit_index")
    labels = _all_rows("labels", "crop_id, label_human")

    # crop_id → (pdf_path, field_name, digit_index)
    crop_meta = {c["crop_id"]: c for c in crops}

    # Total crops expected per acta (denominator for completion %)
    expected: dict[str, int] = defaultdict(int)
    for c in crops:
        expected[c["pdf_path"]] += 1

    # Build labeled structure
    acta_fields: dict = defaultdict(lambda: defaultdict(dict))
    labeled_count: dict[str, int] = defaultdict(int)
    for lab in labels:
        meta = crop_meta.get(lab["crop_id"])
        if not meta:
            continue
        lh = lab.get("label_human", "")
        if lh == SKIP_LABEL:
            continue
        pdf = meta["pdf_path"]
        acta_fields[pdf][meta["field_name"]][meta["digit_index"]] = lh
        labeled_count[pdf] += 1

    return acta_fields, expected, labeled_count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-complete", type=float, default=100.0,
                        help="Min %% of acta crops labeled to revalidate (default: 100)")
    parser.add_argument("--output", default="data/revalidation.jsonl",
                        help="Output JSONL with revalidation results")
    args = parser.parse_args()

    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])

    print("Fetching crops + labels from Supabase ...")
    acta_fields, expected, labeled_count = fetch_labels(client)
    print(f"Actas with at least one label: {len(acta_fields)}")

    resolved, persisted, partial = 0, 0, 0
    results = []

    for pdf_path, fields in acta_fields.items():
        total_crops = expected.get(pdf_path, 0)
        done = labeled_count.get(pdf_path, 0)
        pct = (done / total_crops * 100) if total_crops else 0

        if pct < args.min_complete:
            partial += 1
            continue

        fd = build_formdata(pdf_path, fields)
        fd.validate()

        verdict = "RESOLVED" if not fd.is_suspicious else "PERSISTS"
        if fd.is_suspicious:
            persisted += 1
        else:
            resolved += 1

        results.append({
            "pdf_path": pdf_path,
            "completion_pct": round(pct, 1),
            "verdict": verdict,
            "human_flags": fd.flags,
            "ocr_flags": fd.ocr_flags,
            "suma_total": fd.suma_total,
            "total_urna": fd.total_urna,
            "total_votantes": fd.total_votantes,
            "votos_candidatos": fd.votos_candidatos,
        })

    # Write results
    out = Path(args.output)
    with open(out, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n{'='*50}")
    print(f" REVALIDATION RESULTS (>= {args.min_complete}% labeled)")
    print(f"{'='*50}")
    print(f" Actas revalidated : {len(results)}")
    print(f"   RESOLVED (era error OCR)      : {resolved}")
    print(f"   PERSISTS (discrepancia real)  : {persisted}")
    print(f" Partial (incompletas, omitidas) : {partial}")
    print(f"\n Output: {out}")

    if results:
        print(f"\n--- Sample (first 10) ---")
        for r in results[:10]:
            print(f"  [{r['verdict']:<8}] {Path(r['pdf_path']).name}")
            if r["human_flags"]:
                for flag in r["human_flags"]:
                    print(f"             {flag}")


if __name__ == "__main__":
    main()
