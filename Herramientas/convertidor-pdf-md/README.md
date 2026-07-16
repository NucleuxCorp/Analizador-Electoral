# Convertidor PDF -> Markdown (MarkItDown)

Herramienta ciudadana para convertir archivos PDF a formato Markdown usando [MarkItDown](https://github.com/microsoft/markitdown) de Microsoft. Pensada para procesar lotes de PDFs de forma local, sin internet ni servicios en la nube.

---

## Que hace

Convierte todos los PDFs de una carpeta a archivos Markdown individuales, conservando el texto y la estructura del documento (subtitulos, tablas, listas). Cada PDF genera un `.md` con el mismo nombre en la carpeta de salida.

```
pdfs/mi_acta.pdf  -->  md/mi_acta.md
```

Recorre automaticamente todos los `.pdf` de `pdfs/`, salta los ya convertidos (a menos que uses `--force`) y muestra un resumen al finalizar.

---

## Como usar

1. Copia tus archivos PDF a la carpeta `pdfs/`
2. Haz doble clic en `ejecutar.bat`
3. Recoge tus Markdown en la carpeta `md/`

La primera ejecucion instala las dependencias automaticamente si Python esta presente.

### Opciones

| Comando | Que hace |
|---------|----------|
| `python convertidor.py` | Convierte solo los PDFs nuevos |
| `python convertidor.py --force` | Re-convierte todos, incluso los ya procesados |
| `python convertidor.py --clean` | Borra los `.md` anteriores antes de convertir |
| `python convertidor.py --input DIR` | Usa DIR como carpeta de entrada (default: `pdfs/`) |
| `python convertidor.py --output DIR` | Usa DIR como carpeta de salida (default: `md/`) |

---

## Estructura de archivos

```
convertidor-pdf-md/
+-- pdfs/            <- Coloca aqui tus PDFs
+-- md/              <- Los Markdown se generan aqui
+-- convertidor.py   <- Script principal
+-- ejecutar.bat     <- Lanzador (doble clic)
+-- requirements.txt <- Dependencias (markitdown[pdf])
+-- .gitignore
+-- README.md        <- Este archivo
```

---

## Requisitos

- **Python 3.10 o superior** — [Descargar](https://www.python.org/downloads/)
- Sin internet necesario para la conversion (las dependencias se instalan una sola vez)

### Dependencias

```
markitdown[pdf]
```

Incluye `pdfminer.six` y `pdfplumber` para extraccion de texto de PDFs.

---

## Limitaciones conocidas

- **PDFs escaneados (imagenes):** MarkItDown extrae texto digital, no hace OCR. Si el PDF es una imagen escaneada sin capa de texto, el resultado estara vacio. Para esos casos se necesita un motor OCR adicional (Tesseract, EasyOCR, Azure Document Intelligence).
- **Tablas complejas:** Las tablas se convierten a Markdown lo mejor posible, pero formatos muy irregulares pueden perder estructura.
- **Las E-14 (actas electorales):** Al ser formularios escaneados con texto manuscrito, la conversion de texto digital puede ser parcial. Esta herramienta es util para PDFs con texto digital (resoluciones, circulares, documentos generados por computador).

---

## Notas metodologicas

- Los PDFs originales nunca se modifican; la herramienta solo lee de `pdfs/` y escribe en `md/`.
- Si un PDF falla, el resto del lote continua procesandose. El resumen indica cuantos tuvieron exito, cuantos estaban vacios y cuantos fallaron.
- La conversion es 100% local. Ningun archivo sale de tu equipo.

---

Esta herramienta es parte del **Proyecto Analizador Electoral** - auditoria ciudadana de actas electorales colombianas 2026.

Repositorio: https://github.com/NucleuxCorp/Analizador-Electoral/