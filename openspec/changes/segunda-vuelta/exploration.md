## Exploration: Segunda Vuelta — Calibración de Layout E-14 2026

### Current State

El analizador actual (`src/modules/analyzer/`) está calibrado para la **primera vuelta** presidencial del 31 de mayo de 2026:

- `form_extractor.py` espera un PDF de **1260×3897 px @ 300 DPI** con **3 páginas**, copia tipo **CLAVEROS**.
- Los campos numéricos se definen como tuplas `(page_idx, y1, y2, x1, x2)` hardcodeadas en las constantes `NIVELACION`, `CANDIDATE_ROWS_P0`, `CANDIDATE_ROWS_P1` y `TOTALS`.
- La lógica de extracción es secuencial: renderizar páginas → recortar regiones → OCR por lote o individual → poblar `FormData` → `validate()`.
- `notes_extractor.py` extrae notas de la página 1 (y > 3480) y página 2 completa; también hardcodea regiones.
- `tachon_detector.py` opera sobre recortes de celdas y no depende del layout global, por lo que es reutilizable sin cambios.
- `ocr_engines.py` expone una fábrica `get_engine(name)` con motores `segmented`, `easyocr`, `trocr`, `vlm`. `SegmentedEngine` divide cada celda en dígitos y clasifica con la CNN `models/digit_classifier.pth`.
- `main.py` invoca `form_extractor.process_directory()` para `analyze-e14c` y `notes_extractor.batch_extract_notes()` para `extract-notes`.
- `analyze_all_depts.py` itera `data/pdfs/{DEPT}` y ejecuta `main.py analyze-e14c` por departamento.

La **muestra oficial** (`docs/MUESTRA_FORMULARIO_E-14.pdf`) confirma que el formulario de segunda vuelta cambia drásticamente: **2 páginas de 3900×3900 px** que muestran las 3 copias (CLAVEROS | DELEGADOS | TRANSMISIÓN) en paralelo. Sin embargo, los PDFs reales de CLAVEROS probablemente sigan siendo de ~1260 px de ancho (una sola columna). Todos los campos (NIVELACIÓN + 2 candidatos + TOTALES + VOTO EN BLANCO) caben en **una sola página**.

### Affected Areas

- `docs/MUESTRA_FORMULARIO_E-14.pdf` — referencia visual única disponible hasta que lleguen actas reales.
- `Segunda Vuelta/` (nueva carpeta aislada) — aquí vivirá todo el código nuevo.
- `src/modules/analyzer/ocr_engines.py` — se puede importar directamente (sin modificar) porque expone `get_engine()` y la interfaz `OCREngine`.
- `src/utils/logger.py` — utilidad compartida; se puede importar sin tocar.
- `main.py` — opcionalmente agregar comandos `analyze-segunda-vuelta` y `extract-notes-sv`, pero solo como invocadores que apunten al nuevo módulo.
- `data/pdfs/` y `data/` — estructura de salida JSONL para análisis por departamento.

### Exploration Questions

#### 1. ¿Arquitectura mínima viable para un módulo aislado?

Crear `Segunda Vuelta/src/` con la misma separación de responsabilidades del analizador actual, pero sin acoplamiento a las constantes de primera vuelta:

```
Segunda Vuelta/
├── src/
│   ├── __init__.py
│   ├── layout.py          # specs de regiones para segunda vuelta
│   ├── form_data.py       # dataclass + validación aritmética
│   ├── renderer.py        # renderizado PDF → np.ndarray (reutilizable)
│   ├── extractor.py       # extract_form() + process_directory()
│   ├── notes_extractor.py # extracción de notas para 1-2 páginas
│   ├── calibrate.py       # --calibrate: debug visual de regiones
│   └── cli.py             # entrypoint propio o funciones para main.py
├── data/                  # salidas JSONL (opcional, runtime)
└── tests/                 # tests de layout y validación
```

#### 2. ¿Duplicar `form_extractor.py` o hacerlo configurable por layout?

