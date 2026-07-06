# Metodología — Análisis de Actas E-14 (Primera y Segunda Vuelta 2026)

> Documento vivo. Se actualiza a medida que se completa y entiende el flujo completo.

---

## 1. Contexto

Las elecciones presidenciales colombianas 2026 generan **actas E-14** por mesa de votación en cada vuelta electoral. Cada acta registra los votos por candidato, votos en blanco, nulos, no marcados, e incinerados. La Registraduría Nacional publica estas actas en formato PDF a través de tres plataformas por vuelta:

- **Plataforma de escrutinio oficial (E-14C)** — actas digitalizadas y escrutadas por la comisión escrutadora.
- **Plataforma de transmisión (E-14T)** — actas dictadas por teléfono para el preconteo informativo en tiempo real.
- **Plataforma de delegados (E-14D)** — actas escaneadas en el puesto de votación la noche electoral.

> E-14T y E-14D son dos plataformas distintas con dominios separados, pero comparten la misma arquitectura técnica (mismo índice `allTransmissionCodes.json`, mismo `expectedName` por mesa) y se confunden frecuentemente como una sola. La diferencia es la copia física que publican: Transmisión publica la copia dictada por teléfono, Delegados publica la copia escaneada en el puesto.

El sistema cubre **ambas vueltas**:

| Vuelta | Estado | PDFs disponibles | Plataforma E-14C |
|--------|--------|-----------------|-----------------|
| **Primera vuelta** | Pausada — segunda vuelta es prioritaria | ~26,194 PDFs en `data/e14_primera/` | `escrutiniospresidente2026.registraduria.gov.co` |
| **Segunda vuelta** | Descarga completa — análisis transversal completo (34/34 depts, 115,741 mesas) — upload a Supabase en curso | 118,543 PDFs E14C únicos en `E:/Nucleux/.../Data/e14_segunda/E14C/` · `E:/e14_segunda/E14T/` · `E:/e14_segunda/E14D/` (unidad externa) | `escrutinios2vueltapresidente2026.registraduria.gov.co` |

El objetivo del sistema es detectar posibles irregularidades: tachones, enmiendas, errores aritméticos, y patrones anómalos en los datos de votación. Los modelos de OCR y detección se entrenan con datos de ambas vueltas.

### Herramientas ciudadanas complementarias

Esta metodología es un marco flexible que puede coexistir con iniciativas de terceros orientadas a la misma fuente de datos. Una herramienta relevante en el ecosistema es:

| Herramienta | URL | Descripción |
|-------------|-----|-------------|
| ConteoCol | https://conteocol.lat/ | Plataforma ciudadana de conteo y verificación de actas E-14. Iniciativa independiente que opera sobre los mismos documentos públicos de la Registraduría. |
| Colombia Elige | https://colombiaelige.co/ | Plataforma de análisis estadístico electoral. Enfoque en visualización y análisis de resultados electorales colombianos. |

La existencia de múltiples iniciativas independientes sobre la misma fuente primaria fortalece la verificabilidad del proceso electoral — cada una aporta una perspectiva metodológica distinta sobre los mismos datos.

---

## 2. Fuentes de datos

Los jurados de votación diligencian a mano **tres copias físicas idénticas** del formulario E-14 el mismo día de la elección. Aunque las llena el mismo jurado en la misma mesa, son documentos independientes — pueden presentar diferencias por tachaduras o errores humanos distintos en cada copia. Esto hace que la cross-validación entre las tres versiones tenga valor forense real.

| Copia | Destino físico | Cuándo se digitaliza | Plataforma primera vuelta | Plataforma segunda vuelta |
|---|---|---|---|---|
| **E14C** (Claveros) | Sobre sellado → arca triclave → sede comisión escrutadora | Días después, en la comisión | `escrutiniospresidente2026.registraduria.gov.co` | `escrutinios2vueltapresidente2026.registraduria.gov.co` |
| **E14D** (Delegados) | Se escanea en el puesto de votación la misma noche | Noche electoral | `divulgacione14presidente.registraduria.gov.co` | `e14segundavueltapresidente.registraduria.gov.co` |
| **E14T** (Transmisión) | Se dicta por teléfono para el preconteo informativo | Noche electoral | `divulgacione14presidentet.registraduria.gov.co` | `e14segundavueltapresidentet.registraduria.gov.co` |

> El E14C es el documento con **máximo valor legal y probatorio** — es la base del escrutinio oficial y la referencia para resolver reclamaciones. El E14D y E14T sirven para transparencia inmediata y preconteo informativo.

### 2.1 E14C — Claveros (escrutinio oficial)

La estructura de la API es idéntica en ambas vueltas; sólo cambia el dominio base:

| Vuelta | BASE URL |
|--------|---------|
| Primera vuelta | `https://escrutiniospresidente2026.registraduria.gov.co` |
| Segunda vuelta | `https://escrutinios2vueltapresidente2026.registraduria.gov.co` |

```
# Aplica a cualquiera de los dos dominios según la vuelta

GET /data/index.json
  → { "data/esc/v1/actas-documentos/001/{dept}/{mpio}/{zona}/{puesto}/mesas/": "filename.json" }

GET /data/esc/v1/actas-documentos/001/{dept}/{mpio}/{zona}/{puesto}/mesas/{filename}.json
  → [{ "numero": 1, "digitalizado": 1, "escrutado": true, "nombre_archivo": "/docs/E14/..." }]

GET BASE + nombre_archivo  →  PDF del acta (~1.8–2.2 MB)
```

**Particularidades:**
- `data/index.json` se actualiza dinámicamente — puede tener menos entradas entre sesiones.
- Campos clave por mesa: `digitalizado` (0/1), `escrutado` (true/false), `nombre_archivo` (ruta al PDF).
- El `nombre_archivo` incluye un timestamp que cambia cuando la Registraduría re-sube el acta.

#### Estructura del nombre de archivo E14C

El `nombre_archivo` sigue el patrón:

```
/docs/E14/{dept}/{mpio}/{zona}/{puesto}/E14_PRE_{dept}_{mpio}_{zona_3d}_{field4}_{puesto}_{mesa_3d}_{COMISION}.pdf
```

El sufijo `{COMISION}` es el **código de la comisión escrutadora** — el nivel más específico de la jerarquía en `comisiones.json`:
- Si el municipio tiene auxiliares → código de auxiliar (`5XXX`/`6XXX`)
- Si no tiene auxiliares (ej: corregimientos) → código de comisión municipal (`2XXX`/`3XXX`)

**Endpoints de comisiones por vuelta:**

| Vuelta | URL |
|--------|-----|
| Segunda | `https://escrutinios2vueltapresidente2026.registraduria.gov.co/data/esc/v1/comision/comisiones_{timestamp}.json` |
| Primera | `https://escrutiniospresidente2026.registraduria.gov.co/data/esc/v1/comision/comisiones_{timestamp}.json` |

Archivo local: `data/comisiones_segunda.json` (172 KB, 33 depts, 1107 comisiones municipales).

**Mapeo url_dept → comisión departamental** (33 depts, verificado):

| url_dept | Departamento | commission_key |
|----------|-------------|----------------|
| 01 | ANTIOQUIA | 1000 | 03 | ATLANTICO | 1001 | 05 | BOLIVAR | 1002 |
| 07 | BOYACA | 1003 | 09 | CALDAS | 1004 | 11 | CAUCA | 1005 |
| 12 | CESAR | 1006 | 13 | CORDOBA | 1007 | 15 | CUNDINAMARCA | 1008 |
| 16 | BOGOTA D.C. | 1009 | 17 | CHOCO | 1010 | 19 | HUILA | 1011 |
| 21 | MAGDALENA | 1012 | 23 | NARIÑO | 1013 | 24 | RISARALDA | 1014 |
| 25 | NORTE DE SANTANDER | 1015 | 26 | QUINDIO | 1016 | 27 | SANTANDER | 1017 |
| 28 | SUCRE | 1018 | 29 | TOLIMA | 1019 | 31 | VALLE | 1020 |
| 40 | ARAUCA | 1021 | 44 | CAQUETA | 1022 | 46 | CASANARE | 1023 |
| 48 | LA GUAJIRA | 1024 | 50 | GUAINIA | 1025 | 52 | META | 1026 |
| 54 | GUAVIARE | 1027 | 56 | SAN ANDRES | 1028 | 60 | AMAZONAS | 1029 |
| 64 | PUTUMAYO | 1030 | 68 | VAUPES | 1031 | 72 | VICHADA | 1032 |
| 88 | CONSULADOS | — (sin comisión) |

