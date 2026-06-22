# TODO — Segunda Vuelta Portal Switch

Tracking de deuda técnica explícita del change `segunda-vuelta-portal-switch`.
Cada entrada debe ser específica (no "limpiar el código") y tener un riesgo asociado.

---

## DEUDA TÉCNICA — VT-3 (test_queries_filter_by_active_vuelta)</h3>

**Estado:** Abierta. Mock confirmó sintaxis; no confirmó comportamiento.</p>

**Test actual** (`tests/labeler/test_vuelta_tagging.py::test_queries_filter_by_active_vuelta`):
hace mock del Supabase client y verifica `chain.eq.call_args_list == [call("vuelta", "segunda")]`.
Esto confirma que el código **invoca** el filtro con esa sintaxis, pero no ejecuta el filtro
contra datos reales. Un bug de lógica invertida o columna mal nombrada no sería detectado.

**Riesgo concreto:**
`list_crops_by_vuelta` (`src/modules/labeler/db.py:334`) no tiene cobertura real de comportamiento.
Cualquier función que dependa de este filtro — en particular PR-C (`assign_next_crop_v2` con
filtro `vuelta`) — hereda el punto ciego. Si hay un bug silencioso en `list_crops_by_vuelta`,
se propaga al pipeline de asignación sin alarma.

**Fix pendiente:**
Reemplazar el mock por seed de filas reales (con ambas vueltas: `primera` y `segunda`) y un
assert sobre el resultado devuelto (count + contenido). Esto requiere una de:
- Testcontainers + Postgres local + pgcrypto para ejecutar las consultas;
- SQLite con la misma firma de métodos (no recomendado — PostgREST API no se replica);
- Un fixture pre-seeded apuntando a un proyecto Supabase de test.

**No bloquea PR-C** (el mock sí confirma la invocación correcta), pero tech debt real que debe
cerrarse antes de `sdd-archive` para que el change quede completo.

**Effort estimado:** medio — la mayor parte del costo es infra de tests, no código.

---

## Timeline

- 2026-06-22: Anotada después de re-verify inline de commit `b533215` (CRÍTICOS resueltos).
- Pendiente: antes de `sdd-archive` de `segunda-vuelta-portal-switch`.