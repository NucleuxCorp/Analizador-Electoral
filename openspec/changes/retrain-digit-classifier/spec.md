# Spec: Dual Fine-Tuning Pipeline for Digit Classifier

> Capacidad nueva: `digit-retraining-pipeline` (no existe spec previa en `openspec/specs/`).
> Modo: spec NUEVA (no delta). Artefacto SDD — español neutro solicitado por el usuario.

## Propósito

Establecer un pipeline reproducible de cuatro fases (fetch → split → dual-train → compare) que reentrena el clasificador de dígitos MobileNetV2 sobre crops etiquetados humanos, usando los crops con `status='confirmed'` (~3,124, doble/triple validación humana) como **test set fijo**, y entrenando dos variantes de fine-tuning (conservadora y agresiva) sobre el pool restante de crops etiquetados (~13,000), evaluando ambos contra el test set confirmado **sin reemplazar ni modificar el modelo en producción** (`models/digit_classifier.pth`).

La fuente de verdad del estado `confirmed` es `src/modules/labeler/db.py:evaluate_agreement()` (acuerdo 2/2, mayoría 2/3, o resolución admin).

## Requerimientos funcionales

### Fase 0 — Fetch (`fetch_confirmed_crops.py`)

#### FR-0.1 — Conexión Supabase service_role
El sistema MUST conectarse a Supabase usando `SUPABASE_SERVICE_ROLE_KEY`, con fallback a `SUPABASE_ANON_KEY` si aquella no está definida, replicando la lógica de `src/modules/labeler/db.py`. Si ninguna clave está presente, SHALL abortar con error explícito.

- GIVEN que `SUPABASE_SERVICE_ROLE_KEY` no está definida pero `SUPABASE_ANON_KEY` sí
- WHEN se ejecuta `fetch_confirmed_crops.py`
- THEN el sistema usa la anon key y continúa
- AND loguea un warning indicando el fallback

#### FR-0.2 — Query confirmed (test set)
El sistema SHALL consultar los crops confirmed: `SELECT crop_id, confirmed_label, field_name, pdf_path FROM crops WHERE status='confirmed' AND confirmed_label IS NOT NULL`.

- GIVEN que Supabase tiene 3,124 crops con `status='confirmed'` y `confirmed_label` no nulo
- WHEN se ejecuta el fetch
- THEN se recuperan exactamente 3,124 filas
- AND cada fila incluye `crop_id`, `confirmed_label`, `field_name`, `pdf_path`

#### FR-0.3 — Query train pool (con exclusión de needs_third/disputed)
El sistema SHALL consultar los crops etiquetados restantes excluyendo: `status='confirmed'` (test set), `status='needs_third'`, `status='disputed'`, y `label_human='_skip'`. La consulta base es `SELECT crop_id, label_human FROM labels WHERE label_human NOT IN ('_skip') AND crop_id NOT IN (confirmed crop_ids)`, extendida con el filtro de estado para excluir needs_third y disputed (sin este filtro habría fuga de crops ambiguos al pool de entrenamiento).

- GIVEN que existen crops en estado `needs_third` y `disputed` con labels no-skip
- WHEN se construye el train pool
- THEN ningún crop con `status IN ('needs_third','disputed')` aparece en `train_pool_manifest.jsonl`

#### FR-0.4 — Descarga local-first
Para cada crop, el sistema MUST intentar primero la copia local desde `data/labels/digits/{label}/img_{crop_id}.png`; si no existe, SHALL descargar desde Supabase Storage (`{SUPABASE_URL}/storage/v1/object/public/crops/{crop_id}.png`, patrón de `db.get_storage_url`).

- GIVEN que `data/labels/digits/3/img_42.png` existe localmente
- WHEN se procesa el crop_id=42
- THEN el sistema copia el archivo local y NO realiza petición a Storage

#### FR-0.5 — Layout de salida confirmed
El sistema MUST guardar los crops confirmed en `data/labels/confirmed/{label}/img_{crop_id}.png` (layout ImageFolder) y escribir `data/labels/confirmed_manifest.jsonl` con una línea JSON por crop: `{crop_id, confirmed_label, field_name, pdf_path, local_path}`.

