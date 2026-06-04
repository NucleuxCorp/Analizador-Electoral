"""
summarize_suspicious.py — Aggregate suspicious and review-needed actas across all departments.

Reads all data/*_cnn.jsonl files and produces:
  - data/suspicious_summary.jsonl   — one line per suspicious acta (genuine fraud signals)
  - data/review_summary.jsonl       — one line per acta needing OCR review
  - Console report: counts per department, top flags

Usage:
    python summarize_suspicious.py
    python summarize_suspicious.py --suspicious-only
    python summarize_suspicious.py --min-flags 2   # Only actas with 2+ flags
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

DATA_DIR = Path("data")


def load_all_results() -> list[dict]:
    rows = []
    for f in sorted(DATA_DIR.glob("*_cnn.jsonl")):
        dept = f.stem.replace("_cnn", "").upper().replace("_", " ")
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    r["_dept"] = dept
                    r["_source"] = f.name
                    rows.append(r)
                except json.JSONDecodeError:
                    pass
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suspicious-only", action="store_true",
                        help="Print only suspicious actas (genuine arithmetic flags)")
    parser.add_argument("--min-flags", type=int, default=1,
                        help="Minimum number of fraud flags to include (default: 1)")
    args = parser.parse_args()

    rows = load_all_results()
    if not rows:
        print("No results found. Run analyze-e14c first.")
        return

    print(f"Total actas analyzed: {len(rows):,}")

    suspicious = [r for r in rows if r.get("is_suspicious") and len(r.get("flags", [])) >= args.min_flags]
    needs_review = [r for r in rows if r.get("needs_review") and not r.get("is_suspicious")]
    errors = [r for r in rows if r.get("flags") == ["OCR_ERROR"] or r.get("error")]

    print(f"Suspicious (genuine fraud signals): {len(suspicious):,}")
    print(f"Needs OCR review:                  {len(needs_review):,}")
    print(f"Errors (unprocessable):            {len(errors):,}")

    # Per-department breakdown
    print("\n--- Suspicious by Department ---")
    by_dept: dict[str, list] = defaultdict(list)
    for r in suspicious:
        by_dept[r["_dept"]].append(r)

    for dept in sorted(by_dept, key=lambda d: -len(by_dept[d])):
        print(f"  {dept:<25} {len(by_dept[dept]):>4} suspicious")

    # Top flags
    print("\n--- Top Fraud Flags ---")
    flag_counter: Counter = Counter()
    for r in suspicious:
        for flag in r.get("flags", []):
            flag_type = flag.split(":")[0]
            flag_counter[flag_type] += 1
    for flag, count in flag_counter.most_common(10):
        print(f"  {flag:<35} {count:>4}")

    # Write summaries
    susp_out = DATA_DIR / "suspicious_summary.jsonl"
    review_out = DATA_DIR / "review_summary.jsonl"

    with open(susp_out, "w", encoding="utf-8") as f:
        for r in suspicious:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with open(review_out, "w", encoding="utf-8") as f:
        for r in needs_review:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\nSummaries written:")
    print(f"  {susp_out}  ({len(suspicious)} actas)")
    print(f"  {review_out}  ({len(needs_review)} actas)")


if __name__ == "__main__":
    main()
