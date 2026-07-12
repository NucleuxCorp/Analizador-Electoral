"""
check_cross_validation.py
Valida la integridad del archivo cross_mesa_validation.jsonl:
- Estructura de cada linea
- Distribucion por departamento
- Cobertura de fuentes (E14C/E14T/E14D)
- Errores de extraccion
- Discrepancias y fallos aritmeticos
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

JSONL = Path("data/cross_mesa_validation.jsonl")
REQUIRED_TOP   = {"dept", "mpio", "zona", "puesto", "mesa", "sources", "congruencia", "aritmetica"}
REQUIRED_SRC   = {"e14c", "e14t", "e14d"}
VALID_STATUSES = {"ok", "not_available", "extraction_error"}

def main():
    if not JSONL.exists():
        print(f"ERROR: {JSONL} no encontrado")
        sys.exit(1)

    total          = 0
    malformed      = 0
    by_dept        = defaultdict(int)
    src_status     = defaultdict(lambda: defaultdict(int))   # src -> status -> count
    all_3_ok       = 0
    any_error      = 0
    discrepant     = 0
    cross_disc     = 0
    arit_fail      = defaultdict(int)   # src -> count of arithmetic failures
    extraction_err = defaultdict(int)   # src -> count

    errors_detail  = []

    with open(JSONL, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as e:
                malformed += 1
                errors_detail.append(f"  Linea {lineno}: JSON invalido — {e}")
                continue

            total += 1
            missing = REQUIRED_TOP - rec.keys()
            if missing:
                malformed += 1
                errors_detail.append(f"  Linea {lineno}: campos faltantes {missing}")
                continue

            dept = rec.get("dept", "??")
            by_dept[dept] += 1

            sources = rec.get("sources", {})
            ok_count = 0
            for src in REQUIRED_SRC:
                s = sources.get(src, {})
                status = s.get("status", "missing")
                src_status[src][status] += 1
                if status == "ok":
                    ok_count += 1
                elif status == "extraction_error":
                    extraction_err[src] += 1

            if ok_count == 3:
                all_3_ok += 1
            if any(sources.get(s, {}).get("status") == "extraction_error" for s in REQUIRED_SRC):
                any_error += 1

            summary = rec.get("congruencia", {}).get("summary", {})
            if summary.get("any_discrepancy"):
                discrepant += 1
            if summary.get("cross_discrepancy"):
                cross_disc += 1

            arit = rec.get("aritmetica", {})
            for src in REQUIRED_SRC:
                if arit.get(src, {}).get("ok") is False:
                    arit_fail[src] += 1

    # --- Report ---
    print("=" * 60)
    print("  VALIDACION cross_mesa_validation.jsonl")
    print("=" * 60)
    print(f"\n  Total lineas procesadas : {total:,}")
    print(f"  Lineas malformadas      : {malformed:,}")

    print(f"\n  Mesas por departamento:")
    for dept in sorted(by_dept):
        print(f"    dept {dept} : {by_dept[dept]:,}")

    print(f"\n  Cobertura de fuentes:")
    for src in ["e14c", "e14t", "e14d"]:
        stats = src_status[src]
        ok  = stats.get("ok", 0)
        na  = stats.get("not_available", 0)
        err = stats.get("extraction_error", 0)
        mis = stats.get("missing", 0)
        print(f"    {src.upper()}: ok={ok:,}  not_available={na:,}  extraction_error={err:,}  missing={mis:,}")

    print(f"\n  Mesas con las 3 fuentes ok : {all_3_ok:,} / {total:,}")
    print(f"  Mesas con algun error      : {any_error:,}")

    print(f"\n  Discrepancias:")
    print(f"    any_discrepancy  : {discrepant:,} ({100*discrepant/total:.1f}%)" if total else "")
    print(f"    cross_discrepancy: {cross_disc:,} ({100*cross_disc/total:.1f}%)" if total else "")

    print(f"\n  Fallos aritmeticos (ok=False):")
    for src in ["e14c", "e14t", "e14d"]:
        print(f"    {src.upper()}: {arit_fail[src]:,}")

    if errors_detail:
        print(f"\n  Detalle de errores estructurales ({len(errors_detail)}):")
        for e in errors_detail[:20]:
            print(e)
        if len(errors_detail) > 20:
            print(f"  ... y {len(errors_detail)-20} mas")

    print("\n" + "=" * 60)

if __name__ == "__main__":
    main()
