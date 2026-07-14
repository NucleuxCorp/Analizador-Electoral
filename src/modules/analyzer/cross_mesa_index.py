"""Cross-mesa index builder.

Joins URL indexes for E14C, E14T, and E14D into a unified cross-index that
maps normalized geo_key → (e14c_path, e14t_path, e14d_path).

Normalized geo_key: tuple (dept.zfill(2), mpio.zfill(3), zona.zfill(3),
                           puesto.zfill(2), mesa.zfill(3))

Source-specific path resolution:
  E14C  — scan e14c_root recursively; match via URL filename basename.
  E14T  — parse allTransmissionCodes_segunda_index.json; build {txcode}_M{mesa}.pdf
            filename; scan e14t_root recursively; match by filename.
  E14D  — extract sha256 from URL basename; scan e14d_root recursively;
            match by sha256.pdf filename.
"""
from __future__ import annotations

import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def normalize_key(
    dept: str,
    mpio: str,
    zona: str,
    puesto: str,
    mesa: str | int,
) -> tuple[str, str, str, str, str]:
    """Return a canonicalized 5-tuple geo_key.

    Args:
        dept:  Department code (becomes 2-char zero-padded).
        mpio:  Municipality code (becomes 3-char zero-padded).
        zona:  Zone code (becomes 3-char zero-padded).
        puesto: Voting station code (becomes 2-char zero-padded).
        mesa:  Mesa number as string or int (becomes 3-char zero-padded).

    Returns:
        Tuple[str, str, str, str, str] with canonical widths (2,3,3,2,3).
    """
    return (
        str(dept).strip().zfill(2),
        str(mpio).strip().zfill(3),
        str(zona).strip().zfill(3),
        str(puesto).strip().zfill(2),
        str(int(str(mesa).strip())).zfill(3),
    )


# ---------------------------------------------------------------------------
# T1.1 — E14C path resolver
# ---------------------------------------------------------------------------

def parse_e14c_index(
    jsonl_path: str | Path,
    e14c_root: str | Path,
) -> dict[tuple, str | None]:
    """Build normalized_key → absolute_path_str index for E14C PDFs.

    Reads e14c_sv_urls.jsonl; normalizes the geo_key; resolves each URL to a
    local filesystem path by matching the PDF filename against files found in
    e14c_root (recursive scan performed once).

    Args:
        jsonl_path: Path to e14c_sv_urls.jsonl.
        e14c_root:  Root directory of E14C PDFs on disk.

    Returns:
        Dict mapping normalized geo_key → absolute path string or None.
    """
    e14c_root = Path(e14c_root)

    # Build a one-time stem-substring → absolute path index.
    # Disk filenames have the form {prefix}_E14_PRE_{dept}_{mpio}_{zona3}_{zona2}_{puesto}_{mesa}_{seq}.pdf
    # URL filenames have the form E14_PRE_{dept}_{mpio}_{zona3}_{zona2}_{puesto}_{mesa}_{seq}.pdf
    # The URL stem is always a suffix of the disk stem, so we index by the E14_PRE-onward portion.
    e14c_stem_to_path: dict[str, str] = {}
    if e14c_root.exists():
        for pdf in e14c_root.rglob("*.pdf"):
            stem = pdf.stem
            if "E14_PRE" in stem:
                # Key by the E14_PRE... portion of the stem (without leading prefix)
                idx = stem.index("E14_PRE")
                e14c_stem_to_path[stem[idx:]] = str(pdf)
            else:
                # Fallback: index by full filename for other naming conventions
                e14c_stem_to_path[pdf.name] = str(pdf)

    result: dict[tuple, str | None] = {}
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)

            dept = str(entry.get("cod_dept", "")).strip()
            mpio = str(entry.get("cod_mpio", "")).strip()
            zona = str(entry.get("zona", "")).strip()
            puesto = str(entry.get("cod_puesto", "")).strip()
            mesa = entry.get("mesa", 0)

            key = normalize_key(dept, mpio, zona, puesto, mesa)

            # Resolve path via URL filename stem (E14_PRE... portion)
            pdf_url = entry.get("pdf_url", "")
            fname = pdf_url.split("/")[-1].split("?")[0] if pdf_url else ""
            url_stem = fname.replace(".pdf", "") if fname else ""
            result[key] = e14c_stem_to_path.get(url_stem)

    return result


