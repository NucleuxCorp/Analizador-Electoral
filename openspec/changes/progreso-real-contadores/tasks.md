# Tasks: Progreso Real — Contadores Compuestos y Mensaje Contextual

## Review Workload Forecast

| Campo | Valor |
|-------|-------|
| Líneas estimadas de cambio | ~160-170 |
| Riesgo budget 400 líneas | Low |
| PRs encadenados recomendados | No |
| Split sugerido | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | RPC + db.py + server.py + label.html | PR 1 | Single PR, < 200 líneas, sin dependencias externas |

## Fase 1: Base de datos — RPC `count_started_crops()`

- [ ] 1.1 `scripts/supabase_schema.sql` — agregar RPC `count_started_crops()` después de `count_distinct_labeled_crops` — SELECT COUNT(DISTINCT crop_id) FROM labels WHERE label_human != '_skip' (~12 líneas)

## Fase 2: Capa de datos — `get_real_progress()` en db.py

- [ ] 2.1 `src/modules/labeler/db.py` — crear `get_real_progress()` sin parámetros, llama RPC `count_started_crops()` para `started`, query directa `SELECT COUNT(*) FROM crops WHERE status='confirmed'` para `confirmed`, query directa `SELECT COUNT(*) FROM crops` para `total`, con fallback a query directa si RPC falla (~45 líneas)

## Fase 3: Controladores — server.py (3 endpoints)

- [ ] 3.1 `src/modules/labeler/server.py` — `work_view()` (L~904): reemplazar `get_global_stats()` por `get_real_progress()`, calcular `done_reason`, pasar `started/confirmed/total/done_reason/pct` al template (~15 líneas)
- [ ] 3.2 `src/modules/labeler/server.py` — `next_view()` (L~1169): reemplazar `get_global_stats()` por `get_real_progress()`, retornar `started/confirmed/total/done_reason` en JSON (~15 líneas)
- [ ] 3.3 `src/modules/labeler/server.py` — `next_view_local()` (L~1480): agregar `started=queue.labeled`, `confirmed=queue.labeled`, `done_reason` en JSON según `remaining()==0` vs `total==0` (~10 líneas)
- [ ] 3.4 `src/modules/labeler/server.py` — local `work_view()` (L~1333): pasar `started/confirmed/total/done_reason/pct` al template (~5 líneas)

## Fase 4: Presentación — label.html

- [ ] 4.1 `templates/label.html` — CSS: agregar `.progress-metrics`, `.pm-num`, `.pm-lbl`, `.pm-sub` (~15 líneas antes del cierre `</style>`)
- [ ] 4.2 `templates/label.html` — reemplazar barra de progreso (L~299-328) con layout de 3 métricas: Iniciados (X de total), Confirmados (X), Progreso real (X%) (~25 líneas)
- [ ] 4.3 `templates/label.html` — cambiar bloque done (L~291-295) para mensaje contextual según `done_reason`: "No hay más dígitos para validar..." vs "¡Todo listo!" (~8 líneas)
- [ ] 4.4 `templates/label.html` — `updateDOM()` (L~974-988): agregar actualización de `pm-started`, `pm-confirmed`, `pm-pct` desde `item.started/confirmed/total` (~15 líneas)

## Orden de implementación

1. RPC SQL (Fase 1) — sin dependencias, se deploya primero
2. `get_real_progress()` (Fase 2) — depende del RPC, fallback sin él
3. server.py (Fase 3) — depende de `get_real_progress()`
4. label.html (Fase 4) — depende de las variables del template que llegan de server.py

Sin tests automatizados (el proyecto no tiene test suite). Verificación manual con escenarios del spec.
