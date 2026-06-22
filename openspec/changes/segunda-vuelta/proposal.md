# Proposal: Segunda Vuelta — Extracción de Actas E-14

## Intent

El sistema actual solo procesa actas de primera vuelta (31 may 2026). La segunda vuelta presidencial (21 jun 2026) usa un formulario diferente: 2 candidatos + voto en blanco, layout potencialmente distinto, y una plataforma web nueva (`e14segundavueltapresidentet.registraduria.gov.co`). Se necesita un módulo de extracción aislado que pueda analizar actas de segunda vuelta tan pronto como la Registraduría publique los PDFs.

## Scope

### In Scope
- Módulo aislado en `Segunda Vuelta/src/` con extractor, layout, validación y calibración
- FormData adaptado a 2 candidatos + voto en blanco en TOTALES
- CLI propio via `Segunda Vuelta/run.py` (sin modificar `main.py`)
- Importación directa de `ocr_engines.py` y `logger.py` desde `src/` (sin modificar)
- Utilidades de calibración visual (`--calibrate`) para ajustar coordenadas con el primer PDF real
- Soporte para notas de jurado (1-2 páginas)

### Out of Scope
- Integración con `main.py` (comandos `analyze-segunda-vuelta`) — diferido post-calibración
- Refactor del extractor existente (`src/modules/analyzer/form_extractor.py`)
- Reentrenamiento del clasificador CNN (se evalúa tras medir accuracy con datos reales)
- Colector de URLs o descarga de PDFs (la plataforma E-14C de segunda vuelta puede tener API diferente)
- Validación de schema de salida con datos reales (pendiente hasta que lleguen PDFs)

## Capabilities

### New Capabilities
- `sv-form-extraction`: Extracción de campos numéricos de actas E-14 segunda vuelta (2 candidatos + blanco + nulos + no marcados)
- `sv-notes-extraction`: Extracción de notas manuscritas de jurados para formato segunda vuelta
- `sv-calibration`: Utilidades de debug visual para calibrar coordenadas de regiones del formulario

### Modified Capabilities
- None (no existen specs previos en `openspec/specs/`)

## Approach

Módulo aislado que clona la estructura del extractor de primera vuelta con constantes propias:
1. `layout.py` — coordenadas de regiones a 300 DPI (hardcodeadas, ajustables via `--calibrate`)
2. `extractor.py` — clon adaptado de `form_extractor.py` con 2 candidatos
3. `form_data.py` — dataclass con validación aritmética (misma fórmula: `suma = cands + blanco + nulos + no_marcados`)
4. Importar motores OCR existentes sin modificar código fuente

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `Segunda Vuelta/src/` | New | Módulo completo de extracción |
| `src/modules/analyzer/ocr_engines.py` | None | Se importa sin modificar |
| `src/utils/logger.py` | None | Se importa sin modificar |
| `main.py` | None | No se modifica en esta fase |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| PDF real no coincide con muestra (3900×3900) | Media | `--calibrate` permite ajustar coordenadas en minutos |
| CNN de primera vuelta no generaliza | Baja | Medir accuracy en primeras 50 actas; reentrenar si necesario |
| Templates distintos por departamento | Baja | Coordenadas con márgenes generosos + calibración regional |
| Platforma E-14C segunda vuelta tiene API diferente | Alta | Módulo independiente; colector de URLs se diseña cuando API esté disponible |

## Rollback Plan

Eliminar `Segunda Vuelta/` completa. No hay dependencias del código existente con este módulo — solo importa desde `src/`, nunca escribe hacia él.

## Dependencies

- PDFs reales de segunda vuelta (publicación esperada noche del 21 jun o mañana 22 jun)
- Plataforma web `e14segundavueltapresidentet.registraduria.gov.co` operativa

## Success Criteria

- [ ] `Segunda Vuelta/run.py` procesa un PDF real de segunda vuelta y produce JSONL válido
- [ ] Validación aritmética pasa para actas con 2 candidatos
- [ ] `--calibrate` genera imagen debug con regiones dibujadas correctamente
- [ ] Cero modificaciones al código fuente de primera vuelta