> **Bache no resuelto — `{field4}` (2026-07-03 01:08 UTC):**** el campo entre `{zona_3d}` y `{puesto}` en el nombre de archivo no sigue una regla determinista conocida. Casos observados:
>
> | URL (path) | zona | field4 | ¿Coincide? |
> |---|---|---|---|
> | `/docs/E14/29/001/11/03/E14_PRE_29_001_011_11_03_020_6568.pdf` | 11 | 11 | ✓ igual a zona |
> | `/docs/E14/01/001/90/02/E14_PRE_01_001_090_00_02_049_5045.pdf` | 90 | 00 | ✗ |
> | `/docs/E14/27/001/04/01/E14_PRE_27_001_004_03_01_014_6425.pdf` | 04 | 03 | ✗ (zona−1) |
>
> Hipótesis: puede ser la zona escrutadora (distinta a la zona de votación) o un subcódigo de la comisión auxiliar. Mientras no se resuelva, el método estático requiere descargar los mesas JSON para obtener el `nombre_archivo` real. Ver `scripts/e14/e14c/build_urls.py`.

> **Nota de seguridad — SSL bypass (2026-07-02 00:00 UTC):** todos los scripts usan `ssl.CERT_NONE` / `verify=False`. La causa original puede ser un certificado de CA colombiana que el servidor usó en algún momento (históricamente no incluida en el bundle estándar), o un proxy corporativo durante el desarrollo. Con el bypass activo no es posible distinguir el servidor real de un MITM, y la verificación de integridad de la sección 2.5 garantiza solo consistencia entre descargas, no autenticidad del origen. **Pendiente verificar sin VPN:** si el servidor actualmente usa una CA global (DigiCert), el bypass puede eliminarse de todos los scripts.

### 2.2 E14D (Delegados) y E14T (Transmisión)

La copia de Delegados se escanea en el puesto de votación la misma noche electoral y se publica en el portal de preconteo informativo. La copia de Transmisión se dicta por teléfono para alimentar el preconteo en tiempo real. Ambas se nombran con un **identificador hexadecimal de 64 caracteres** (ej: `d07d842f...bb1bd.pdf`) asignado por el campo `expectedName` en el JSON de la Registraduría — longitud de SHA256, pero el sistema no expone qué se hashea ya que el nombre viene pre-asignado.

Ambas plataformas (E14T y E14D) sirven el **mismo índice** `allTransmissionCodes.json` — verificado: lógicamente idéntico en los dos dominios. El campo `expectedName` es el mismo para la misma mesa en E14T y E14D, pero las URLs resultantes apuntan a dominios distintos y los PDFs descargados son documentos físicamente diferentes (copia de Transmisión vs copia de Delegados).

El índice está guardado localmente en `data/allTransmissionCodes_segunda_index.json` (122,019 entradas, captura 2026-06-30). La constante `TOTAL_UNIVERSE = 122_020` en el código refleja el universo oficial CNE — la diferencia de 1 mesa respecto al índice no está explicada por la plataforma. La captura temprana del 2026-06-21 se conserva en `data/allTransmissionCodes_segunda.json` (1,183 status11 + 217 status3).

**Endpoints del índice por vuelta y tipo:**

| Vuelta | Tipo | URL |
|--------|------|-----|
| Primera | E14T | `https://divulgacione14presidentet.registraduria.gov.co/assets/temis/divipol_json/allTransmissionCodes.json` |
| Primera | E14D | `https://divulgacione14presidente.registraduria.gov.co/assets/temis/divipol_json/allTransmissionCodes.json` |
| Segunda | E14T | `https://e14segundavueltapresidentet.registraduria.gov.co/assets/temis/divipol_json/allTransmissionCodes.json` |
| Segunda | E14D | `https://e14segundavueltapresidente.registraduria.gov.co/assets/temis/divipol_json/allTransmissionCodes.json` |

> Verificado: E14T y E14D de segunda vuelta sirven contenido lógicamente idéntico (mismo `expectedName` por mesa). Se asume el mismo comportamiento para primera vuelta.


**Estado segunda vuelta — capturado al 2026-06-21:**

| Status | Descripción | Cantidad total | De consulados (dept 88) |
|--------|-------------|---------------|------------------------|
| `status11` | PDF subido al servidor | 1,377 | 661 |
| `status3` | Aún sin subir | 23 | 23 |

> **Nota metodológica (2026-06-21):** Al momento de la captura, 23 actas figuraban como `status3` (sin subir al servidor). Estas actas existen en el índice pero no tienen PDF disponible para descarga.

### 2.3 Consulados (dept 88)

Los consulados representan actas de votantes en el exterior. Evolución documentada en la plataforma de transmisión (E14T/E14D):

| Fecha captura | Total actas | `status11` (subidas) | `status3` (sin subir) |
|---|---|---|---|
| 2026-06-21 | 684 | 661 | 23 |
| 2026-06-30 | **3,670** | 3,670 | **0** |

- Al 2026-06-30 todas las actas de consulados están subidas en la plataforma de transmisión y el volumen aumentó 5× respecto a la captura anterior.
- **Nunca descargados localmente** — no existe carpeta `E14C/CONSULADOS/` ni equivalente.

**Hallazgo (2026-06-24): E14C de consulados aún no publicado en esa fecha**

Como toda mesa, los consulados generan tres copias físicas (E14C, E14D, E14T). Sin embargo, al momento del scrape el E14C de consulados no estaba disponible en la plataforma de escrutinio:

| Aspecto | Resultado |
|---|---|
| Puestos de consulados en el índice E14C | 949 |
| Entradas en `e14c_sv_urls.jsonl` (scrapeado 2026-06-24) | 0 |
| Respuesta al consultar los endpoints de mesas | HTML (Angular SPA) — sin datos |

El índice registra 949 puestos pero los endpoints devuelven la página Angular sin datos. Esto es consistente con el proceso: el E14C viaja físicamente al arca triclave y se digitaliza en la comisión escrutadora días después — es posible que al 2026-06-24 los claveros de consulados aún no hubieran completado ese proceso. El E14D y E14T de consulados sí estaban disponibles en la plataforma de transmisión (ver tabla de evolución arriba).

---

## 2.4 Observación: variabilidad en los datos de la plataforma de transmisión

Durante el proceso de recolección se realizaron dos consultas al mismo endpoint de la plataforma de transmisión en fechas distintas. Los resultados obtenidos difieren de manera significativa:

| Métrica | 2026-06-21 | 2026-06-30 | Variación |
|---|---|---|---|
| Total nodos | 1,400 | 122,019 | +8,614% |
| Consulados (dept 88) | 684 | 3,670 | +437% |
| Actas sin subir (`status3`) | 23 | 0 | −100% |

**Capturas conservadas:**
- `data/allTransmissionCodes_segunda.json` — consulta del 2026-06-21
- `data/allTransmissionCodes_segunda_2026-06-30.json` — consulta del 2026-06-30

La plataforma no dispone de mecanismos de versionado, registro de cambios ni trazabilidad histórica accesible públicamente. El estado que devuelve el endpoint corresponde al momento exacto de la consulta, sobre una base de datos que puede modificarse sin dejar constancia visible. Esto genera las siguientes implicaciones para el análisis:

1. Los datos disponibles en la plataforma en un momento dado no son verificables retroactivamente desde la misma fuente.
2. La incorporación de aproximadamente 118,000 nodos adicionales entre el 21 y el 30 de junio no cuenta con registro público que explique el origen, fecha o procedimiento de dicha incorporación.
3. Las 23 actas que figuraban como `status3` (pendientes de subir) al 21 de junio no aparecen en la consulta del 30 de junio, sin que exista constancia del momento o la forma en que fueron procesadas.

Por lo anterior, las capturas realizadas en este proyecto constituyen el único registro disponible del estado histórico de la plataforma en esas fechas.

---

## 2.5 Verificación de integridad byte a byte — E14C

### Metodología

Los PDFs E14C fueron descargados de la plataforma oficial de escrutinio aproximadamente el 22 de junio de 2026. Con el fin de detectar modificaciones posteriores a esa descarga, se ejecuta una comparación byte a byte entre cada archivo local y la versión actualmente servida por la Registraduría.

El procedimiento es el siguiente:

1. Por cada PDF local, se construye la URL del servidor a partir del nombre del archivo.
2. Se descarga la versión actual del servidor.
3. Se calcula el SHA256 de ambas copias (local y remota).
4. Si los hashes coinciden, el archivo no fue modificado — se registra como `OK` en el checkpoint. El archivo local se conserva en disco.
5. Si los hashes difieren, se registra como `DIF` en el checkpoint. El archivo local se conserva en disco como evidencia de modificación.

**Script:** `verify_e14c.py --full <carpeta>`  
**Checkpoint:** `<carpeta>/.verify_checkpoint.json` (el script recibe la carpeta como argumento posicional; el checkpoint queda dentro de ella)

### Interpretación de resultados

| Estado | Significado |
|--------|-------------|
| `OK` | Hash local == hash servidor — archivo sin cambios. Conservado en disco, registrado en checkpoint |
| `DIF` | Hash local ≠ hash servidor — modificación confirmada. La versión del servidor se guarda automáticamente en `dif_evidence/{nombre}_SERVER.pdf`. El archivo local no se toca |
| `ERR` | Error de red durante la verificación — se reintenta en la próxima corrida |

Los archivos marcados como `DIF` constituyen evidencia de que el contenido del acta en la plataforma oficial fue alterado entre la fecha de descarga original y la fecha de verificación.

> **Nota sobre alcance:** La comparación cubre los PDFs E14C disponibles localmente (~90,838 archivos). Los 27,509 PDFs no descargados originalmente no pueden verificarse por esta vía, ya que no existe copia local de referencia.

### Hallazgo (2026-06-30): Re-subidas confirmadas por divergencia de tamaño

Durante el escaneo HEAD (modo de verificación por tamaño, sin descarga completa) se identificaron **5 PDFs con tamaño remoto significativamente distinto al archivo local**. Para confirmar que las diferencias correspondían a contenido real y no a errores de red, se descargó la versión actual del servidor para cada uno y se comparó visualmente con la copia local.

La comparación visual confirmó que **son actas distintas**: el contenido de los PDFs descargados del servidor difiere del contenido original almacenado localmente. No se trata de errores de transmisión ni redirecciones — el `Content-Type` fue `application/octet-stream` y ambas copias tienen cabecera PDF válida (`%PDF`).

| Archivo | Dept / Mpio / Zona / Puesto / Mesa | Tamaño local | Tamaño servidor | Variación |
|---|---|---|---|---|
| `01_001_90_02_..._049_5045.pdf` | 01 / 001 / 90 / 02 / 049 | 626,219 B | 5,538,253 B | **+780%** |
| `13_031_99_11_..._002_5719.pdf` | 13 / 031 / 99 / 11 / 002 | 1,389,518 B | 8,323,690 B | **+499%** |
| `27_071_00_00_..._006_2812.pdf` | 27 / 071 / 00 / 00 / 006 | 1,018,357 B | 3,115,524 B | **+206%** |
| `27_001_04_01_..._014_6425.pdf` | 27 / 001 / 04 / 01 / 014 | 1,251,453 B | 1,281,672 B | +2.4% |
| `54_007_00_00_..._003_3091.pdf` | 54 / 007 / 00 / 00 / 003 | 1,206,741 B | 1,223,780 B | +1.4% |

La versión del servidor se conserva en `dif_evidence/{nombre}_SERVER.pdf` dentro de la carpeta del PDF. El archivo local original no se modifica.

**Nota metodológica (2026-07-02 00:00 UTC):** El script `verify_e14c.py` fue corregido para clasificar SIZE_DIFF como `dif` (evidencia, no se reintenta) en lugar de `err` (error transitorio de red, se reintenta en cada corrida). Un archivo cuyo servidor responde correctamente pero con tamaño o hash distinto al local es una divergencia confirmada, no un fallo de conexión.

### Resultado de verificación completa byte a byte (2026-06-30)

Ejecutado el **2026-06-30** con `verify_e14c.py --full <carpeta>` sobre los PDFs E14C disponibles localmente:

| Estado | Cantidad | Acción |
|--------|----------|--------|
| `OK` — hash SHA256 idéntico | **83,498** | Conservados en disco; registrados en checkpoint |
| `DIF` — hash SHA256 diferente | **20** | Conservados en disco como evidencia de modificación |
| `ERR` — error irrecuperable | **10** | No verificables; detalle abajo |

**Total verificado: 83,538 PDFs.** El corpus disponible localmente al momento de la verificación era inferior al total descargado originalmente (~90,838) porque una fracción de los archivos ya había sido procesada o purgada en corridas previas de verificación por tamaño (HEAD mode).

**Desglose de los 10 ERR irrecuperables:**

| Tipo | Cantidad | Causa |
|------|----------|-------|
| Archivos con nombre hash (`{sha256}.pdf`) en subcarpeta `transmission/` | 5 | La convención de nombre no permite reconstruir la URL jerárquica E14C — pertenecen a un pipeline distinto |
| Archivos con nombre estructurado E14C pero URL inaccesible | ~5 | La Registraduría no responde para esas URLs específicas (actas posiblemente retiradas del servidor) |

Los 10 ERR no corresponden a fallos de red transitorios — fueron reintentados en múltiples corridas y el resultado fue consistente. No constituyen evidencia de modificación, sino de inaccesibilidad persistente.

**Estado final del disco:** Todos los archivos verificados se conservan en disco. El checkpoint registra cuáles son OK, DIF o ERR. Los 20 DIF son evidencia de modificación posterior a la descarga original.

### Verificación de seguimiento (2026-07-02)

Para las 20 mesas identificadas como DIF el 2026-06-30, se descargó nuevamente la versión actual del servidor el **2026-07-02** con el script `fetch_dif_server_copies.py`. Objetivo: obtener las versiones del servidor como evidencia y confirmar el estado actual de cada acta.

| Estado | Cantidad | Significado |
|--------|----------|-------------|
| `STILL_DIF` — sigue diferente | **18** | El servidor continúa sirviendo una versión distinta a la descargada el 22-jun. Modificación activa y persistente |
| `NOW_EQUAL` — ahora igual | **2** | El servidor coincide con la copia local al 2026-07-02. Fueron modificadas entre el 22-jun y el 30-jun, y posteriormente revertidas sin trazabilidad pública |

Las 2 actas con `NOW_EQUAL`:

| Mesa | Ubicación |
|------|-----------|
| `16_001_15_06_..._025_6068.pdf` | Bogotá D.C. / zona 15 / puesto 06 / mesa 025 |
| `29_001_11_03_..._020_6568.pdf` | Tolima / Ibagué / zona 11 / puesto 03 / mesa 020 |

Para estas dos actas, la modificación y la reversión ocurrieron sin registro público visible. La copia del servidor al momento de la reversión se conserva en `dif_evidence/{nombre}_SERVER.pdf` como constancia de que el contenido fue distinto al menos en algún momento entre el 22-jun y el 02-jul.