# ---------------------------------------------------------------------------
# T1.2 — E14T transmission code loader
# ---------------------------------------------------------------------------

def parse_transmission_index(
    json_path: str | Path,
) -> dict[tuple, str]:
    """Parse allTransmissionCodes_segunda_index.json → normalized_key: txcode.

    The JSON structure is:
        {"data": {"statusN": {"nodes": [...]}}}

    Each node has:
        idTransmissionCode, numberStand (mesa), idDepartmentCode,
        municipalityCode, idZoneCode, standCode (puesto).

    Args:
        json_path: Path to allTransmissionCodes_segunda_index.json.

    Returns:
        Dict mapping normalized geo_key → idTransmissionCode string.
    """
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    inner: dict = data.get("data", {})
    result: dict[tuple, str] = {}

    for _status, group in inner.items():
        for node in group.get("nodes", []):
            tx_code = str(node.get("idTransmissionCode", "")).strip()
            mesa = str(node.get("numberStand", "")).strip()
            dept = str(node.get("idDepartmentCode", "")).strip()
            mpio = str(node.get("municipalityCode", "")).strip()
            zona = str(node.get("idZoneCode", "")).strip()
            puesto = str(node.get("standCode", "")).strip()

            if not tx_code:
                continue

            key = normalize_key(dept, mpio, zona, puesto, mesa)
            result[key] = tx_code

    return result


def build_e14t_sha256_index(json_path: str | Path) -> dict[str, tuple]:
    """Build sha256_stem → geo_key mapping from allTransmissionCodes JSON.

    Uses the expectedName field (SHA256 filename) to map each E14T PDF
    directly to its geo_key, eliminating the need for positional assignment.
    This is the canonical fix for the E14T positional assignment bug where
    SHA256 alphabetical sort order ≠ mesa number order.

    Args:
        json_path: Path to allTransmissionCodes_segunda_index.json (or the segunda variant).

    Returns:
        Dict mapping sha256_stem (64-char hex, no .pdf) → normalized geo_key tuple.
    """
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    inner: dict = data.get("data", {})
    result: dict[str, tuple] = {}

    for _status, group in inner.items():
        for node in group.get("nodes", []):
            expected = str(node.get("expectedName", "")).strip()
            if not expected:
                continue
            sha256_stem = Path(expected).stem  # strip .pdf

            mesa = str(node.get("numberStand", "")).strip()
            dept = str(node.get("idDepartmentCode", "")).strip()
            mpio = str(node.get("municipalityCode", "")).strip()
            zona = str(node.get("idZoneCode", "")).strip()
            puesto = str(node.get("standCode", "")).strip()

            if not sha256_stem or not mesa:
                continue

            key = normalize_key(dept, mpio, zona, puesto, mesa)
            result[sha256_stem] = key

    return result


# ---------------------------------------------------------------------------
# T1.3 — E14T local path resolver
# ---------------------------------------------------------------------------

