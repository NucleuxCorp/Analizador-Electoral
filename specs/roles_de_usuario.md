# SDD — Roles de Usuario

## Problema
Actualmente el portal tiene un solo tipo de usuario: todos ven lo mismo y etiquetan igual. No hay diferenciación entre administradores, validadores, revisores, ni lectores.

## Objetivo
Implementar un sistema de roles que permita:
- **Admin**: gestionar usuarios, ver conflictos, resolver disputas, acceder a todas las funcionalidades
- **Validador**: etiquetar dígitos, ver concordancia, reportar fraudes
- **Revisor**: validar actas completas, ver reportes, no etiqueta dígitos individuales
- **Lector**: solo puede ver el estado, progreso, y estadísticas (no etiqueta)

## Requerimientos

### 1. Roles en Supabase Auth
- Almacenar `role` en `app_metadata` del usuario en Supabase Auth
- Roles: `admin`, `validator`, `reviewer`, `reader`
- Por defecto: `validator`

### 2. Guards en backend
- Cada ruta protegida debe verificar el rol del usuario
- `@require_auth(role="admin")` → solo admin
- `@require_auth(role=["admin", "validator"])` → admin o validador

### 3. UI adaptativa
- El menú/template muestra opciones según el rol
- Admin ve panel de administración y conflictos
- Validador ve la interfaz de etiquetado
- Revisor ve actas completas
- Lector solo ve dashboard

### 4. Middleware
- After login, redirigir según el rol
- Si no tiene rol asignado, asignar `validator` por defecto

### 5. Base de datos
- Tabla `user_roles` opcional (como respaldo si no se usa app_metadata)

## Decisiones técnicas
- Usar `app_metadata` de Supabase Auth (ya existe `_is_admin_user()`)
- Extender `_is_admin_user()` a `_get_user_role()` que devuelva el rol
- Crear decorador `require_role()` para rutas
- Template recibe `user_role` y ajusta UI