Las 18 `STILL_DIF` tienen su versión del servidor guardada en `dif_evidence/{nombre}_SERVER.pdf` junto al PDF local original. Resultados completos en `data/dif_server_fetch_results.json`.

---

## 2.6 Análisis de re-subidas por clase de acta (2026-06-30)

Script: `scripts/detect_reuploads.py`  
Resultados: `data/analysis_segunda_vuelta/reupload_detection.json`

### Hallazgo 1: Sin re-subidas en la plataforma de transmisión

Comparando las 1,400 mesas presentes en el snapshot del 2026-06-21 contra el snapshot del 2026-06-30:

| Métrica | Valor |
|---|---|
| Mesas comparables (presentes en ambos snapshots) | 1,400 |
| `expectedName` idéntico (no re-subido) | 1,400 |
| `expectedName` diferente (re-subido) | **0** |

Las 120,619 mesas que aparecieron únicamente en el snapshot del 2026-06-30 no pueden compararse con el anterior (no existían en el registro del 21 de junio).

### Hallazgo 2: E14C — 1 acta con versión revertida

Comparando 90,838 PDFs E14C descargados localmente (~22 de junio) contra el índice del servidor (`e14c_sv_urls.jsonl`, scrapeado el 24 de junio):

| Métrica | Valor |
|---|---|
| Mesas comparables | 90,838 |
| Sufijo idéntico (sin cambio) | 90,837 |
| Sufijo diferente (re-subida o revert) | **1** |

El único caso anómalo:

| Campo | Valor |
|---|---|
| Mesa | dept=13 (Córdoba), mpio=022 (Lorica), zona=99, puesto=05, mesa=3 |
| Versión descargada (~22-jun) | `5705` |
| Versión en índice servidor (24-jun) | `2422` |

El servidor muestra una versión con sufijo **menor** (2422 < 5705), lo que indica que el acta fue **revertida a una versión anterior** entre el momento de descarga y el scrape del índice. Es el único caso de 90,838 comparables.

> **Nota metodológica (2026-07-02 00:00 UTC):** El sufijo numérico al final del nombre de archivo E14C identifica la versión del acta en el sistema de escrutinio. Un sufijo mayor indica una versión más reciente. La reversión de esta acta específica sin trazabilidad pública es consistente con el patrón de mutabilidad silenciosa documentado en la sección 2.4.

---

## 3. Pipeline de procesamiento — Segunda vuelta

El pipeline aplica a los **tres tipos de acta** que los jurados diligencian por mesa. Las tres copias las llenan los mismos jurados el mismo día:

| Tipo | Copia física | Destino | Plataforma segunda vuelta |
|---|---|---|---|
| **E14C** | Claveros — original, máximo valor legal | Arca triclave → comisión escrutadora → escaneo | `escrutinios2vueltapresidente2026.registraduria.gov.co` |
| **E14D** | Delegados — copia para preconteo web | Escaneo en puesto de votación la noche electoral | `e14segundavueltapresidente.registraduria.gov.co` |
| **E14T** | Transmisión — copia para preconteo por teléfono | Dictado por teléfono la noche electoral | `e14segundavueltapresidentet.registraduria.gov.co` |


```
┌─────────────────────────────────────────────────────────────┐
│                    FASE 1 — DESCARGA                        │
├──────────────┬──────────────────┬──────────────────────────┤
│    E14C      │      E14T        │          E14D            │
│  API REST    │  allTransmission │    allTransmission       │
│  jerárquica  │  Codes + routes  │    Codes + routes        │
│      ↓       │        ↓         │           ↓              │
│ filtro:      │  status = subido │   status = subido        │
│ digital=1    │                  │                          │
│ escrutado=T  │                  │                          │
│      ↓       │        ↓         │           ↓              │
│ PDF nombrado │  PDF nombrado    │   PDF nombrado           │
│ por versión  │  por hex64       │   por hex64              │
│              │  (expectedName)  │   (expectedName)         │
└──────────────┴──────────────────┴──────────────────────────┘
         ↓                ↓                    ↓
E:/Nucleux/tools/Analizador de Elecciones/Data/e14_segunda/E14C/
                 E:/Nucleux/tools/Analizador de Elecciones/Data/e14_segunda/E14T/
                                      E:/Nucleux/tools/Analizador de Elecciones/Data/e14_segunda/E14D/
```

### Fase 1 — Descarga

**E14C:**
El colector consulta el índice maestro (`data/index_e14c_segunda.json`, 13,489 puestos). Por cada puesto, descarga el JSON de mesas y filtra las que cumplen:
- `digitalizado = 1` — acta escaneada y cargada
- `escrutado = true` — revisada por la comisión escrutadora

**E14D / E14T:**
El colector consulta el endpoint `allTransmissionCodes` que devuelve el árbol de mesas con estado de transmisión. Se descargan las actas con `status11` (PDF disponible en servidor). El nombre del archivo es un identificador hex de 64 chars (`expectedName`) pre-asignado por la Registraduría.

| | E14C | E14T / E14D |
|---|---|---|
| **Nombre archivo** | `{dept}_{mpio}_{zona}_{puesto}_E14_PRE_..._{version}.pdf` | `{hex64}.pdf` (campo `expectedName` de la Registraduría) |
| **Script descarga** | `download_antioquia.py` | `scripts/e14/e14t/turbo_e14.py --source e14t\|e14d` |
| **Checkpoint** | `data/e14c_segunda_progress.json` | por dept, resumible |
| **Storage** | `E:/Nucleux/.../Data/e14_segunda/E14C/` | `E:/Nucleux/.../Data/e14_segunda/E14T/` · `E:/Nucleux/.../Data/e14_segunda/E14D/` |
| **Escala (al 2026-07-04)** | 118,543 PDFs únicos (`MESAS_ALL_THREE`) | ~122,019 PDFs c/u |

### Fase 2 — Render

```
PDF → PyMuPDF → fitz.Matrix(300/72) → pixmap → numpy BGR
    ↓
Escala de grises (cv2.cvtColor, COLOR_BGR2GRAY)
```

Se renderiza a 300 DPI con la matriz de escala `300/72` sobre la base de 72 DPI del PDF. Las dimensiones y páginas difieren por vuelta:

| Parámetro | Primera vuelta | Segunda vuelta |
|---|---|---|
| Dimensiones resultado | 1260 × 3897 px (calibrado en AMAZONAS/LETICIA) | ~ancho × 3500 px (`PAGE_HEIGHT_REF = 3500`) |
| Páginas del formulario | 3 (p0: candidatos 1-7 · p1: candidatos 8-13 + totales · p2: firmas) | 1 (p0 única) |
| Formato interno | NumPy array BGR → escala de grises | NumPy array BGR → escala de grises |
| Librería | PyMuPDF (`fitz`) + OpenCV (`cv2`) | PyMuPDF (`fitz`) + OpenCV (`cv2`) |

---

## 3.1 Recolección y descarga (detalle técnico)

### 3.1.0 Dos estrategias de descarga según plataforma

El proyecto descarga PDFs de **dos plataformas con arquitecturas distintas**, lo que genera dos flujos de descarga diferentes:

| | E14C (Claveros) | E14D (Delegados) / E14T (Transmisión) |
|---|---|---|
| Fuente | API REST pública jerárquica | Plataforma de transmisión |
| Nombre del archivo | Estructurado: `{dept}_{mpio}_{zona}_{puesto}_E14_PRE_..._{timestamp}.pdf` | Hex de 64 chars asignado por la Registraduría (`expectedName`): `{hex64}.pdf` |
| Organización | Descarga directamente en jerarquía `{DEPT}/{MPIO}/zona_{X}/puesto_{X}/` | Descarga plana en `E14T/` o `E14D/`, luego reorganiza con `scripts/organize_pdfs.py` |
| Índice de trazabilidad | `data/e14c_urls.jsonl` | `data/e14t_sv_urls.jsonl` / `data/e14d_sv_urls.jsonl` |

