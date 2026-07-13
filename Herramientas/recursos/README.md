# Recursos — Índices públicos

Datos de referencia usados por las herramientas de auditoría ciudadana de este repositorio, o publicados como insumo para quien quiera construir sus propias verificaciones.

---

## `resoluciones_jurados_index.json`

Índice de las resoluciones oficiales de designación de jurados de votación, publicadas por la Registraduría Nacional para las elecciones presidenciales de Colombia 2026.

**1,151 entradas** — una por municipio, cubriendo 33 departamentos.

### Estructura

```json
{
  "departamento": "AMAZONAS",
  "municipio": "EL ENCANTO",
  "ruta": "Res_El Encanto_Amazonas.pdf",
  "url": "https://wapp.registraduria.gov.co/electoral/2026/presidente-de-la-republica/documentos/resoluciones_jurados/AMAZONAS/Res_El%20Encanto_Amazonas.pdf",
  "local_path": "Resoluciones Jurados/AMAZONAS/Res_El Encanto_Amazonas.pdf"
}
```

| Campo | Descripción |
|---|---|
| `departamento` | Departamento colombiano |
| `municipio` | Municipio dentro del departamento |
| `ruta` | Nombre de archivo del PDF en el servidor de la Registraduría |
| `url` | URL pública directa al PDF de la resolución |
| `local_path` | Ruta relativa sugerida para guardar el archivo localmente |

### Uso

Cada `url` es un PDF público servido directamente por la Registraduría — se puede descargar sin autenticación. Sirve como base para: auditar que la lista de jurados designados coincida con quienes efectivamente firmaron el acta E-14 de una mesa, o para construir un descargador propio iterando el índice.

**Nota:** este índice es un snapshot tomado en la fecha de generación — la Registraduría puede actualizar o agregar resoluciones después.
