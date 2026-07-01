"""
verificador_e14c.py — Distributed offline verifier for E-14C PDFs.

This is the ONLY file shipped to collaborators (together with hash_index_e14c.json,
ejecutar.bat, and README.md). It makes ZERO network requests.

Usage:
    python verificador_e14c.py [folder]   (default: PDFs-V2)

Dependencies: Python 3.8+ stdlib + tqdm (auto-installed by ejecutar.bat).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# Enable ANSI escape codes on Windows
os.system("")
_COLOR  = "\033[38;2;140;193;211m"  # #8cc1d3
_RESET  = "\033[0m"

try:
    from tqdm import tqdm
except ImportError:
    print("tqdm is required: pip install tqdm", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Script directory — informe.md is written here, not inside the PDFs folder
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
INDEX_FILENAME = "hash_index_e14c.json"
REPORT_FILENAME = "informe.md"
CHUNK = 64 * 1024  # 64 KB
VERSION = "2026-07-01"
RELEASES_API = "https://api.github.com/repos/NucleuxCorp/Analizador-Electoral/releases/latest"

LOGO = """
                  +++++++++++++++++++
              +++++++++++++++++++++++++++
           +++++++++++++++++++++++++++++++++
         ++++++++++++++++++=====++++++++++++++
       ++++++++++++++=----------------=+++++++++
      ++++++++++++--------==+++++==----=++++++++++
    +++++++++++=-----=+++++++++++++++++++++++++++++
   +++++++++++----=++++++++=====++++++++++++++++++++
  ++++++++++=---=++++++++=---------==++++++++++++++++
 ++++++++++----+++++++++=--------------=++++++++++++++
 +++++++++=---+++++++++---------------------=++++++++++
+++++++++=---++++++++------------------------=+++++++++
+++++++++=--+++++++++++++==----------------++++++++++++
+++++++++--=+++++++++++++++++++++=----------+++++++++++
++++++++=--+++++++++++++++++++++++++=--------++++++++++
++++++++=--+++++++++++++++++++++++++++-------++++++++++
++++++++=--++++++++++++++++++++++++++++------=+++++++++
+++++++++--++++++++++++++++++++++++++++=-----=+++++++++
+++++++++=-=+++++++++++++++++++++++++++=-----++++++++++
 +++++++++--+++++++++++++++++++++++++++=-----++++++++++
 ++++++++++--++++=-++++++++++++++++++++=----++++++++++
  ++++++++++--+=---=++++++++++++++++++=----++++++++++
   ++++++++++=------=++++++++++++++++=----++++++++++
    +++++++++++-------=++++++++++++=----=++++++++++
     *+++++++++++=--------======------=+++++++++++
       +++++++++++++=--------------=++++++++++++
         ++++++++++++++++=====++++++++++++++++
           +++++++++++++++++++++++++++++++++
              +++++++++++++++++++++++++++
                  +++++++++++++++++++