**Flujo de reorganización (E-14T / E-14D):** `organize_pdfs.py` toma los PDFs descargados con nombre hash, los cruza con el JSONL de URLs (que contiene los códigos geográficos), y los mueve a la jerarquía `{dept}/{mpio}/zona_{X}/puesto_{X}/{hash}.pdf`. El archivo hash original **se conserva como nombre de archivo** — sólo cambia su ubicación.

> Este movimiento de archivos es relevante para el mapping crop→PDF: los crops fueron generados con el path original al momento del procesamiento. Si el PDF fue movido después, el hash `md5(path)[:8]` ya no coincide con el path actual.

---

### 3.1.1 Recolección de URLs (E-14C)

```bash
python main.py collect-e14c
python main.py collect-e14c --concurrent 50
```

El colector recorre el índice maestro (`data/index.json` para primera vuelta, `data/index_e14c_segunda.json` para segunda vuelta), visita cada puesto, y registra las URLs de PDFs en `data/e14c_urls.jsonl`. El proceso es **resumible** — usa un checkpoint en `data/e14c_progress.json`.

**Escala:** ~86,929 URLs recolectadas (primera vuelta) · 13,489 puestos procesados (segunda vuelta — descarga completa).

### 3.1.2 Descarga masiva de PDFs

```bash
python main.py download-e14c --departamento AMAZONAS --concurrent 20
```

Los PDFs E14C se almacenan en la unidad externa, organizados por jerarquía geográfica:

```
E:/Nucleux/tools/Analizador de Elecciones/Data/e14_segunda/E14C/{DEPARTAMENTO}/{MUNICIPIO}/zona_{XX}/puesto_{XX}/{archivo}.pdf
```

Los PDFs E14T y E14D van al mismo volumen externo:

```
E:/Nucleux/tools/Analizador de Elecciones/Data/e14_segunda/E14T/{dept}/{mpio}/zona_{X}/puesto_{X}/{expectedName}.pdf
E:/Nucleux/tools/Analizador de Elecciones/Data/e14_segunda/E14D/{dept}/{mpio}/zona_{X}/puesto_{X}/{expectedName}.pdf
```

**Estado al 2026-07-04:** 118,543 PDFs únicos en disco (constante `MESAS_ALL_THREE`) — descarga completa con ambas versiones del colector, tras dedup y normalización de nombres.

El script de descarga es **idempotente**: si el PDF ya existe en disco, lo saltea. El checkpoint persiste en `data/e14c_segunda_progress.json`.

---

## 4. Procesamiento de PDFs

### 4.1 Estructura del formulario E-14

Los formularios de primera y segunda vuelta tienen dimensiones y estructura distintas:

| Aspecto | Primera vuelta | Segunda vuelta |
|---------|---------------|----------------|
| Páginas | 3 (p0: candidatos 1–7 · p1: candidatos 8–13 + totales · p2: firmas de jurados) | 1 (formulario único, p0) |
| Dimensiones | 1260 × 3897 px a 300 DPI (calibrado AMAZONAS/LETICIA) | ~ancho × 3500 px a 300 DPI |
| Candidatos | Múltiples (primera ronda) | 2 (C1 Cepeda, C2 Abelardo) |
| Filas de datos | Votos por candidato, blanco, nulos, no marcados, total | Votos por candidato, blanco, nulos, no marcados, incinerados, total |
| Layout | Columna de votos + columna de totales | Columna de votos (izquierda) + columna de totales (derecha) |
| Storage PDFs | `data/e14_primera/` (~26,194 PDFs) | `E:/Nucleux/.../Data/e14_segunda/E14C/` (118,543 PDFs únicos) |

### 4.2 Detección de grilla (`grid_detector_v2`)

El formulario E-14 usa una **grilla de líneas grises** para separar filas y columnas. El detector identifica:

1. **Líneas verticales grises** — delimitan las columnas de votos y totales.
2. **Líneas horizontales grises** — delimitan las filas de cada candidato/categoría.
3. **Construcción del grid** — cruces de líneas forman celdas individuales.
4. **Etiquetado de filas** — asignación de etiquetas (`C1_CEPEDA`, `C2_ABELARDO`, `BLANCO`, `NULOS`, `NO_MARCADOS`, `INCINER`, `SUMA`) según posición relativa.

**Resultado validado:** 9/9 filas detectadas correctamente en PDFs reales de segunda vuelta (al 2026-06-22).

> El enfoque de grilla gris superó al enfoque anterior de componentes conectados, que fallaba en celdas vacías y celdas con muy poca tinta.

### 4.3 Extracción de campos numéricos

Una vez detectada la grilla, se extraen subceldas para cada fila × columna. El motor de OCR lee los dígitos de cada subcelda.

**Motor principal:** CNN MobileNetV2 entrenado sobre crops de dígitos del proyecto (98.7% de precisión en validación).  
**Fallback:** EasyOCR (español + inglés) para casos donde la CNN no converge.

### 4.4 Validación aritmética

Por cada acta procesada se verifica la suma de votos contra el total declarado. La fórmula varía por vuelta:

**Primera vuelta** (13 candidatos):
```
C1 + C2 + ... + C13 + BLANCO + NULOS + NO_MARCADOS = TOTAL
```

**Segunda vuelta** (2 candidatos):
```
C1_CEPEDA + C2_ABELARDO + BLANCO + NULOS + NO_MARCADOS = TOTAL
```

> Los incinerados se registran como campo separado pero **no entran en la suma de validación** en ninguna vuelta (`form_extractor.py`).

El umbral de separación entre fraude y ruido OCR es `FRAUD_MAX_DIFF = 30` votos (`form_extractor.py`). Los valores fuera del rango 0–999 se consideran misread (`MAX_MESA_VOTES = 999`).

> **Hallazgo resuelto — `overall_status` (2026-07-04 15:12 UTC):** En la cross-validación, el estado `critical` se asignaba cuando el delta aritmético `URNA - suma_votos_candidatos` superaba `FRAUD_MAX_DIFF`. La revisión manual de PDFs reales confirmó que el 66 % de las mesas en ese estado tenían deltas grandes **causados por errores de OCR** — el modelo lee dígitos incorrectamente (p. ej. el campo BLANCO leído como `703` en lugar de `3`, delta = 700). El estado **no indicaba fraude confirmado** sino *"requiere revisión humana por discrepancia numérica grande"*. **Resuelto 2026-07-04:** el estado fue renombrado a `needs_review_large_delta` en código, esquema y base de datos. Pendiente aún: pipeline de re-OCR sobre la celda conflictiva antes de escalar a revisión humana.

> **Hallazgo — primer mapa automatizado de estados (2026-07-05):** Al completar la cross-validación de los 34 departamentos (115,691 mesas) se ejecutó el script `scripts/export_clean_mesas.py` que aplica `compute_overall_status()` sobre cada fila del JSONL y clasifica cada mesa sin intervención humana. El resultado es un **mapeo inicial automatizado** — ninguna mesa fue revisada manualmente para asignar su estado; la clasificación proviene exclusivamente del pipeline OCR (CNN MobileNetV2, **98.7% de precisión en validación**) seguido de la lógica aritmética y de congruencia cruzada entre las tres fuentes (E14C, E14T, E14D).
>
> | Estado | Mesas | % del total procesado |
> |---|---|---|
> | `needs_review_large_delta` | 75,612 | 65.4% |
> | `discrepancy` | 25,979 | 22.5% |
> | **`clean`** | **10,579** | **9.1%** |
> | `known_anomaly` | 2,414 | 2.1% |
> | `warning` | 1,107 | 1.0% |
> | **Total procesado** | **115,691** | — |
> | Universo CNE | 122,020 | — |
>
> Las 10,579 mesas `clean` son las que pasaron **todos** los filtros del pipeline: ≥ 2 fuentes disponibles, URNA ≠ 0 con votos presentes, delta aritmético = 0 en todas las fuentes, sin discrepancia entre fuentes, y sin indicios de tachón. Exportadas a `data/clean_mesas.csv`.
>
> **Interpretación del 65.4% en `needs_review_large_delta`:** este porcentaje **no equivale a tasa de fraude**. El umbral `FRAUD_MAX_DIFF = 30` detecta cualquier delta > 30 votos, y el error de OCR del modelo (~1.3% de dígitos individuales) puede generar deltas artificialmente grandes cuando un campo de 3 dígitos se mislee (p. ej. `003` leído como `703` produce un delta de 700). La intervención humana o un segundo motor OCR (EasyOCR) sobre la celda conflictiva reduciría este volumen significativamente. El mapa actual sirve como **triage priorizado**, no como dictamen.

