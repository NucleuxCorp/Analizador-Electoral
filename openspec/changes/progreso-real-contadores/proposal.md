# Proposal: Progreso Real — Contadores Compuestos y Mensaje Contextual

## Intent

El portal de etiquetado muestra un solo número (`global_labeled`) que incluye `_skip` y crops con solo 1 label como "etiquetados". Esto infla el progreso percibido y engaña al usuario sobre cuánto trabajo real queda. Cuando un usuario se queda sin crops asignables, el mensaje "¡Todo listo!" es confuso porque el sistema aún tiene crops sin confirmar — el usuario cree que todo el trabajo está terminado cuando solo se agotó su cola personal.

## Scope

### In Scope
- Nuevo `get_real_progress()` en `db.py` que retorne `started`, `confirmed`, `total`
- Excluir `_skip` del conteo de iniciados (crops con label ≠ `_skip`)
- Nuevo RPC `count_started_crops()` o query SQL que cuente crops con ≥1 label real
- Mostrar en `label.html`: "Iniciados: X / Total", "Confirmados: X / Total", "Progreso real: X%"
- Cambiar mensaje "done" a "No hay más dígitos para validar..." cuando `assign_next_crop` retorna NULL
- Funcionar en producción (Supabase) y local (state.queue)
- Actualizar `updateDOM()` para reflejar los nuevos campos en el JSON de `/next`

### Out of Scope
- Cambios en la lógica de asignación de crops
- Nuevos filtros de cola o priorización
- Dashboard administrativo con métricas avanzadas
- Migración de datos existentes

## Capabilities

### New Capabilities
None — no se introduce una nueva capability separada.

### Modified Capabilities
- `web-portal`: Cambia la métrica de progreso y el mensaje de cola agotada. Requiere delta spec.

## Approach

1. **db.py — `get_real_progress()`**: Nueva función que consulta:
   - `started`: COUNT de crops con ≥1 label WHERE label ≠ `_skip`
   - `confirmed`: COUNT de crops con status = `confirmed`
   - `total`: COUNT total de crops
   - Fallback idéntico al actual si RPC falla

2. **SQL/RPC**: Agregar `count_started_crops()` que excluya `_skip`:
   ```sql
   SELECT COUNT(DISTINCT crop_id)::integer
   FROM public.labels WHERE label != '_skip';
   ```

3. **server.py**: En `work_view`, `next_view`, y `next_view_local`:
   - Llamar `get_real_progress()` en vez de `get_global_stats()`
   - Retornar `started`, `confirmed`, `total` en JSON
   - Cuando `assign_next_crop` retorna NULL: verificar `confirmed < total` → set `done_reason = "queue_exhausted"` vs `"all_done"`

4. **label.html**:
   - Reemplazar barra de progreso simple con layout de 3 métricas
   - `updateDOM()`: leer `started`, `confirmed`, `total` del JSON
   - Bloque done: mostrar mensaje contextual según `done_reason`

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/modules/labeler/db.py` | Modified | Nueva función `get_real_progress()` + RPC |
| `src/modules/labeler/server.py` | Modified | 3 endpoints usan nueva función + nuevo campo `done_reason` |
| `src/modules/labeler/templates/label.html` | Modified | Nuevo layout de progreso + mensaje done contextual |
| `scripts/supabase_schema.sql` | Modified | Nuevo RPC `count_started_crops()` |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| RPC nuevo falla en Supabase | Low | Fallback con query directa (ya existe patrón en `get_global_stats`) |
| Performance de nueva query | Low | RPC sobre índices existentes, mismo patrón que `count_distinct_labeled_crops` |
| Local mode no tiene `confirmed` status | Medium | En local, `started` = labeled, `confirmed` = labeled (simplificación aceptable) |
| Mensaje "done" confunde usuarios existentes | Low | Mensaje es más informativo, no ambiguo |

## Rollback Plan

1. Revertir cambios en `label.html` (restaurar barra simple + "¡Todo listo!")
2. Revertir cambios en `server.py` (volver a llamar `get_global_stats()`)
3. Eliminar `get_real_progress()` de `db.py`
4. Eliminar RPC `count_started_crops()` de Supabase
5. No hay migración de datos — rollback es seguro sin pérdida

## Dependencies

- RPC `count_distinct_labeled_crops()` existente (referencia para nuevo RPC)
- Supabase SQL access para crear nuevo RPC

## Success Criteria

- [ ] Counter muestra "Iniciados: X / Total" excluyendo `_skip`
- [ ] Counter muestra "Confirmados: X / Total"
- [ ] Progreso real calculado como `(started + confirmed) / (2 * total) * 100`
- [ ] Cuando cola personal se agota pero hay crops sin confirmar: mensaje "No hay más dígitos para validar..."
- [ ] Cuando todos los crops están confirmados: "¡Todo listo!" (mensaje original)
- [ ] Funciona en producción (Supabase) y local (dev mode)
- [ ] `updateDOM()` actualiza los 3 contadores en tiempo real
- [ ] Rollback sin pérdida de datos
