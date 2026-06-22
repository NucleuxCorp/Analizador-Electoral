# Tachon Method Scan Report

## 1. Run Metadata

- Run ID: `tachon-pattern-scan-500pdf`
- Completed: 2026-06-22T23:55:24.411849+00:00
- Workers: 8
- Elapsed (s): 46.574
- Manifest hash: `sha256:e24b1719bd5bebd9194fbd0c8fdaa1b2530f3241d3ef547696f1882ec8a3fb13`
- Error rate: 0.0000 (0/500)
- Scan mode: all_methods

## 2. Per-Method Flag Rates

| Method | Flagged | Ink Subcells | Rate |
|---|---:|---:|---:|
| TACHON | 6591 | 7064 | 0.933 |
| DOBLE_ESCRITURA | 0 | 7064 | 0.000 |
| DENSIDAD_ALTA | 217 | 7064 | 0.031 |
| ZONA_SUCIA | 289 | 7064 | 0.041 |
| COMBINED | 467 | 7064 | 0.066 |

## 3. Top Departments by TACHON Rate

National TACHON baseline: **0.933**

| Dept | PDFs | TACHON Rate | Delta vs National |
|---|---:|---:|---:|
| 17 | 5 | 1.000 | +0.067 |
| 40 | 3 | 1.000 | +0.067 |
| 46 | 4 | 1.000 | +0.067 |
| 54 | 1 | 1.000 | +0.067 |
| 64 | 4 | 1.000 | +0.067 |
| 68 | 1 | 1.000 | +0.067 |
| 21 | 17 | 0.974 | +0.041 |
| 03 | 26 | 0.971 | +0.038 |
| 25 | 19 | 0.969 | +0.036 |
| 13 | 18 | 0.968 | +0.035 |
| 05 | 18 | 0.965 | +0.032 |
| 23 | 17 | 0.964 | +0.031 |
| 12 | 14 | 0.960 | +0.027 |
| 44 | 4 | 0.952 | +0.019 |
| 19 | 14 | 0.952 | +0.019 |
| 27 | 25 | 0.948 | +0.015 |
| 11 | 17 | 0.934 | +0.001 |
| 26 | 7 | 0.931 | -0.002 |
| 16 | 58 | 0.928 | -0.005 |
| 28 | 11 | 0.928 | -0.005 |
| 01 | 66 | 0.920 | -0.013 |
| 52 | 12 | 0.919 | -0.014 |
| 29 | 16 | 0.918 | -0.015 |
| 15 | 31 | 0.917 | -0.017 |
| 09 | 12 | 0.913 | -0.020 |
| 31 | 42 | 0.913 | -0.020 |
| 24 | 11 | 0.895 | -0.038 |
| 07 | 15 | 0.892 | -0.041 |
| 48 | 7 | 0.865 | -0.068 |
| 72 | 1 | 0.864 | -0.069 |
| 50 | 1 | 0.857 | -0.076 |
| 56 | 1 | 0.857 | -0.076 |
| 60 | 1 | 0.000 | -0.933 |

## 4. Per-Label Hit Rates

| Label | TACHON Rate | ZONA_SUCIA Rate |
|---|---:|---:|
| BLANCO | 0.945 | 0.027 |
| C1_CEPEDA | 0.923 | 0.035 |
| C2_ABELARDO | 0.918 | 0.026 |
| INCINER | 0.965 | 0.028 |
| NO_MARCADOS | 0.960 | 0.156 |
| NULOS | 0.946 | 0.042 |
| SUMA_TOTAL | 0.924 | 0.018 |
| URNA | 0.908 | 0.021 |
| VOTANTES | 0.922 | 0.017 |

## 5. Co-occurrence Matrix

| | TACHON | DOBLE_ESCRITURA | DENSIDAD_ALTA | ZONA_SUCIA |
|---|---|---|---|---|
| TACHON | 6591 | 0 | 217 | 289 |
| DOBLE_ESCRITURA | 0 | 0 | 0 | 0 |
| DENSIDAD_ALTA | 217 | 0 | 217 | 134 |
| ZONA_SUCIA | 289 | 0 | 134 | 289 |

## 6. Score Percentiles

| Score | p50 | p90 | p99 | max |
|---|---:|---:|---:|---:|
| tachon_score | 1.000 | 1.000 | 1.000 | 1.000 |
| double_score | 0.000 | 0.000 | 0.000 | 0.167 |
| density_score | 0.000 | 0.000 | 1.000 | 1.000 |
| noise_score | 0.007 | 0.328 | 1.000 | 1.000 |

## 7. Validate Baseline Note

Validate holdout overlap: **0** manifest PDFs tagged `validate_holdout=true`. Dept `01` validate JSONL lacks `double_score`; this scan records all four isolated scores for national calibration.
