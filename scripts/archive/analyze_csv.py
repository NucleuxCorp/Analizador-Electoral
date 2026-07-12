#!/usr/bin/env python3
"""Analyze CSV for anomalies."""
import csv
from collections import Counter

with open("C:/Users/Administrador/Downloads/segunda_vuelta_enlaces.csv", "r", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

print(f"Total: {len(rows)} mesas publicadas")

# Codigos duplicados
trans_ids = [r["codigo_transmision"] for r in rows]
dups = {t: c for t, c in Counter(trans_ids).items() if c > 1}
if dups:
    print(f"\n⚠️ CODIGOS DE TRANSMISION DUPLICADOS: {len(dups)}")
    for t, c in sorted(dups.items(), key=lambda x: -x[1])[:10]:
        hrows = [r for r in rows if r["codigo_transmision"] == t]
        print(f"  {t} aparece {c} veces:")
        for r in hrows:
            print(f"    dept={r['departamento_id']} mpio={r['municipio_id']} zona={r['zona_id']} puesto={r['puesto_id']} mesa={r['mesa_numero']}")

# PDF hashes duplicados (mismo PDF para diferentes mesas)
hashes = [r["nombre_archivo"] for r in rows]
hash_dups = {h: c for h, c in Counter(hashes).items() if c > 1}
if hash_dups:
    print(f"\n⚠️ HASHES DUPLICADOS (mismo PDF en distintas mesas): {len(hash_dups)}")
    for h, c in sorted(hash_dups.items(), key=lambda x: -x[1])[:10]:
        hrows = [r for r in rows if r["nombre_archivo"] == h]
        print(f"  Hash {h[:30]}... aparece {c} veces:")
        for r in hrows:
            print(f"    dept={r['departamento_id']} mpio={r['municipio_id']} zona={r['zona_id']} puesto={r['puesto_id']} mesa={r['mesa_numero']}")

# Mesas con mismo dept-mpio-zona-puesto y diferente mesa (normal)
# Pero mesas con numero de mesa raro (fuera de rango)
print(f"\n📊 Estadisticas:")
print(f"  Departamentos con datos: {len(set(r['departamento_id'] for r in rows))}")
print(f"  Municipios: {len(set(r['municipio_id'] for r in rows))}")
print(f"  Zonas: {len(set((r['departamento_id'], r['municipio_id'], r['zona_id']) for r in rows))}")
print(f"  Puestos: {len(set((r['departamento_id'], r['municipio_id'], r['zona_id'], r['puesto_id']) for r in rows))}")

# Mesas por departamento
dept = Counter(r["departamento_id"] for r in rows)
print(f"\nMesas por departamento:")
for d in sorted(dept):
    print(f"  {d}: {dept[d]}")

if not dups and not hash_dups:
    print("\n✅ Sin anomalias detectadas en el CSV")
