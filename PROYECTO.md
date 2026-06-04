# Analizador de Elecciones Colombia 2026

Sistema de recolección y análisis de Actas E-14 de las elecciones presidenciales colombianas 2026, orientado a la detección de irregularidades electorales.

---

## Objetivo

Recolectar todos los documentos E-14 (actas de escrutinio por mesa de votación) publicados por la Registraduría Nacional del Estado Civil, y procesarlos con IA para identificar patrones anómalos que puedan indicar fraude electoral.

---

## Fuentes de Datos

| Módulo | Plataforma | URL | Estado |
|---|---|---|---|
| E-14 Delegados | Divulgación E-14 | divulgacione14presidente.registraduria.gov.co | En desarrollo |
| E-14C Oficial | Consulta Escrutinios | escrutiniospresidente2026.registraduria.gov.co | **Completo** |

### E-14 de Delegados
Actas levantadas por los delegados de la Registraduría en cada mesa, durante el día de votación. Son la primera fuente de datos disponible.

### E-14C Oficial (Escrutinio)
Actas oficiales del proceso de escrutinio, consolidadas por las comisiones escrutadoras. Son los documentos legalmente vinculantes del resultado electoral.

---

## Arquitectura

```
Analizador de Elecciones/
├── main.py                          # CLI principal
├── requirements.txt
├── src/
│   ├── modules/
│   │   ├── e14/                     # Módulo E-14 Delegados (Playwright)
│   │   │   ├── scraper.py           # Orquestador: navega dept→mpio→zona→puesto→mesas
│   │   │   ├── form_handler.py      # Interacción con formulario Angular (app-custom-select)
│   │   │   ├── url_extractor.py     # Captura URL del PDF por intercepción de red
│   │   │   └── models.py
│   │   └── e14c/                    # Módulo E-14C Oficial (HTTP puro)
│   │       └── scraper.py           # Recolector async: index.json → mesas JSON → PDF URLs
│   └── utils/
│       ├── storage.py               # JSONL + SQLite checkpoint
│       ├── rate_limiter.py
│       └── logger.py
└── data/
    ├── departamentos.json           # 34 departamentos con IDs internos
    ├── divipole.json                # Jerarquía electoral completa (dept/mpio/zona/puesto)
    ├── e14c_index.json              # Índice maestro: 14,438 puestos → archivos actuales
    ├── e14c_urls.jsonl              # 86,929 URLs de PDFs E-14C recolectadas
    ├── e14c_progress.json           # Checkpoint del colector E-14C
    └── urls/
        └── e14_urls.jsonl           # URLs de PDFs E-14 Delegados (en construcción)
```

---

## Comandos CLI

```bash
# Recolectar URLs de E-14C (sin browser — puro HTTP)
python main.py collect-e14c
python main.py collect-e14c --concurrent 50   # más velocidad

# Recolectar URLs de E-14 Delegados (requiere browser visible)
python main.py collect-urls --departamento AMAZONAS --visible
python main.py collect-urls --delay 2000

# Estado del scraping E-14 Delegados
python main.py status
python main.py status --pending        # ver puestos con 0% publicado

# Debug — inspeccionar el formulario del sitio
python main.py discover

# Reset completo
python main.py reset
```

---

## Patrón de APIs Descubiertas (E-14C)

El sitio de escrutinios expone una API REST pública sin autenticación:

```
# Índice maestro — mapea cada puesto a su archivo JSON actual
GET /data/index.json
→ { "data/esc/v1/actas-documentos/001/{dept}/{mpio}/{zona}/{puesto}/mesas/": "filename.json" }

# Lista de mesas de un puesto
GET /data/esc/v1/actas-documentos/001/{dept}/{mpio}/{zona}/{puesto}/mesas/{filename}.json
→ [{ "numero": 1, "digitalizado": 1, "escrutado": true, "nombre_archivo": "/docs/E14/..." }]

# PDF del acta
GET /docs/E14/{dept}/{mpio}/{zona}/{puesto}/E14_PRE_{dept}_{mpio}_{mpio}_{zona}_{puesto}_{mesa:003}_{code}.pdf
```

Los timestamps en los nombres de archivo son únicos por puesto y se actualizan cuando la Registraduría sube nuevos datos. Ejecutar `collect-e14c` periódicamente captura los nuevos documentos automáticamente (checkpoint resumible).

---

## Resultados Actuales (31 mayo 2026)

| Métrica | Valor |
|---|---|
| URLs E-14C recolectadas | **86,929** |
| Mesas escrutadas | 86,590 (99.6%) |
| Puestos cubiertos | 13,673 / 14,438 |
| Departamentos con datos | 33 de 34 |
| Tamaño del índice | 33 MB |

---

## Roadmap

### Fase 1 — Recolección (En curso)
- [x] Recolectar todos los URLs de E-14C
- [x] Recolectar departamentos y jerarquía electoral
- [ ] Completar módulo E-14 Delegados
- [ ] Descargador de PDFs en batch

### Fase 2 — Extracción
- [ ] OCR de los PDFs (extracción de votos por candidato, mesa, puesto)
- [ ] Parser estructurado del formulario E-14C
- [ ] Base de datos de resultados por mesa

### Fase 3 — Análisis (Detección de Fraude)
- [ ] Comparación E-14 Delegados vs E-14C Oficial por mesa
- [ ] Detección de anomalías estadísticas (distribución de Benford, outliers)
- [ ] Identificación de mesas con discrepancias entre actas
- [ ] Dashboard de alertas por departamento/municipio

---

## Stack Tecnológico

| Componente | Tecnología |
|---|---|
| Lenguaje | Python 3.12 |
| Browser automation | Playwright 1.44 (async) |
| HTTP concurrente | asyncio + urllib |
| Storage | SQLite (aiosqlite) + JSONL |
| OCR (próximo) | Por definir |
| IA/ML (próximo) | Por definir |