**Opción A — Duplicar y especializar (recomendada para esta fase):**
Copiar la estructura de `form_extractor.py` en `Segunda Vuelta/src/extractor.py`, pero con las constantes de segunda vuelta. Mantiene el módulo autocontenido, reduce riesgo de romper primera vuelta y permite iterar rápido mientras llegan datos reales.

**Opción B — Extraer un motor genérico configurable:**
Refactorizar `form_extractor.py` para que acepte un objeto `LayoutSpec` con regiones, número de candidatos y páginas. Es más limpio a largo plazo pero viola la restricción de no tocar `src/modules/analyzer/` y requiere más diseño previo.

> **Decisión**: Opción A. La segunda vuelta es un evento puntual con deadline; no vale la pena generalizar el extractor existente hasta que se confirme que el layout será reutilizado en futuras elecciones.

#### 3. ¿Cómo compartir OCR y utilidades sin tocar archivos existentes?

Los motores OCR y el logger se importan directamente desde `src/`:

```python
from src.modules.analyzer.ocr_engines import get_engine
from src.utils.logger import get_logger
```

Esto respeta el aislamiento: no se modifica el código fuente existente; solo se consumen como bibliotecas. Si en el futuro los motores OCR cambian, el módulo de segunda vuelta se beneficia sin cambios.

#### 4. ¿Qué utilidades de calibración construir?

Replica `--calibrate` de `notes_extractor` y extiéndela al formulario completo:

- `calibrate_form_regions(pdf, out_dir)` — renderiza la primera página, dibuja rectángulos sobre NIVELACIÓN, candidatos, blanco y TOTALES, y guarda `debug/sv_form_layout.png`.
- `calibrate_notes_regions(pdf, out_dir)` — igual que el existente pero con regiones de segunda vuelta (página 1, sección inferior).
- `render_thumbnail(pdf, page, out_dir)` — utilidad para que subagentes de visión confirmen coordenadas.
- `run_calibration_sample(pdf)` — imprime dimensiones, número de páginas y verifica que las regiones estén dentro de los límites.

#### 5. ¿Qué se puede preparar AHORA vs. qué debe esperar datos reales?

**Preparable ahora:**
- Estructura de carpetas y módulos.
- Datamodel `FormData` adaptado a 2 candidatos + voto en blanco.
- Lógica de validación aritmética (igual que primera vuelta, pero con 2 candidatos).
- CLI stub y calibrador visual sobre `docs/MUESTRA_FORMULARIO_E-14.pdf`.
- Wrapper que importe `get_engine("segmented")` y verifique que el modelo CNN carga.

**Que debe esperar actas reales:**
- Coordenadas exactas de cada campo a 300 DPI.
- Verificación de que el ancho real sigue siendo ~1260 px (no 3900 px).
- Validación de OCR: la CNN entrenada con dígitos de primera vuelta probablemente funcione, pero hay que medir accuracy.
- Ajuste de `notes_extractor` si la sección de notas cambia de posición o si solo hay 1 página de notas.

#### 6. ¿Riesgos de coordenadas hardcodeadas vs. layout paramétrico?

Las coordenadas hardcodeadas son el enfoque actual y funcionaron para primera vuelta. El riesgo principal es que la Registraduría pueda imprimir templates ligeramente distintos por departamento o cambiar el PDF final respecto a la muestra. Un layout paramétrico (con detección de líneas de tabla o OCR de encabezados) sería más robusto pero mucho más complejo y fuera de alcance sin datos reales.

Mitigación: mantener las coordenadas en `layout.py` como constantes con nombres claros y un comando `--calibrate` que permita ajustarlas visualmente en cuestión de minutos cuando llegue el primer PDF real.

### Approaches

1. **Módulo aislado con layout hardcodeado (recomendado)**
   - Descripción: copiar la estructura del extractor actual en `Segunda Vuelta/src/`, reemplazar constantes por el layout de segunda vuelta, importar motores OCR desde `src/modules/analyzer/`.
   - Pros: Rápido de implementar, cero riesgo para primera vuelta, fácil de calibrar con `--calibrate`, entrega valor inmediato al llegar actas reales.
   - Cons: Duplica ~300 líneas de lógica de extracción; mantener dos extractores si hay una tercera vuelta o cambio futuro.
   - Esfuerzo: **Bajo–Medio**.