- GIVEN que el crop `c_99` tiene `confirmed_label='7'`
- WHEN se materializa el confirmed set
- THEN el archivo se guarda en `data/labels/confirmed/7/img_c_99.png`
- AND el manifest contiene `{"crop_id":"c_99","confirmed_label":"7","field_name":"...","pdf_path":"...","local_path":"data/labels/confirmed/7/img_c_99.png"}`

#### FR-0.6 — Layout de salida train pool
El sistema MUST guardar los crops del train pool en `data/labels/train_pool/{label}/img_{crop_id}.png` y escribir `data/labels/train_pool_manifest.jsonl` con `{crop_id, label_human, local_path}`.

#### FR-0.7 — Logging de distribución y no-encontrados
El sistema SHALL loguear: totales por manifest, distribución por clase (0-9 + variantes), y la lista de crops no encontrados ni local ni en Storage.

- GIVEN que 5 crops no existen local ni en Storage
- WHEN termina el fetch
- THEN el log lista los 5 crop_ids faltantes
- AND el total confirmed descargado = 3,124 − 5

#### FR-0.8 — Retry con backoff exponencial
El sistema MUST reintentar descargas fallidas con backoff exponencial (mínimo 3 reintentos) antes de marcar un crop como no encontrado.

- GIVEN que una descarga de Storage falla con timeout
- WHEN se aplica el retry
- THEN el sistema reintenta hasta 3 veces con backoff creciente
- AND solo registra el crop como no encontrado tras agotar los reintentos

#### FR-0.9 — Resumible
El sistema MUST skip la descarga/copiar cuando el archivo destino ya existe (resumible por `crop_id`).

- GIVEN que `data/labels/confirmed/3/img_c_42.png` ya existe
- WHEN se reejecuta el fetch
- THEN el sistema skip ese crop y no lo descarga de nuevo

### Fase 1 — Split

#### FR-1.1 — Test set = todos los confirmed
El sistema MUST cargar `confirmed_manifest.jsonl` y usar TODOS sus crops como test set. Ningún crop confirmed debe aparecer en train/val.

- GIVEN `confirmed_manifest.jsonl` con 3,124 crops
- WHEN se construyen los conjuntos
- THEN el test set tiene exactamente 3,124 crops
- AND la intersección test ∩ (train ∪ val) es vacía

#### FR-1.2 — Dedupe por crop_id
El sistema MUST deduplicar `train_pool_manifest.jsonl` por `crop_id` (el manifest puede tener múltiples labels por crop: ~4,642 líneas vs ~4,593 únicos). Se conserva el label más reciente por crop_id (orden por timestamp de la fila labels).

- GIVEN que el crop_id `c_7` aparece 2 veces en el manifest con labels `5` y `3` (este último más reciente)
- WHEN se deduplica
- THEN queda una sola entrada para `c_7` con `label_human='3'`

#### FR-1.3 — Filtrado y normalización de labels
El sistema MUST filtrar a clases 0-9: excluir `_skip` y normalizar variantes de cero (`*`, `-`, `.`, `+`, `o`, `O`) a `0`, replicando `ZERO_VARIANTS` de `src/modules/labeler/db.py`.

- GIVEN un crop del pool con `label_human='*'`
- WHEN se aplica el filtrado
- THEN el label se normaliza a `0` y el crop se conserva en clase 0
- AND un crop con `label_human='_skip'` se excluye del pool

#### FR-1.4 — Stratified 90/10 split
El sistema SHALL realizar un split estratificado 90/10 (train/val) sobre el train pool con `seed=42` determinista, preservando la distribución por clase en ambos subconjuntos.

- GIVEN el train pool dedupado con distribución por clase conocida
- WHEN se ejecuta el split con seed=42 dos veces
- THEN el partition es idéntico en ambas ejecuciones
- AND la distribución por clase en val es ~10% de cada clase del pool

#### FR-1.5 — Abortar si clase < 50
El sistema MUST abortar con error si cualquier clase (0-9) tiene menos de 50 ejemplos en el train pool (post-filtrado, pre-split).

- GIVEN que la clase 9 tiene 47 ejemplos tras el filtrado
- WHEN se ejecuta el split
- THEN el sistema aborta con mensaje `Class 9 has 47 samples (< 50 threshold)`
- AND no escribe `train_val_split.json`

#### FR-1.6 — Cache de split
El sistema MUST escribir `data/labels/test_crop_ids.json` (lista de crop_ids del test set) y `data/labels/train_val_split.json` (mapeo `crop_id → "train"|"val"`) para que ambos modelos usen el mismo split.