### Flags de fraude aritmético (`flags` → `is_suspicious = True`)

Diferencia pequeña (≤ 30) con valores plausibles: señal genuina de irregularidad.

| Flag | Condición |
|------|-----------|
| `ARITMETICA_SUMA` | `suma_total` declarada ≠ suma calculada de votos; diferencia ≤ 30 |
| `URNA_VS_SUMA` | `total_urna` ≠ `suma_total`; diferencia ≤ 30 |
| `VOTOS_EXCEDEN_VOTANTES` | `total_urna` > `total_votantes`; exceso ≤ 30 |
| `VALOR_NEGATIVO` | Cualquier candidato con valor negativo |

### Flags de revisión OCR (`ocr_flags` → `needs_review = True`, no implica fraude)

Diferencia grande (> 30), valores fuera de rango, o campo no diligenciado: probable misread o acta incompleta.

| Flag | Condición |
|------|-----------|
| `OCR_SUMA_DUDOSA` | `suma_total` existe pero difiere del calculado en > 30 votos |
| `URNA_NO_DILIGENCIADA` | `total_urna = 0` con votos reales presentes — campo en blanco en el acta física |
| `OCR_URNA_DUDOSA` | `total_urna` difiere de `suma_total` en > 30 votos |
| `OCR_VOTOS_DUDOSOS` | `total_urna` > `total_votantes` por más de 30 votos |

> `URNA_NO_DILIGENCIADA` se trata como revisión y no como fraude porque es común que los jurados dejen el campo "Total votos en urna" en blanco. En ese caso el OCR lee 0, que no debe compararse contra `suma_total` ni `total_votantes`. Los checks `URNA_VS_SUMA` y `VOTOS_EXCEDEN_VOTANTES` se omiten automáticamente cuando este flag está presente.

---

## 5. Detección de tachones y enmiendas

> Metodología completa documentada en [`docs/metodologia_tachones.md`](metodologia_tachones.md).

**Estado de integración:** la detección está activa en el pipeline de cross-validación vía `_run_tachon_scan()` en `cross_validator_cli.py`. El módulo fuente carga desde `debug_sv/tachon_method_scan.py` — pendiente migrar a `src/modules/analyzer/tachon_scanner.py` para formalizar la arquitectura (ver BACKLOG).

Se aplican cuatro métodos de detección sobre cada subcelda con tinta:

| Método | Descripción | Umbral |
|--------|-------------|--------|
| `TACHON` | Densidad de tinta horizontal (tachón clásico) | 0.45 |
| `DOBLE_ESCRITURA` | Superposición de dígitos | 0.50 |
| `DENSIDAD_ALTA` | Alta densidad de píxeles en zona de dígito | 0.60 |
| `ZONA_SUCIA` | Ruido generalizado en la celda | 0.50 |

Un score combinado (`COMBINED`) pondera los cuatro métodos:

```
combined = 0.40 × tachon + 0.30 × doble + 0.20 × densidad + 0.10 × zona_sucia
```

### 5.1 Patrones de fraude físico documentados

#### Pre-marcación de tarjetas para anulación selectiva de votos

**Descripción:** Durante el traslado de las tarjetas electorales desde la imprenta hasta los puestos de votación, o durante la custodia previa a la jornada, actores con acceso físico a las tarjetas las pre-marcaban con señales pequeñas en la casilla de un candidato específico (generalmente el candidato no afín al jurado o a quien controlaba el puesto). Cuando un votante llegaba y marcaba ese mismo candidato, su tarjeta quedaba con dos marcas en esa casilla y era declarada nula por los jurados. Los votantes que marcaban otros candidatos no sufrían este efecto porque la pre-marca quedaba en una casilla sin voto válido y pasaba desapercibida o se ignoraba.

**Efecto observable en el acta:**
- Incremento anómalo de `votos_nulos` en mesas específicas sin causa aparente.
- La proporción de nulos sobre votantes habilitados es significativamente mayor que en mesas del mismo puesto o municipio.
- El patrón es asimétrico: afecta con mayor intensidad las mesas donde el candidato objetivo tenía más intención de voto.

**Señales de detección posibles:**
- `votos_nulos / total_votantes` > umbral estadístico para la zona (outlier por percentil).
- Comparación de ratio de nulos entre mesas del mismo puesto: dispersión inusual sugiere selección deliberada.
- Correlación entre mesas con nulos elevados y resultados electorales bajos para un candidato específico en esa zona.

**Limitación actual del sistema:** La validación aritmética actual (`form_extractor.py`) verifica que los nulos estén incluidos correctamente en la suma, pero no detecta ratios anómalos de nulos dentro de un contexto geográfico. Esta detección requiere análisis estadístico comparativo entre mesas, no validación por acta individual.

> **Fuente:** Reportado por observadores electorales. Documentado el 2026-07-03.

#### Alteración de la sección de notas y constancias

**Descripción:** La sección de notas y constancias del E-14 es un espacio de texto libre que los jurados pueden usar para registrar observaciones sobre la jornada. Si los jurados no utilizan ese espacio (o lo utilizan parcialmente), dejan áreas en blanco que pueden ser llenadas posteriormente por terceros con anotaciones falsas que alteren el valor probatorio del acta.

**Buena práctica de los jurados:** Rayar completamente los espacios en blanco de la sección de notas al finalizar la jornada — tanto si no se escribió ninguna nota como si se escribió solo una parte. Esto impide físicamente que alguien añada contenido posterior al cierre de mesa. Es un procedimiento análogo al rayado de espacios en blanco en documentos notariales y cheques.

**Efecto observable en el acta digitalizada:**
- Actas con práctica correcta: sección de notas con rayas diagonales o líneas cruzando el espacio no utilizado.
- Actas con práctica incorrecta o manipuladas: espacios en blanco intactos, o texto que visualmente no encaja con la tinta o caligrafía del resto del acta.

**Implicación para el sistema:** El módulo `notes_extractor.py` extrae el contenido de la sección de notas. Una señal de alerta adicional sería detectar notas en actas donde el resto del documento presenta patrones de escritura inconsistentes, o notas que aparecen en actas de puestos donde otras mesas registraron espacio en blanco.

> **Fuente:** Buena práctica reportada por jurados electorales. Documentado el 2026-07-03.

---

## 6. Crops y etiquetado

### 6.1 Generación de crops

Para cada PDF procesado, se recortan las celdas completas (`full_cell`) y subceldas individuales (`sub_{LABEL}_{idx}`). La ubicación en disco varía por vuelta:

**Segunda vuelta** (`antioquia_crops.py`):
```
E:/e14c_segunda/crops/antioquia/{pdf_hash}/
  full_{LABEL}.png
  sub_{LABEL}_{idx}.png
```
Donde `pdf_hash = md5(pdf_path.encode()).hexdigest()[:8]` calculado sobre el path original al momento de generación.

