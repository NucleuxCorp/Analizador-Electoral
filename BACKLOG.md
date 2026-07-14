# Backlog — Analizador de Elecciones

Actualizado: 2026-07-09

## Ideas / mejoras pendientes de implementar

### GPU re-run Model A/B antes de promover reemplazo de `digit_classifier.pth` (2026-07-14)

**Contexto:** SDD `crops-labeling-cnn-actualizacion` documentó el estado real en `docs/metodologia.md` §7. El reentrenamiento dual (`openspec/changes/archive/2026-06-23-retrain-digit-classifier/`) produjo `models/digit_classifier_model_a.pth` y `_model_b.pth` (10 clases, Model A recomendado en CPU: 98.69% val). El `archive-report.md` dejó como próximo paso un **GPU re-run** con métricas exactas antes de promover — sin evidencia de ejecución posterior.

**Pendiente:** correr `train_dual_finetune.py` en GPU, comparar Model A vs B con métricas representativas, decidir promoción a `models/digit_classifier.pth`. Separado del objetivo de clases `ZERO_VARIANTS` (9 símbolos en portal, futuro modelo multi-clase).

**Archivos:** `train_dual_finetune.py`, `models/digit_classifier_model_{a,b}.pth`, `models/dual_finetune_report.txt`

**Prioridad:** Media — producción sigue en v1 estable; no bloquea operación actual.

---

### Extender `check_arithmetic()` a igualdad de 3 vías: URNA == VOTANTES == SUMA_TOTAL (2026-07-14)

**Problema detectado:** el acta E14C declara tres cantidades que en una mesa correctamente diligenciada deberían coincidir entre sí: `URNA` (conteo físico), `VOTANTES` (total de votantes que sufragaron, según el jurado) y `SUMA_TOTAL` (suma declarada a mano por el jurado). Los tres campos **se extraen** (`cross_validator.py` L32, L40), pero `check_arithmetic()` (L302–326) **solo verifica** `C1+C2+BLANCO+NULOS+NO_MARCADOS == URNA` — nunca cruza `VOTANTES` ni `SUMA_TOTAL` contra `URNA` ni contra la suma recalculada. Una mesa donde `URNA` cuadre con la suma de votos pero `VOTANTES` o `SUMA_TOTAL` difieran de ambos **no se detecta con el pipeline actual**. Documentado en `docs/metodologia.md` §4.4.

**Mejora propuesta:** extender `check_arithmetic()` (o agregar una función complementaria) para verificar también `URNA == VOTANTES` y `URNA == SUMA_TOTAL` (o `sum_votes == VOTANTES == SUMA_TOTAL`, la cadena completa), emitiendo un flag/estado distinto cuando alguno de los dos tramos adicionales falle aunque la suma de candidatos sí cuadre con `URNA` — esto puede revelar discrepancias que hoy pasan como `clean` sin serlo del todo.

**Archivos involucrados:**
- `src/modules/analyzer/cross_validator.py` — `check_arithmetic()` (L302–326), `_VOTE_ADDENDS` (L299)
- `scripts/upload/upload_mesa_results.py` — `compute_overall_status()`, para decidir si esto cambia la clasificación de 5 estados o agrega uno nuevo

**Prioridad:** Media — es un gap de detección real, no solo cosmético, pero no bloquea nada en producción hoy.

---

### Evaluar marcas de registro como método único de grid (E14C) (2026-07-14)

**Problema detectado:** `grid_detector.py::label_rows_by_structure()` (L881) usa la grilla de líneas grises como método primario para E14C, y solo recurre a `detect_4_registration_marks()` (las 4 marcas de esquina, coordenadas relativas por %) como desempate secundario cuando la detección de "mega gap" entre bloques de filas resulta ambigua (`expected_c1_y = top_y + 1693`, delta fijo de calibración). Esto significa que en los casos ambiguos, el resultado depende de que ese delta de calibración fijo sea preciso para ese PDF específico — si no lo es, se puede introducir error silencioso sin que el desempate lo detecte. Documentado en `docs/metodologia.md` §4.2.

