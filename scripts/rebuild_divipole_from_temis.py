"""
Rebuild data/divipole.json (canonical hierarchy for /mesas names) from Temis
Registraduría DIVIPOL JSON snapshots.

Default input: data/temis_divipol/departmentsTree.json
  (+ allCorporations.json, allDepartments.json for metadata)

Output shape (unchanged contract used by server.py):
  {
    "corporaciones": [{"codigo": "001", "etiqueta": "PRESIDENTE"}, ...],
    "departamentos": {
      "01": {
        "nombre": "ANTIOQUIA",
        "listaCorporaciones": ["001"],
        "municipios": {
          "001": {
            "nombre": "MEDELLIN",
            "listaCorporaciones": ["001"],
            "zonas": {
              "01": {
                "nombre": "01",
                "listaCorporaciones": ["001"],
                "puestos": {
                  "01": {"nombre": "COLEGIO …", "listaCorporaciones": ["001"]}
                }
              }
            }
          }
        }
      }
    }
  }

Also writes:
  data/divipole_meta.json — provenance + counts
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMIS = ROOT / "data" / "temis_divipol"
DEFAULT_OUT = ROOT / "data" / "divipole.json"
DEFAULT_META = ROOT / "data" / "divipole_meta.json"


def backup_output(path: Path, max_backups: int = 25) -> Path | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup = path.with_name(f"{path.stem}.bak_{ts}{path.suffix}")
    shutil.copy2(path, backup)
    existing = sorted(path.parent.glob(f"{path.stem}.bak_*{path.suffix}"))
    while len(existing) > max_backups:
        existing.pop(0).unlink(missing_ok=True)
        existing = sorted(path.parent.glob(f"{path.stem}.bak_*{path.suffix}"))
    return backup


def _norm_code(value, width: int) -> str:
    s = str(value or "").strip()
    if s.isdigit():
        return s.zfill(width)
    return s


def _corp_list(corps) -> list[str]:
    if not corps:
        return ["001"]
    out: list[str] = []
    for c in corps:
        code = _norm_code(c, 3)
        if code and code not in out:
            out.append(code)
    return out or ["001"]


def load_corporaciones(path: Path) -> list[dict]:
    if not path.is_file():
        return [{"codigo": "001", "etiqueta": "PRESIDENTE"}]
    raw = json.loads(path.read_text(encoding="utf-8"))
    # Temis: { data: { allCorporations: { edges: [ { node: {...} } ] } } }
    # or CorpIndex style
    data = raw.get("data", raw)
    edges = None
    if isinstance(data, dict):
        if "allCorporations" in data:
            edges = (data["allCorporations"] or {}).get("edges")
        elif "edges" in data:
            edges = data["edges"]
    corps: list[dict] = []
    for edge in edges or []:
        node = edge.get("node") or edge
        code = _norm_code(node.get("idCorporationCode") or node.get("codigo"), 3)
        name = (
            node.get("nameCorporation")
            or node.get("etiqueta")
            or node.get("name")
            or code
        )
        if code:
            corps.append({"codigo": code, "etiqueta": str(name).strip()})
    return corps or [{"codigo": "001", "etiqueta": "PRESIDENTE"}]


def build_divipole(tree_path: Path, corps_path: Path) -> tuple[dict, dict]:
    tree_raw = json.loads(tree_path.read_text(encoding="utf-8"))
    tree = tree_raw.get("data", tree_raw)
    if "departmentsTree" in tree:
        tree = tree["departmentsTree"]
    edges = tree.get("edges") or []

    departamentos: dict = {}
    stats = {
        "departments": 0,
        "municipalities": 0,
        "zones": 0,
        "stands": 0,
        "tables": 0,
    }

    for edge in edges:
        node = edge.get("node") or edge
        dept_code = _norm_code(node.get("idDepartmentCode") or node.get("code"), 2)
        dept_name = (node.get("departmentName") or node.get("name") or dept_code).strip()
        if not dept_code:
            continue
        mpios: dict = {}
        dept_corps: set[str] = set()
        for muni in node.get("municipalities") or []:
            mpio_code = _norm_code(
                muni.get("municipalityCode") or muni.get("code"), 3
            )
            mpio_name = (
                muni.get("municipalityName") or muni.get("name") or mpio_code
            ).strip()
            if not mpio_code:
                continue
            zonas: dict = {}
            mpio_corps: set[str] = set()
            for zone in muni.get("zones") or []:
                zona_code = _norm_code(zone.get("idZoneCode") or zone.get("code"), 2)
                zona_name = (
                    zone.get("zoneName") or zone.get("name") or zona_code
                ).strip()
                if not zona_code:
                    continue
                puestos: dict = {}
                zone_corps = _corp_list(zone.get("corporations"))
                for stand in zone.get("stands") or []:
                    puesto_code = _norm_code(
                        stand.get("standCode") or stand.get("code"), 2
                    )
                    # stand codes can be alphanumeric (A1, B2) — do not force zfill
                    raw_stand = str(
                        stand.get("standCode") or stand.get("code") or ""
                    ).strip()
                    if raw_stand and not raw_stand.isdigit():
                        puesto_code = raw_stand
                    puesto_name = (
                        stand.get("standName") or stand.get("name") or puesto_code
                    ).strip()
                    if not puesto_code:
                        continue
                    tables = stand.get("countTable")
                    try:
                        stats["tables"] += int(tables or 0)
                    except (TypeError, ValueError):
                        pass
                    puestos[puesto_code] = {
                        "nombre": puesto_name,
                        "listaCorporaciones": list(zone_corps),
                    }
                    if tables is not None:
                        puestos[puesto_code]["mesas"] = tables
                    stats["stands"] += 1
                zonas[zona_code] = {
                    "nombre": zona_name,
                    "listaCorporaciones": list(zone_corps),
                    "puestos": puestos,
                }
                mpio_corps.update(zone_corps)
                dept_corps.update(zone_corps)
                stats["zones"] += 1
            mpios[mpio_code] = {
                "nombre": mpio_name,
                "listaCorporaciones": sorted(mpio_corps) or ["001"],
                "zonas": zonas,
            }
            stats["municipalities"] += 1
        departamentos[dept_code] = {
            "nombre": dept_name,
            "listaCorporaciones": sorted(dept_corps) or ["001"],
            "municipios": mpios,
        }
        stats["departments"] += 1

    corporaciones = load_corporaciones(corps_path)
    catalog = {
        "corporaciones": corporaciones,
        "departamentos": departamentos,
    }
    return catalog, stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--temis-dir", type=Path, default=DEFAULT_TEMIS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--meta", type=Path, default=DEFAULT_META)
    args = ap.parse_args()

    tree_path = args.temis_dir / "departmentsTree.json"
    corps_path = args.temis_dir / "allCorporations.json"
    depts_path = args.temis_dir / "allDepartments.json"
    if not tree_path.is_file():
        raise SystemExit(f"Missing {tree_path} — download Temis JSON first")

    catalog, stats = build_divipole(tree_path, corps_path)
    bak = backup_output(args.out)
    args.out.write_text(
        json.dumps(catalog, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "temis_divipol",
        "temis_dir": str(args.temis_dir.as_posix()),
        "inputs": {
            "departmentsTree": tree_path.name if tree_path.is_file() else None,
            "allCorporations": corps_path.name if corps_path.is_file() else None,
            "allDepartments": depts_path.name if depts_path.is_file() else None,
        },
        "output": str(args.out.as_posix()),
        "backup": str(bak.as_posix()) if bak else None,
        "counts": stats,
        "corporaciones": catalog["corporaciones"],
    }
    if args.meta.exists():
        backup_output(args.meta)
    args.meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {args.out} ({args.out.stat().st_size} bytes)")
    print(f"Counts: {stats}")
    if bak:
        print(f"Backup: {bak}")
    print(f"Meta: {args.meta}")


if __name__ == "__main__":
    main()
