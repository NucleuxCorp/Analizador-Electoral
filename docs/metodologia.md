# Metodología — Análisis de Actas E-14 (Primera y Segunda Vuelta 2026)

> Documento vivo. Se actualiza a medida que se completa y entiende el flujo completo.

---

## Glosario

| Término | Significado |
|---|---|
| **E-14** | Formulario oficial de acta de votación. Lo diligencian los jurados al cierre de cada mesa. |
| **E14C — Claveros** | Copia del acta custodiada en el arca triclave. Es la de **mayor valor legal** — base del escrutinio oficial. |
| **E14T — Transmisión** | Copia dictada por teléfono la noche electoral para el preconteo informativo. |
| **E14D — Delegados** | Copia escaneada en el puesto de votación la noche electoral. |
| **Mesa** | Unidad básica de votación. Cada mesa tiene su propio acta E-14. |
| **Jurado de votación** | Ciudadanos elegidos para administrar la mesa y diligenciar el acta. |
| **Puesto de votación** | Sede física donde se agrupan varias mesas. |
| **Registraduría** | Registraduría Nacional del Estado Civil — entidad que administra y publica las elecciones en Colombia. |
| **Escrutinio** | Proceso oficial de conteo y verificación de votos posterior a la jornada electoral. |
| **Tachón / enmienda** | Corrección física sobre el acta — línea que cubre un dígito o texto modificado. |
| **SHA-256** | Huella digital criptográfica que identifica el contenido exacto de un archivo. Dos archivos con SHA-256 idéntico son byte a byte iguales. |
| **Semáforo** | Sistema de colores del portal (⚪🔴🟡🟢) que indica el estado de revisión de cada mesa. |
| **OCR** | Reconocimiento óptico de caracteres — tecnología que lee dígitos escritos a mano en el acta. |

---

## 1. Contexto

Las elecciones presidenciales colombianas 2026 generan **actas E-14** por mesa de votación en cada vuelta electoral. Cada acta registra los votos por candidato, votos en blanco, nulos, no marcados, e incinerados. La Registraduría Nacional publica estas actas en formato PDF a través de tres plataformas por vuelta:

- **Plataforma de escrutinio oficial (E-14C)** — la acta fisica se introducce en las bolsas luego son digitalizadas y escrutadas por la comisión escrutadora.
- **Plataforma de transmisión (E-14T)** — actas que fueron dictadas por teléfono para el preconteo informativo en tiempo real.
- **Plataforma de delegados (E-14D)** — actas entregadas a los delegados de la registraduria y escaneadas en el puesto de votación la noche electoral.

> E-14T y E-14D son dos plataformas distintas con dominios separados, pero comparten la misma arquitectura técnica (mismo índice `allTransmissionCodes.json`, mismo `expectedName` por mesa) y se confunden frecuentemente como una sola. La diferencia es la copia física que publican: Transmisión publica la copia dictada por teléfono, Delegados publica la copia entregada por los jurados de votacion.

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

### ConteoCol — comparación visual par a par por humanos

