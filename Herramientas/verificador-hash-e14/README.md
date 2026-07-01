# Verificador de Hash E-14

Herramienta ciudadana para verificar la integridad de actas E-14 de las elecciones presidenciales colombianas 2026. Actualmente soporta actas **E-14C**.

---

## Que hace

Compara los PDFs que tienes contra un indice de huellas digitales SHA-256 generado a partir de los archivos descargados directamente del servidor de la Registraduria el **2026-06-30**. No hace conexiones a internet.

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

## Estructura de archivos

```
verificador-e14c/
├── verificador_e14c.py      <- Codigo fuente (auditoria)
├── verificador_e14c.exe     <- Ejecutable compilado (sin Python)
├── hash_index_e14c.json     <- Indice de huellas digitales
├── ejecutar.bat             <- Lanzador para usuarios con Python
├── compilar.bat             <- Compilador desde fuente (auditoria)
├── README.md                <- Este archivo
└── PDFs-V2/                 <- Carpeta donde colocar tus PDFs
```

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
