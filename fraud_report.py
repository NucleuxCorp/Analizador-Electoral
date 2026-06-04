"""
fraud_report.py — Detailed fraud detection report across all analyzed departments.

Reads all data/*_cnn.jsonl and produces:
  - Console: per-department suspicious rates, top flags, top suspicious actas
  - data/fraud_report.json: machine-readable full report

Usage:
    python fraud_report.py
    python fraud_report.py --top 20           # Show top 20 most suspicious actas
    python fraud_report.py --dept ARAUCA      # Filter to one department
    python fraud_report.py --flag VOTOS_EXCEDEN_VOTANTES
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

DATA_DIR = Path("data")


def load_results(dept_filter: str = "") -> list[dict]:
    rows = []
    for f in sorted(DATA_DIR.glob("*_cnn.jsonl")):
        dept = f.stem.replace("_cnn", "").upper().replace("_", " ")
        if dept_filter and dept != dept_filter.upper():
            continue
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    r["_dept"] = dept
                    rows.append(r)
                except json.JSONDecodeError:
                    pass
    return rows


def suspicion_score(r: dict) -> float:
    """Heuristic score: number of flags × max discrepancy magnitude."""
    flags = r.get("flags", [])
    if not flags:
        return 0.0
    score = len(flags) * 10.0
    # Boost for multi-flag actas
    for flag in flags:
        parts = flag.split("=")
        # Extract numeric differences from flag strings
        for part in parts:
            try:
                nums = [int(x) for x in part.replace("!=", " ").replace(">", " ").split() if x.lstrip("-").isdigit()]
                if len(nums) >= 2:
                    score += abs(nums[0] - nums[1]) * 0.1
            except (ValueError, IndexError):
                pass
    return round(score, 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=15, help="Number of top suspicious actas to display")
    parser.add_argument("--dept", help="Filter to one department")
    parser.add_argument("--flag", help="Filter to actas with this flag type")
    args = parser.parse_args()

    rows = load_results(args.dept)
    if not rows:
        print("No results found. Run analyze-e14c first.")
        return

    suspicious = [r for r in rows if r.get("is_suspicious")]
    if args.flag:
        suspicious = [r for r in suspicious if any(args.flag in f for f in r.get("flags", []))]

    total = len(rows)
    print(f"\n{'='*60}")
    print(f" FRAUD DETECTION REPORT — Elecciones Colombia 2026")
    print(f"{'='*60}")
    print(f" Total actas analyzed : {total:>8,}")
    print(f" Suspicious           : {len(suspicious):>8,}  ({len(suspicious)/total*100:.1f}%)")
    needs_review = [r for r in rows if r.get("needs_review") and not r.get("is_suspicious")]
    print(f" Needs OCR review     : {len(needs_review):>8,}  ({len(needs_review)/total*100:.1f}%)")
    print(f"{'='*60}")

    # Per-department table
    by_dept: dict[str, dict] = defaultdict(lambda: {"total": 0, "suspicious": 0})
    for r in rows:
        d = r["_dept"]
        by_dept[d]["total"] += 1
        if r.get("is_suspicious"):
            by_dept[d]["suspicious"] += 1

    print(f"\n{'Department':<25} {'Total':>7} {'Susp':>7} {'Rate':>7}")
    print("-" * 50)
    for dept in sorted(by_dept, key=lambda d: -by_dept[d]["suspicious"]):
        t, s = by_dept[dept]["total"], by_dept[dept]["suspicious"]
        rate = s / t * 100 if t else 0
        marker = " !" if rate > 20 else ""
        safe_dept = dept.encode("ascii", "replace").decode("ascii")
        print(f"{safe_dept:<25} {t:>7,} {s:>7,} {rate:>6.1f}%{marker}")

    # Top flags
    print(f"\n{'Flag Type':<35} {'Count':>7}")
    print("-" * 44)
    flag_counter: Counter = Counter()
    for r in suspicious:
        for flag in r.get("flags", []):
            flag_counter[flag.split(":")[0]] += 1
    for flag, count in flag_counter.most_common(10):
        print(f"{flag:<35} {count:>7,}")

    # Top suspicious actas by score
    scored = sorted(suspicious, key=suspicion_score, reverse=True)
    print(f"\n{'Top ' + str(args.top) + ' Most Suspicious Actas':}")
    print("-" * 80)
    for i, r in enumerate(scored[:args.top], 1):
        path = Path(r.get("pdf_path", "")).name
        flags = "; ".join(r.get("flags", []))
        score = suspicion_score(r)
        print(f"{i:>3}. [{r['_dept']:<12}] {path}")
        print(f"     Score={score:.1f}  Flags: {flags}")

    # Machine-readable report
    report = {
        "total_analyzed": total,
        "total_suspicious": len(suspicious),
        "suspicious_rate_pct": round(len(suspicious) / total * 100, 2),
        "by_department": {
            dept: {
                "total": by_dept[dept]["total"],
                "suspicious": by_dept[dept]["suspicious"],
                "rate_pct": round(by_dept[dept]["suspicious"] / by_dept[dept]["total"] * 100, 2)
                if by_dept[dept]["total"] else 0,
            }
            for dept in sorted(by_dept)
        },
        "top_flags": dict(flag_counter.most_common()),
        "top_suspicious": [
            {
                "pdf_path": r.get("pdf_path"),
                "dept": r["_dept"],
                "score": suspicion_score(r),
                "flags": r.get("flags", []),
                "suma_total": r.get("suma_total"),
                "total_urna": r.get("total_urna"),
                "total_votantes": r.get("total_votantes"),
            }
            for r in scored[:50]
        ],
    }

    report_path = DATA_DIR / "fraud_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\nFull report saved: {report_path}")


if __name__ == "__main__":
    main()
