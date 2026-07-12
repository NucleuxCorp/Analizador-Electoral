"""
analizar_duplicados.py — Analiza duplicados por nombre en G:\ sin descargar nada.

Genera G:\reporte_duplicados.md con:
  - Cuantos nombres unicos hay
  - Cuantos nombres tienen duplicados
  - Si los duplicados tienen el mismo tamanio o no
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    from tqdm import tqdm
except ImportError:
    print("tqdm requerido: pip install tqdm", file=sys.stderr)
    sys.exit(1)

_E14C_RE = re.compile(r"^\d{2}_\d{3}_\d{2}_\d{2}_E14_PRE_.*\.pdf$", re.IGNORECASE)

SOURCE = Path(r"G:\\")


def main() -> None:
    print("Escaneando G:\\...", flush=True)
    all_pdfs = list(SOURCE.rglob("*.[Pp][Dd][Ff]"))
    e14c = [p for p in all_pdfs if _E14C_RE.match(p.name)]

    print(f"Total PDFs:       {len(all_pdfs):,}", flush=True)
    print(f"E14C validos:     {len(e14c):,}", flush=True)

    # Agrupar por nombre de archivo
    grupos: dict[str, list[Path]] = defaultdict(list)
    for p in tqdm(e14c, desc="Agrupando", unit="PDF"):
        grupos[p.name].append(p)

    unicos     = {k: v for k, v in grupos.items() if len(v) == 1}
    duplicados = {k: v for k, v in grupos.items() if len(v) > 1}

    print(f"\nNombres unicos:   {len(unicos):,}")
    print(f"Nombres duplicados: {len(duplicados):,}")

    # Analizar duplicados
    mismo_tamanio = []
    distinto_tamanio = []

    for nombre, paths in tqdm(duplicados.items(), desc="Analizando duplicados", unit="grupo"):
        sizes = [p.stat().st_size for p in paths]
        entry = {
            "filename": nombre,
            "copias": len(paths),
            "rutas": [str(p) for p in paths],
            "sizes": sizes,
            "mismo_tamanio": len(set(sizes)) == 1,
        }
        if entry["mismo_tamanio"]:
            mismo_tamanio.append(entry)
        else:
            distinto_tamanio.append(entry)

    print(f"\nDuplicados mismo tamanio:    {len(mismo_tamanio):,}")
    print(f"Duplicados distinto tamanio: {len(distinto_tamanio):,}")

    # Guardar JSON completo
    json_path = SOURCE / "reporte_duplicados.json"
    json_path.write_text(
        json.dumps({
            "total_pdfs": len(all_pdfs),
            "total_e14c": len(e14c),
            "nombres_unicos": len(unicos),
            "nombres_duplicados": len(duplicados),
            "duplicados_mismo_tamanio": len(mismo_tamanio),
            "duplicados_distinto_tamanio": len(distinto_tamanio),
            "detalle_mismo_tamanio": mismo_tamanio,
            "detalle_distinto_tamanio": distinto_tamanio,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # Reporte legible .md
    md_path = SOURCE / "reporte_duplicados.md"
    lines = [
        "# Reporte de duplicados — G:\\",
        "",
        f"| Metrica | Valor |",
        f"|---------|-------|",
        f"| Total PDFs en G:\\ | {len(all_pdfs):,} |",
        f"| PDFs con nombre E14C valido | {len(e14c):,} |",
        f"| Nombres unicos (sin duplicado) | {len(unicos):,} |",
        f"| Nombres con duplicados | {len(duplicados):,} |",
        f"| Duplicados mismo tamanio | {len(mismo_tamanio):,} |",
        f"| Duplicados distinto tamanio | {len(distinto_tamanio):,} |",
        "",
        "---",
        "",
        f"## Duplicados con DISTINTO tamanio ({len(distinto_tamanio)})",
        "",
        "Estos son los mas interesantes — mismo nombre, contenido posiblemente diferente.",
        "",
    ]

    if distinto_tamanio:
        lines.append("| Archivo | Copias | Tamanios |")
        lines.append("|---------|--------|---------|")
        for e in distinto_tamanio[:200]:
            sizes_str = " / ".join(f"{s:,}B" for s in e["sizes"])
            lines.append(f"| {e['filename']} | {e['copias']} | {sizes_str} |")
        if len(distinto_tamanio) > 200:
            lines.append(f"| ... y {len(distinto_tamanio)-200:,} mas | | |")
    else:
        lines.append("Ninguno.")

    lines += [
        "",
        f"## Duplicados con MISMO tamanio ({len(mismo_tamanio)})",
        "",
        "Probablemente copias identicas del mismo archivo.",
        "",
    ]

    if mismo_tamanio:
        lines.append("| Archivo | Copias | Tamanio |")
        lines.append("|---------|--------|---------|")
        for e in mismo_tamanio[:100]:
            lines.append(f"| {e['filename']} | {e['copias']} | {e['sizes'][0]:,}B |")
        if len(mismo_tamanio) > 100:
            lines.append(f"| ... y {len(mismo_tamanio)-100:,} mas | | |")
    else:
        lines.append("Ninguno.")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    print(f"\nReporte guardado:")
    print(f"  {json_path}")
    print(f"  {md_path}")


if __name__ == "__main__":
    main()
