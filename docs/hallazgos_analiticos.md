# Hallazgos analíticos — Analizador de Elecciones

Log acumulativo de descubrimientos cuantitativos sobre el dataset E-14 segunda vuelta.
Cada entrada es un hallazgo emergido en sesión de análisis, capturado en el momento.

> Este archivo es complementario a `docs/metodologia.md`. La metodología documenta
> el proceso; este log documenta lo que el proceso encuentra.

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

---

### [2] Resultados en mesas limpias (E14C, sin revisión humana) — 2026-07-05

**Qué:** Votos de C1 y C2 en las 10,579 mesas que pasaron todos los filtros del pipeline.

| Campo | Votos | % sobre votos válidos |
|---|---|---|
| C1 Cepeda | 170,116 | 44.81% |
| C2 Abelardo | 198,973 | 52.41% |
| Blanco | 7,249 | 1.91% |
| Nulos | 3,003 | 0.79% |
| No marcados | 338 | 0.09% |
| **Total suma** | **379,679** | — |
| Votantes habilitados | 375,072 | — |
| Total urna | 379,679 | — |

**Contexto:** Fuente E14C, `sources.e14c.fields`. Universo total: 26.34 millones de votantes. Esta muestra representa **1.44%** del total nacional.

**Interpretación:** La muestra NO es representativa del universo electoral — es un subconjunto sesgado hacia mesas donde el OCR funcionó perfectamente y las tres fuentes coincidieron. Cualquier irregularidad empuja una mesa fuera de `clean`, por lo que este subconjunto está sesgado hacia resultados no manipulados. Sirve como línea base de comparación, no como proyección electoral.

**Nota:** Total urna (379,679) supera votantes habilitados (375,072) en 4,607. Mesas con exceso ≤ 30 pasan el filtro como `clean` — el check `VOTOS_EXCEDEN_VOTANTES` solo escala a `needs_review_large_delta` cuando el exceso > 30.

**Preguntas abiertas:** Comparar porcentajes de esta muestra limpia contra los resultados de las 115,691 mesas totales para detectar si el sesgo de selección introduce diferencias significativas.

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
