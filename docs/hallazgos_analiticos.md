# Hallazgos analíticos — Analizador de Elecciones

Log acumulativo de descubrimientos cuantitativos sobre el dataset E-14 segunda vuelta.
Cada entrada es un hallazgo emergido en sesión de análisis, capturado en el momento.

> Este archivo es complementario a `docs/metodologia.md`. La metodología documenta
> el proceso; este log documenta lo que el proceso encuentra.
>
> **Sincronización (2026-07-14):** El estado canónico de distribución de mesas
> ([6]) se refleja en `docs/metodologia.md` §4.4 y §8. Las tablas completas viven
> aquí; la metodología enlaza [1]–[6] sin duplicarlas.

---

### [1] Distribución de estados cross-validación — 2026-07-05

**Qué:** Primer mapa automatizado de estados sobre 115,691 mesas (34/34 departamentos), sin intervención humana.

| Estado | Mesas | % |
|---|---|---|
| `needs_review_large_delta` | 75,612 | 65.4% |
| `discrepancy` | 25,979 | 22.5% |
| `clean` | 10,579 | 9.1% |
| `known_anomaly` | 2,414 | 2.1% |
| `warning` | 1,107 | 1.0% |
| **Total procesado** | **115,691** | — |
| Universo CNE | 122,020 | — |

**Contexto:** `scripts/export_clean_mesas.py` + `compute_overall_status()` sobre `data/cross_mesa_validation_*.jsonl`. Modelo CNN MobileNetV2, 98.7% precisión en validación. Mesas limpias exportadas a `data/clean_mesas.csv`.

**Interpretación:** El 65.4% en `needs_review_large_delta` no equivale a tasa de fraude. El umbral `FRAUD_MAX_DIFF=30` captura cualquier delta > 30 votos, y un error OCR en un dígito de 3 cifras genera deltas artificialmente grandes (ej. `003` leído como `703` = delta 700). Este mapa es triage priorizado, no dictamen.

**Preguntas abiertas:** Pipeline de re-OCR con EasyOCR sobre celda conflictiva para reducir el volumen de `needs_review_large_delta`. Análisis geográfico de la tasa por departamento para detectar concentraciones anómalas.

> **Supersedido por [6]** (2026-07-11) — dataset parcial pre-repair. La metodología §4.4 ya no replica esta tabla; ver [6] como canónico.

---

### [2] Resultados en mesas limpias (E14C, sin revisión humana) — 2026-07-05 / actualizado 2026-07-14

**Qué (canónico post-repair):** Agregación de votos E14C sobre mesas `clean` con **lectura completa y aritmética verificada** — subconjunto estricto de las 9,424 mesas exportadas a `data/clean_mesas.csv` (re-export 2026-07-14).

| Capa | Mesas | % universo (122,019) |
|---|---|---|
| `clean` (`compute_overall_status`) | 9,424 | 7.7% |
| + E14C `aritmetica.delta == 0` | 1,371 | 1.1% |
| + todos los campos de voto legibles + `suma == URNA` | **803** | **0.66%** |

| Campo | Votos (803 mesas) | % sobre total suma |
|---|---|---|
| C1 Cepeda | 89,257 | 50.41% |
| C2 Abelardo | 82,803 | 46.77% |
| Blanco | 3,030 | 1.71% |
| Nulos | 1,693 | 0.96% |
| No marcados | 263 | 0.15% |
| **Total suma** | **177,046** | — |
| VOTANTES (E14C) | 178,805 | — |
| URNA (E14C) | 177,046 | — |

**Contexto:** `scripts/export_clean_mesas.py` (2026-07-14) + agregación manual sobre `data/cross_mesa_validation_*.jsonl`. Fuente E14C, `sources.e14c.fields` vía `derive_scalar()`. Filtro estricto: solo mesas donde los 5 campos de voto y URNA son enteros y la suma recalculada iguala URNA.

**Interpretación:** La muestra **no es representativa** del universo electoral. Tras el repair [6], solo **803 de 9,424** mesas `clean` tienen lectura E14C completa con aritmética verificada — el resto queda `clean` por ausencia de flags (sin `cross_discrepancy`, sin deltas computables), no por OCR perfecto. En este subconjunto estricto C1 supera a C2 (50.4% vs 46.8%), distinto del snapshot pre-repair (ver histórico abajo). Sirve como línea base de comparación, no como proyección electoral.

