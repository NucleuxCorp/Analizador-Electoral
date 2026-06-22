# Progreso Real — Contadores Compuestos y Mensaje Contextual

## Propósito

Reemplazar el contador único `global_labeled` (que incluye `_skip` e infla el progreso percibido) con tres métricas — iniciados, confirmados, total — y agregar `done_reason` para que la UI distinga entre "cola personal agotada" y "todo completado".

## Requerimientos Funcionales

| ID | Requerimiento | Prioridad |
|----|---------------|-----------|
| RF-1 | `get_real_progress()` en db.py retorna `{started, confirmed, total}` | Alta |
| RF-2 | `started` cuenta DISTINCT crops con ≥1 label WHERE label_human ≠ `_skip` | Alta |
| RF-3 | `confirmed` cuenta crops cuyo status = `'confirmed'` | Alta |
| RF-4 | `total` cuenta todos los crops vía `COUNT(*)` | Alta |
| RF-5 | Fallback a query directa si RPC falla (mismo patrón que `get_global_stats`) | Alta |
| RF-6 | Nuevo RPC `count_started_crops()`: `SELECT COUNT(DISTINCT crop_id) FROM labels WHERE label_human != '_skip'` | Alta |
| RF-7 | `work_view` usa `get_real_progress()`, pasa `started/confirmed/total/done_reason` al template | Alta |
| RF-8 | `done_reason` = `"queue_exhausted"` si `confirmed < total`, `"all_done"` si `confirmed >= total` | Alta |
| RF-9 | `next_view` retorna `started/confirmed/total/done_reason` en JSON | Alta |
| RF-10 | `next_view_local`: started = `queue.labeled`, confirmed = `queue.labeled` (simplificación local) | Media |
| RF-11 | label.html muestra 3 métricas en vez de barra simple | Alta |
| RF-12 | Progreso real = `(started + confirmed) / (2 * total) * 100` | Alta |
| RF-13 | Mensaje contextual: "No hay más dígitos para validar..." si `done_reason="queue_exhausted"` | Alta |
| RF-14 | `updateDOM()` maneja `started`, `confirmed`, `total` | Alta |

## Requerimientos No Funcionales

| ID | Requerimiento |
|----|---------------|
| RNF-1 | Sin impacto performance en asignación (nuevas queries fuera del hot path) |
| RNF-2 | Fallback query directa si RPC falla (patrón probado en `get_global_stats`) |
| RNF-3 | Modo local no contacta Supabase; simplifica confirmed = labeled |
| RNF-4 | Rollback sin pérdida de datos ni migración |

## Escenarios

### E-1: Happy path — progreso normal (producción)

DADO 1000 crops, 400 con ≥1 label real (no _skip), 250 confirmados
CUANDO `get_real_progress()` se ejecuta
ENTONCES retorna `{started: 400, confirmed: 250, total: 1000}`
Y el progreso real = (400+250)/(2×1000)×100 = 32.5%

### E-2: Cola agotada con trabajo pendiente

DADO `assign_next_crop()` retorna NULL, confirmed=250, total=1000
CUANDO `work_view` renderiza
ENTONCES `done=True`, `done_reason="queue_exhausted"`
Y el template muestra "No hay más dígitos para validar..."

### E-3: Todo completado

DADO `assign_next_crop()` retorna NULL, confirmed=1000, total=1000
CUANDO `work_view` renderiza
ENTONCES `done=True`, `done_reason="all_done"`
Y el template muestra "¡Todo listo!"

### E-4: Fallback de RPC

DADO el RPC `count_started_crops()` lanza excepción
CUANDO `get_real_progress()` se ejecuta
ENTONCES fallback a `SELECT COUNT(DISTINCT crop_id) FROM labels WHERE label_human != '_skip'`

### E-5: Modo local simplificado

DADO local mode, queue.labeled=500, queue.remaining()=0, total=1000
CUANDO `/next` retorna done
ENTONCES started=500, confirmed=500, done_reason="queue_exhausted"

## Criterios de Aceptación

- [ ] Iniciados: X / Total excluye `_skip`
- [ ] Confirmados: X / Total refleja crops con status confirmed
- [ ] Progreso real = `(started + confirmed) / (2 * total) * 100`
- [ ] Cola agotada con crops sin confirmar → "No hay más dígitos para validar..."
- [ ] Todos confirmados → "¡Todo listo!"
- [ ] Funciona en producción (Supabase) y local
- [ ] `updateDOM()` actualiza 3 contadores sin page reload
- [ ] Rollback sin pérdida de datos

## Dependencias

- RPC existente `count_distinct_labeled_crops()` como referencia de implementación
- Acceso SQL a Supabase para crear `count_started_crops()`
- Sin dependencias con otros cambios activos