"""


# ---------------------------------------------------------------------------
# Index loading
# ---------------------------------------------------------------------------
def load_index(
    index_path: Path,
) -> tuple[dict[str, str], dict[str, str], dict[str, dict], dict]:
    """
    Load hash_index_e14c.json. Returns (by_hash, by_filename, known_dif, meta).
    Exits non-zero with a clear message if the file is missing.
    """
    if not index_path.exists():
        print(
            f"ERROR: Index file not found: {index_path}\n"
            f"  Place hash_index_e14c.json next to this script and retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    t0 = datetime.now()
    with open(index_path, encoding="utf-8") as f:
        data = json.load(f)
    elapsed = (datetime.now() - t0).total_seconds()

    by_hash: dict[str, str] = data.get("by_hash", {})
    by_filename: dict[str, str] = data.get("by_filename", {})
    known_dif: dict[str, dict] = data.get("known_dif", {})
    meta = {
        "version": data.get("version", "desconocida"),
        "generated_at": data.get("generated_at", ""),
        "entries_count": data.get("entries_count", len(by_hash)),
        "known_dif_count": data.get("known_dif_count", len(known_dif)),
        "coverage_note": data.get("coverage_note", ""),
    }

    print(
        f"Indice cargado: version {meta['version']}, "
        f"{meta['entries_count']:,} entradas, "
        f"{meta['known_dif_count']} DIF conocidos "
        f"({elapsed:.2f}s)",
        flush=True,
    )
    return by_hash, by_filename, known_dif, meta


# ---------------------------------------------------------------------------
# PDF scanning
# ---------------------------------------------------------------------------
def scan_folder(folder: Path) -> list[Path]:
    """Return sorted list of PDFs found recursively (case-insensitive extension)."""
    pdfs = list(folder.rglob("*.[Pp][Dd][Ff]"))
    return sorted(pdfs)


# ---------------------------------------------------------------------------
# SHA-256 hashing
# ---------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    """Stream a file in 64 KB chunks and return its SHA-256 hex digest."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 4-step classification decision tree
# ---------------------------------------------------------------------------
def classify(
    path: Path,
    actual_sha256: str,
    by_hash: dict[str, str],
    by_filename: dict[str, str],
    known_dif: dict[str, dict],
) -> dict:
    """
    Classify a PDF using the ordered 4-step decision tree.
    Returns a result dict with keys: path, filename, status, sha256,
    expected_sha256, note.
    """
    filename = path.name
    result = {
        "path": str(path),
        "filename": filename,
        "status": "DESCONOCIDA",
        "sha256": actual_sha256,
        "expected_sha256": "",
        "note": "",
    }

    # Step 1: hash match — fastest positive path
    if actual_sha256 in by_hash:
        result["status"] = "VERIFICADA"
        result["expected_sha256"] = actual_sha256
        return result

    # Step 2: filename in index but hash differs — confirmed alteration
    if filename in by_filename and by_filename[filename] != actual_sha256:
        result["status"] = "ALTERADA"
        result["expected_sha256"] = by_filename[filename]
        return result

    # Step 3: known DIF — acta con multiples versiones documentadas
    if filename in known_dif:
        entry = known_dif[filename]
        # Soporta tanto versions[] (nuevo) como local/server_sha256 (legacy)
        versions = entry.get("versions", [])
        if not versions:
            for key, note in [
                ("local_sha256",  "version original"),
                ("server_sha256", "version servidor post-modificacion"),
            ]:
                sha = entry.get(key, "")
                if sha:
                    versions.append({"sha256": sha, "note": note})

        for v in versions:
            if actual_sha256 == v.get("sha256", ""):
                result["status"] = "VERIFICADA"
                result["note"] = f"version conocida en known_dif: {v.get('note', '')}"
                return result

        result["status"] = "DESCONOCIDA"
        result["note"] = f"archivo en known_dif pero hash no coincide con ninguna de las {len(versions)} versiones conocidas"
        return result

    # Step 4: catch-all
    return result


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------
def _pct(n: int, total: int) -> str:
    if total == 0:
        return "0.00%"
    return f"{100 * n / total:.2f}%"


