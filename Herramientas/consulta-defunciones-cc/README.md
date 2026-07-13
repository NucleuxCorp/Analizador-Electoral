# Consulta Defunciones — Vigencia de Cédula

Herramienta ciudadana para consultar en lote el estado de vigencia de cédulas colombianas (NUIP) contra la API pública de la Registraduría Nacional.

---

## Qué hace

Lee una lista de números de cédula y consulta el endpoint **VigenciaCédula** de `defunciones.registraduria.gov.co`. Clasifica cada NUIP y genera informes en CSV y Markdown.

| Clasificación | Significado |
|---------------|-------------|
| **VIVO** | Cédula vigente (`Vigente (Vivo)`) |
| **FALLECIDO** | Cancelada por muerte en el registro |
| **NO_EXISTE** | NUIP no encontrado |
| **SIN_DATOS** | Entrada vacía o inválida para la API |
| **INVALIDO** | Formato rechazado localmente (sin llamada API) |
| **ERROR** | Fallo de red o HTTP |
| **DESCONOCIDO** | Respuesta no reconocida |

Al finalizar genera:

- `resultados.csv` — todas las consultas
- `fallecidos.csv` — solo filas con clasificación **FALLECIDO**
- `informe.md` + `informe_YYYY-MM-DD_HH-mm.md`

---

## Limitaciones importantes

- La API **no devuelve** nombre del titular ni fecha de defunción.
- El campo `fecha` en la respuesta es la **fecha de consulta**, no la fecha del fallecimiento.
- Un resultado `FALLECIDO` **no implica automáticamente** fraude electoral; puede reflejar desfase registral o uso legítimo en contextos de auditoría.
- NUIPs con ceros a la izquierda se envían como entero a la API (los ceros iniciales pueden perderse).

---

## Privacidad y uso responsable (Ley 1581 de 2012)

Los números de cédula son **datos personales sensibles**. Al usar esta herramienta:

- Consulta solo listas para las que tengas **base legal o autorización**.
- **No publiques** archivos `cedulas.txt`, `resultados.csv`, `fallecidos.csv` ni informes con NUIPs reales.
- Elimina los outputs cuando ya no los necesites.
- El operador es responsable del tratamiento de los datos.

---

## Requisitos

| Opción | Requisito |
|--------|-----------|
| Windows | Python 3.8+ o doble clic en `ejecutar.bat` |
| Linux/macOS | Python 3.8+ y `pip install tqdm` |

---

## Cómo usar

### Opción A — Menú interactivo (recomendado)

1. Copia tu lista de cédulas a `cedulas.txt` (una por línea) o usa un `.csv`.
2. Haz doble clic en `ejecutar.bat`.
3. Selecciona `[1] Consulta en lote` o `[2] Consulta individual`.
4. Abre `informe.md` o `fallecidos.csv` para revisar hallazgos.

### Opción B — Línea de comandos

```bash
pip install tqdm
python consulta_defunciones.py --file cedulas.txt
python consulta_defunciones.py --file lista.csv --delay 1.5 --resume
```

---

## Menú principal

```
[1] Consulta en lote (archivo)
[2] Consulta individual (una cédula)
[3] Reanudar consulta pendiente
[4] Configuración (delay, workers, IP)
[5] Ver último informe
[0] Salir
```

### Formato de entrada

**`.txt`** — una cédula por línea; líneas vacías y comentarios (`#`) se ignoran.

**`.csv`** — detecta columna `nuip`, `cedula`, `documento` o `cc`; si no hay encabezado reconocido, usa la primera columna.

Validación local: 6–10 dígitos numéricos.

---

## Estructura de archivos

```
consulta-defunciones-cc/
├── consulta_defunciones.py   <- Código fuente
├── ejecutar.bat               <- Lanzador Windows
├── README.md                  <- Este archivo
├── requirements.txt           <- tqdm
├── cedulas.txt.example        <- Ejemplo de entrada
└── cedulas.txt                <- Tu lista (no incluir en git)
```

---

## Configuración

| Parámetro | Default | Descripción |
|-----------|---------|-------------|
| Delay | 1.0 s | Pausa entre consultas API |
| Workers | 1 | Hilos paralelos (máx. 4; puede provocar bloqueo) |
| IP | vacío | Campo `ip` del JSON; parece cosmético en la API |

Tras 3 fallos consecutivos de red, la herramienta pausa 60 segundos (cooldown).

---

## Checkpoint y reanudación

Si interrumpes con Ctrl+C o quedan errores pendientes, se guarda `.consulta_checkpoint.json`. Usa `[3] Reanudar` para continuar sin duplicar filas en el CSV.

---

## Pruebas manuales

Antes de publicar cambios, verifica al menos:

- Menú arranca sin errores (`ejecutar.bat`)
- Consulta individual con NUIP conocido
- Lote genera `resultados.csv` y `fallecidos.csv`
- NUIP inválido (`12345`) → `INVALIDO` sin llamada API
- `git status` no muestra CSV ni informes (`.gitignore`)

---

Esta herramienta es parte del **Proyecto Analizador Electoral** — auditoría ciudadana de actas electorales colombianas 2026.

Repositorio: [https://github.com/NucleuxCorp/Analizador-Electoral](https://github.com/NucleuxCorp/Analizador-Electoral)