- GIVEN que el split ya está cacheado en disco
- WHEN se reejecuta la fase de split
- THEN el sistema carga el cache en lugar de recalcular
- AND los crop_ids del test set coinciden con los del cache

### Fase 2 — Dual Train (`train_dual_finetune.py`)

#### FR-2.1 — Carga de pesos existentes
El sistema MUST cargar `models/digit_classifier.pth` en `mobilenet_v2` con `classifier[1] = nn.Linear(1280, 10)` como pesos iniciales para ambos modelos (continúa desde el modelo 98.7%, no desde ImageNet).

- GIVEN `models/digit_classifier.pth` con state_dict de MobileNetV2 + Linear(1280,10)
- WHEN se instancian los Modelos A y B
- THEN ambos cargan los pesos existentes vía `load_state_dict`
- AND no descargan pesos IMAGENET1K_V1

#### FR-2.2 — Reutilizar transforms sin modificar
El sistema MUST reutilizar `get_transforms(train=True/False)` de `train_digit_classifier.py` SIN modificación (Resize 64x64, Grayscale 3ch, ToTensor, Normalize 0.5; el transform de train conserva su augmentación existente: RandomRotation, RandomAffine, ColorJitter, GaussianBlur).

- GIVEN el transform de entrenamiento existente con augmentación
- WHEN se construyen los DataLoaders
- THEN se usa `get_transforms(train=True)` para train y `get_transforms(train=False)` para val
- AND el archivo `train_digit_classifier.py` no es modificado

#### FR-2.3 — WeightedRandomSampler
El sistema MUST reutilizar el patrón `WeightedRandomSampler` existente (pesos `1/class_count` por muestra) para mitigar el desbalance de clases en el train loader de ambos modelos.

#### FR-2.4 — Modelo A (conservador)
El sistema SHALL entrenar el Modelo A congelando `model.features` (sin grad), entrenando solo el classifier head, optimizador Adam `lr=1e-4`, early stopping con `patience=5` monitoreando val F1 macro.

- GIVEN el Modelo A con `features` congelado
- WHEN se ejecuta el backward
- THEN solo `classifier[1].weight` y `.bias` reciben gradientes
- AND los `features.*` params tienen `requires_grad=False`

#### FR-2.5 — Modelo B (agresivo)
El sistema SHALL entrenar el Modelo B descongelando todo el modelo, con learning rate discriminativo por tres grupos de parámetros: features early `1e-5`, features mid `1e-4`, classifier `1e-3`; `weight_decay=1e-4`; early stopping con `patience=3` monitoreando val F1 macro.

- GIVEN el Modelo B con todos los parámetros descongelados
- WHEN se construye el optimizador
- THEN se registran 3 grupos de parámetros con LR distintos (1e-5, 1e-4, 1e-3)
- AND `weight_decay=1e-4` aplica a todos los grupos

#### FR-2.6 — Métricas por época
El sistema MUST registrar por época, para cada modelo: accuracy (train/val), F1 macro (val), loss (train/val promedio), tiempo de época en segundos.

#### FR-2.7 — Guardar pesos e historial
El sistema MUST guardar `models/digit_classifier_model_a.pth` y `models/digit_classifier_model_b.pth` (best weights según val F1 macro) y `models/dual_train_history.json` con el historial completo de ambos modelos (métricas por época + epochs-to-convergence).

- GIVEN que el Modelo A alcanza su mejor val F1 macro en época 8
- WHEN termina el entrenamiento
- THEN `digit_classifier_model_a.pth` contiene los pesos de la época 8
- AND `dual_train_history.json` registra `epochs_to_convergence: 8` para el Modelo A

#### FR-2.8 — CPU only
El sistema MUST ejecutar en CPU (`torch.device("cpu")`); no debe requerir ni fallar por ausencia de CUDA.

- GIVEN un entorno sin GPU
- WHEN se ejecuta `train_dual_finetune.py`
- THEN el entrenamiento corre en CPU hasta convergencia

### Fase 3 — Compare (`compare_models.py`)

#### FR-3.1 — Cargar ambos modelos
El sistema SHALL cargar `digit_classifier_model_a.pth` y `digit_classifier_model_b.pth` en la misma arquitectura MobileNetV2 + Linear(1280,10).

