# Migración a Producción — Portal de Etiquetado

Runbook para levantar un proyecto Supabase **de producción** limpio, separado del
actual (que pasa a ser **desarrollo/sandbox**), y migrar los datos reales.

> Marcá cada `[ ]` a medida que lo completás. No saltees el orden de las fases:
> hay dependencias duras (FKs, estado derivado).

---

## Decisiones tomadas

- **Split dev/prod.** El proyecto Supabase actual queda como **dev** (RLS off,
  `FAKE_USER_ID`, experimentación). El nuevo proyecto es **prod**, armado bien
  desde cero.
- **Modelo de seguridad: Opción B** (JWT por usuario + RLS real con políticas por
  fila + funciones `SECURITY DEFINER` para la lógica de sistema).
- **Imágenes:** re-upload desde local al bucket de prod (Supabase NO copia buckets
  entre proyectos; como tenemos los PNG locales, re-subir = mismo resultado, menos
  vueltas).
- **Labels:** son trabajo real → se migran dev → prod (atribuidas a un usuario
  "legacy"; las UUIDs de dev no matchean el auth nuevo de prod).

---

## Fase 0 — Preparación

- [ ] Crear el **proyecto Supabase de producción** (región cercana a los usuarios).
- [ ] Anotar `SUPABASE_URL` de prod y generar las keys:
  - [ ] **Publishable** (anon) → para el portal.
  - [ ] **Secret** (service_role) → SOLO para migración y para Railway env.
- [ ] Confirmar plan/free tier: 2 proyectos activos, 1 GB Storage c/u (los crops
      pesan ~650 MB, entran). Ojo: Supabase pausa proyectos free inactivos (~1 sem).
- [ ] Definir un `SECRET_KEY` nuevo y fuerte para Flask (NO reutilizar el de dev).

## Fase 1 — Schema y funciones (SQL Editor de prod)

> Orden importa: tablas → función de cola → (B) funciones de sistema → RLS.

- [ ] Correr `scripts/supabase_schema.sql` (crea crops/labels/assignments + índices,
      incluye `source_url` y el estado `needs_third`).
- [ ] Correr `scripts/assign_next_crop.sql` (cola complete-acta-first + regla 2→3).
- [ ] **(Opción B)** Crear las funciones `SECURITY DEFINER` que encapsulan la lógica
      de sistema (mover `write_label` + `evaluate_agreement` a la base, para que el
      usuario tenga grants mínimos). *(pendiente de escribir)*
- [ ] **(Opción B)** Aplicar las políticas RLS:
  - [ ] `labels`: `authenticated` INSERT solo `annotator_id = auth.uid()`.
  - [ ] `crops`: `authenticated` SELECT; escrituras solo vía funciones DEFINER.
  - [ ] `assignments`: gestionadas por la función de cola (DEFINER).
  - [ ] `anon`: sin acceso directo a las tablas.
  - [ ] Habilitar RLS en las 3 tablas.
- [ ] Crear tabla **`acta_fraud_marks`** (pdf_path, reason, annotator, marked_at)
      — el botón F hoy escribe a JSONL local, que en Railway se pierde. *(pendiente)*

## Fase 2 — Storage (imágenes)

- [ ] Crear bucket **`crops`** en prod, **Public** ✅.
- [ ] **(Opción B)** Definir políticas del bucket (lectura pública; escritura solo
      service_role / migración).
- [ ] Re-subir los PNG locales a prod:
      `python scripts/migrate_to_supabase.py --skip-crops --skip-labels`
      (apuntando el `.env`/env al proyecto **prod**; usa `SUPABASE_SERVICE_ROLE_KEY`).
      ~60-80 min. El fix de paginación ya sube los 15k (no se corta en 1000).

## Fase 3 — Datos

> **Orden duro:** crops PRIMERO (FK `labels.crop_id → crops`), labels DESPUÉS.