def write_report(results: list[dict], index_meta: dict, folder: Path) -> Path:
    """
    Write informe.md (latest) and informe_YYYY-MM-DD_HH-MM.md (historical record).
    Returns the timestamped path.
    """
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d_%H-%M")
    report_path = SCRIPT_DIR / REPORT_FILENAME
    timestamped_path = SCRIPT_DIR / f"informe_{ts}.md"

    verificadas = [r for r in results if r["status"] == "VERIFICADA" and not r["note"]]
    verificadas_dif = [r for r in results if r["status"] == "VERIFICADA" and r["note"]]
    alteradas = [r for r in results if r["status"] == "ALTERADA"]
    desconocidas = [r for r in results if r["status"] == "DESCONOCIDA"]
    total = len(results)

    now_str = now.strftime("%Y-%m-%d %H:%M")
    version = index_meta.get("version", "desconocida")
    entries = index_meta.get("entries_count", 0)

    lines: list[str] = []

    # --- Header ----------------------------------------------------------------
    lines.append("# Informe de Verificacion E-14C")
    lines.append("")
    lines.append(f"**Fecha:** {now_str}  ")
    lines.append(f"**Indice version:** {version} ({entries:,} entradas)  ")
    lines.append(f"**PDFs analizados:** {total:,}  ")
    lines.append(f"**Carpeta analizada:** {folder}  ")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Coverage disclosure BEFORE summary (mandatory per spec) ---------------
    lines.append(
        "> **NOTA DE COBERTURA:** Este indice cubre aproximadamente 83,498 actas E-14C "
        "de un total de ~118,347 registradas en el servidor de la Registraduria. "
        "Los PDFs de las ~27,000 mesas no incluidas en el indice apareceran como "
        "**DESCONOCIDA** — esto NO significa que esten alteradas, sino que no tenemos "
        "su huella digital de referencia. "
        "DESCONOCIDA = fuera de la cobertura del indice. "
        "ALTERADA = evidencia positiva de modificacion."
    )
    lines.append("")

    # --- Summary table ---------------------------------------------------------
    lines.append("## Resumen")
    lines.append("")
    lines.append("| Estado | Cantidad | Porcentaje |")
    lines.append("|--------|--------:|----------:|")
    verificadas_total = len(verificadas) + len(verificadas_dif)
    lines.append(f"| VERIFICADA | {verificadas_total:,} | {_pct(verificadas_total, total)} |")
    lines.append(f"| ALTERADA | {len(alteradas):,} | {_pct(len(alteradas), total)} |")
    lines.append(f"| DESCONOCIDA | {len(desconocidas):,} | {_pct(len(desconocidas), total)} |")
    lines.append(f"| **Total** | **{total:,}** | **100%** |")
    lines.append("")

    # --- ALTERADA section ------------------------------------------------------
    lines.append(f"## Actas ALTERADAS ({len(alteradas)})")
    lines.append("")
    if alteradas:
        lines.append(
            "Archivos cuyo nombre esta en el indice pero cuyo hash SHA-256 "
            "difiere del hash canonico registrado. Evidencia de modificacion."
        )
        lines.append("")
        lines.append("| Archivo | SHA-256 esperado | SHA-256 encontrado |")
        lines.append("|---------|-----------------|-------------------|")
        for r in alteradas:
            lines.append(f"| {r['filename']} | {r['expected_sha256']} | {r['sha256']} |")
    else:
        lines.append("Ninguna.")
    lines.append("")

    # --- DESCONOCIDA section ---------------------------------------------------
    lines.append(f"## Actas DESCONOCIDAS ({len(desconocidas)})")
    lines.append("")
    if desconocidas:
        lines.append(
            "Archivos cuyo nombre NO esta en el indice canonico y cuyo hash tampoco "
            "coincide con ninguna entrada. Pueden ser: (a) actas de las ~27,000 mesas "
            "fuera del indice, (b) PDFs E-14T con nombre-hash, (c) archivos ajenos al "
            "corpus E-14C."
        )
        lines.append("")
        lines.append("| Archivo | SHA-256 calculado |")
        lines.append("|---------|-----------------|")
        for r in desconocidas:
            note = f" — {r['note']}" if r.get("note") else ""
            lines.append(f"| {r['filename']}{note} | {r['sha256']} |")
    else:
        lines.append("Ninguna.")
    lines.append("")

    # --- VERIFICADA con nota DIF section ---------------------------------------
    lines.append(f"## Actas VERIFICADAS con nota DIF ({len(verificadas_dif)})")
    lines.append("")
    if verificadas_dif:
        lines.append(
            "Archivos que coinciden con una entrada de `known_dif` — el servidor de la "
            "Registraduria publico dos versiones distintas de estas actas. Ambas versiones "
            "provienen del servidor oficial."
        )
        lines.append("")
        lines.append("| Archivo | Version coincidente |")
        lines.append("|---------|-------------------|")
        for r in verificadas_dif:
            lines.append(f"| {r['filename']} | {r['note']} |")
    else:
        lines.append("Ninguna.")
    lines.append("")

    # --- Footer ----------------------------------------------------------------
    lines.append("---")
    lines.append("")
    lines.append(f"*Fecha del snapshot del indice: {version}*  ")
    lines.append("*Generado por verificador-e14c v1.0 — Proyecto Analizador de Elecciones*")

    content = "\n".join(lines) + "\n"
    timestamped_path.write_text(content, encoding="utf-8-sig")
    report_path.write_text(content, encoding="utf-8-sig")
    return timestamped_path