#### FR-3.2 — Evaluar contra test set confirmed
El sistema MUST evaluar ambos modelos contra `data/labels/confirmed/` (test set fijo), usando `test_crop_ids.json` para garantizar el mismo conjunto de evaluación.

- GIVEN el test set confirmed cacheado con 3,124 crops
- WHEN se evalúan ambos modelos
- THEN ambos se evalúan sobre exactamente los mismos 3,124 crops
- AND ningún crop confirmed fuera del cache se incluye

#### FR-3.3 — Métricas de comparación
El sistema MUST calcular por modelo: accuracy global, F1 por clase (0-9), F1 macro, matriz de confusión 10x10, y epochs-to-convergence (extraído de `dual_train_history.json`).

#### FR-3.4 — Reporte
El sistema MUST generar `models/dual_finetune_report.json` (estructurado, machine-readable) y `models/dual_finetune_report.txt` (human-readable) con todas las métricas por modelo y la comparación lado a lado.

#### FR-3.5 — Recomendación de promoción
El sistema SHOULD recomendar cuál modelo promover basado en una combinación de accuracy global + F1 macro, incluyendo la justificación numérica en el reporte.

- GIVEN Modelo A con accuracy 98.9% / F1 macro 0.985 y Modelo B con 99.1% / F1 macro 0.982
- WHEN se genera la recomendación
- THEN el reporte indica cuál modelo tiene mejor combined score
- AND incluye la justificación con ambas métricas

#### FR-3.6 — No reemplazar production
El sistema MUST NOT modificar, sobrescribir ni reemplazar `models/digit_classifier.pth`. La promoción es una decisión manual fuera del scope de este cambio.

- GIVEN que el pipeline completa las 4 fases con éxito
- WHEN termina `compare_models.py`
- THEN `models/digit_classifier.pth` es byte-idéntico a su estado previo
- AND solo existen los nuevos archivos `*_model_a.pth`, `*_model_b.pth`, `dual_finetune_report.*`, `dual_train_history.json`

## Requerimientos no funcionales

- **NFR-1 Reproducibilidad**: `seed=42` en el split estratificado; cache de `test_crop_ids.json` y `train_val_split.json` en disco garantiza que ambos modelos ven el mismo test y val.
- **NFR-2 Aislamiento de test**: ningún crop confirmed debe aparecer en train/val; validable vía intersección de `test_crop_ids.json` con `train_val_split.json`.
- **NFR-3 Solo CPU**: `torch 2.12.0+cpu` / `torchvision 0.27.0+cpu` (instalados); sin dependencia de CUDA.
- **NFR-4 Reutilización sin modificación**: transforms (`get_transforms`) y `WeightedRandomSampler` reutilizados sin editar `src/modules/analyzer/ocr_engines.py` ni `train_digit_classifier.py`.
- **NFR-5 Additive / rollback trivial**: todos los outputs son archivos nuevos; rollback = eliminar `fetch_confirmed_crops.py`, `train_dual_finetune.py`, `compare_models.py`, `data/labels/confirmed/`, `data/labels/confirmed_manifest.jsonl`, `data/labels/train_pool/`, `data/labels/train_pool_manifest.jsonl`, `data/labels/test_crop_ids.json`, `data/labels/train_val_split.json`, `models/digit_classifier_model_{a,b}.pth`, `models/dual_finetune_report.{json,txt}`, `models/dual_train_history.json`. Sin cambios de schema DB; acceso Supabase read-only.
- **NFR-6 Resumible / idempotente**: fetch y split son idempotentes; reejecución segura gracias al cache y al skip por archivo existente.
- **NFR-7 Testabilidad (strict_tdd)**: pytest cubre dedup del manifest, determinismo del split (misma seed → mismo partition), filtrado/normalización de labels, aislamiento test/train (sin fuga), y abort por clase < 50.
- **NFR-8 Observabilidad**: log de totales, distribución por clase, crops no encontrados, tiempos por época y resumen de convergencia.

## Escenarios / ejemplos de uso