**Nota:** 14 mesas en el subconjunto estricto tienen URNA > VOTANTES (exceso agregado neto: VOTANTES supera URNA en 1,759 votos a nivel nacional en estas 803 mesas). El umbral `FRAUD_MAX_DIFF=30` opera por mesa individual, no por agregado.

<details>
<summary>Histórico pre-repair (2026-07-05, 10,579 mesas `clean` — metodología menos explícita)</summary>

| Campo | Votos | % |
|---|---|---|
| C1 Cepeda | 170,116 | 44.81% |
| C2 Abelardo | 198,973 | 52.41% |
| Blanco | 7,249 | 1.91% |
| Nulos | 3,003 | 0.79% |
| No marcados | 338 | 0.09% |
| **Total suma** | **379,679** | — |

Dataset parcial pre-repair [1]. C2 lideraba en ese snapshot; el subconjunto estricto post-repair no reproduce ese orden.
</details>

**Preguntas abiertas:** Comparar porcentajes del subconjunto estricto (803) contra agregado nacional en las 122,019 mesas. ¿Cuántas de las 8,621 mesas `clean` sin delta E14C computable se reclasificarían con re-OCR?

---

### [3] C1 con más conflictos reales que C2 entre fuentes — 2026-07-06

**Qué:** Conteo de subceldas donde las tres fuentes tienen valores legibles pero no coinciden (conflicto real, descartando null-vs-valor).

| Campo | null_vs_val | val_vs_val (conflicto real) |
|---|---|---|
| VOTANTES | 211,760 | **55,175** |
| SUMA_TOTAL | 212,858 | 40,870 |
| URNA | 208,265 | 40,424 |
| **C1_CEPEDA** | 225,912 | **32,897** |
| **C2_ABELARDO** | 224,047 | **32,117** |
| BLANCO | 260,753 | 19,765 |
| NULOS | 269,749 | 16,320 |
| NO_MARCADOS | 289,401 | 11,125 |
| INCINER | 296,128 | 8,454 |

**Contexto:** Análisis sobre `congruencia.fields[*].subcells` de los 115,691 JSONL. Conflicto real = subceldas donde `agree=false` y ningún valor es null — todas las fuentes leyeron algo pero difieren.

**Interpretación:** C1_CEPEDA tiene 780 conflictos reales más que C2_ABELARDO (2.4% más). Diferencia pequeña pero consistente. Más llamativo: VOTANTES (55K), SUMA_TOTAL (40K) y URNA (40K) tienen más conflictos reales que los candidatos. Causa probable dominante: campo no diligenciado en alguna copia (OCR lee `0`, otra fuente lee el valor real → cuenta como `val_vs_val` cuando 0 es técnicamente un valor). Causas secundarias: error aritmético del jurado al sumar en una sola copia, corrección posterior.

**Preguntas abiertas:** Filtrar conflictos de VOTANTES/URNA/SUMA_TOTAL donde uno de los valores es exactamente `0` para cuantificar qué fracción se explica por campo vacío vs. corrección deliberada.

---

### [4] 13.52% de mesas con al menos un campo de total vacío en E14C — 2026-07-06

**Qué:** 15,639 mesas (13.52%) tienen al menos uno de los tres campos de resumen en 0 en la fuente E14C.

| Campo | Mesas con valor = 0 | % |
|---|---|---|
| SUMA_TOTAL | 10,124 | 8.75% |
| URNA | 7,828 | 6.77% |
| VOTANTES | 6,755 | 5.84% |
| **Al menos uno** | **15,639** | **13.52%** |
| Los tres | 2,617 | 2.26% |

Desglose por combinación (mesas con C1+C2 > 0 — omisión real del jurado):

| Omitido | Mesas |
|---|---|
| Solo SUMA_TOTAL | 4,254 |
| Solo URNA | 2,073 |
| Solo VOTANTES | 1,639 |
| URNA + SUMA_TOTAL | 978 |
| VOTANTES + URNA | 953 |
| VOTANTES + SUMA_TOTAL | 688 |
| Los tres | 1,341 |

**Contexto:** `sources.e14c.fields` de 115,691 JSONL. Campo = 0 con C1+C2 > 0 se trata como omisión real.

**Interpretación:** Explica el hallazgo [3]: una copia con 0 y otra con valor real genera `val_vs_val` en cross-validación. SUMA_TOTAL es el más omitido — el jurado lo considera redundante. No implica fraude.