# ---------------------------------------------------------------------------
# Organize / revert — Registraduría folder structure
# ---------------------------------------------------------------------------
ORG_LOG_FILENAME = "organizacion_log.json"

_DEPT_NAMES: dict[str, str] = {
    "01": "ANTIOQUIA",    "03": "ATLANTICO",      "05": "BOLIVAR",
    "07": "BOYACA",       "09": "CALDAS",          "11": "CAUCA",
    "12": "CESAR",        "13": "CORDOBA",         "15": "CUNDINAMARCA",
    "16": "BOGOTA D.C.",  "17": "CHOCO",           "19": "HUILA",
    "21": "MAGDALENA",    "23": "NARINO",           "24": "RISARALDA",
    "25": "NORTE DE SAN", "26": "QUINDIO",         "27": "SANTANDER",
    "28": "SUCRE",        "29": "TOLIMA",           "31": "VALLE",
    "40": "ARAUCA",       "44": "CAQUETA",         "46": "CASANARE",
    "48": "LA GUAJIRA",   "50": "GUAINIA",         "52": "META",
    "54": "GUAVIARE",     "56": "SAN ANDRES",      "60": "AMAZONAS",
    "64": "PUTUMAYO",     "68": "VAUPES",           "72": "VICHADA",
    "88": "CONSULADOS",
}


def _parse_dept_parts(filename: str) -> tuple[str, str, str, str] | None:
    """Extract (dept, mpio, zona, puesto) from E14C filename, or None if not parseable."""
    stem = Path(filename).stem
    parts = stem.split("_", 5)
    if len(parts) > 4:
        return parts[0], parts[1], parts[2], parts[3]
    return None


def organize_pdfs(folder: Path) -> tuple[int, int, int]:
    """
    Move PDFs into {folder}/{dept}/{mpio}/{zona}/{puesto}/ structure.
    Saves organizacion_log.json next to the script.
    Returns (moved, skipped_no_pattern, skipped_conflict).
    """
    log_path = SCRIPT_DIR / ORG_LOG_FILENAME
    pdfs = scan_folder(folder)

    log: dict = {
        "organized_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "base_folder": str(folder),
        "files": {},
    }

    moved = 0
    skipped_no_pattern = 0
    skipped_conflict = 0

    for pdf in pdfs:
        parsed = _parse_dept_parts(pdf.name)
        if not parsed:
            skipped_no_pattern += 1
            continue

        dept_code, mpio, zona, puesto = parsed
        dept_name = _DEPT_NAMES.get(dept_code)
        if not dept_name:
            skipped_no_pattern += 1
            continue

        dest = folder / dept_name / mpio / zona / puesto / pdf.name

        if dest == pdf:
            continue

        if dest.exists():
            skipped_conflict += 1
            print(f"  CONFLICTO (ya existe): {dest.name}")
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        log["files"][pdf.name] = {
            "original": str(pdf),
            "organized": str(dest),
        }
        pdf.rename(dest)
        moved += 1

    if log["files"]:
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

    return moved, skipped_no_pattern, skipped_conflict


def revert_organization() -> tuple[int, int]:
    """
    Move PDFs back to their original paths using organizacion_log.json.
    Returns (reverted, errors).
    """
    log_path = SCRIPT_DIR / ORG_LOG_FILENAME
    log = json.loads(log_path.read_text(encoding="utf-8"))

    reverted = 0
    errors = 0

    for filename, entry in log["files"].items():
        organized = Path(entry["organized"])
        original = Path(entry["original"])

        if not organized.exists():
            print(f"  NO ENCONTRADO: {filename}")
            errors += 1
            continue

        original.parent.mkdir(parents=True, exist_ok=True)
        try:
            organized.rename(original)
            reverted += 1
        except Exception as exc:
            print(f"  ERROR al mover {filename}: {exc}")
            errors += 1

    if errors == 0:
        log_path.unlink()

    return reverted, errors