2. **Refactor generalizado con `LayoutSpec` en `src/modules/analyzer/`**
   - Descripción: extraer un motor genérico que reciba un spec de layout (regiones, candidatos, páginas) y use el mismo `FormData`/`validate()`.
   - Pros: Código DRY, reutilizable para cualquier elección, más fácil de mantener.
   - Cons: **Viola la restricción de no tocar `src/modules/analyzer/`**; requiere migrar y re-verificar la primera vuelta; mayor riesgo de regresión; más trabajo antes de poder analizar la segunda vuelta.
   - Esfuerzo: **Alto**.

3. **Híbrido: módulo aislado que clona solo `layout.py`**
   - Descripción: copiar `form_extractor.py` tal cual en `Segunda Vuelta/` pero leer constantes desde `layout.py`. La lógica de extracción y validación se mantiene idéntica.
   - Pros: Equilibrio entre aislamiento y claridad; separar coordenadas facilita los ajustes de calibración.
   - Cons: Sigue duplicando el motor de extracción.
   - Esfuerzo: **Bajo**.

### Recommendation

Ejecutar la **Opción 1** (módulo aislado con layout hardcodeado), combinada con la separación de coordenadas en `layout.py` al estilo Opción 3. Esto significa:

1. Crear `Segunda Vuelta/src/extractor.py` como clon adaptado de `form_extractor.py`.
2. Crear `Segunda Vuelta/src/layout.py` con las constantes de segunda vuelta.
3. Importar `get_engine("segmented")` y `get_logger` desde `src/`.
4. Agregar `calibrate.py` con utilidades de debug visual.
5. No modificar `main.py` en esta fase; usar un CLI propio `Segunda Vuelta/run.py` o un script `analyze_segunda_vuelta.py` en la raíz del proyecto.

Esta estrategia respeta la restricción de aislamiento, minimiza el riesgo de regresión y permite calibrar el layout tan pronto como llegue el primer acta real.

### Risks

- **El PDF real no coincide con la muestra**: la muestra es 3900×3900 px con 3 copias; el PDF real de CLAVEROS puede tener dimensiones diferentes o ser de una sola columna. Mitigación: confirmar con el primer PDF real antes de fijar coordenadas finales.
- **La CNN de primera vuelta no generaliza a la segunda vuelta**: la escritura de los jurados no debería cambiar, pero el tamaño de celda sí. Mitigación: medir accuracy en las primeras 50 actas y, si es necesario, reentrenar/re-etiquetar dígitos.
- **Hardcodear coordenadas por departamento**: si la Registraduría imprime templates distintos por región, las coordenadas únicas fallarán. Mitigación: el comando `--calibrate` y regiones con márgenes generosos.
- **Sobrecarga de mantener dos extractores**: cualquier mejora en `src/modules/analyzer/form_extractor.py` no se refleja automáticamente en segunda vuelta. Mitigación: documentar que el módulo es temporal y evaluar refactorización post-elección.
- **Sin datos reales no se puede verificar OCR**: solo se puede preparar el layout; la precisión real se desconoce hasta el 21 de junio o posterior.

### Unknowns

- Ancho final real del PDF CLAVEROS de segunda vuelta (¿1260 px otra vez?).
- Posición exacta de cada campo a 300 DPI.
- Si el voto en blanco aparece como fila de candidato adicional o como campo separado en TOTALES.
- Si la sección de notas se mantiene en página 1 inferior + página 2 completa, o cambia a una sola página.
- Si `digitalizado=1` y `escrutado=true` conservan el mismo significado en la API E-14C para segunda vuelta.

### Ready for Proposal

**Sí.** La siguiente fase recomendada es `sdd-propose` para definir el alcance, las rutas de archivos, los comandos CLI y el plan de calibración visual una vez lleguen los PDFs reales.