**Mejora a evaluar (NO implementar todavía):** analizar si usar el método de marcas de registro (4 cuadrados negros, % relativo) como detector **único** para E14C —en vez de grilla gris + desempate condicional— sería más preciso y consistente, dado que ya es el método principal usado con éxito para E14T/E14D. Requiere: (1) correr ambos métodos en paralelo sobre una muestra ya validada y comparar precisión fila por fila, (2) confirmar que las marcas de registro están presentes/legibles con la misma confiabilidad en el formulario E14C que en E14T/E14D (el layout físico es distinto), (3) decidir si conviene mantener la grilla gris como fallback en vez de reemplazarla del todo.

**Archivos involucrados:**
- `src/modules/analyzer/grid_detector.py` — `label_rows_by_structure()`, `detect_4_registration_marks()`
- `debug_sv/corner_grid_calibrator.py` — implementación de referencia ya usada en E14T/E14D

**Prioridad:** Baja (no urgente, evaluar antes de decidir)

---

### `corner_grid_calibrator` — extracción robusta con 3-de-4 esquinas (2026-07-09)

**Problema detectado:** Cuando una esquina es mal detectada (ej. TL captura el código de barras o QR en vez del cuadro de registro), la proyección del grid queda completamente desviada y las subceldas apuntan a zonas incorrectas. Confirmado en E14D de Valencia (Córdoba): TL=y:965 vs TR=y:219 → inclinación falsa de 746px → subceldas de 103px en vez de ~300px.

**Mejora propuesta:** Generar las 4 combinaciones posibles de 3 esquinas de las 4 disponibles (TL+TR+BL, TL+TR+BR, TL+BL+BR, TR+BL+BR) y ejecutar 4 modos de extracción en paralelo. Comparar los resultados y elegir el más consistente (por ejemplo, el que produzca más subceldas con área razonable y mayor número de dígitos reconocidos con confianza alta). Esto hace el calibrador tolerante a 1 esquina mal detectada.

**Archivos involucrados:**
- `debug_sv/corner_grid_calibrator.py` — `detect_corner_markers()` + `GridCalibrator.from_corners()`
- `debug_sv/e14t_extractor.py` — lógica de fallback entre los 4 modos

---

## En progreso activo

### Cross-Mesa Validator — PR-2 en curso (`cross-mesa-validator`)

**Estado:** 🟡 Funcional pero con limitación de fiabilidad en E14T positional assignment

**Dónde estamos:**
- ✅ Fase 0: Spike gate — `scripts/spike_e14t_compat.py` (C1+C2 rate 93.3%)
- ✅ Fase 1: Index builder — `src/modules/analyzer/cross_mesa_index.py` (31 tests)
- ✅ Fase 2: Congruence engine — `src/modules/analyzer/cross_congruence.py` (26 tests)
- ✅ Fase 3: Extractor wrapper — `src/modules/analyzer/cross_validator.py` (10 tests)
- ✅ Fase 4: CLI — `src/modules/analyzer/cross_validator_cli.py` (12 tests)
- ✅ Fase 5: Reporte de resumen — `generate_summary_report()` + `cmd_report` (5 tests)
- ✅ Fase 6: Hook en `main.py` — `cross-build-index`, `cross-validate`, `cross-report`
- ✅ E14D habilitado — mismo extractor que E14T (mismo formulario físico)
- ✅ `cross_discrepancy` agregado — separa falta de fuentes de conflicto real

**Limitación conocida — E14T positional assignment:**
- SHA256 sort order ≠ mesa order → el PDF asignado a mesa N puede NO ser la mesa N
- 3/50 mesas con ≥2 fuentes arithmetic-OK muestran 100% cross_discrepancy → evidencia de mismatch
- Fix posible: leer metadatos del PDF para extraer mesa/puesto; o usar registro de carga del servidor
- E14D sí tiene geo_key explícito en el URL file → su asignación es correcta

**Tests actuales:** 84/84 GREEN
**Comandos disponibles:**
```bash
python main.py cross-build-index
python main.py cross-validate --dept 01 --limit 50 --workers 6
python main.py cross-report --input data/cross_mesa_validation.jsonl
```

---

## Pendientes nuevos

### Tachones — scoring y auditoría post-bugfix (2026-07-14)

**Contexto:** SDD `tachones-actualizacion` reparó el import roto de `subcell_tachon.py` (archivado `grid_detector_v2` desde 2026-07-12). Desde esa fecha, Path A (`e14_worker._analyze_primary`) devolvía `extraction_error` visible; Path B (`cross_validator_cli._run_tachon_scan`) degradaba en silencio a `tachones.e14c = null`. El batch principal (115,691 mesas, 2026-07-05) es anterior al bug.

