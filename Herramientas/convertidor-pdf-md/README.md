# Convertidor PDF -> Markdown + CSV (MarkItDown)

Herramienta ciudadana para convertir archivos PDF a formato Markdown usando [MarkItDown](https://github.com/microsoft/markitdown) de Microsoft, con deteccion de tablas y exportacion a CSV mediante pdfplumber. Pensada para procesar lotes de PDFs de forma local, sin internet ni servicios en la nube.

---

## Que hace

Convierte todos los PDFs de una carpeta a archivos Markdown individuales, conservando el texto y la estructura del documento (subtitulos, tablas, listas). Adicionalmente puede:

- **Embeber tablas como CSV** dentro del `.md` en bloques de codigo ` ```csv `
- **Generar archivos `.csv` de respaldo**, uno por tabla detectada, organizados en subcarpetas por PDF

```
pdfs/mi_documento.pdf  -->  md/mi_documento.md
                        -->  csv/mi_documento/tabla1.csv
                        -->  csv/mi_documento/tabla2.csv
                        -->  ...
```

Recorre automaticamente todos los `.pdf` de `pdfs/`, salta los ya convertidos (a menos que uses `--force`) y muestra un resumen al finalizar.

---

## Como usar

### Modo basico (solo texto)

1. Copia tus archivos PDF a la carpeta `pdfs/`
2. Haz doble clic en `ejecutar.bat`
3. Recoge tus Markdown en la carpeta `md/`

### Modo tablas (CSV embebido + archivos de respaldo)

```bash
python convertidor.py --force --embed-csv --csv-files
```

Esto produce:
- `md/Divulgacion_7_julio_2026.md` — Markdown con texto + tablas embebidas como bloques ` ```csv `
- `csv/Divulgacion_7_julio_2026/tabla1.csv` hasta `tabla6.csv` — un CSV por tabla detectada

La primera ejecucion instala las dependencias automaticamente si Python esta presente.

### Opciones

| Comando | Que hace |
|---------|----------|
| `python convertidor.py` | Convierte solo los PDFs nuevos |
| `python convertidor.py --force` | Re-convierte todos, incluso los ya procesados |
| `python convertidor.py --clean` | Borra los `.md` anteriores antes de convertir |
| `python convertidor.py --embed-csv` | Detecta tablas con pdfplumber y las embebe dentro del `.md` como CSV en texto plano |
| `python convertidor.py --csv-files` | Genera ademas archivos `.csv` de respaldo, uno por tabla, en `csv/{pdf_stem}/` |
| `python convertidor.py --input DIR` | Usa DIR como carpeta de entrada (default: `pdfs/`) |
| `python convertidor.py --output DIR` | Usa DIR como carpeta de salida para Markdown (default: `md/`) |
| `python convertidor.py --csv-dir DIR` | Carpeta base para `.csv` de respaldo (default: `csv/`) |

`--embed-csv` y `--csv-files` se pueden usar juntos o por separado:
- Solo `--embed-csv`: tablas van dentro del `.md`, no se generan `.csv` sueltos
- Solo `--csv-files`: se generan `.csv` sueltos, el `.md` no incluye tablas
- Ambos: `.md` con tablas embebidas + `.csv` de respaldo

---

## Estructura de archivos

```
convertidor-pdf-md/
+-- pdfs/            <- Coloca aqui tus PDFs
+-- md/              <- Los Markdown se generan aqui
+-- csv/             <- Los CSV de respaldo se generan aqui (subcarpeta por PDF)
+-- convertidor.py   <- Script principal
+-- ejecutar.bat     <- Lanzador (doble clic)
+-- requirements.txt <- Dependencias (markitdown[pdf])
+-- .gitignore
+-- README.md        <- Este archivo
```

### Estructura de salida con --csv-files

```
csv/
+-- mi_documento/
|   +-- tabla1.csv
|   +-- tabla2.csv
|   +-- tabla3.csv
+-- otro_pdf/
    +-- tabla1.csv
```

Cada PDF tiene su propia subcarpeta para que las tablas de diferentes documentos no colisionen.

---

## Requisitos

- **Python 3.10 o superior** — [Descargar](https://www.python.org/downloads/)
- Sin internet necesario para la conversion (las dependencias se instalan una sola vez)

### Dependencias

```
markitdown[pdf]
```

Incluye `pdfminer.six` y `pdfplumber` para extraccion de texto y tablas de PDFs.

---

## Limitaciones conocidas

- **PDFs escaneados (imagenes):** MarkItDown extrae texto digital, no hace OCR. Si el PDF es una imagen escaneada sin capa de texto, el resultado estara vacio. Para esos casos se necesita un motor OCR adicional (Tesseract, EasyOCR, Azure Document Intelligence).
- **Tablas complejas:** Las tablas se detectan mediante lineas y bordes del PDF. Formatos muy irregulares o tablas sin bordes pueden no detectarse correctamente.
- **Las E-14 (actas electorales):** Al ser formularios escaneados con texto manuscrito, la conversion de texto digital puede ser parcial. Esta herramienta es util para PDFs con texto digital (resoluciones, circulares, documentos generados por computador).
- **Watermarks en headers:** Algunos PDFs institucionales tienen marcas de agua en el header que pdfplumber detecta como parte de la primera fila de la tabla. Los datos reales empiezan en la segunda fila.

---

## Notas metodologicas

- Los PDFs originales nunca se modifican; la herramienta solo lee de `pdfs/` y escribe en `md/` y `csv/`.
- Si un PDF falla, el resto del lote continua procesandose. El resumen indica cuantos tuvieron exito, cuantos estaban vacios y cuantos fallaron.
- La deteccion de tablas usa pdfplumber (analisis de lineas y bordes del PDF), no OCR. Solo funciona con PDFs que tienen tablas con estructura visual discernible.
- La conversion es 100% local. Ningun archivo sale de tu equipo.

---

Esta herramienta es parte del **Proyecto Analizador Electoral** - auditoria ciudadana de actas electorales colombianas 2026.

Repositorio: https://github.com/NucleuxCorp/Analizador-Electoral/