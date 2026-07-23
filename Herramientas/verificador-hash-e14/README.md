# Verificador de Hash E-14

Herramienta ciudadana para verificar la integridad de actas E-14 de las elecciones presidenciales colombianas 2026. Actualmente soporta actas **E-14C**.

---

## Que hace

Compara los PDFs que tienes contra un indice de huellas digitales SHA-256 generado a partir de los archivos descargados directamente del servidor de la Registraduria el **2026-06-30**. Puede operar en modo offline (sin internet) o en modo online comparando contra el servidor live.

| Estado | Significado |
|--------|------------|
| **VERIFICADA** | Identico al original del indice. No fue modificado. |
| **ALTERADA** | El nombre esta en el indice pero el contenido no coincide. Evidencia de modificacion. |
| **DESCONOCIDA** | No esta en el indice. Puede ser una acta fuera de la cobertura, un E-14T, o un archivo ajeno. **No implica alteracion.** |

Al finalizar genera `informe.md` con el resumen completo.

---

## Cobertura del indice

> El indice contiene aproximadamente **83,498 actas** de un total de ~118,347 registradas.
> Las ~27,000 mesas restantes no fueron capturadas en la descarga original y apareceran como **DESCONOCIDA**.
>
> Snapshot del indice: **2026-06-30**

---

## Como usar

### Opcion A — Ejecutable (sin instalar nada)

1. Copia los PDFs que quieres verificar dentro de la carpeta `PDFs-V2/`
2. Haz doble clic en `verificador_e14c.exe`
3. Abre `informe.md` para ver los resultados

### Opcion B — Con Python instalado

```bash
pip install tqdm          # solo la primera vez
python verificador_e14c.py PDFs-V2
```

O en Windows haz doble clic en `ejecutar.bat`.

### Opcion C — Compilar desde el codigo fuente

Si no confias en el `.exe` distribuido, puedes compilarlo tu mismo a partir del codigo fuente (`verificador_e14c.py`) y verificar que el resultado es identico:

1. Haz doble clic en `compilar.bat`
2. Se generara un nuevo `verificador_e14c.exe` en esta misma carpeta
3. Compara el SHA-256 del `.exe` generado con el distribuido

---

## Menu de navegacion

El verificador tiene un menu de dos niveles.

### Nivel 1 — Menu principal

```
[1] Offline  — verificar con indice local (sin internet)
[2] Online   — verificar contra la Registraduria
[3] Organizar / Revertir carpeta
[0] Salir
```

### Nivel 2 — Sub-menu Offline (`[1]`)

Aparece solo al seleccionar `[1] Offline`:

```
[1] PDFs-V2/  (carpeta por defecto)
[2] Otra carpeta
[0] Volver al menu principal
```

Ejecuta la verificacion SHA-256 local contra el indice (`hash_index_e14c.json`).
No hace conexion a internet. Requiere que el indice este presente.

### Nivel 2 — Sub-menu Online (`[2]`)

Aparece solo al seleccionar `[2] Online`:

```
[1] Verificar tamano        (rapido — cabeceras HTTP HEAD)
[2] Verificar contenido     (SHA256 — descarga completa)
[3] Verificar ambos         (tamano primero, SHA256 si coincide)
[0] Volver al menu principal
```

Todas las opciones piden la carpeta (por defecto `PDFs-V2/`) antes de proceder.

---

## Verificacion de tamano (opcion Online [1])

Envia una peticion HTTP HEAD por cada PDF y compara el campo `Content-Length` del servidor contra el tamano del archivo local. **Sin descarga completa** — es significativamente mas rapido que la verificacion SHA-256.

| Estado | Significado |
|--------|------------|
| **IGUAL_TAMANIO** | El tamano local coincide con lo que reporta el servidor. No garantiza contenido identico, pero es una señal positiva. |
| **DIFERENTE_TAMANIO** | El tamano difiere. Se recomienda ejecutar verificacion SHA-256 completa. |
| **TAMANIO_NO_DISPONIBLE** | El servidor no devolvio el header `Content-Length`. No es posible comparar tamanos. |

Genera `informe_tamanio.md` y una copia con timestamp.

### Verificar ambos (opcion Online [3])

Ejecuta la verificacion de tamano primero. Los archivos con `DIFERENTE_TAMANIO` son descartados sin descargar (ahorra ancho de banda). Los demas pasan a verificacion SHA-256 completa.

Genera dos informes:
- `informe_tamanio.md` — resultado del paso 1 (tamanos)
- `informe_online.md` — resultado del paso 2 (SHA-256)

### Progreso guardado (verificaciones largas)

Si estas verificando muchos PDFs en modo Online, el verificador guarda tu avance cada 50 archivos en un archivo `.verify_checkpoint.json` dentro de la misma carpeta de PDFs. Si cierras el programa o se corta la conexion, simplemente vuelve a ejecutar la misma verificacion sobre la misma carpeta: los archivos ya verificados se omiten automaticamente y solo se reintentan los que tuvieron error de red. No necesitas hacer nada especial — es automatico.

---

## Estructura de archivos

