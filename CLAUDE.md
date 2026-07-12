# Analizador de Elecciones — Instrucciones para Claude

## Qué es este proyecto

Sistema de recolección y análisis de Actas E-14 de las elecciones presidenciales colombianas 2026 para detección de fraude. Hay dos módulos principales:

- **E-14 Delegados** — actas de delegados (Angular SPA, requiere Playwright)
- **E-14C Oficial** — actas de escrutinio oficial (API HTTP pública, sin browser)

## Comandos clave

```bash
# Recolección
python main.py collect-e14c                         # Recolectar URLs E-14C (sin browser)
python main.py collect-e14c --concurrent 50
python main.py collect-urls --departamento AMAZONAS --visible   # E-14 Delegados
python main.py download-e14c --departamento AMAZONAS --concurrent 20

# Análisis y detección de fraude
python main.py analyze-e14c --dir data/pdfs/AMAZONAS --output data/amazonas_cnn.jsonl
python scripts/analyze/analyze_all_depts.py --max-pdfs 3000         # Análisis batch por departamento

# Notas de jurado
python main.py extract-notes --dir data/pdfs/AMAZONAS --output data/amazonas_notes.jsonl
python main.py extract-notes --dir data/pdfs/AMAZONAS --calibrate  # Debug: verificar región

# Etiquetado
python main.py export-crops --limit 2000            # Exportar crops de dígitos
python main.py label                                # Portal de etiquetado (localhost:5000)

# Estado y utilidades
python main.py status
python main.py status --pending
python main.py discover                             # Inspeccionar formulario Angular en browser
```

## Stack

- Python 3.12, Playwright 1.44 (async), aiosqlite, rich
- Sin test suite automatizado — validación manual con scripts `diagnostic*.py`
- El sitio E-14C **bloquea headless** — siempre usar `headless=False` o HTTP directo

## Archivos de datos importantes

| Archivo | Descripción |
|---|---|
| `data/departamentos.json` | 34 departamentos con IDs internos (NO son códigos DANE) |
| `data/divipole.json` | Jerarquía completa: dept → mpio → zona → puesto → nombre |
| `data/e14c_index.json` | Índice maestro: 14,438 puestos → filename JSON actual |
| `data/e14c_urls.jsonl` | 86,929 URLs de PDFs E-14C recolectadas |
| `data/e14c_progress.json` | Checkpoint resumible del colector E-14C |
| `data/urls/e14_urls.jsonl` | URLs E-14 Delegados (en construcción) |

## APIs descubiertas (E-14C)

```
# Sin auth, SSL bypass necesario (certificado corporativo colombiano)
BASE = https://escrutiniospresidente2026.registraduria.gov.co

GET /data/index.json
  → { "data/esc/v1/actas-documentos/001/{dept}/{mpio}/{zona}/{puesto}/mesas/": "filename.json" }

GET /data/esc/v1/actas-documentos/001/{dept}/{mpio}/{zona}/{puesto}/mesas/{filename}.json
  → [{ "numero": 1, "digitalizado": 1, "escrutado": true, "nombre_archivo": "/docs/E14/..." }]

GET BASE + nombre_archivo  →  PDF del acta (~1.8-2.2 MB cada uno)
```

Cada puesto tiene su **propio timestamp** en el filename — se actualiza cuando la Registraduría sube nuevos datos. Correr `collect-e14c` sin `--reset` retoma desde el checkpoint.

## Módulo E-14 Delegados — gotchas críticos

- El sitio usa `<app-custom-select>` (Angular custom), NO `<select>` estándar ni Angular Material
- Hay **múltiples** `div.dropdown-list` en el DOM simultáneamente — SIEMPRE scopear al `app-custom-select` padre con `_FIELD_CONTAINERS` en `form_handler.py`
- La home no tiene dropdown de departamentos — son `<a href="/departamento/{id}">` en una tabla paginada
- Para ver todos los departamentos en la home: cambiar `#pageSize` al valor máximo (40)
- El formulario se navega así: home → click depto → form con municipio/zona/puesto → Consultar → mesas
- Los IDs de departamento en la URL de E-14 Delegados coinciden con los de E-14C (ej: ANTIOQUIA=01)
- El porcentaje en los dropdowns (ej: `"001 — LETICIA (100%)"`) es confiable a nivel municipio; no saltear zonas ni puestos por 0%

## E-14C — gotchas

- `data/index.json` puede tener menos entradas en una sesión que en otra — se actualiza dinámicamente
- Puestos con `digitalizado=0` y `nombre_archivo=""` son mesas sin acta subida aún
- El campo `escrutado=true` confirma que el acta fue revisada por la comisión
- `id_informacion_mesa_corporacion` = `{corp:2}{dept:2}{mpio:3}{zona:2}{puesto:2}{mesa:n}` padded 16 chars
- Los PDFs son `application/octet-stream` (~1.8-2.2 MB), header `%PDF` confirmado

## Módulos del analizador

| Módulo | Descripción |
|---|---|
| `src/modules/analyzer/form_extractor.py` | Extrae campos numéricos + fraude aritmético |
| `src/modules/analyzer/ocr_engines.py` | CNN (MobileNetV2, 98.7%) + EasyOCR fallback |
| `src/modules/analyzer/notes_extractor.py` | Extrae notas manuscritas de jurados (EasyOCR es+en) |
| `src/modules/analyzer/tachon_detector.py` | Detección de tachones/enmiendas |
| `src/modules/labeler/server.py` | Portal Flask local (single-user) + producción (Supabase multi-user) |
| `src/modules/labeler/auth.py` | JWT + Supabase Auth (dev bypass con FAKE_USER_ID) |
| `src/modules/labeler/db.py` | Capa Supabase PostgreSQL |
| `src/modules/labeler/export_supabase.py` | Sube crops a Supabase Storage |
| `models/digit_classifier.pth` | Pesos CNN MobileNetV2 (98.7% val accuracy) |

## Portal web (producción)

```bash
# Deploy Railway — requiere vars de entorno:
# SUPABASE_URL, SUPABASE_ANON_KEY, SECRET_KEY, LABELS_DIR, USE_SUPABASE_STORAGE

# Setup inicial (una sola vez):
# 1. Ejecutar scripts/supabase_schema.sql en Supabase dashboard
# 2. Ejecutar scripts/assign_next_crop.sql en Supabase dashboard
# 3. python scripts/migrate_to_supabase.py  # importa crops + labels existentes

# Dev local (sin Supabase):
python main.py label  # arranca en localhost:5000 con _STATE singleton local
```

## notes_extractor — coordenadas calibradas

Las regiones de notas están configuradas para el template AMAZONAS/LETICIA a 300 DPI:
- `NOTES_REGION_P1` = página 1, y=3480-3897 (sección Notas/Constancias)
- `NOTES_REGION_P2` = página 2, completa (firmas + cédulas + notas continuación)

Si otro departamento tiene un template diferente, correr:
```bash
python main.py extract-notes --dir data/pdfs/{DEPT} --calibrate
# Revisa debug/notes_region_p1.png y debug/notes_region_p2.png
```

## Convenciones de código

- Todo el código fuente en inglés (identificadores, comentarios, UI strings)
- Logs y mensajes al usuario en inglés
- Sin test suite por ahora — scripts `diagnostic*.py` para validación manual
- Los módulos de scraping son independientes — no comparten estado entre E-14 y E-14C
- Storage: JSONL para output principal, SQLite para checkpoint (E-14) / JSON simple para checkpoint (E-14C)