**Primera vuelta (etiquetados para entrenamiento)** (`exporter.py`):
```
data/labels_v2/crops/
  {sha1_key}.png   ← archivos planos, sin subdirectorios por PDF
```
13,177 subceldas (archivos PNG planos). El identificador es `sha1(key)[:16]` donde `key` es un compuesto de `crop_id`, no el path del PDF. Anotaciones exportadas a `data/labels_primera_vuelta_export.json`.

> **Nota:** Los esquemas de hash son distintos entre vueltas. Para segunda vuelta aplica `md5(path)[:8]`; para primera vuelta aplica `sha1(key)[:16]`. No son intercambiables para reconstruir el mapping crop→PDF.

### 6.2 Portal de etiquetado

```bash
python main.py label  # dev local (localhost:5000)
```

El portal permite a anotadores humanos clasificar crops de dígitos. En producción usa **Supabase** (PostgreSQL + Storage) con autenticación JWT. El sistema de cola asigna crops a anotadores evitando duplicación.

**Estado del upload (al 2026-07-04):** Los 392,088 crops de Antioquia están generados localmente pero el upload a Supabase Storage está pendiente — no se ha subido más desde la carga parcial inicial (~12%). Bloqueante para activar el etiquetado de segunda vuelta.

**Concordancia inter-anotador:** Se calcula por crop cuando hay múltiples anotaciones. Sirve como métrica de calidad del conjunto de entrenamiento.

---

## 7. Modelo CNN de clasificación de dígitos

### v1 — producción actual

- **Arquitectura:** MobileNetV2 (transfer learning desde ImageNet)
- **Clases:** 10 (dígitos 0–9)
- **Precisión en validación:** 98.7%
- **Pesos:** `models/digit_classifier.pth`
- **Umbral de confianza en cross-validación:** 70% (`CONFIDENCE_THRESHOLD = 0.70` en `cross_validator.py`) — dígitos con confianza menor se enmascaran como `None` para no confundirlos con discrepancias reales entre fuentes
- **`needs_review`** — se activa cuando el OCR produce valores implausibles (flag en `form_extractor.py`), no por umbral de confianza
- **`is_suspicious`** — se activa por flags de fraude aritmético o valores negativos, independiente de la confianza del OCR

### v2 — primera versión entrenada, etiquetado activo para reentrenamiento

- **Clases:** 12 (0–9, `.`, `*`)
- **Nuevas clases:**
  - `.` — celda vacía / separador decimal
  - `*` — tachón / anulación / corrección (189 muestras etiquetadas, clase más confundida en producción)
- **Dataset v2.0:** 13,177 subceldas primera vuelta
- **Estado:** primera versión entrenada. Etiquetado activo en curso para ampliar el dataset con crops de segunda vuelta y mejorar cobertura de clases minoritarias (`.`, `*`) antes del reentrenamiento.
- **Variantes entrenadas:** A (ImageNet → 12 clases), B (v1 → 12 clases), C (scratch)
- **Estrategia anti-desbalance:** class weights inverso-frecuencia + WeightedRandomSampler + augmentación 5× para `*`
- **Checkpoints:** `models/digit_classifier_v2_{imagenet,finetune,scratch}.pth`
- **`models/digit_classifier.pth` no se toca** hasta que el usuario promueva manualmente una variante v2

### Inferencia top-3 softmax (2026-07-03)

A partir del 2026-07-03, el pipeline de inferencia produce **top-3 candidatos** por subceldas en lugar de solo el argmax:

```python
# Antes (v1 original)
digit, conf = CNN.predict(crop)          # solo el ganador
if conf < 0.70: digit = None            # información descartada

# Ahora (top-3)
digit, conf, top3 = CNN.predict(crop)   # ganador + 2 alternativas
# top3 = [{"digit":"3","prob":0.61}, {"digit":"8","prob":0.28}, {"digit":"5","prob":0.09}]
# Cuando conf < 0.70: digit → None, pero top3 se preserva en SourceResult["top3_digits"]
```

**Motivación forense:** cuando el modelo duda entre `"3"` y `"8"` (confusión legítima entre candidatos) vs. entre `"1"` y caracteres ruido, la distribución de probabilidad tiene valor distinto. Descartar la subceldas sin preservar los candidatos perdía esa señal.

**Campo nuevo en el pipeline:**
- `e14_worker.py` → `rows[i]["top3_digits"]` — lista de 3 listas (una por subceldas)
- `cross_validator.SourceResult` → campo `top3_digits` — mismo shape por field
- `apply_confidence_filter()` pasa `top3_digits` intacto (solo enmascara `digits`)
- E14T/E14D: `top3_digits = {}` (extractor distinto, sin acceso a subceldas CNN)

**Nota:** el fallback EasyOCR devuelve `top3 = []` — no tiene distribución de probabilidad.

> **Nota metodológica (2026-07-03 02:46 UTC):** El campo `digits` en `SourceResult` está determinado por la **regla del 70%**: un dígito existe (`"0"`–`"9"`) solo si la confianza CNN ≥ 0.70; por debajo es `None`. Esta regla opera sobre `digits` únicamente — `top3_digits` se preserva siempre, independientemente del umbral. Por lo tanto `digits` es el campo que alimenta `compare_mesa()` y decide si hay discrepancia; `top3_digits` es observacional y nunca bloquea ni genera una discrepancia.

> **Nota metodológica (2026-07-03 02:21 UTC):** El `None` bajo 0.70 **no se eliminó** — sigue protegiendo la cross-validación de falsos positivos. El `top3_digits` es paralelo, no un reemplazo: queda guardado en el JSONL para minería posterior — por ejemplo, qué dígitos confunde más el modelo entre sí, o identificar subceldas donde dudó entre candidatos electoralmente relevantes (ej. `"3"` vs `"8"` en C1/C2).

---

## 8. Estado general del dataset (al 2026-07-02)

### Primera vuelta

| Métrica | Valor |
|---------|-------|
| PDFs E14C descargados | ~26,194 |
| Crops etiquetados | 13,177 subceldas |
| Anotaciones humanas | 16,249 (labels 0–9, `.`, `*`) |
| Instancias de `*` etiquetadas | 189 |
| Supabase project | `rlbmbapqoptxzikdoyot` (pausado, datos exportados a `data/labels_primera_vuelta_export.json`) |

### Segunda vuelta