def run_organize_and_verify(folder_str: str) -> None:
    folder = Path(folder_str)
    if not folder.is_absolute():
        folder = SCRIPT_DIR / folder

    if not folder.exists():
        print(f"\n  ERROR: Carpeta no encontrada: {folder}")
        input("\n  Presiona Enter para continuar...")
        return

    print(f"\n  Organizando PDFs en estructura Registraduria...")
    moved, no_pat, conflict = organize_pdfs(folder)
    print(f"  Movidos:           {moved:,}")
    if no_pat:
        print(f"  Sin patron E14C:   {no_pat:,}  (no se movieron)")
    if conflict:
        print(f"  Conflictos:        {conflict:,}  (ya existian en destino)")
    print()

    index_path = SCRIPT_DIR / INDEX_FILENAME
    if not index_path.exists():
        print("  AVISO: indice no encontrado, se omite la validacion offline.")
        input("\n  Presiona Enter para continuar...")
        return

    run_verification(str(folder))


def run_revert_organization() -> None:
    log_path = SCRIPT_DIR / ORG_LOG_FILENAME
    log = json.loads(log_path.read_text(encoding="utf-8"))
    total = len(log["files"])
    print(f"\n  Revirtiendo {total:,} archivos a estructura original...")
    reverted, errors = revert_organization()
    print(f"  Revertidos: {reverted:,}")
    if errors:
        print(f"  Errores:    {errors:,}  (log conservado para revision)")
    else:
        print(f"  Log eliminado.")
    input("\n  Presiona Enter para continuar...")


# ---------------------------------------------------------------------------
# Online verification against Registraduría server
# ---------------------------------------------------------------------------
BASE_URL = "https://escrutinios2vueltapresidente2026.registraduria.gov.co"

# SSL bypass — Registraduría uses a Colombian corporate cert not in default store
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def build_url(filename: str) -> str | None:
    """Reconstruct Registraduría download URL from an E14C filename."""
    stem = Path(filename).stem
    parts = stem.split("_", 5)
    if len(parts) > 5 and parts[4] == "E14":
        dept, mpio, zona, puesto = parts[0], parts[1], parts[2], parts[3]
        url_fn = "_".join(parts[4:]) + ".pdf"
        return f"{BASE_URL}/docs/E14/{dept}/{mpio}/{zona}/{puesto}/{url_fn}"
    return None


def download_and_hash(url: str) -> tuple[str, str]:
    """
    Download PDF from url and compute SHA-256 without saving to disk.
    Returns (sha256_hex, error_message). On success error_message is "".
    """
    time.sleep(0.3)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, context=_SSL_CTX, timeout=60)
        h = hashlib.sha256()
        while chunk := resp.read(CHUNK):
            h.update(chunk)
        resp.close()
        return h.hexdigest(), ""
    except Exception as exc:
        return "", str(exc)


def run_verification_online(folder_str: str) -> None:
    folder = Path(folder_str)
    if not folder.is_absolute():
        folder = SCRIPT_DIR / folder

    if not folder.exists():
        print(f"\n  ERROR: Carpeta no encontrada: {folder}")
        input("\n  Presiona Enter para continuar...")
        return

    pdfs = scan_folder(folder)
    print(f"\n  PDFs encontrados: {len(pdfs):,}", flush=True)

    if not pdfs:
        print("  La carpeta esta vacia. Coloca PDFs dentro de PDFs-V2/ e intenta de nuevo.")
        input("\n  Presiona Enter para continuar...")
        return

    # Load index for cross-reference if available
    index_path = SCRIPT_DIR / INDEX_FILENAME
    by_hash: dict = {}
    by_filename: dict = {}
    known_dif: dict = {}
    index_meta: dict = {}
    has_index = index_path.exists()
    if has_index:
        by_hash, by_filename, known_dif, index_meta = load_index(index_path)

    print("  Verificando contra el servidor de la Registraduria...\n")

    results: list[dict] = []
    with tqdm(pdfs, desc="  Verificando", unit="PDF") as bar:
        for pdf_path in bar:
            filename = pdf_path.name
            url = build_url(filename)
            local_sha = ""
            server_sha = ""
            note = ""
            indice_status = "SIN_INDICE"

            if not url:
                status = "URL_NO_CONSTRUIBLE"
                note = "El nombre no corresponde al formato E14C"
            else:
                try:
                    local_sha = sha256_file(pdf_path)
                except Exception as exc:
                    status = "ERROR_LOCAL"
                    note = f"error al leer archivo: {exc}"
                else:
                    if has_index:
                        indice_status = classify(pdf_path, local_sha, by_hash, by_filename, known_dif)["status"]
                    server_sha, err = download_and_hash(url)
                    if err:
                        status = "ERROR_DESCARGA"
                        note = err
                    elif local_sha == server_sha:
                        status = "IGUAL_SERVIDOR"
                    else:
                        status = "DIFERENTE_SERVIDOR"

            results.append({
                "path": str(pdf_path),
                "filename": filename,
                "status": status,
                "indice_status": indice_status,
                "local_sha256": local_sha,
                "server_sha256": server_sha,
                "url": url or "",
                "note": note,
            })

    igual = sum(1 for r in results if r["status"] == "IGUAL_SERVIDOR")
    diferente = sum(1 for r in results if r["status"] == "DIFERENTE_SERVIDOR")
    errores = sum(1 for r in results if r["status"] not in ("IGUAL_SERVIDOR", "DIFERENTE_SERVIDOR"))

    print()
    print(f"  IGUAL AL SERVIDOR:      {igual:,}")
    print(f"  DIFERENTE AL SERVIDOR:  {diferente:,}")
    print(f"  ERRORES:                {errores:,}")

    report_path = write_report_online(results, folder)
    print(f"\n  Informe escrito: {report_path}")
    input("\n  Presiona Enter para continuar...")