**Pendiente 1 — Revisar peso 0.40 de TACHON en score combinado**
- `docs/metodologia.md` §5 asigna peso 0.40 a TACHON en la fórmula `combined`
- `docs/metodologia_tachones.md` L172 documenta TACHON como "ruido universal" (93.3% flag rate, 100% PDFs) y excluido de scans de producción
- **No decidir en el bugfix** — evaluar si la fórmula de §5 debe alinearse con la exclusión de producción o documentarse como investigación vs runtime

**Pendiente 2 — Auditoría/re-scan condicional post-2026-07-12**
- Si hubo corridas incrementales de `cross-validate` después del 2026-07-12, revisar filas con `tachones.e14c = null` o `sources.e14c.status = extraction_error`
- Re-scan correctivo es decisión operativa separada, no automática tras el hotfix

**Archivos:** `debug_sv/subcell_tachon.py`, `src/modules/analyzer/cross_validator_cli.py`, `docs/metodologia.md` §5

---

### Bugs de producción (Sentry)

**Estado:** 🔴 Pendiente

#### Bug 1 — `requests>=2.31` faltante en `requirements.txt`
- **Sentry ID:** 7545413592 — `recaptcha verify failed: No module named 'requests'`
- **Impacto:** reCAPTCHA falla silenciosamente en login/register (fail-open, no bloquea al usuario pero no verifica)
- **Fix:** agregar `requests>=2.31` a `requirements.txt`
- **Archivos:** `src/modules/labeler/server.py` L122, L722

#### Bug 2 — `full_cell_crop_id` llega undefined al frontend
- **Sentry ID:** 7543242111 — `img-full load failed: undefined`
- **Impacto:** imagen de celda completa no carga en portal labeler → Sentry error
- **Causa probable:** `full_cell_crop_id` es None en Supabase para algunos crops → `src="/image/undefined"` → 404
- **Archivos:** `src/modules/labeler/templates/label.html` L849, `src/modules/labeler/server.py` L1156
- Prioridad: **Alta**

---

### Renombrar repo público y configurar submodule

**Estado:** 🔴 Pendiente

1. Renombrar `NucleuxCorp/Analizador-Electoral` → `NucleuxCorp/Open-Election-Analyzer` en GitHub
2. En repo privado: `git mv Herramientas/ "Open Election Analyzer/Herramientas/"`
3. Remover `Open Election Analyzer/Herramientas/` del tracking del repo privado
4. Agregar el repo público como submodule en ese path
5. Actualizar remote del submodule al nuevo nombre del repo

**Restricción:** hacer el rename ANTES de configurar el submodule para no tener que actualizar la URL después.

---

### Verificador local de PDFs E-14C para colaboradores (`verificador-local-pdfs`)

**Estado:** ✅ Implementado — pendiente correr `build_hash_index.py` y armar ZIP de distribución

- `build_hash_index.py` — corre una vez en máquina mantenedor, genera `hash_index_e14c.json`
- `verificador_e14c.py` — verifier distribuido, sin internet, stdlib + tqdm
- `ejecutar.bat` — lanzador Windows
- `PDFs-V2/` — carpeta donde colaborador deposita sus PDFs
- `VERIFICADOR_README.md` → renombrar a `README.md` al armar el ZIP
- SDD archivado: `openspec/changes/verificador-local-pdfs/`
- **Próximo paso:** correr `python build_hash_index.py --checkpoint E:\...\E14C\.verify_checkpoint.json` para generar el índice

### Upload Antioquia a Supabase + insert en tabla crops

**Estado:** 🔴 En progreso (subida parcial ~12%)