### Escenario E2E-1 — Pipeline completo happy path
- GIVEN Supabase con 3,124 crops confirmed y ~13,000 crops en el train pool, todas las clases ≥ 50
- WHEN se ejecutan secuencialmente `fetch_confirmed_crops.py` → split → `train_dual_finetune.py` → `compare_models.py`
- THEN se generan `data/labels/confirmed/` + manifest, `data/labels/train_pool/` + manifest, `test_crop_ids.json`, `train_val_split.json`, `digit_classifier_model_{a,b}.pth`, `dual_train_history.json`, `dual_finetune_report.{json,txt}`
- AND `models/digit_classifier.pth` permanece sin cambios
- AND el reporte recomienda un modelo con accuracy + F1 macro justificados

### Escenario E2E-2 — Reejecución resumible tras interrupción
- GIVEN que el fetch se interrumpió tras descargar 1,500 de 3,124 confirmed
- WHEN se reejecuta `fetch_confirmed_crops.py`
- THEN se skip los 1,500 ya descargados y se descargan solo los 1,624 restantes
- AND `confirmed_manifest.jsonl` termina con 3,124 líneas

### Escenario E2E-3 — Abort por clase minoritaria
- GIVEN que tras el filtrado del train pool la clase 9 tiene 30 ejemplos
- WHEN se ejecuta el split
- THEN el sistema aborta antes de escribir `train_val_split.json`
- AND el mensaje de error nombra la clase y el conteo

### Escenario E2E-4 — Fuga de test set detectada
- GIVEN un bug donde un crop confirmed aparece en el train pool
- WHEN se valida el aislamiento
- THEN la intersección `test_crop_ids ∩ train_val_crop_ids` es no vacía
- AND el test de aislamiento falla (strict_tdd)

### Escenario E2E-5 — Modelo B no mejora sobre A
- GIVEN que el Modelo B sobreajusta y su val F1 macro cae bajo el del Modelo A
- WHEN se genera el reporte
- THEN `dual_finetune_report` recomienda el Modelo A
- AND `digit_classifier.pth` no se modifica

## Criterios de aceptación

- [ ] `fetch_confirmed_crops.py` descarga todos los confirmed (test set) + el train pool, y escribe ambos manifests con log de distribución por clase y crops no encontrados.
- [ ] El test set = TODOS los confirmed (~3,124), determinista e idéntico para ambos modelos (verificable vía `test_crop_ids.json` cacheado).
- [ ] El train pool excluye `needs_third`, `disputed`, `_skip` y confirmed crops (sin fuga de test set).
- [ ] El split es estratificado 90/10 con seed=42; reejecución produce el mismo partition.
- [ ] El pipeline aborta si cualquier clase tiene < 50 ejemplos en el train pool.
- [ ] El Modelo A congela `features` y entrena solo el head; el Modelo B descongela todo con LR discriminativo.
- [ ] Ambos modelos alcanzan convergencia por early stopping en CPU.
- [ ] `compare_models.py` emite `dual_finetune_report.{json,txt}` con accuracy, F1 por clase, matriz de confusión 10x10, y epochs-to-convergence sobre el test set confirmed.
- [ ] `models/digit_classifier.pth` NO se modifica ni reemplaza.
- [ ] `get_transforms()` y `WeightedRandomSampler` se reutilizan sin editar `ocr_engines.py` ni `train_digit_classifier.py`.
- [ ] pytest cubre: dedup del manifest, determinismo del split, filtrado/normalización de labels, aislamiento test/train, abort por clase < 50 (strict_tdd).

## Dependencias entre cambios

- **Depende de (debe existir antes):**
  - `models/digit_classifier.pth` — pesos iniciales del reentrenamiento (existente, 98.7% val acc).
  - `src/modules/labeler/db.py` — lógica de `evaluate_agreement`/`ZERO_VARIANTS`/`get_storage_url` que define el contrato de `confirmed` y URLs de Storage (sin modificar).
  - `train_digit_classifier.py` — fuente de `get_transforms()` y patrón `WeightedRandomSampler` (sin modificar, solo importar/reutilizar).
  - Supabase `crops` rows con `status='confirmed'` (~3,124) + `labels` rows con 1 label real (~13,000).
  - Variables de entorno `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (o `SUPABASE_ANON_KEY`).
- **Bloquea (debe esperar a este cambio):**
  - Cambio futuro de promoción manual de modelo (out of scope aquí) — depende de los outputs `digit_classifier_model_{a,b}.pth` y `dual_finetune_report.*` generados por este pipeline.
- **Sin conflicto con:** `ocr_engines.py` (inferencia intocada), portal labeler (intocato), módulos E-14/E-14C (independientes).