def write_report_online(results: list[dict], folder: Path) -> Path:
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d_%H-%M")
    report_path = SCRIPT_DIR / "informe_online.md"
    timestamped_path = SCRIPT_DIR / f"informe_online_{ts}.md"

    igual = [r for r in results if r["status"] == "IGUAL_SERVIDOR"]
    diferente = [r for r in results if r["status"] == "DIFERENTE_SERVIDOR"]
    errores = [r for r in results if r["status"] not in ("IGUAL_SERVIDOR", "DIFERENTE_SERVIDOR")]
    total = len(results)

    now_str = now.strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []

    lines.append("# Informe de Verificacion Online E-14C")
    lines.append("")
    lines.append(f"**Fecha:** {now_str}  ")
    lines.append("**Fuente:** Servidor live de la Registraduria  ")
    lines.append(f"**PDFs analizados:** {total:,}  ")
    lines.append(f"**Carpeta analizada:** {folder}  ")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "> **NOTA:** Esta verificacion compara tus PDFs contra lo que el servidor de la "
        "Registraduria sirve en este momento, y cruza el resultado con el indice local "
        "(snapshot 2026-06-30) cuando esta disponible. "
        "**DIFERENTE_SERVIDOR + VERIFICADA en indice** = tu copia es la original, el servidor cambio. "
        "**DIFERENTE_SERVIDOR + ALTERADA en indice** = tu copia difiere de ambos origenes."
    )
    lines.append("")

    lines.append("## Resumen")
    lines.append("")
    lines.append("| Estado | Cantidad | Porcentaje |")
    lines.append("|--------|--------:|----------:|")
    lines.append(f"| IGUAL AL SERVIDOR | {len(igual):,} | {_pct(len(igual), total)} |")
    lines.append(f"| DIFERENTE AL SERVIDOR | {len(diferente):,} | {_pct(len(diferente), total)} |")
    lines.append(f"| ERRORES | {len(errores):,} | {_pct(len(errores), total)} |")
    lines.append(f"| **Total** | **{total:,}** | **100%** |")
    lines.append("")

    lines.append(f"## PDFs diferentes al servidor ({len(diferente)})")
    lines.append("")
    if diferente:
        lines.append(
            "Estos archivos difieren del contenido que el servidor sirve hoy. "
            "Puede indicar modificacion local o actualizacion del servidor post-descarga."
        )
        lines.append("")
        lines.append("| Archivo | Estado indice | SHA-256 local | SHA-256 servidor |")
        lines.append("|---------|--------------|--------------|-----------------|")
        for r in diferente:
            lines.append(f"| {r['filename']} | {r['indice_status']} | {r['local_sha256']} | {r['server_sha256']} |")
    else:
        lines.append("Ninguno.")
    lines.append("")

    lines.append(f"## Errores ({len(errores)})")
    lines.append("")
    if errores:
        lines.append("| Archivo | Estado | Detalle |")
        lines.append("|---------|--------|---------|")
        for r in errores:
            lines.append(f"| {r['filename']} | {r['status']} | {r['note']} |")
    else:
        lines.append("Ninguno.")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*Generado por verificador-e14c — Proyecto Analizador de Elecciones*")

    content = "\n".join(lines) + "\n"
    timestamped_path.write_text(content, encoding="utf-8-sig")
    report_path.write_text(content, encoding="utf-8-sig")
    return timestamped_path