[ConteoCol](https://conteocol.lat/) propone un enfoque diferente al de este proyecto: en lugar de usar reconocimiento automático de caracteres, pone a ciudadanos voluntarios a comparar visualmente las tres versiones del acta de una misma mesa — E14C, E14T y E14D — lado a lado. El voluntario determina directamente si existen discrepancias entre las tres copias, sin intermediación algorítmica.

| Dimensión | Analizador E-14 | ConteoCol |
|---|---|---|
| Método principal | OCR automatizado + validación aritmética | Comparación visual humana par a par |
| Rol del voluntario | Confirmar o corregir dígitos dudosos marcados por el algoritmo | Comparar directamente las tres versiones del acta |
| Detección de discrepancias | Automática — el sistema identifica las mesas con alerta | Manual — el voluntario revisa y determina si hay diferencias |
| Escala | Procesamiento masivo (118,543 mesas E14C) con revisión humana focalizada en alertas | Revisión humana directa de cada mesa comparada |

### Colombia Elige — análisis estadístico y visualización electoral

[Colombia Elige](https://colombiaelige.co/) trabaja sobre los resultados ya consolidados para identificar patrones estadísticos en la distribución de votos, a diferencia de este proyecto, que parte del documento físico (el PDF del acta) para detectar irregularidades en la transcripción.

### Comparación de enfoques metodológicos

Las tres iniciativas son metodológicamente distintas y cubren ángulos complementarios del mismo problema:

| Iniciativa | Enfoque |
|---|---|
| **Analizador E-14** | Integridad del documento físico — OCR, aritmética, tachones, comparación entre versiones |
| **ConteoCol** | Comparación visual humana entre las tres copias del acta por mesa |
| **Colombia Elige** | Análisis estadístico de los resultados consolidados — patrones y visualización |

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

> **Consecuencia descubierta (2026-07-13/14):** el script que originalmente armó los nombres de archivo locales planos (`{dept}_{mpio}_{X}_{Y}_E14_PRE_...`) usó por error `{field4}` en la posición de `zona` del prefijo, en vez de `{zona_3d}` — confirmado visualmente contra un acta real (AMAZONAS/LETICIA zona 01 puesto 01 mesa 002: el acta escaneada dice `ZONA: 01`, pero el nombre local dice `60_001_00_01_...`, zona=00). La carpeta (`zona_01/puesto_01/`) quedó correcta — solo el nombre plano del archivo tiene el bug. **Escaneo del corpus completo (118,543 PDFs E14C): 22,967 archivos (19.4%) tienen esta discrepancia** entre la zona/puesto de la carpeta y la zona/puesto embebida en el nombre. Esto afecta a `build_url()` en **ambos** `verify_e14c.py` y `Herramientas/verificador-hash-e14/verificador_e14c.py` (mismo patrón duplicado): para estos ~23,000 archivos, la URL reconstruida a partir del nombre de archivo apunta a un recurso inexistente en el servidor — independientemente de si el servidor bloquea tráfico de script o no. Queda como cambio SDD pendiente y separado (no mezclado con `e14c-verification-dual-mode`, que resuelve el bloqueo anti-bot pero no este bug de nomenclatura): hacer que `build_url()` derive dept/mpio/zona/puesto de la ruta de carpeta en vez de parsear el nombre plano.

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

> **Alcance actual — solo E14C:** todo lo documentado en esta sección (script online `verify_e14c.py` y herramienta ciudadana offline `verificador_e14c.py`/`.exe`) está **validado únicamente para E14C**. Ambos parsean el nombre de archivo con el patrón estructurado `{dept}_{mpio}_{zona}_{puesto}_E14_PRE_..._{version}.pdf` (`build_url()` en `verify_e14c.py`, índice de hashes en `verificador_e14c.py`) — un formato que no existe en E14T/E14D, cuyos archivos se nombran por `expectedName` (hash de 64 caracteres, sección 2.2). **Generalizar esta verificación a E14T/E14D es trabajo pendiente**, no un descuido de alcance: requeriría un índice de hashes separado (indexado por `expectedName`, no por geografía) y, para el modo online, un mecanismo de descarga con sesión de browser real en vez de HTTP GET plano (ver sección 3.1.0 — E14T/E14D no responden a un GET simple).
>
> **Ensayo de validación (2026-07-13):** se corrieron ambas herramientas y sus dos modos contra 3 PDFs E14C reales descargados en un ensayo puntual de descarga (`Debug/1. Descargas de E14/E14C/`, dept 01 ANTIOQUIA / MEDELLIN / zona 01 / puesto 01 — mismo método de la sección 3.1.1, probado contra la carpeta de ensayo en vez de la ruta de producción).
>
> | Herramienta / modo | Resultado |
> |---|---|
> | `verify_e14c.py --full` (online) | 3/3 `OK`, 0 `DIF` |
> | `verificador_e14c.py` — modo offline (índice snapshot 2026-06-30) | 3/3 `VERIFICADA`, 0 `ALTERADA` |
> | `verificador_e14c.py` — modo online (`run_verification_online`, invocado directo sin pasar por el menú) | 3 corridas: 0/3 → 1/3 → 3/3 `IGUAL_SERVIDOR` |
>
> El modo online del verificador ciudadano necesitó 3 intentos para estabilizarse — mismo patrón de `WinError 10054` (conexión reiniciada por el host remoto) observado en el primer ensayo de descarga de la sección 3.1.1. Causa probable: `verify_e14c.py` tiene rate-limiting incorporado (`_RATE`: 1s entre requests + cooldown de 60s tras 3 fallos consecutivos), mientras que `verificador_e14c.py::download_and_hash()` solo aplica un `sleep(0.3)` fijo, sin backoff ni cooldown ante bloqueo — lo hace más frágil frente al mismo comportamiento del servidor. **Mejora pendiente identificada:** portar la lógica de rate-limiting de `verify_e14c.py` al modo online de `verificador_e14c.py`.

### Metodología

Los PDFs E14C fueron descargados de la plataforma oficial de escrutinio aproximadamente el 22 de junio de 2026. Con el fin de detectar modificaciones posteriores a esa descarga, se ejecuta una comparación byte a byte entre cada archivo local y la versión actualmente servida por la Registraduría.

El procedimiento es el siguiente:

1. Por cada PDF local, se construye la URL del servidor a partir del nombre del archivo.
2. Se descarga la versión actual del servidor.
3. Se calcula el SHA256 de ambas copias (local y remota).
4. Si los hashes coinciden, el archivo no fue modificado — se registra como `OK` en el checkpoint. El archivo local se conserva en disco.
5. Si los hashes difieren, se registra como `DIF` en el checkpoint. El archivo local y remoto (Se agrega marca) se conserva en disco como evidencia de modificación.

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

### Herramienta ciudadana offline — `verificador_e14c.py` / `.exe`

`verify_e14c.py` (arriba) es la herramienta de mantenedor: requiere el repositorio completo y conexión a internet constante durante la corrida. Para distribución a auditores ciudadanos sin acceso al repositorio ni necesariamente buena conectividad, existe una segunda herramienta independiente en `Herramientas/verificador-hash-e14/`:

| Aspecto | `verify_e14c.py` (mantenedor) | `verificador_e14c.py` / `.exe` (ciudadano) |
|---|---|---|
| Público | Equipo del proyecto | Auditores ciudadanos externos |
| Modo offline | No — siempre contra el servidor live | Sí — contra un índice de huellas SHA-256 pre-generado (`hash_index_e14c.json`, snapshot 2026-06-30) |
| Modo online | Sí (única modalidad) | Sí — opcional, con verificación por tamaño (HEAD) y/o SHA-256 completo |
| Requiere Python | Sí | No, si se usa el `.exe` compilado (Windows 10/11) |
| Distribución | Vive en el repo del proyecto | Paquete standalone con índice + ejecutable + `.bat` |

**Cobertura del índice offline:** ~83,498 actas de un total de ~118,347 registradas (snapshot 2026-06-30). Las ~27,000 mesas restantes —no capturadas en la descarga original— se reportan como `DESCONOCIDA`, nunca como `ALTERADA`.

**Estados (modo offline, contra `hash_index_e14c.json`):**

| Estado | Significado |
|--------|------------|
| `VERIFICADA` | El PDF coincide byte a byte con el hash registrado en el índice del 2026-06-30. |
| `ALTERADA` | El nombre de archivo está en el índice pero el contenido (SHA-256) no coincide — evidencia de modificación. |
| `DESCONOCIDA` | El archivo no está en el índice: puede ser una mesa fuera de cobertura, un E14T (nombrado por hash), o un archivo ajeno. **No implica alteración.** |

**Modo online** ofrece además una verificación de tamaño vía HTTP HEAD (`Content-Length`, sin descarga completa) previa al hash SHA-256 completo, con estados `IGUAL_TAMANIO` / `DIFERENTE_TAMANIO` / `TAMANIO_NO_DISPONIBLE` — mismo principio en dos pasos que `verify_e14c.py` (sección "Nota metodológica 2026-07-02" arriba).

**Auditabilidad del ejecutable:** el `.exe` se distribuye junto con su código fuente (`verificador_e14c.py`) y un script de compilación (`compilar.bat`), de forma que cualquier auditor desconfiado del binario puede recompilarlo y comparar el SHA-256 resultante contra el distribuido.

**Salida:** genera `informe.md` (modo offline) o `informe_tamanio.md` + `informe_online.md` (modo online) con el resumen de la corrida.

> Documentación completa de uso: `Herramientas/verificador-hash-e14/README.md`.

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

### Resultado de verificación por tamaño (HEAD) — 2026-06-30, corregido 2026-07-13

> **Corrección (2026-07-13):** esta sección se titulaba "verificación completa byte a byte" y reportaba `OK: 83,498 / DIF: 20 / ERR: 10`. Al inspeccionar el checkpoint real (`E:\...\E14C\.verify_checkpoint.json`) para validar esta sección, se encontró que **el título sobreestima el método real usado a esa escala**. El checkpoint tiene dos modos independientes:
>
> ```
> head: {ok: 83,486, dif: 0,  err: 35}   ← escaneo por tamaño (HTTP HEAD), corpus completo
> full: {ok: 0,       dif: 30, err: 35}  ← SHA256 byte a byte, deduplicado: 20 dif, 35 err
> ```
>
> El valor **83,498** documentado corresponde al escaneo **HEAD** (comparación de `Content-Length`, sin descarga completa), no a una verificación SHA256 byte a byte del corpus completo — `full.ok` está vacío (`[]`): nunca se registró un solo archivo como verificado byte a byte a esa escala. El modo `--full` (SHA256) solo se corrió sobre el subconjunto ya marcado sospechoso por el escaneo HEAD (~65 archivos), no sobre los ~83,500 disponibles. Además, el checkpoint acumuló corridas posteriores al 2026-06-30 que hicieron crecer el conteo real de `ERR` a **35 archivos únicos** (10 con nombre-hash + 25 con nombre E14C estructurado), no 10 como se documentaba — y el conteo de `DIF` sí se sostiene en **20 archivos únicos** una vez eliminados los duplicados de ruta (10 entradas repetidas con una subcarpeta `e14c_segunda` insertada por error, evidencia de que el script corrió más de una vez desde working directories distintos).

Ejecutado el **2026-06-30** con `verify_e14c.py <carpeta>` (modo HEAD, por defecto) sobre los PDFs E14C disponibles localmente:

| Estado | Cantidad | Acción |
|--------|----------|--------|
| `OK` (HEAD) — tamaño igual al servidor | **~83,486** | Conservados en disco; registrados en checkpoint |
| `DIF` (SHA256, subconjunto sospechoso) — hash diferente | **20** (deduplicado) | Conservados en disco como evidencia de modificación |
| `ERR` (SHA256, subconjunto sospechoso) — error irrecuperable | **35** (deduplicado, al 2026-07-13) | No verificables; detalle abajo |

**Total con verificación HEAD: ~83,500 PDFs.** El corpus disponible localmente al momento de la verificación era inferior al total descargado originalmente (~90,838) porque una fracción de los archivos ya había sido procesada o purgada en corridas previas de verificación por tamaño (HEAD mode). **Pendiente real:** correr `verify_e14c.py --full` sobre el corpus completo para obtener una verificación SHA256 byte a byte genuina — ver instrucciones abajo.

**Desglose de los 35 ERR irrecuperables (al 2026-07-13, deduplicado):**

| Tipo | Cantidad | Causa |
|------|----------|-------|
| Archivos con nombre hash (`{sha256}.pdf`) en subcarpeta `transmission/` | 10 | La convención de nombre no permite reconstruir la URL jerárquica E14C — pertenecen a un pipeline distinto |
| Archivos con nombre estructurado E14C pero URL inaccesible | 25 | La Registraduría no responde para esas URLs específicas (actas posiblemente retiradas del servidor) |

Estos ERR no corresponden a fallos de red transitorios — fueron reintentados en múltiples corridas y el resultado fue consistente. No constituyen evidencia de modificación, sino de inaccesibilidad persistente.

**Estado final del disco:** Todos los archivos verificados se conservan en disco. El checkpoint registra cuáles son OK, DIF o ERR. Los 20 DIF son evidencia de modificación posterior a la descarga original.

### Cómo correr la verificación SHA256 completa (pendiente)

Para obtener una verificación byte a byte real del corpus completo (~118,543 PDFs E14C en `E:\...\E14C\`), no solo del subconjunto sospechoso:

```bash
python verify_e14c.py "E:\Nucleux\tools\Analizador de Elecciones\Data\e14_segunda\E14C" --full
```

- **Es resumible**: el checkpoint (`E:\...\E14C\.verify_checkpoint.json`, clave `full`) se actualiza incrementalmente; si se corta, correr el mismo comando retoma donde quedó (`done_paths` excluye lo ya registrado en `full.ok`/`full.dif`).
- **Es lento**: descarga el PDF completo del servidor por cada archivo para calcular su SHA256 — a diferencia del modo HEAD (por defecto, sin `--full`), que solo compara tamaños. Con ~118,543 archivos, conviene correrlo en tandas o dejarlo corriendo por horas.
- **Opcional — acotar el alcance primero:** `--dept NN` restringe a un departamento, o `--limit N` a una cantidad fija — útil para validar que corre bien antes de lanzarlo contra el corpus completo.
- **Correr siempre desde el mismo working directory** — el bug de paths duplicados con `e14c_segunda` en el checkpoint actual sugiere que corrió antes desde ubicaciones distintas (una vez desde la raíz del repo, otra vez posiblemente desde dentro de la carpeta `E14C`). Ejecutarlo siempre con la ruta absoluta de la carpeta como en el ejemplo de arriba evita repetir ese problema.
- Al terminar, actualizar esta sección con el resultado real (`full.ok` debería acercarse a ~118,500 si no hay más divergencias que las 20 ya conocidas).

### Hallazgo (2026-07-13): bloqueo de tráfico tipo script contra el servidor de segunda vuelta

Al intentar ejecutar la verificación SHA256 completa de arriba, el comando entró en un loop de "Bloqueo detectado. Pausa 60s..." sin progresar — reproducido con `--workers 10` (default) y con `--workers 2` (mismo resultado). Diagnóstico de red en paralelo:

| Prueba | Resultado |
|---|---|
| `curl`/`ping` directo a `escrutinios2vueltapresidente2026...` (segunda vuelta) | Timeout total de conexión TCP; 100% packet loss al ping |
| `curl` a `escrutiniospresidente2026...` (primera vuelta, otro dominio) | HTTP 200 normal |
| `curl` a google.com | HTTP 200 normal (conectividad general OK) |
| Navegador real (Brave/Chrome) contra el mismo dominio de segunda vuelta | **Funciona sin problema** |

**Conclusión:** no es rate-limiting por volumen (2 workers falla igual que 10) ni un problema de red general (otros dominios y el browser funcionan). Es un bloqueo específico a tráfico tipo script (`urllib.request` con solo `User-Agent: Mozilla/5.0`) — muy probablemente fingerprinting TLS (JA3/JA4) o un WAF que distingue por la huella de la conexión, no por volumen. Consistente con por qué E14T/E14D ya requerían Playwright + perfil de Chrome real (sección 3.1.0); la novedad es que la **verificación online de E14C** también está topando con esta protección, aunque la descarga de E14C con `urllib` plano sí funcionó esta mañana (con reintentos — ver sección 3.1.1).

**No es migración, es soporte de dos modos** — `urllib` plano no se descarta: es más simple y liviano, y funciona la mayor parte del tiempo (las descargas de E14C de esta mañana funcionaron bien con reintentos, ver sección 3.1.1). El objetivo es agregar Playwright + perfil de browser real como **modo de respaldo** (fallback) para cuando el servidor bloquea al cliente urllib, no reemplazar el modo liviano por defecto.

**3 métodos candidatos a soportar ambos modos** (liviano `urllib` por defecto + fallback Playwright):

1. `verify_e14c.py --full` (SHA256 completo) — bloqueado hoy en modo urllib, confirmado arriba.
2. `verify_e14c.py` modo HEAD por defecto (sin `--full`) — mismo cliente `urllib`, mismo riesgo de bloqueo (no confirmado aún si ya está afectado o solo el modo `--full`).
3. `verificador_e14c.py` — modo online (`run_verification_online`, `verify_size_online`, `run_both_online`) — usa `download_and_hash()`, también `urllib.request` plano; ya se validó el 2026-07-13 que necesitó 3 intentos para estabilizarse en modo urllib (ver sección 2.5 arriba), y podría degradarse al mismo bloqueo total visto hoy.

Diseño sugerido: mantener `urllib` como intento primario (rápido, sin dependencia de browser); si se detectan N fallos consecutivos (mismo umbral que ya usa `_RATE`), caer automáticamente al modo Playwright con perfil persistente en vez de seguir reintentando en loop indefinido.

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
| **E14T** | Transmisión — copia para preconteo por teléfono | Dictado por teléfono a menida que termina el preconteo por los jurados | `e14segundavueltapresidentet.registraduria.gov.co` |


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

### Fase 1 — Descarga (detalle técnico)

**E14C:**
El colector consulta el índice maestro (`data/index_e14c_segunda.json`, 13,489 puestos). Por cada puesto, descarga el JSON de mesas y filtra las que cumplen:
- `digitalizado = 1` — acta escaneada y cargada
- `escrutado = true` — revisada por la comisión escrutadora

**E14D / E14T:**
El colector consulta el endpoint `allTransmissionCodes` que devuelve el árbol de mesas con estado de transmisión. Se descargan las actas con `status11` (PDF disponible en servidor). El nombre del archivo es un identificador hex de 64 chars (`expectedName`) pre-asignado por la Registraduría.

#### 3.1.0 Dos estrategias de descarga según plataforma

El proyecto descarga PDFs de **dos plataformas con arquitecturas distintas**, lo que genera dos flujos de descarga diferentes:

| | E14C (Claveros) | E14D (Delegados) / E14T (Transmisión) |
|---|---|---|
| Fuente | API REST pública jerárquica | Plataforma de transmisión |
| Organización | Descarga directamente en jerarquía `{DEPT}/{MPIO}/zona_{X}/puesto_{X}/` | Descarga plana en `E14T/` o `E14D/`, luego reorganiza con `scripts/organize_pdfs.py` |
| Índice de trazabilidad | `data/e14c_urls.jsonl` | `data/e14t_sv_urls.jsonl` / `data/e14d_sv_urls.jsonl` |

**Flujo de reorganización (E-14T / E-14D):** `organize_pdfs.py` toma los PDFs descargados con nombre hash, los cruza con el JSONL de URLs (que contiene los códigos geográficos), y los mueve a la jerarquía `{dept}/{mpio}/zona_{X}/puesto_{X}/{hash}.pdf`. El archivo hash original **se conserva como nombre de archivo** — sólo cambia su ubicación.

> Este movimiento de archivos es relevante para el mapping crop→PDF: los crops fueron generados con el path original al momento del procesamiento. Si el PDF fue movido después, el hash `md5(path)[:8]` ya no coincide con el path actual.

#### 3.1.1 Recolección de URLs (E-14C)

```bash
python main.py collect-e14c
python main.py collect-e14c --concurrent 50
```

El colector recorre el índice maestro (`data/index.json` para primera vuelta, `data/index_e14c_segunda.json` para segunda vuelta), visita cada puesto, y registra las URLs de PDFs en `data/e14c_urls.jsonl`. El proceso es **resumible** — usa un checkpoint en `data/e14c_progress.json`.

**Escala:** ~86,929 URLs recolectadas (primera vuelta) · 13,489 puestos procesados (segunda vuelta — descarga completa).

#### 3.1.2 Descarga masiva de PDFs

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

#### Resumen — nombre de archivo, scripts y escala

| | E14C | E14T / E14D |
|---|---|---|
| **Nombre archivo** | `{dept}_{mpio}_{zona}_{puesto}_E14_PRE_..._{version}.pdf`¹ | `{hex64}.pdf` (campo `expectedName` de la Registraduría) |
| **Script descarga** | No identificado con certeza² | `scripts/e14/e14t/turbo_e14.py --source e14t\|e14d` |
| **Checkpoint** | `data/e14c_segunda_progress.json` | por dept, resumible |
| **Storage** | `E:/Nucleux/.../Data/e14_segunda/E14C/` | `E:/Nucleux/.../Data/e14_segunda/E14T/` · `E:/Nucleux/.../Data/e14_segunda/E14D/` |
| **Escala (al 2026-07-04)** | 118,543 PDFs únicos (`MESAS_ALL_THREE`) | ~122,019 PDFs c/u |

> ¹ **Nota (2026-07-14):** el segmento `{zona}` del nombre plano no es confiable para el 19.4% del corpus (22,967/118,543 archivos) — ver el bache `{field4}` documentado en §2.1 y su corrección en el cambio SDD `e14c-url-builder-folder-source-of-truth` (archivado). `{dept}` y `{mpio}` sí son confiables. La carpeta (`zona_XX/puesto_XX/`) es la fuente de verdad real, no el nombre de archivo.
>
> ² **Nota (2026-07-14):** `download_antioquia.py` (referenciado aquí anteriormente) fue confirmado obsoleto y eliminado — nunca escribió en la ruta de producción real (`E:/.../E14C/`). Se investigó cuál script produjo efectivamente los 118,543 PDFs reales y no se encontró un candidato claro entre los scripts existentes del repositorio; queda como bache abierto de trazabilidad, no urgente ya que los datos en disco están íntegros y verificados.

#### Estructura orientada a usuario final y estimado de almacenamiento

La jerarquía técnica de arriba (`E:/Nucleux/.../Data/e14_segunda/{E14C,E14T,E14D}/...`) es específica de este entorno de desarrollo. Para distribución a auditores ciudadanos, la misma jerarquía se traduce a una nomenclatura neutral por vuelta electoral en vez de rutas absolutas del proyecto:

```
{tu_carpeta}/
  {Nombre de Elecciones}/
    Votaciones 1/          ← primera vuelta
      E14C/
        {DEPARTAMENTO}/
          {MUNICIPIO}/
            zona_{XX}/
              puesto_{XX}/
                {archivo}.pdf
      E14T/
        (misma jerarquía)
      E14D/
        (misma jerarquía)
    Votaciones 2/           ← segunda vuelta
      E14C/
        (misma jerarquía)
      E14T/
        (misma jerarquía)
      E14D/
        (misma jerarquía)
```

**Estimado de almacenamiento:** cada acta E14C pesa entre 1.8 y 2.2 MB. Para las 118,543 actas E14C únicas de segunda vuelta esto representa **~250 GB**. Los PDFs de E14T y E14D son sustancialmente más livianos — validado en el ensayo de la sección 3.1.1 (2026-07-13): ~51-53 KB por acta — por lo que el total combinado de las ~244,000 actas E14T+E14D de segunda vuelta agrega solo del orden de **~12-13 GB** adicionales, marginal frente al peso de E14C.

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

### 4.2 Detección de grilla (segunda vuelta)

El pipeline de cross-validación usa **detectores distintos según el tipo de acta**. No hay un único detector universal: E14C (escrutinio oficial) y E14T/E14D (transmisión/delegados) tienen layouts físicos diferentes.

#### E14C — grilla de líneas grises

**Archivo canónico:** `src/modules/analyzer/grid_detector.py` (internamente v3; promovido desde `debug_sv/grid_detector_v3.py` en el cambio SDD `grid-detector-consolidation`, 2026-07-12).

El detector identifica líneas verticales y horizontales grises, construye celdas completas acotadas en los cuatro lados, y etiqueta filas conocidas (`VOTANTES`, `C1_CEPEDA`, `C2_ABELARDO`, `BLANCO`, `NULOS`, `NO_MARCADOS`, `INCINER`, `URNA`, `SUMA_TOTAL`). Consumido por `debug_sv/e14_worker.py` (`_analyze_primary()`, L67–77).

**Resultado validado:** 9/9 filas detectadas correctamente en PDFs reales de segunda vuelta (al 2026-06-22).

> El enfoque de grilla gris superó al enfoque anterior de componentes conectados, que fallaba en celdas vacías y celdas con muy poca tinta.

**Ancla secundaria — marcas de registro (2026-07-14):** `label_rows_by_structure()` (`grid_detector.py`, L881) también usa `detect_4_registration_marks()` — las mismas 4 marcas negras de esquina que calibran E14T/E14D — pero solo como **desempate geométrico**, no como método principal: cuando la grilla de líneas grises detecta un "mega gap" ambiguo entre bloques de filas, la posición relativa de las marcas de registro (`expected_c1_y = top_y + 1693`, delta de calibración fijo) decide cuál de los gaps candidatos corresponde al inicio del bloque C1/C2. Sin ambigüedad, la grilla gris resuelve todo sola y las marcas de registro no se consultan.

#### E14T — marcas de esquina

**Archivo:** `debug_sv/corner_grid_calibrator.py`, invocado desde `debug_sv/e14t_extractor.py` (L13–16). Proyecta las filas de votación como porcentaje del cuadrilátero definido por las 4 marcas de registro negras. No usa la grilla de líneas grises de E14C.

#### E14D — cadena de tres estrategias

**Orquestador:** `src/modules/analyzer/grid_detector_e14d.py::detect_grid()` (L27–36) — cadena de fallback:

1. `grid_detector_corners.py` — marcas de esquina (estrategia preferida).
2. `grid_detector_frame.py` — marco negro impreso de la tabla de votos.
3. Grilla fija de página completa (último recurso).

Umbral de confianza: 0.5. El layout E14D no tiene subceldas grises como E14C; por eso existe esta cadena separada.

> **Estado de integración (módulos en `debug_sv/`):** `e14_worker.py`, `e14t_extractor.py` y `corner_grid_calibrator.py` siguen en `debug_sv/` sin migrar formalmente a `src/modules/analyzer/` — pendiente promover (ver cambio SDD `promote-debug-sv-modules-to-src` y §5).

### 4.3 Extracción de campos numéricos (pipeline de cross-validación)

El motor que generó el mapa de 115,691 mesas de §4.4 **no es** `form_extractor.py`. El flujo operativo de segunda vuelta es:

```
src/modules/analyzer/cross_validator_cli.py::_worker_extract()
  → cross_validator.py::_extract_source()          (L191–239)
     ├─ e14c → debug_sv/e14_worker.py::process_pdf_task()   (L433–507)
     └─ e14t/e14d → debug_sv/e14t_extractor.py::extract_fields()
```

(`debug_sv/cross_validator_cli.py` es un shim de 16 líneas que reenvía al canónico en `src/`.)

#### E14C — arrays ADR-2 + CNN

`e14_worker.py` renderiza la página, detecta grilla vía `grid_detector.py`, recorta subceldas y lee dígitos con `SegmentedEngine` (CNN MobileNetV2, 98.7% val accuracy; EasyOCR como fallback).

**Esquema ADR-2** (`debug_sv/field_array.py`, L24–47): `sources.e14c.fields.{FIELD}` es **siempre** un array de 3 elementos, nunca un escalar:

| Valor | Significado |
|-------|-------------|
| `[None, None, None]` | Celda en blanco real (sin tinta) |
| `["?", "?", "?"]` | Tinta detectada, dígito ilegible |
| `["9", "1", "2"]` | Dígitos leídos → escalar derivable `912` |

Almacenamiento en `e14_worker.py` (L169–184): `fields[label] = digits` (array, nunca escalar).

#### E14T / E14D — escalares

`e14t_extractor.py` devuelve `{label: int | None}` por fila (L17–18), calibrado por marcas de esquina. Mismo extractor físico para E14T y E14D.

> **El motor de OCR es el mismo que E14C, no uno aparte.** `e14t_extractor.py::_ensure_modules()` (L81) importa `SegmentedEngine` desde `src/modules/analyzer/grid_detector.py` — es la **misma instancia de CNN MobileNetV2** que usa el flujo de E14C. Lo único genuinamente distinto entre fuentes es *cómo se encuentran los bordes de las celdas* (líneas grises vs. marcas de esquina) — el modelo que lee los dígitos ya recortados es uno solo, compartido.

#### Filtro de confianza

Antes de la congruencia cruzada, `cross_validator.py::apply_confidence_filter()` (L246–268) enmascara dígitos con confianza < `CONFIDENCE_THRESHOLD = 0.70` como `None`, para no confundir ruido OCR con discrepancias reales entre fuentes.

> **`form_extractor.py` está fuera de este pipeline.** Sigue activo en otros flujos (`main.py` analyze-e14c, `exporter.py`, `revalidate_actas.py`, `prepare_v2_export.py`, `run_sv_chunk.py` para chunks de una sola fuente). Ver §4.5.

### 4.4 Validación aritmética y clasificación de mesas

#### Congruencia cruzada (sin aritmética)

`cross_congruence.py::compare_mesa()` (L152–169) compara dígito a dígito las 3 fuentes (E14C, E14T, E14D) en los 9 campos de voto. `null` nunca concuerda con un dígito legible. No evalúa sumas aquí.

#### Aritmética por fuente

`cross_validator.py::check_arithmetic()` (L302–326) verifica, por cada fuente disponible:

```
C1_CEPEDA + C2_ABELARDO + BLANCO + NULOS + NO_MARCADOS == URNA
```

- **INCINER** se registra pero **no entra en la suma** (igual en todas las vueltas).
- Gate binario: `delta == 0` → `ok: true`; cualquier otro delta → `ok: false`.
- Para E14C, los campos almacenados son arrays ADR-2; la conversión a escalar aritmético usa `field_array.py::derive_scalar()` — solo produce `int` cuando los 3 subceldas son dígitos legibles (`isinstance(scalar, int)` es el gate; ver `debug_stale_aritmetica.py::_fields_to_scalars()`, L29–38). E14T/E14D ya entregan escalares.

> **Igualdad conceptual de 3 vías, verificada solo en 1 tramo (2026-07-14):** el acta física declara tres cantidades que, en una mesa correctamente diligenciada, deberían coincidir entre sí: `URNA` (conteo físico de la urna), `VOTANTES` (total de votantes que sufragaron, según el jurado) y `SUMA_TOTAL` (suma declarada a mano por el jurado). `URNA`, `VOTANTES` y `SUMA_TOTAL` **se extraen los tres** (`cross_validator.py` L32, L40 — están en la lista de campos), pero `check_arithmetic()` **solo compara la suma recalculada de votos contra `URNA`** — `VOTANTES` y `SUMA_TOTAL` quedan almacenados en el JSONL pero **nunca se cruzan automáticamente** contra `URNA` ni contra la suma recalculada. Una mesa donde `URNA` y la suma de votos coincidan pero `VOTANTES` o `SUMA_TOTAL` difieran de ambos **no se detecta hoy** — ver ítem agregado a `BACKLOG.md` para evaluar extender `check_arithmetic()` a esta igualdad de 3 vías.

#### Clasificación global (`overall_status`)

La función canónica es `scripts/upload/upload_mesa_results.py::compute_overall_status()` (L106–158), con `FRAUD_MAX_DIFF = 30` definido localmente (L45). Clasifica cada fila de `cross_mesa_validation_{DD}.jsonl` en 5 estados (prioridad descendente):

| Estado | Condición resumida |
|--------|-------------------|
| `needs_review_large_delta` | Discrepancia cruzada + tachón, o \|delta aritmético\| > 30 en cualquier fuente |
| `discrepancy` | Discrepancia cruzada o delta ≠ 0 |
| `warning` | Tachón sospechoso sin problema aritmético |
| `known_anomaly` | < 2 fuentes, o URNA=0 con votos presentes |
| `clean` | Ninguna de las anteriores |

`scripts/export_clean_mesas.py` (L25) **solo importa** `compute_overall_status()` para exportar `data/clean_mesas.csv` — no la define.

> **Hallazgo resuelto — `overall_status` (2026-07-04 15:12 UTC):** En la cross-validación, el estado `critical` se asignaba cuando el delta aritmético `URNA - suma_votos_candidatos` superaba `FRAUD_MAX_DIFF`. La revisión manual de PDFs reales confirmó que el 66 % de las mesas en ese estado tenían deltas grandes **causados por errores de OCR** — el modelo lee dígitos incorrectamente (p. ej. el campo BLANCO leído como `703` en lugar de `3`, delta = 700). El estado **no indicaba fraude confirmado** sino *"requiere revisión humana por discrepancia numérica grande"*. **Resuelto 2026-07-04:** el estado fue renombrado a `needs_review_large_delta` en código, esquema y base de datos. Pendiente aún: pipeline de re-OCR sobre la celda conflictiva antes de escalar a revisión humana.

> **Hallazgo — estado canónico post-repair (2026-07-11 12:00 UTC):** Tras reparar 60,427 bloques `aritmetica.e14c` corruptos (`debug_sv/repair_stale_aritmetica.py`; causa: `reanalyze_e14c_blanks.py` actualizó dígitos sin recomputar aritmética), el universo completo de **122,019 mesas** quedó reclasificado con `compute_overall_status()`. Upload a Supabase confirmado: processed=122,019, upserted=122,019, errors=0. Detalle completo y comparativa pre/post en `docs/hallazgos_analiticos.md` **[6]**.
>
> | Estado | Mesas | % del total (122,019) |
> |---|---|---|
> | `needs_review_large_delta` | 74,963 | 61.4% |
> | `discrepancy` | 32,602 | 26.7% |
> | **`clean`** | **9,424** | **7.7%** |
> | `warning` | 2,648 | 2.2% |
> | `known_anomaly` | 2,382 | 2.0% |
> | **Total procesado** | **122,019** | — |
>
> Las 9,424 mesas `clean` pasaron los filtros de `compute_overall_status()`: sin `cross_discrepancy`, sin delta aritmético > 0 **cuando fue computable**, sin tachón sospechoso, sin anomalía de fuentes. Exportadas a `data/clean_mesas.csv` (re-export 2026-07-14). Solo **803** tienen lectura E14C completa + `suma == URNA` — ver `hallazgos_analiticos.md` **[2]**.
>
> **Interpretación del 61.4% en `needs_review_large_delta`:** este porcentaje **no equivale a tasa de fraude**. El umbral `FRAUD_MAX_DIFF = 30` detecta cualquier delta > 30 votos; errores OCR (~1.3% de dígitos) generan deltas artificialmente grandes (p. ej. `003` → `703` = delta 700). Tras el repair, −14,040 mesas salieron de `needs_review_large_delta` hacia `discrepancy` — campos parcialmente ilegibles reclasificados correctamente. El mapa sirve como **triage priorizado**, no como dictamen.
>
> **Histórico — primer mapa parcial pre-repair (2026-07-05):** 115,691 mesas (34/34 depts, dataset incompleto + aritmética stale). Tabla y contexto en `docs/hallazgos_analiticos.md` **[1]** — **supersedido por [6]**.
>
> **Hallazgos analíticos vinculados (2026-07-14 17:27 UTC):** Cifras detalladas y tablas completas viven en `docs/hallazgos_analiticos.md` — no duplicar aquí:
>
> | ID | Tema | Resumen |
> |---|---|---|
> | [2] | Resultados en mesas `clean` | 9,424 `clean`; subconjunto estricto 803 mesas — C1 50.4% vs C2 46.8% (no representativo) |
> | [3] | Conflictos `val_vs_val` | VOTANTES/URNA/SUMA con más conflictos reales que candidatos; C1 > C2 en +780 |
> | [4] | Totales vacíos E14C | 13.5% mesas con al menos un campo de resumen en 0 |
> | [5] | Totales vacíos por fuente | E14C 22% vs E14D/E14T ~5–6% — brecha 3.5–4× |

### 4.5 Legado: `form_extractor.py` (fuera del pipeline de cross-validación)

`src/modules/analyzer/form_extractor.py` es un extractor de **regiones de píxeles fijas** calibrado en AMAZONAS/LETICIA (1260×3897 px, layout de 13 candidatos). **No lo importa** el pipeline de cross-validación (`cross_validator.py`, `e14_worker.py`, `e14t_extractor.py` nunca lo cargan).

Sigue teniendo usos reales fuera de ese flujo:

| Caller | Uso |
|--------|-----|
| `main.py` | `analyze-e14c` (primera vuelta) |
| `exporter.py` | Export de crops |
| `revalidate_actas.py` | Revalidación de actas |
| `prepare_v2_export.py` | Helpers de render |
| `run_sv_chunk.py` | Runner de chunk de una sola fuente sobre PDFs SV |

#### Flags propios de `form_extractor.py` (no aplican al clasificador de §4.4)

Estos flags alimentan `is_suspicious` / `needs_review` dentro de `form_extractor.py`, **no** el mapa de 5 estados de `compute_overall_status()`:

**Fraude aritmético** (`flags` → `is_suspicious = True`) — diferencia ≤ `FRAUD_MAX_DIFF = 30`:

| Flag | Condición |
|------|-----------|
| `ARITMETICA_SUMA` | `suma_total` declarada ≠ suma calculada; diferencia ≤ 30 |
| `URNA_VS_SUMA` | `total_urna` ≠ `suma_total`; diferencia ≤ 30 |
| `VOTOS_EXCEDEN_VOTANTES` | `total_urna` > `total_votantes`; exceso ≤ 30 |
| `VALOR_NEGATIVO` | Cualquier candidato con valor negativo |

**Revisión OCR** (`ocr_flags` → `needs_review = True`):

| Flag | Condición |
|------|-----------|
| `OCR_SUMA_DUDOSA` | `suma_total` difiere del calculado en > 30 votos |
| `URNA_NO_DILIGENCIADA` | `total_urna = 0` con votos reales presentes |
| `OCR_URNA_DUDOSA` | `total_urna` difiere de `suma_total` en > 30 votos |
| `OCR_VOTOS_DUDOSOS` | `total_urna` > `total_votantes` por más de 30 votos |

> `URNA_NO_DILIGENCIADA` se trata como revisión y no como fraude porque es común que los jurados dejen el campo "Total votos en urna" en blanco. Los checks `URNA_VS_SUMA` y `VOTOS_EXCEDEN_VOTANTES` se omiten cuando este flag está presente. Valores fuera de 0–999 se marcan como misread (`MAX_MESA_VOTES = 999` en `form_extractor.py`).

---

## 5. Detección de tachones y enmiendas

> **Log histórico de investigación (2026-06-22/23):** [`docs/metodologia_tachones.md`](metodologia_tachones.md) documenta los experimentos de calibración que precedieron la integración en `e14_worker.py` — no menciona `subcell_tachon.py`, `candidate_subcells.py` ni el pipeline actual de cross-validación. Tratarlo como **bitácora de investigación**, no como especificación operativa vigente.

**Estado de integración:** la detección está activa en el pipeline de cross-validación por dos rutas:

1. **Enriquecimiento por subcelda** — `debug_sv/e14_worker.py::_analyze_primary()` (L62) llama a `debug_sv/subcell_tachon.py::build_subcell_payload()` por cada dígito con tinta; alimenta `tachon_summary` vía `_build_field_groups()` (L471–479).
2. **Scan agregado por PDF** — `_run_tachon_scan()` en `src/modules/analyzer/cross_validator_cli.py` (L56–107) carga `debug_sv/tachon_method_scan.py` y escribe `tachones.e14c` en cada fila del JSONL.

Los módulos fuente (`subcell_tachon.py`, `tachon_method_scan.py`) siguen en `debug_sv/` — pendiente migrar a `src/modules/analyzer/tachon_scanner.py` (ver BACKLOG y cambio SDD `promote-debug-sv-modules-to-src`, cuya exploración necesita **re-validación** antes de retomarla).

> **Misma deuda arquitectónica en §4:** los módulos de extracción documentados en §4.2–4.3 comparten este patrón (`debug_sv/` operativo, sin promoción formal a `src/`).

> **Bugfix (2026-07-14) — import roto desde 2026-07-12:** `grid-detector-consolidation` archivó `debug_sv/grid_detector_v2.py`, pero `subcell_tachon.py:7` seguía importando `CELL_PAD` desde esa ruta. Reproducción **antes** del fix:
> ```
> python -c "from debug_sv.subcell_tachon import build_subcell_payload"
> ModuleNotFoundError: No module named 'debug_sv.grid_detector_v2'
> ```
> **Después** del fix: `from src.modules.analyzer.grid_detector import CELL_PAD` (`grid_detector.py:141`). Impacto mientras estuvo roto:
> - **Path A (extracción E14C):** `process_pdf_task()` capturaba el error → `extraction_error` visible por PDF (`e14_worker.py:509-515`, `cross_validator.py:227-228`).
> - **Path B (scan agregado):** `_run_tachon_scan()` tragaba la excepción → `tachones.e14c = null` sin log (`cross_validator_cli.py:80-81`).
> El batch principal (115,691 mesas, 2026-07-05) es **anterior** al bug. Auditoría/re-scan de corridas post-2026-07-12: ver BACKLOG.

Se aplican cuatro métodos de detección sobre cada subcelda con tinta:

| Método | Descripción | Umbral |
|--------|-------------|--------|
| `TACHON` | Densidad de tinta horizontal (tachón clásico) | 0.45 |
| `DOBLE_ESCRITURA` | Superposición de dígitos | 0.50 |
| `DENSIDAD_ALTA` | Alta densidad de píxeles en zona de dígito | 0.60 |
| `ZONA_SUCIA` | Ruido generalizado en la celda | 0.50 |

> **Caveat — TACHON como ruido universal:** `metodologia_tachones.md` ("Lección aprendida #1", L172) documenta que TACHON flaggea el **93.3%** de subceldas con tinta y aparece en el **100%** de PDFs — por eso fue **excluido de los scans de producción** (batches sin TACHON). La fórmula de score combinado abajo conserva peso 0.40 para TACHON por decisión histórica de la integración en runtime; **revisar alineación peso vs exclusión de producción** queda en BACKLOG — no cambiar aquí sin análisis de scoring.

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

**Limitación actual del sistema:** La validación aritmética del pipeline de cross-validación (`check_arithmetic()` en §4.4) verifica que los nulos estén incluidos correctamente en la suma por acta, pero no detecta ratios anómalos de nulos dentro de un contexto geográfico. Esta detección requiere análisis estadístico comparativo entre mesas, no validación por acta individual.

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

Para cada PDF procesado, se recortan celdas completas (`full_cell`) y subceldas (`sub_{LABEL}_{idx}` o equivalente plano). Hay **tres caminos** en producción hoy — no confundir paths ni esquemas de hash:

#### Primera vuelta — exporter unificado (`src/modules/labeler/exporter.py`)

Invocado vía `main.py export-crops` / `main.py label` (`main.py` L220–238):

```
data/labels/crops/
  {sha1_key}.png   ← archivos planos, sin subdirectorios por PDF
  index.jsonl
```

**16,034** subceldas PNG en disco (conteo 2026-07-14). Identificador determinístico:

`sha1(f"{pdf_path}|{field_name}|{digit_index}")[:16]` (`exporter.py::_make_crop_id`, L46–48).

Anotaciones históricas exportadas a `data/labels_primera_vuelta_export.json`. El path `data/labels_v2/` figura en scripts legacy (`prepare_v2_export.py`, `setup_local_portal.py`) pero **no existe** como directorio vivo de producción.

#### Segunda vuelta — camino A: dump Antioquia (`debug_sv/antioquia_crops.py`)

Script aislado, hardcodeado a Antioquia, orientado a volumen masivo + upload Supabase:

```
E:/e14c_segunda/crops/antioquia/{pdf_hash}/
  full_{LABEL}.png
  sub_{LABEL}_{idx}.png
```

`pdf_hash = md5(pdf_path.encode()).hexdigest()[:8]` (`antioquia_crops.py` L58). Cifras de volumen según `BACKLOG.md` L148: ~392,088 crops / 11,981 PDFs (no re-verificado en disco `E:/` en esta sesión).

#### Segunda vuelta — camino B: exporter unificado (`MODULE=segunda`)

Mismo `exporter.py` y **mismo esquema sha1** que primera vuelta. En producción, `server.py` resuelve `LABELS_DIR` a `data/labels_segunda/` cuando `MODULE=segunda` (`server.py` L788–815):

```
data/labels_segunda/crops/
  {sha1_key}.png
```

**2,998** PNGs en disco (conteo 2026-07-14). Fuente PDF: `data/pdfs_e14c_segunda/`.

> **Nota sobre hashes:** el esquema **md5(path)[:8]** de `antioquia_crops.py` (camino A) **no es intercambiable** con el **sha1(pdf|field|idx)[:16]** del exporter (primera vuelta + camino B). Entre primera vuelta y camino B de segunda vuelta el esquema sha1 **sí** es el mismo — solo cambia el directorio raíz (`data/labels/` vs `data/labels_segunda/`).

### 6.2 Portal de etiquetado

**Producción (uso real):** [https://analizadore14.porciudad.com/work](https://analizadore14.porciudad.com/work) — requiere cuenta y rol `validator` o `admin`. La home pública (`/`) enlaza a esta ruta vía `work_url` cuando la verificación está abierta (`home.html` L136, `server.py` L894: `/work` en producción).

El portal permite a anotadores humanos clasificar crops de dígitos. Usa **Supabase** (PostgreSQL + Storage) con autenticación JWT. El sistema de cola asigna crops a anotadores evitando duplicación.

> **Dev local (opcional):** `python main.py label` levanta el mismo `create_app()` en `localhost:5000`, pero con `work_url="/"` en lugar de `/work`. No es el entorno donde operan los voluntarios en producción.

> **Mismo Flask app que §6.3:** §6.2 y §6.3 comparten `src/modules/labeler/server.py::create_app()` (L735). La cola de etiquetado vive en `/work`, `/next`, `/label` (L1274, L1380+). El semáforo ciudadano (§6.3) expone el estado agregado en `/` y `/mesas` (L1265).

**Estado del upload — última cifra confirmada 2026-07-04 (`BACKLOG.md` L144–152):** Los ~392,088 crops de Antioquia (camino A, §6.1) están generados localmente; el upload a Supabase Storage quedó en ~12% y no hay evidencia documentada de avance desde entonces. **No re-verificado en vivo** en esta sesión (sin acceso a Supabase). Bloqueante para etiquetado masivo de segunda vuelta vía Storage.

**Concordancia inter-anotador:** Se calcula por crop cuando hay múltiples anotaciones. Sirve como métrica de calidad del conjunto de entrenamiento.

### 6.3 Sistema de semáforo por mesa (portal ciudadano)

> **Mismo Flask app que §6.2:** el semáforo (`/`, `/mesas`) es la capa de estado público; la revisión dígito a dígito ocurre en `/work` (§6.2).

El portal en producción ([analizadore14.porciudad.com](https://analizadore14.porciudad.com)) combina dos capas: (a) un **semáforo público** de estado por mesa/departamento (`/`, `/mesas`) y (b) la **misma UI de etiquetado** de §6.2 en `/work`, donde los voluntarios confirman o corrigen dígitos — priorizando mesas con irregularidades (`priority` 0 = requiere revisión, 1 = sospechoso).

**Flujo:**
1. El sistema identifica las mesas con posibles irregularidades y recorta las celdas con dígitos dudosos.
2. El voluntario entra a `/work`, ve el crop del dígito junto con la sugerencia OCR y el contexto de la celda completa (`label.html`).
3. Confirma o corrige la lectura con el teclado numérico de la UI (`label.html` L524–542):
   - **Enter** — confirma la sugerencia OCR tal cual.
   - **0–9** — envía el dígito directamente.
   - **Variantes de cero** — `*`, `-`, `.` (botones; tecla `+` equivale a `*`). El backend también acepta `o`, `O`, `/`, `//`, `///` vía campo de texto (`ZERO_VARIANTS` en `db.py` L68; `VALID_REPORT_GLYPH_RE` en `server.py` L612).
   - **Esc** — saltar crop; **F** — marcar fraude; **← Atrás** — retroceder en la cola.
   - **Campo de texto** — entrada manual (modo fallback multi-dígito).
   - **📋 Reportar** — modal para enmienda, mesa u otro hallazgo (producción/Supabase).

**Estados del semáforo:**

| Estado | Significado |
|--------|-------------|
| ⚪ Sin alerta | El algoritmo no detectó irregularidades aritméticas |
| 🔴 Alerta | El algoritmo detectó una posible irregularidad — pendiente de revisión humana |
| 🟡 En revisión | Al menos un voluntario está revisando los dígitos de esta mesa |
| 🟢 Revisada | Los voluntarios confirmaron los datos — resultado disponible |

**Resultado final de una mesa revisada:**
- **Mesa Limpia** — la suma de votos por candidato cuadra con el total declarado en el acta.
- **Posible Fraude** — la suma de votos no cuadra con el total; discrepancia confirmada por revisión humana.

**Implementación:** `src/modules/labeler/db.py::_get_review_semaphore_uncached()` calcula ⚪/🔴 vía la RPC `get_review_semaphore_by_dept` (Supabase) y clasifica cada mesa en `en_revision` / `revisada`, con `revisada_result` ∈ {`mesa_limpia`, `posible_fraude`, `sin_datos`} cuando `revisada = True`. Los conteos globales se exponen a las plantillas `home.html` y `mesas.html` vía `get_mesa_semaphore_stats()` (TTL de caché de 5 minutos).

> **Nota de correspondencia interna:** este semáforo de 4 estados es la capa de comunicación ciudadana sobre el resultado del pipeline técnico. Es una clasificación **paralela e independiente** a `compute_overall_status()` (sección 4.4), que clasifica cada mesa en 5 estados (`clean`, `needs_review_large_delta`, `discrepancy`, `known_anomaly`, `warning`) a partir de `aritmetica.e14c`. El semáforo del portal, en cambio, opera sobre el estado de anotación humana (`priority_total`/`priority_confirmed`) y compara `candidato_sum` contra `total_urna_val` directamente — no consume `overall_status`. Ambas clasificaciones pueden divergir para la misma mesa; no hay un mapeo formal entre las dos actualmente.

---

## 7. Modelo CNN de clasificación de dígitos

### v1 — producción actual

- **Arquitectura:** MobileNetV2 (transfer learning desde ImageNet)
- **Clases:** 10 (dígitos 0–9)
- **Precisión en validación:** 98.69% (`models/training_report.json`, `best_val_acc`)
- **Pesos:** `models/digit_classifier.pth`
- **Umbral de confianza en cross-validación:** 70% (`CONFIDENCE_THRESHOLD = 0.70` en `cross_validator.py`) — dígitos con confianza menor se enmascaran como `None` para no confundirlos con discrepancias reales entre fuentes
- **`needs_review`** — en el pipeline de cross-validación, dígitos con confianza < 70% se enmascaran como `None` (no generan `needs_review` por sí solos). En `form_extractor.py` (§4.5 legado), `needs_review` se activa por flags OCR implausibles
- **`is_suspicious`** — en `form_extractor.py` (§4.5), por flags de fraude aritmético; en cross-validación, tachones sospechosos alimentan `warning` / `needs_review_large_delta` vía `compute_overall_status()`

### Reentrenamiento Model A / Model B — estancado desde 2026-06-23

Lo implementado y archivado en `openspec/changes/archive/2026-06-23-retrain-digit-classifier/` es un experimento **dual fine-tune** (`train_dual_finetune.py`), **no** el "CNN v2" de 12 clases que §7 describía antes.

| Variante | Estrategia | Checkpoint |
|----------|------------|------------|
| **Model A** | Conservador — features congeladas (`build_model_a`, L120–124) | `models/digit_classifier_model_a.pth` |
| **Model B** | Agresivo — discriminative LR (`build_model_b`, L128–132) | `models/digit_classifier_model_b.pth` |

- **Clases:** 10 (dígitos 0–9) — `split_dataset.py::normalize_label()` (L44–55) colapsa variantes de cero a `"0"` para entrenamiento.
- **Métricas CPU (representativas, no GPU):** Model A 98.69% val (`models/training_report.json`); recomendación Model A sobre B (`models/dual_finetune_report.txt` L24).
- **Estado:** estancado desde 2026-06-23. El `archive-report.md` dejó pendiente un **GPU re-run** antes de promover — sin evidencia de ejecución posterior (ver BACKLOG).
- **Producción sin cambio:** `models/digit_classifier.pth` sigue siendo el modelo activo en inferencia.

> **Origen del drift "CNN v2 / 12 clases":** el texto viejo copió la propuesta **nunca implementada** `openspec/changes/digit-classifier-v2/proposal.md` (checkpoints `digit_classifier_v2_*` — **cero** artefactos en el repo). No describir como entrenado.

### Etiquetado de anulaciones — objetivo vigente (`ZERO_VARIANTS`)

Separar símbolos de anulación en clases propias **no es una meta abandonada** — la capa de etiquetado ya captura más riqueza de la que el modelo de producción usa hoy:

- **Portal (9 símbolos):** `src/modules/labeler/db.py::ZERO_VARIANTS` (L68) = `{"*", "-", ".", "+", "o", "O", "/", "//", "///"}` — los anotadores humanos ya los usan.
- **Preservación cruda:** `label_ocr` guarda el glifo real anotado (p. ej. L313+) para minería y análisis forense posterior.
- **Colapso en entrenamiento actual:** `_normalize_label()` / `split_dataset.py::normalize_label()` mapean variantes de cero → clase `"0"` (el script de entrenamiento reconoce **6** de los 9 símbolos: `*`, `-`, `.`, `+`, `o`, `O`).
- **Dirección del proyecto:** ampliar el modelo futuro para que distinga estos símbolos como clases propias, usando datos ya acumulados en `label_ocr` — el modelo v1/A/B de 10 clases aún no lo implementa.

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

## 8. Estado general del dataset (sincronizado 2026-07-14)

### Primera vuelta

| Métrica | Valor |
|---------|-------|
| PDFs E14C descargados | ~26,194 |
| Crops etiquetados (primera vuelta) | 16,034 subceldas (`data/labels/crops/`) |
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
| Cross-validación completada | 34 departamentos — **122,019 mesas** (universo completo, post-repair 2026-07-11) |
| Mapa de estados canónico (automatizado, sin revisión humana) | 9,424 `clean` · 74,963 `needs_review_large_delta` · 32,602 `discrepancy` · 2,382 `known_anomaly` · 2,648 `warning` — ver §4.4 y `hallazgos_analiticos.md` [6] |
| Mesas limpias exportadas | `data/clean_mesas.csv` — **9,424** mesas (re-export 2026-07-14, post-repair [6]) |
| Log de hallazgos cuantitativos | `docs/hallazgos_analiticos.md` — [1]–[6]; complementa esta metodología |
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
| `models/digit_classifier.pth` | 10 (0–9) | 98.69% val | Producción |
| `models/digit_classifier_model_{a,b}.pth` | 10 (0–9) | 98.69% val (Model A, CPU) | Reentrenamiento estancado 2026-06-23 — GPU re-run pendiente (BACKLOG) |

---

## 9. Pendientes y próximos pasos

### Resultados y datos — Alta prioridad

- [x] Re-correr `scripts/export_clean_mesas.py` post-repair — `data/clean_mesas.csv` actualizado (9,424 mesas, 2026-07-14); hallazgo [2] recalculado
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
- [x] ~~Integrar `grid_detector_v2` al módulo principal~~ — **completado 2026-07-12** (`grid-detector-consolidation`): canónico en `src/modules/analyzer/grid_detector.py`
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

Con los 34 departamentos cross-validados (**122,019 mesas**, estado canónico post-repair §4.4), el estado `needs_review_large_delta` representa el **61.4% del total procesado** (74,963 mesas). Si ese porcentaje varía significativamente entre departamentos o zonas, la variación misma es una señal.

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

### Estado actual del pipeline E14C (2026-07-14 17:27 UTC)

Los 118,563 PDFs E14C de segunda vuelta están descargados en `E:\Nucleux\tools\Analizador de Elecciones\Data\e14_segunda\E14C\`. La **cross-validación** cubre los 34 departamentos y **122,019 mesas** en `data/cross_mesa_validation_*.jsonl` (estado canónico post-repair — §4.4, `hallazgos_analiticos.md` [6]).

> **Nota de desambiguación (2026-07-14 17:27 UTC):** Los JSONL `data/analysis_e14c_{dept}.jsonl` del comando `main.py analyze-e14c` (pipeline `form_extractor.py`, §4.5) son un **artefacto distinto** al de cross-validación. Una nota anterior (2026-07-06) listaba departamentos con E14C `not_available` en cross_mesa — **obsoleta** tras completar 34/34 y el repair de aritmética. Cobertura OCR standalone por departamento: ver `mem_search(topic: pipeline/e14c-ocr-coverage)` o re-verificar en disco antes de confiar en tablas históricas.

Hallazgos cuantitativos sobre campos vacíos y conflictos entre fuentes: `docs/hallazgos_analiticos.md` **[4]** y **[5]**.