| Métrica | Valor |
|---------|-------|
| PDFs E14C únicos en disco | 118,543 (`MESAS_ALL_THREE` — auditado 2026-07-04) |
| Puestos procesados | 13,489 / 13,489 (100%) |
| PDFs E14T / E14D disponibles | ~122,019 c/u |
| Consulados (dept 88) — E14T / E14D, sin descargar localmente | 661 al 2026-06-21 → 3,670 al 2026-06-30 (ver sección 2.3) |
| Consulados (dept 88) — E14C | 949 puestos en índice / 0 PDFs disponibles al 2026-06-24 (ver hallazgo sección 2.3) |
| Cross-validación completada | 34 departamentos — 115,691 mesas (al 2026-07-04) |
| Mapa inicial de estados (automatizado, sin revisión humana) | 10,579 `clean` · 75,612 `needs_review_large_delta` · 25,979 `discrepancy` · 2,414 `known_anomaly` · 1,107 `warning` — ver sección 4.4 |
| Mesas limpias exportadas | `data/clean_mesas.csv` — 10,579 mesas que pasaron todos los filtros del pipeline (al 2026-07-05) |
| Crops generados (segunda vuelta, Antioquia) | 392,088 crops — 11,981 PDFs (8.64 GB en `E:\e14c_segunda\crops\antioquia\`) |

> **Nota metodológica (2026-07-03 01:51 UTC):** Las cross-validaciones de Antioquia y Atlántico se procesaron con el modelo **pre-top3** (umbral de descarte 0.70 → `None`, sin preservar candidatos alternativos). A partir de Bolívar (dept 05) se usó el modelo **top-3** (`SourceResult["top3_digits"]` activo) — 15 departamentos adicionales completados al 2026-07-04. Esto crea dos grupos de muestras con comportamiento diferente en subceldas de baja confianza, útiles para comparar cobertura de detección entre ambas versiones del pipeline.
>
> | Grupo | Departamentos | Mesas | Modelo | `top3_digits` |
> |-------|--------------|-------|--------|--------------|
> | A — descarte | Antioquia (01), Atlántico (03) | 21,991 | pre-top3 | ✗ vacío |
> | B — top-3 | Bolívar (05) en adelante | 93,750 (32 depts — completo al 2026-07-04) | top-3 softmax | ✓ activo |
>
> Los archivos del grupo A son: `data/cross_mesa_validation_01.jsonl`, `data/cross_mesa_validation_03.jsonl`.

### Modelos

| Modelo | Clases | Precisión | Estado |
|--------|--------|-----------|--------|
| `models/digit_classifier.pth` | 10 (0–9) | 98.7% val | Producción |
| `models/digit_classifier_v2_*.pth` | 12 (0–9, `.`, `*`) | — | v2.0 entrenada — etiquetado activo para reentrenamiento |

---

## 9. Pendientes y próximos pasos

### Resultados y datos — Alta prioridad

- [ ] Subir resultados cross-validación 17 departamentos a Supabase `mesa_results` — desbloquea contador público y admin panel
- [ ] Upload crops Antioquia a Supabase Storage (~345,000 crops pendientes, script `debug_sv/antioquia_crops.py`) — desbloquea etiquetado de segunda vuelta

### Recolección y datos

- [ ] Descargar PDFs de consulados E14T/E14D (dept 88 — 661 al 2026-06-21 → 3,670 al 2026-06-30; ver sección 2.3)
- [ ] Confirmar disponibilidad E14C de consulados (dept 88 — 0 PDFs al 2026-06-24; re-verificar plataforma oficial)
- [x] Escalar cross-validación a todos los departamentos — 34/34 completados al 2026-07-04 (115,741 mesas)
- [ ] Renombrar 147 archivos E14C con ñ/tildes que el script `normalize_e14c_names.py` no pudo procesar (encoding issue en Windows)

### Modelo CNN

- [ ] Completar SDD `digit-classifier-v2`: tareas → implementación → verificación (proposal/spec/design listos)
- [ ] Pipeline de dígitos sospechosos: cross-validate discrepancias → recortar subceldas E14C → subir al portal de etiquetado

### Procesamiento

- [ ] Pipeline re-OCR para `needs_review_large_delta`: recortar celda conflictiva → segundo motor OCR → solo escalar a revisión humana si ambos confirman delta grande
- [ ] Migrar `tachon_method_scan.py` de `debug_sv/` a `src/modules/analyzer/tachon_scanner.py`
- [ ] Integrar `grid_detector_v2` al módulo principal (`src/modules/analyzer/`) — actualmente en `debug_sv/`
- [ ] Completar mapping `crop_hash → PDF path` para Antioquia (crops generados con path original antes de reorganización)

---

## 10. Sugerencias de análisis futuro

### Detección estadística de anulación selectiva por pre-marcación

Relacionado con el patrón documentado en la sección 5.1. La validación aritmética actual no detecta este fraude porque el acta es aritméticamente correcta — los nulos simplemente son más altos de lo esperado.

**Análisis sugerido:** calcular el ratio `votos_nulos / total_votantes` por mesa y compararlo contra la distribución del mismo puesto, municipio y zona. Mesas con ratio de nulos en el percentil 95+ de su contexto geográfico, combinadas con resultados bajos para un candidato específico, constituyen una señal estadística del patrón.

**Señales a cruzar:**
- Ratio de nulos por mesa vs. mediana del puesto/zona.
- Dispersión inusual de nulos entre mesas del mismo puesto (todas deberían tener proporciones similares).
- Correlación entre nulos elevados y porcentaje de votos bajo para un candidato en esa mesa vs. mesas vecinas.

Este análisis opera sobre los resultados agregados del OCR, no requiere revisión de PDFs individuales, y podría implementarse sobre el JSONL de cross-validación existente.

### Análisis geográfico de `needs_review_large_delta`

Con los 34 departamentos cross-validados (115,691 mesas), el estado `needs_review_large_delta` representa el **65.4% del total procesado**. Sin embargo, si ese porcentaje varía significativamente entre departamentos o zonas, la variación misma es una señal.

**Hipótesis:** un delta aritmético grande causado por OCR debería distribuirse de forma relativamente uniforme entre departamentos — es ruido del modelo, no del acta. Si un departamento o municipio tiene una tasa de `needs_review_large_delta` significativamente mayor que el promedio, puede indicar:
- Formularios físicos con peor calidad de impresión o escritura (mayor tasa de misread legítima).
- O un patrón de alteración sistemática de dígitos en esa zona que el OCR detecta como anomalía.

**Análisis sugerido:** calcular `tasa_needs_review = mesas_needs_review / total_mesas` por departamento, municipio y zona. Comparar contra la mediana nacional. Zonas en el percentil 95+ de esa tasa merecen revisión humana prioritaria.

**Datos disponibles:** `data/cross_mesa_validation_*.jsonl` (34 departamentos — completo). `data/clean_mesas.csv` para el subconjunto limpio. Implementable sin infraestructura adicional.

---

## 11. Gestión de contexto entre sesiones — Engram

El pipeline de análisis es largo y multi-sesión. Sin memoria persistente, cada sesión de trabajo arranca sin saber qué departamentos fueron procesados, qué resultados existen, ni qué pasos quedaron pendientes. Esto genera trabajo duplicado y errores de diagnóstico ("¿por qué E14C tiene 0% en Bolívar?").

### Protocolo obligatorio

Antes de iniciar cualquier trabajo de análisis o pipeline, el asistente debe:

1. Llamar `mem_context(project: "AnalizadorE14")` para recuperar el historial reciente de sesiones.
2. Llamar `mem_search(query: "pipeline e14c cobertura")` para recuperar el estado de cobertura por departamento.
3. Solo entonces diagnosticar o continuar trabajo.

### Estado a persistir en engram

Cada vez que se completa o modifica el estado del pipeline, guardar con `mem_save`:

| topic_key | Qué guarda |
|---|---|
| `pipeline/e14c-ocr-coverage` | Departamentos con OCR E14C procesado vs. pendiente, con % de cobertura |
| `pipeline/cross-validation-status` | Estado de los 34 JSONL de cross-validación (completo / parcial / pendiente) |
| `pipeline/supabase-upload` | Departamentos con crops subidos a Supabase Storage |
| `findings/blank-fields-by-source` | Hallazgos de campos vacíos por fuente (E14C/D/T) |

### Estado actual del pipeline E14C (2026-07-06)

Los 118,563 PDFs E14C de segunda vuelta están descargados en `E:\Nucleux\tools\Analizador de Elecciones\Data\e14_segunda\E14C\`. Sin embargo, el análisis OCR/CNN **no se ha corrido** para todos los departamentos. Los cross_mesa_validation muestran `status: "not_available"` para E14C en los siguientes departamentos:

| Departamento | Código | PDFs disponibles | OCR corrido |
|---|---|---|---|
| BOLÍVAR | 05 | Sí | No |
| CALDAS | 09 | Sí | No |
| CAQUETÁ | 11 | Sí | No |
| CAUCA | 12 | Sí | No |
| CESAR | 13 | Sí | No |
| CUNDINAMARCA | 15 | Sí | No |
| VALLE | 31 | Sí | Parcial (0.8%) |
| VICHADA | 68 | Sí | Parcial (1.1%) |
| VÁUPES | 72 | Sí | Parcial (0.5%) |
| AMAZONAS | 88 | Sí | No |

Para completar cobertura, correr por cada departamento pendiente:
```bash
python main.py analyze-e14c \
  --dir "E:\Nucleux\tools\Analizador de Elecciones\Data\e14_segunda\E14C\{DEPTO}" \
  --output data/analysis_e14c_{codigo}.jsonl
```