# ---------------------------------------------------------------------------
# Presentation & menu
# ---------------------------------------------------------------------------
def check_for_updates() -> None:
    """Query GitHub releases API and notify if a newer version is available. Silent on failure."""
    try:
        req = urllib.request.Request(
            RELEASES_API,
            headers={"User-Agent": "verificador-e14c"},
        )
        resp = urllib.request.urlopen(req, timeout=4)
        data = json.loads(resp.read().decode())
        latest = data.get("tag_name", "").lstrip("v")
        if latest and latest > VERSION:
            print(f"  *** NUEVA VERSION DISPONIBLE: {latest} (tienes {VERSION}) ***")
            print(f"  Descarga: {data.get('html_url', '')}")
            print()
    except Exception:
        pass


def show_logo() -> None:
    print(f"{_COLOR}{LOGO}{_RESET}")
    print("  Verificador de Hash E-14  |  Auditoria ciudadana Colombia 2026")
    print("  Modo offline e online     |  Codigo abierto")
    print()


def check_index() -> bool:
    """Returns True if index exists, prints a clear message if not."""
    index_path = SCRIPT_DIR / INDEX_FILENAME
    if index_path.exists():
        return True
    print("=" * 60)
    print("  INDICE NO ENCONTRADO")
    print("=" * 60)
    print()
    print(f"  El archivo '{INDEX_FILENAME}' no esta en esta carpeta.")
    print()
    print("  Este archivo contiene las huellas digitales de referencia")
    print("  y es necesario para verificar los PDFs.")
    print()
    print("  Si lo descargaste por separado, coloca el archivo aqui:")
    print(f"  {SCRIPT_DIR}")
    print()
    print("  Si eres el mantenedor, genera el indice con:")
    print("    python generar_hash_index.py")
    print()
    return False