- 392,088 crops generados en `E:\e14c_segunda\crops\antioquia\` (8.64 GB, 11,981 PDFs)
- ~345,000 por subir a Supabase Storage (~4h con 20 workers)
- Pendiente: insertar en tabla crops con `storage_url`, `field_name`, `digit_index`, `full_cell_crop_id`, `vuelta='segunda'`
- Script: `debug_sv/antioquia_crops.py`
- Prioridad: **Alta**

### Subir resultados cross-validación 34 departamentos a Supabase

**Estado:** ✅ Completado — 2026-07-04

- 112,691 mesas subidas a `mesa_results` (upsert exitoso)
- 3,000 filas de consulados (dept 88) fallaron por timeout — se dejaron así porque aún no tienen E14C publicado en la Registraduría
- Contador público y admin panel actualizados

### 147 archivos E14C con ñ/tildes sin prefijo

**Estado:** 🔴 Pendiente

- `normalize_e14c_names.py` no pudo renombrar 147 archivos cuyos nombres contienen ñ o tildes
- Causa: encoding issue en Python `os.rename` sobre paths con caracteres no-ASCII en Windows
- Fix posible: usar `pathlib.Path.rename()` con `encoding='utf-8'` explícito o PowerShell
- Prioridad: **Media**

### Pipeline de re-verificación para `needs_review_large_delta`

**Estado:** 🔴 Pendiente

- Mesas en `needs_review_large_delta` tienen delta aritmético grande causado principalmente por OCR misread
- Propuesta: recortar la celda conflictiva del PDF → pasarla por EasyOCR como segundo criterio → solo si ambos OCR confirman delta grande → escalar a revisión humana
- Reduce el volumen de revisión humana de ~66% a un % mucho menor
- Prioridad: **Baja**

### Procesar departamentos restantes

**Estado:** ✅ Cross-validación completa — 34/34 departamentos (115,741 mesas)

- Pendiente: crops y upload a Supabase Storage para departamentos fuera de Antioquia
- Pipeline: tinta (DENS+ZONA+DOBLE) → aritmética v2 con top-2 → crops → upload
- Usar `scan_logger` en todos los scripts
- Prioridad: **Media** (bloqueado por upload de Antioquia primero)

### Mejorar detección de v_lines/h_lines para PDFs con bajo contraste

**Estado:** 🔴 Pendiente

- ~1% de PDFs tienen 1-2 v_lines en vez de 4 → grid vacío (0 filas)
- PDFs conocidos: 60_001, 15_169, 31_007, ~32 de batches 1+2
- Causa: líneas grises del formulario demasiado tenues para el umbral actual (`gray_frac > 0.40`)
- Posible fix: bajar umbral a 0.30 o usar proyección de píxeles en vez de gray_frac
- Prioridad: **Media**

### Análisis transversal por dígito — tachon + densidad + ruido

**Estado:** 🔴 Pendiente

- grid_clean.py actualmente solo extrae dígitos y confianza
- Falta integrar `subcell_tachon.enrich_subcell_tachon()` por cada sub-celda detectada
- Cada dígito debe incluir:
  - `tipo_e14` (E14C / E14D / E14T)
  - `field` (VOTANTES, URNA, INCINER, C1_CEPEDA, C2_ABELARDO, BLANCO, NULOS, NO_MARCADOS, SUMA_TOTAL)
  - `subcell_idx` (0, 1, 2)
  - `tachon`: score, tachon_score, density_score, noise_score, flags, is_suspicious
- Referencia: `debug_sv/subcell_tachon.py` + `src/modules/analyzer/tachon_detector.py`
- Prioridad: **Alta**

### Limpieza y organización raíz — Opción 3 híbrida

**Estado:** ✅ SDD `cleanup-redundant-scripts` completado (2026-07-12) — 5 fases mergeadas a `develop`

**Lo que quedó hecho (fases 2-5):**
- `scripts/archive/` — scripts one-time/obsoletos (git-tracked)
- `scripts/deploy/` — SQL schemas Railway-safe
- `scripts/upload/` — movimiento de datos local↔Supabase (NUNCA a Railway)
- Scripts activos movidos a subcarpetas correctas
- `debug/` (20 PNGs, sin .py) → `scripts/archive/debug-samples/`
- Raíz del repo limpiada de scripts sueltos

**Pendiente (fuera del SDD cerrado):**
- `scripts/e14/e14d/` está vacía — poblar con scripts de descarga E14D
- Subcarpetas `grid/`, `analyze/`, `compare/`, `download/`, `organize/` — pendientes de poblar

**Restricciones:**
- `src/` y `tests/` no se tocan
- Todo borrado va a papelera, nunca `p.unlink()` ni `Remove-Item`
- Prioridad: **Baja** (SDD cerrado; lo que queda es menor)

### Consolidación segura de `grid_detector` (todas las versiones → v3 canónico)

**Estado:** ✅ Completado — SDD `grid-detector-consolidation` archivado (PR #15, 2026-07-12)

- `src/modules/analyzer/grid_detector.py` — v3 canónico (1268L), importlib path hacks eliminados
- `src/modules/analyzer/grid_detector_e14d.py` — shim E-14D (51L, corner→frame→fallback)
- `debug_sv/_archive/` — v1/v2/v3.bak archivados (gitignored)
- CRIT-001 resuelto: `candidate_subcells.py`, `e14_worker.py`, `e14t_extractor.py` actualizados
- `tests/test_grid_detector.py` reescrito (20 tests v3 gray-line API) — 493 tests passing

---

### Migrar pipelines de debug_sv/ a producción

**Estado:** 🔴 Pendiente

- `debug_sv/*.py` → `src/modules/analyzer/` o `src/calibration/`
- Scripts a migrar: extract_marks_normalized, grid_detector_v2, tachon_method_scan, antioquia_scan, batch_extract_v2, scan_logger
- Prioridad: **Baja** (urgencia baja mientras no haya cambios mayores en los scripts)

### Top-2 predicciones en portal

**Estado:** 🔴 Pendiente

- Ya se guardan en el output de aritmética v2 (pred + alt + alt_conf)
- Falta mostrarlas al etiquetador en el frontend cuando la confianza es baja
- Prioridad: **Media**

---

## Pendientes anteriores (del BACKLOG original)

### Calibración de layout y posicionamiento de celdas

**Estado:** 🟡 En progreso

- Avances: false left border fix en `detect_local_subcell_gray_lines()`, máscara 4px, equalización de ancho de subceldas
- Pendiente: mover fuera de debug_sv/, usar registration marks para remapear Y
- Prioridad: **Media**

### Metodología de tachones a producción

**Estado:** 🟡 En progreso

- Avances: Logger implementado, thresholds calibrados, pipeline de 3 métodos probado en 6,782 + 12,076 PDFs
- Pendiente: mover a `src/modules/analyzer/tachon_scanner.py`, agregar tests
- Prioridad: **Media**

---

## Completados

| Tarea | Fecha |
|---|---|
| SDD `grid-detector-consolidation` — v3 canónico en `src/`, 493 tests passing, PR #15 | 2026-07-12 |
| SDD `cleanup-redundant-scripts` — 5 fases, scripts reorganizados en subcarpetas, raíz limpia | 2026-07-12 |
| Upload post-repair `mesa_results` — 122,019 mesas, 0 errores (estado canónico) | 2026-07-12 |
| Upload 112,691 mesas a Supabase `mesa_results` (34/34 depts, consulados parcial) | 2026-07-04 |
| Cross-validación completa — 34/34 departamentos (115,741 mesas) | 2026-07-04 |
| Rename `overall_status='critical'` → `'needs_review_large_delta'` (código + DB migration) | 2026-07-04 |
| Animación contador "Mesas analizadas" en home (40.787 → actual, localStorage) | 2026-07-04 |
| SDD `home-public-stats` — contadores públicos en home (MESAS_ALL_THREE, mesas_analyzed, anomalías) | 2026-07-04 |
| SDD `mesa-results-upload` — tabla `mesa_results`, uploader, admin panel | 2026-07-04 |
| E14C dedup audit — 22.754 pares idénticos a papelera, 35.178 archivos renombrados con prefijo | 2026-07-04 |
| `verify_e14c.py` — `--purge` eliminado, `p.unlink()` removido | 2026-06-24 |
| `clean_e14t.py` — `p.unlink()` cambiado a papelera de reciclaje | 2026-06-24 |
| Tests y backups obsoletos en debug_sv/ → papelera | 2026-06-24 |
| Diagnóstico de desobediencia documentado | 2026-06-24 |
| Escape bypass `_busy` deployado | 2026-06-24 |
| Concordancias corregidas (hermanos del mismo E14) | 2026-06-24 |
| SDD change `portal-concordance-fix` archivado | 2026-06-24 |
| Scan logger implementado y probado | 2026-06-24 |
| Antioquia tinta completa (DENS+ZONA+DOBLE) | 2026-06-24 |
| Antioquia aritmética v2 con top-2 + threshold 0.75 | 2026-06-24 |
| Batch 1+2 nacional (500 + 1,000) con crops en Supabase | 2026-06-23 |
| Grid fix (falso borde izquierdo) | 2026-06-23 |
| Máscara 4px para tachon scan | 2026-06-23 |
| Portal switch segunda vuelta + PRs A-E | 2026-06-22 |