- [ ] **Seed de crops** desde local:
      `python scripts/migrate_to_supabase.py --skip-labels --skip-storage`
      (re-genera la tabla `crops` desde `index.jsonl`).
- [ ] **Backfill de `source_url`** (links públicos de la Registraduría):
      `python scripts/backfill_source_urls.py`
- [ ] **Migrar labels reales dev → prod** *(script pendiente)*: leer `labels` del
      proyecto dev y escribir en prod. Atribuir a un UUID "legacy" (las cuentas de
      dev no existen en prod). Solo insertar labels de crops que existen en prod.
- [ ] **🔴 Recomputar estado derivado** *(script pendiente)*: después de insertar
      labels, recalcular `annotation_count` / `status` / `confirmed_label` de cada
      crop (replay de la lógica de agreement). Si no, la cola y la regla 2→3 arrancan
      inconsistentes (crops "pending" que ya tienen 2 labels).

## Fase 4 — Aplicación y deploy

- [ ] Elegir host: **Railway** (recomendado) o Render.
- [ ] **WSGI:** cambiar `app.run()` por **gunicorn** con workers (el server de Flask
      no aguanta concurrencia). *(pendiente)*
- [ ] Variables de entorno en el host (apuntando a **prod**):
  - [ ] `SUPABASE_URL`, `SUPABASE_ANON_KEY`
  - [ ] `SECRET_KEY` (el nuevo, fuerte)
  - [ ] `USE_SUPABASE_STORAGE=true` (Railway no tiene los PNG en disco)
  - [ ] `SUPABASE_SERVICE_ROLE_KEY` solo si una tarea admin lo requiere (cuidado).
  - [ ] **Sin** `FAKE_USER_ID` (eso es bypass de dev).
- [ ] **Designar usuario admin** en prod (resolución de conflictos + revisión de
      fraude). Verificar cómo `_is_admin_user` lo determina.
- [ ] Deploy + smoke test:
  - [ ] Registro/login de un usuario real.
  - [ ] Las imágenes cargan desde Storage (no 404).
  - [ ] "Ver acta" abre el PDF de la Registraduría (probar warning de cert SSL;
        si rompe, plan B = proxy server-side).
  - [ ] Etiquetar un dígito → se guarda; back/skip/fraude funcionan.
  - [ ] La regla 2→3 escala correctamente (2 que discrepan → `needs_third`).

## Fase 5 — Cutover

- [ ] A partir de acá, **etiquetar en PROD**. No seguir cargando labels en dev en
      paralelo (no se sincronizan). Dev = sandbox de pruebas.
- [ ] Invitar a los voluntarios al portal de prod.

---

## Gotchas (resumen rápido)

| # | Riesgo | Mitigación |
|---|--------|------------|
| 1 | labels antes que crops → falla FK | Crops primero, siempre |
| 2 | Estado derivado inconsistente | Recomputar `annotation_count`/`status` tras migrar labels |
| 3 | Marcas de fraude se pierden en Railway | Tabla `acta_fraud_marks` en Supabase |
| 4 | UUIDs de dev no matchean auth de prod | Atribuir labels a usuario "legacy" |
| 5 | RLS con anon key no funciona | Opción B: JWT por usuario + funciones DEFINER |
| 6 | Cert SSL de la Registraduría en browser | Probar; fallback = proxy server-side |
| 7 | `app.run()` no aguanta concurrencia | gunicorn con workers |
| 8 | Secretos mezclados | service_role solo en host; `.env` local = dev |

## Scripts pendientes de escribir

- `scripts/migrate_labels_dev_to_prod.py` — export/import de labels entre proyectos.
- `scripts/recompute_crop_state.py` — replay de agreement sobre los crops migrados.
- `scripts/rls_policies.sql` — políticas RLS + funciones `SECURITY DEFINER` (Opción B).
- Tabla + endpoint para `acta_fraud_marks`.