def run_verification(folder_str: str) -> None:
    folder = Path(folder_str)
    if not folder.is_absolute():
        folder = SCRIPT_DIR / folder

    if not folder.exists():
        print(f"\n  ERROR: Carpeta no encontrada: {folder}")
        input("\n  Presiona Enter para continuar...")
        return

    index_path = SCRIPT_DIR / INDEX_FILENAME
    by_hash, by_filename, known_dif, index_meta = load_index(index_path)

    pdfs = scan_folder(folder)
    print(f"\n  PDFs encontrados: {len(pdfs):,}", flush=True)

    if not pdfs:
        print("  La carpeta esta vacia. Coloca PDFs dentro de PDFs-V2/ e intenta de nuevo.")
        input("\n  Presiona Enter para continuar...")
        return

    results: list[dict] = []
    with tqdm(pdfs, desc="  Verificando", unit="PDF") as bar:
        for pdf_path in bar:
            try:
                actual_sha256 = sha256_file(pdf_path)
                result = classify(pdf_path, actual_sha256, by_hash, by_filename, known_dif)
            except Exception as exc:
                result = {
                    "path": str(pdf_path),
                    "filename": pdf_path.name,
                    "status": "DESCONOCIDA",
                    "sha256": "",
                    "expected_sha256": "",
                    "note": f"error al leer archivo: {exc}",
                }
            results.append(result)

    alteradas    = sum(1 for r in results if r["status"] == "ALTERADA")
    verificadas  = sum(1 for r in results if r["status"] == "VERIFICADA")
    desconocidas = sum(1 for r in results if r["status"] == "DESCONOCIDA")

    print()
    print(f"  VERIFICADA:   {verificadas:,}")
    print(f"  ALTERADA:     {alteradas:,}")
    print(f"  DESCONOCIDA:  {desconocidas:,}")

    report_path = write_report(results, index_meta, folder)
    print(f"\n  Informe escrito: {report_path}")
    input("\n  Presiona Enter para continuar...")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    # CLI mode — if folder argument provided, skip menu
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(
            description="Verificador local de PDFs E-14C — sin conexion a internet."
        )
        parser.add_argument("folder", nargs="?", default="PDFs-V2")
        args = parser.parse_args()
        index_path = SCRIPT_DIR / INDEX_FILENAME
        by_hash, by_filename, known_dif, index_meta = load_index(index_path)
        folder = Path(args.folder)
        if not folder.exists():
            print(f"ERROR: Carpeta no encontrada: {folder}", file=sys.stderr)
            sys.exit(1)
        pdfs = scan_folder(folder)
        print(f"PDFs encontrados: {len(pdfs):,}", flush=True)
        if not pdfs:
            write_report([], index_meta, folder)
            return
        results: list[dict] = []
        with tqdm(pdfs, desc="Verificando", unit="PDF") as bar:
            for pdf_path in bar:
                try:
                    actual_sha256 = sha256_file(pdf_path)
                    result = classify(pdf_path, actual_sha256, by_hash, by_filename, known_dif)
                except Exception as exc:
                    result = {"path": str(pdf_path), "filename": pdf_path.name,
                              "status": "DESCONOCIDA", "sha256": "", "expected_sha256": "",
                              "note": f"error al leer archivo: {exc}"}
                results.append(result)
        alt = sum(1 for r in results if r["status"] == "ALTERADA")
        ver = sum(1 for r in results if r["status"] == "VERIFICADA")
        des = sum(1 for r in results if r["status"] == "DESCONOCIDA")
        print(f"\nResultado: VERIFICADA={ver:,}  ALTERADA={alt:,}  DESCONOCIDA={des:,}")
        report_path = write_report(results, index_meta, folder)
        print(f"Informe escrito: {report_path}")
        return

    # Interactive menu mode
    show_logo()
    check_for_updates()

    index_ok = check_index()

    while True:
        org_log_exists = (SCRIPT_DIR / ORG_LOG_FILENAME).exists()
        org_label = (
            "[4] Revertir organizacion (estructura original)"
            if org_log_exists else
            "[4] Organizar y validar  (Registraduria)       "
        ).ljust(48)

        print("  ┌──────────────────────────────────────────────────┐")
        print("  │  MENU PRINCIPAL                                   │")
        print("  ├──────────────────────────────────────────────────┤")
        if index_ok:
            print("  │  [1] Offline — PDFs-V2/  (indice 2026-06-30)    │")
            print("  │  [2] Offline — otra carpeta                      │")
        else:
            print("  │  [!] Indice no disponible (opciones offline)     │")
        print("  │  [3] Online  — comparar contra Registraduria     │")
        print(f"  │  {org_label}│")
        print("  │  [0] Salir                                        │")
        print("  └──────────────────────────────────────────────────┘")
        print()

        opcion = input("  Selecciona una opcion: ").strip()
        print()

        if opcion == "0":
            break
        elif opcion == "1" and index_ok:
            run_verification("PDFs-V2")
            show_logo()
        elif opcion == "2" and index_ok:
            carpeta = input("  Ruta de la carpeta: ").strip().strip('"')
            run_verification(carpeta)
            show_logo()
        elif opcion == "3":
            carpeta = input("  Carpeta con PDFs [Enter = PDFs-V2/]: ").strip().strip('"')
            if not carpeta:
                carpeta = "PDFs-V2"
            run_verification_online(carpeta)
            show_logo()
        elif opcion == "4":
            print("  [1] Carpeta por defecto (PDFs-V2/)")
            print("  [2] Otra carpeta")
            print()
            sub = input("  Selecciona una opcion: ").strip()
            print()
            if sub == "1":
                carpeta = "PDFs-V2"
            elif sub == "2":
                carpeta = input("  Ruta de la carpeta: ").strip().strip('"')
            else:
                print("  Opcion no valida.\n")
                continue
            if org_log_exists:
                run_revert_organization()
            else:
                run_organize_and_verify(carpeta)
            show_logo()
        else:
            print("  Opcion no valida.\n")


if __name__ == "__main__":
    main()