def _normalize_name(name: str) -> str:
    """Normalize a place name for fuzzy matching: uppercase, strip accents and trailing punctuation."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", name.upper().strip().rstrip("."))
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _build_e14t_name_maps(
    divipole_path: Path,
    departamentos_path: Path,
) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """Build lookup tables for E14T directory-to-code resolution.

    Returns:
        dept_name_to_code: normalized_dept_name → dept_code (2-char)
        mpio_name_to_code: (dept_code, normalized_mpio_name) → mpio_code (3-char)
    """
    dept_name_to_code: dict[str, str] = {}
    mpio_name_to_code: dict[tuple[str, str], str] = {}

    # dept name → id from departamentos.json
    if departamentos_path.exists():
        with open(departamentos_path, encoding="utf-8") as f:
            depts_list = json.load(f)
        for d in depts_list:
            code = str(d.get("id", "")).strip().zfill(2)
            name = _normalize_name(d.get("nombre", ""))
            if code and name:
                dept_name_to_code[name] = code

    # (dept_code, mpio_name) → mpio_code from divipole.json
    if divipole_path.exists():
        with open(divipole_path, encoding="utf-8") as f:
            divipole = json.load(f)
        for dept_code, dept_data in divipole.get("departamentos", {}).items():
            dc = str(dept_code).strip().zfill(2)
            for mpio_code, mpio_data in dept_data.get("municipios", {}).items():
                mc = str(mpio_code).strip().zfill(3)
                mname = _normalize_name(mpio_data.get("nombre", ""))
                if mname:
                    mpio_name_to_code[(dc, mname)] = mc

    return dept_name_to_code, mpio_name_to_code


def resolve_e14t_paths(
    transmission_idx: dict[tuple, str],
    e14t_root: str | Path,
    divipole_path: str | Path | None = None,
    departamentos_path: str | Path | None = None,
    e14t_json_path: str | Path | None = None,
) -> dict[tuple, str | None]:
    """Resolve transmission index entries to absolute E14T PDF paths on disk.

    E14T files on disk use SHA256 filenames inside a directory hierarchy:
        {e14t_root}/{DEPT_NAME}/{MPIO_NAME}/zona_{NNN}/puesto_{NN}/{sha256}.pdf

    Resolution strategy (preferred — when e14t_json_path is provided):
        Use build_e14t_sha256_index() to map each SHA256 filename directly to
        its geo_key via the expectedName field. Scans e14t_root once and looks
        up each PDF's stem in the SHA256 index.

    Fallback (legacy — when e14t_json_path is None):
        1. Build dept_name → dept_code and mpio_name → mpio_code lookup tables.
        2. Scan e14t_root once, grouping files by
           (dept_code, mpio_code, zona_code, puesto_code) → sorted PDF paths.
        3. From transmission_idx, group keys by the same 4-tuple → sorted mesas.
        4. Assign pdf_paths[i] → geo_key of mesas[i] (positional match).
           WARNING: This fallback is unreliable — SHA256 sort ≠ mesa number order.

    Args:
        transmission_idx:    Dict from parse_transmission_index().
        e14t_root:           Root directory of E14T PDFs on disk.
        divipole_path:       Optional path to data/divipole.json (auto-detected).
        departamentos_path:  Optional path to data/departamentos.json (auto-detected).
        e14t_json_path:      Path to allTransmissionCodes JSON with expectedName
                             fields. When provided, enables accurate SHA256-based
                             assignment instead of unreliable positional matching.

    Returns:
        Dict mapping normalized geo_key → absolute path string or None.
    """
    import re
    from collections import defaultdict

    e14t_root = Path(e14t_root)

    # Auto-detect data files relative to this module's project root
    _data_root = Path(__file__).resolve().parents[3] / "data"
    if divipole_path is None:
        divipole_path = _data_root / "divipole.json"
    if departamentos_path is None:
        departamentos_path = _data_root / "departamentos.json"

    dept_name_to_code, mpio_name_to_code = _build_e14t_name_maps(
        Path(divipole_path), Path(departamentos_path)
    )

    # Scan e14t_root and group PDFs by (dept_code, mpio_code, zona_code, puesto_code)
    # Expected depth: root / DEPT_NAME / MPIO_NAME / zona_{NNN} / puesto_{NN} / file.pdf
    dir_groups: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)

    zona_re = re.compile(r"^zona_(\d+)$", re.IGNORECASE)
    puesto_re = re.compile(r"^puesto_(\d+)$", re.IGNORECASE)

    if e14t_root.exists():
        for dept_dir in e14t_root.iterdir():
            if not dept_dir.is_dir():
                continue
            dept_code = dept_name_to_code.get(_normalize_name(dept_dir.name))
            if dept_code is None:
                continue

            for mpio_dir in dept_dir.iterdir():
                if not mpio_dir.is_dir():
                    continue
                mpio_code = mpio_name_to_code.get((dept_code, _normalize_name(mpio_dir.name)))
                if mpio_code is None:
                    continue

                for zona_dir in mpio_dir.iterdir():
                    if not zona_dir.is_dir():
                        continue
                    zm = zona_re.match(zona_dir.name)
                    if not zm:
                        continue
                    zona_code = str(int(zm.group(1))).zfill(3)

                    for puesto_dir in zona_dir.iterdir():
                        if not puesto_dir.is_dir():
                            continue
                        pm = puesto_re.match(puesto_dir.name)
                        if not pm:
                            continue
                        puesto_code = str(int(pm.group(1))).zfill(2)

                        group_key = (dept_code, mpio_code, zona_code, puesto_code)
                        pdfs = sorted(str(p) for p in puesto_dir.glob("*.pdf"))
                        dir_groups[group_key].extend(pdfs)

    # Group transmission keys by the same 4-tuple, sorted by mesa
    tx_groups: dict[tuple[str, str, str, str], list[tuple[str, tuple]]] = defaultdict(list)
    for geo_key in transmission_idx:
        dept_c, mpio_c, zona_c, puesto_c, mesa_c = geo_key
        group_key = (dept_c, mpio_c, zona_c, puesto_c)
        tx_groups[group_key].append((mesa_c, geo_key))

    for group_key in tx_groups:
        tx_groups[group_key].sort(key=lambda x: x[0])

    result: dict[tuple, str | None] = {key: None for key in transmission_idx}

    if e14t_json_path is not None:
        # Preferred path: SHA256-based direct lookup using expectedName from the JSON.
        # Each PDF's stem (SHA256) uniquely identifies its mesa — no positional guessing.
        # We scan ALL PDFs in e14t_root (rglob) so that folders whose dept/mpio names
        # can't be resolved via divipole (e.g. CONSULADOS/MPIO_360) are still matched.
        sha256_idx = build_e14t_sha256_index(e14t_json_path)
        for pdf in e14t_root.rglob("*.pdf"):
            geo_key = sha256_idx.get(pdf.stem)
            if geo_key is not None and geo_key in result:
                result[geo_key] = str(pdf)
    else:
        # Legacy fallback: positional assignment (SHA256 sort ≠ mesa order — unreliable).
        for group_key, mesa_list in tx_groups.items():
            pdf_paths = dir_groups.get(group_key, [])
            for i, (_, geo_key) in enumerate(mesa_list):
                result[geo_key] = pdf_paths[i] if i < len(pdf_paths) else None

    return result


# ---------------------------------------------------------------------------
# T1.4 — E14D path resolver
# ---------------------------------------------------------------------------

def resolve_e14d_paths(
    e14d_jsonl_path: str | Path,
    e14d_root: str | Path,
) -> dict[tuple, str | None]:
    """Build normalized_key → absolute_path_str index for E14D PDFs.

    E14D files are named {sha256}.pdf; sha256 is extracted from the URL
    basename (before the '?' query string). Performs a single recursive scan
    of e14d_root.

    Args:
        e14d_jsonl_path: Path to e14d_sv_urls.jsonl.
        e14d_root:       Root directory of E14D PDFs on disk.

    Returns:
        Dict mapping normalized geo_key → absolute path string or None.
    """
    e14d_root = Path(e14d_root)

    # Build sha256.pdf → absolute path index (one-time scan)
    sha_to_path: dict[str, str] = {}
    if e14d_root.exists():
        for pdf in e14d_root.rglob("*.pdf"):
            sha_to_path[pdf.name] = str(pdf)

    result: dict[tuple, str | None] = {}
    with open(e14d_jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)

            dept = str(entry.get("cod_dept", "")).strip()
            mpio = str(entry.get("cod_mpio", "")).strip()
            zona = str(entry.get("zona", "")).strip()
            puesto = str(entry.get("puesto", "")).strip()
            mesa = str(entry.get("mesa", "")).strip()

            key = normalize_key(dept, mpio, zona, puesto, mesa)

            # Extract sha256 from URL
            pdf_url = entry.get("pdf_url", "")
            sha_fname = pdf_url.split("/")[-1].split("?")[0] if pdf_url else ""
            result[key] = sha_to_path.get(sha_fname)

    return result


# ---------------------------------------------------------------------------
# T1.5 — Build cross-index
# ---------------------------------------------------------------------------

def build_cross_index(
    e14c_paths: dict[tuple, str | None],
    e14t_paths: dict[tuple, str | None],
    e14d_paths: dict[tuple, str | None],
) -> list[dict]:
    """Join three path dicts on the union of all geo_keys.

    Args:
        e14c_paths: Dict from parse_e14c_index() — key → path or None.
        e14t_paths: Dict from resolve_e14t_paths() — key → path or None.
        e14d_paths: Dict from resolve_e14d_paths() — key → path or None.

    Returns:
        List of dicts, one per unique geo_key across all three sources:
            {dept, mpio, zona, puesto, mesa,
             e14c_path, e14t_path, e14d_path, all_available}
    """
    all_keys: set[tuple] = set(e14c_paths) | set(e14t_paths) | set(e14d_paths)
    records: list[dict] = []

    for key in sorted(all_keys):
        dept, mpio, zona, puesto, mesa = key
        e14c_p = e14c_paths.get(key)
        e14t_p = e14t_paths.get(key)
        e14d_p = e14d_paths.get(key)

        all_available = (e14c_p is not None) and (e14t_p is not None) and (e14d_p is not None)

        records.append({
            "dept": dept,
            "mpio": mpio,
            "zona": zona,
            "puesto": puesto,
            "mesa": mesa,
            "e14c_path": e14c_p,
            "e14t_path": e14t_p,
            "e14d_path": e14d_p,
            "all_available": all_available,
        })

    return records


# ---------------------------------------------------------------------------
# T1.6 — CLI entry point for index building
# ---------------------------------------------------------------------------

def build_index(
    e14c_jsonl: str | Path,
    e14t_json: str | Path,
    e14d_jsonl: str | Path,
    e14c_root: str | Path,
    e14t_root: str | Path,
    e14d_root: str | Path,
    output_path: str | Path,
) -> int:
    """Build cross-mesa index and write to output_path as JSONL.

    Args:
        e14c_jsonl:  Path to data/e14c_sv_urls.jsonl.
        e14t_json:   Path to data/allTransmissionCodes_segunda_index.json.
        e14d_jsonl:  Path to data/e14d_sv_urls.jsonl.
        e14c_root:   Root directory of E14C PDFs.
        e14t_root:   Root directory of E14T PDFs.
        e14d_root:   Root directory of E14D PDFs.
        output_path: Destination JSONL file path.

    Returns:
        Count of records written.
    """
    print("[build_index] Scanning E14C...")
    e14c_paths = parse_e14c_index(e14c_jsonl, e14c_root)
    print(f"  E14C entries: {len(e14c_paths)}")

    print("[build_index] Loading transmission codes...")
    tx_idx = parse_transmission_index(e14t_json)
    print(f"  Transmission codes: {len(tx_idx)}")

    print("[build_index] Resolving E14T paths (SHA256 direct lookup)...")
    e14t_paths = resolve_e14t_paths(tx_idx, e14t_root, e14t_json_path=e14t_json)
    resolved_e14t = sum(1 for v in e14t_paths.values() if v is not None)
    print(f"  E14T resolved: {resolved_e14t}/{len(e14t_paths)}")

    print("[build_index] Scanning E14D...")
    e14d_paths = resolve_e14d_paths(e14d_jsonl, e14d_root)
    resolved_e14d = sum(1 for v in e14d_paths.values() if v is not None)
    print(f"  E14D resolved: {resolved_e14d}/{len(e14d_paths)}")

    print("[build_index] Joining indexes...")
    records = build_cross_index(e14c_paths, e14t_paths, e14d_paths)
    all_avail = sum(1 for r in records if r["all_available"])
    print(f"  Total records: {len(records)}, all_available: {all_avail}")

    # Atomic write: temp → fsync → rename
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".jsonl.tmp")

    with open(tmp_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        import os
        os.fsync(f.fileno())

    tmp_path.replace(output_path)
    print(f"[build_index] Written to {output_path}")
    return len(records)


# ---------------------------------------------------------------------------
# Quick manual test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    key = normalize_key("1", "1", "01", "8", "1")
    print("normalize_key:", key)
    assert key == ("01", "001", "001", "08", "001"), f"Got {key}"
    print("normalize_key OK")