```
verificador-e14c/
├── verificador_e14c.py      <- Codigo fuente (auditoria)
├── e14c_paths.py            <- Extraccion dept/mpio/zona/puesto desde la carpeta (copia sincronizada, no editar directo)
├── http_fallback.py         <- Respaldo con navegador (opcional, ver abajo)
├── verificador_e14c.exe     <- Ejecutable compilado (sin Python)
├── hash_index_e14c.json     <- Indice de huellas digitales SHA-256
├── url_index_e14c.json      <- Indice de URLs por filename (opcional)
├── ejecutar.bat             <- Lanzador para usuarios con Python
├── compilar.bat             <- Compilador desde fuente (auditoria)
├── README.md                <- Este archivo
└── PDFs-V2/                 <- Carpeta donde colocar tus PDFs
```

---

## Respaldo con navegador (opcional, solo modo Online)

A veces el servidor de la Registraduria bloquea temporalmente las descargas automaticas (peticiones "de script"). Si eso pasa mientras estas verificando en modo **Online**, el verificador puede abrir automaticamente una ventana de navegador (Chromium) para seguir descargando desde ahi, como si fueras tu navegando manualmente. Es completamente opcional:

- **Si no instalas nada**, el verificador sigue funcionando normal en modo urllib (sin navegador). Si el servidor bloquea, simplemente veras mas errores en el reporte — puedes reintentar mas tarde, el progreso ya se guardo.
- **Si quieres activar el respaldo con navegador**, instala Playwright una sola vez:

  ```bash
  pip install playwright
  playwright install chromium
  ```

  A partir de ahi, si el verificador detecta 3 fallos de descarga seguidos, abrira automaticamente una ventana de Chromium y continuara desde ahi — sin que tengas que hacer nada. Cuando el bloqueo pase, vuelve a usar la conexion directa (mas rapida) por si sola.

- **No necesitas iniciar sesion en nada.** La ventana que se abre no usa tu perfil de Chrome ni tus contraseñas guardadas — es una ventana nueva y vacia, solo para descargar el PDF.

- **Variable opcional `E14C_FALLBACK_HEADLESS`**: por defecto la ventana de Chromium es visible (asi funciona de forma mas confiable contra el bloqueo del servidor). Si prefieres que no aparezca ninguna ventana (a costa de que el respaldo pueda fallar con mas frecuencia), puedes activarlo asi antes de ejecutar el verificador:

  ```bash
  # Windows (cmd)
  set E14C_FALLBACK_HEADLESS=1
  python verificador_e14c.py PDFs-V2

  # Windows (PowerShell)
  $env:E14C_FALLBACK_HEADLESS = "1"
  python verificador_e14c.py PDFs-V2

  # Linux / macOS
  E14C_FALLBACK_HEADLESS=1 python verificador_e14c.py PDFs-V2
  ```

---

## url_index_e14c.json

Archivo opcional que mapea cada nombre de archivo a su URL de descarga en el servidor de la Registraduria. Formato:

```json
{
  "01_001_01_01_E14_...pdf": "https://escrutinios2vueltapresidente2026.registraduria.gov.co/docs/E14/...",
  "_meta": {
    "generated_at": "2026-07-04 20:44",
    "entries": 109425,
    "skipped": 0,
    "source": "Herramientas/verificador-hash-e14/hash_index_e14c.json"
  }
}
```

El archivo es generado por el script `build_url_index.py` (herramienta de mantenedor, **no incluida en la distribucion ciudadana**). Los colaboradores ciudadanos reciben este archivo pre-generado como parte del paquete de distribucion.

### Construccion de URLs — carpeta como fuente de verdad

Las URLs se construyen a partir de la **carpeta** donde vive cada PDF
(`{DEPT}/{MPIO}/zona_XX/puesto_XX/`), no del nombre plano del archivo — el
nombre plano tiene una posicion sin resolver que no siempre coincide con la
zona/puesto real. Esta logica vive en `e14c_paths.py` (copia sincronizada
del modulo canonico del proyecto).

Si ejecutas el verificador sobre una carpeta **plana** (PDFs sueltos, sin
organizar en `zona_XX/puesto_XX`), el verificador usa como respaldo la
clave `by_location` dentro de `hash_index_e14c.json` (mapa
`filename -> "dept/mpio/zona/puesto"`, precalculado por el mantenedor a
partir de la estructura de carpetas original). Si un archivo no tiene
entrada en `by_location` y la carpeta es plana, la URL no puede construirse
(`URL_NO_CONSTRUIBLE`) — el verificador nunca adivina la ubicacion.

---

## Requisitos

| Opcion | Requisito |
|--------|----------|
| Ejecutable (.exe) | Solo Windows 10/11, sin instalar nada |
| Python | Python 3.8+, cualquier sistema operativo |
| Compilar | Python 3.8+ + PyInstaller (compilar.bat lo instala) |

---

## Estados detallados

**VERIFICADA** — El PDF es byte-por-byte identico al registrado en el indice el 2026-06-30.

**ALTERADA** — El nombre del archivo existe en el indice pero el contenido difiere. Requiere revision.

**DESCONOCIDA** — No se encontro en el indice. Causas mas comunes:
- Acta de las ~27,000 mesas fuera de la cobertura
- PDF de tipo E-14T (nombre basado en hash)
- Archivo no perteneciente al corpus E-14C

**DESCONOCIDA nunca implica ALTERADA.**

---

Esta herramienta es parte del **Proyecto Analizador Electoral** — auditoria ciudadana de actas electorales colombianas 2026.

Repositorio: https://github.com/NucleuxCorp/Analizador-Electoral