**Preguntas abiertas:** Cuántos de los 40K conflictos reales en URNA/SUMA_TOTAL se explican por este patrón (uno de los valores = 0).

---

### [5] Campos de totales vacíos por fuente E14 — cobertura completa — 2026-07-06

**Qué:** Porcentaje de mesas con al menos uno de VOTANTES, URNA, SUMA_TOTAL en blanco, sobre cobertura casi completa del universo (97,879 mesas E14C, 111,851 E14D, 111,129 E14T).

| Campo | E14C | E14D | E14T |
|---|---|---|---|
| VOTANTES | 5,468 (5.59%) | 799 (0.71%) | 1,048 (0.94%) |
| URNA | 7,767 (7.94%) | 1,069 (0.96%) | 1,347 (1.21%) |
| SUMA_TOTAL | 15,520 (15.86%) | 4,975 (4.45%) | 5,127 (4.61%) |
| **Al menos 1** | **21,581 (22.05%)** | **6,284 (5.62%)** | **6,803 (6.12%)** |
| Los 3 en 0 | 1,433 (1.46%) | 56 (0.05%) | 84 (0.08%) |

**Contexto:** `scripts/analyze_blank_fields.py` sobre `data/cross_mesa_validation_*.jsonl` + `data/*_cnn.jsonl` (10 deptos complementarios: BOLIVAR, CALDAS, CAQUETA, CAUCA, CESAR, CUNDINAMARCA, VALLE, VICHADA, VAUPES, AMAZONAS). Campo en blanco = null o 0 cuando C1+C2 > 0.

**Interpretación:** E14C tiene 22% de mesas con al menos un campo de totales vacío vs 5-6% en E14D y E14T (brecha 3.5-4×). La copia oficial es paradójicamente la más incompleta. SUMA_TOTAL es el más omitido en las tres fuentes — el jurado lo considera redundante. "Los 3 en 0" es 29× más frecuente en E14C que en E14D. No implica fraude — refleja comportamiento diferencial del jurado al llenar la copia oficial vs. las copias de delegados y testigos.

**Preguntas abiertas:** ¿El patrón varía por departamento? ¿Las mesas con campos vacíos en E14C coinciden con mesas en `needs_review_large_delta`? ¿La omisión de SUMA_TOTAL afecta la validación aritmética cross-fuente?

---

### [6] Distribución post-repair — 122,019 mesas, dataset completo — 2026-07-11

**Qué:** Estado de cross-validación sobre el universo completo de 122,019 mesas tras reparar 60,427 bloques `aritmetica.e14c` corruptos (49.5% del dataset). Upload confirmado a Supabase: processed=122,019, upserted=122,019, errors=0.

| Estado | Pre-repair | Post-repair | Δ |
|---|---|---|---|
| `needs_review_large_delta` | 89,003 | **74,963** | −14,040 (−15.8%) |
| `discrepancy` | 22,044 | **32,602** | +10,558 (+47.9%) |
| `clean` | 6,991 | **9,424** | +2,433 (+34.8%) |
| `warning` | 1,873 | **2,648** | +775 (+41.4%) |
| `known_anomaly` | 2,108 | **2,382** | +274 (+13.0%) |
| **Total** | **122,019** | **122,019** | — |

**Contexto:** `debug_sv/repair_stale_aritmetica.py` sobre `data/cross_mesa_validation_*.jsonl` (34 archivos). El bug: `scripts/reanalyze_e14c_blanks.py` actualizó los digit arrays en `sources.e14c.fields` pero no recomputó `aritmetica.e14c`, dejando valores pre-fix en 60,427 mesas. Reparación: recomputar `check_arithmetic()` con los scalars correctos usando gate `isinstance(scalar, int)`.

**Interpretación:** El movimiento más significativo es la caída de `needs_review_large_delta` (−14,040) y el aumento de `discrepancy` (+10,558). Mesas que el pipeline marcaba como "delta grande" cuando en realidad tenían campos parcialmente ilegibles ahora se reclasifican correctamente como `discrepancy` (campos ilegibles sin aritmética computable). Las 9,424 mesas `clean` representan **7.7% del universo** — línea base confiable para análisis electorales.

**Nota:** Hallazgo [1] (2026-07-05) reportó 115,691 mesas con distribución diferente — era un dataset parcial pre-repair. Este hallazgo [6] es el **estado canónico** — reflejado en `docs/metodologia.md` §4.4 y §8 (sincronizado 2026-07-14).

---
