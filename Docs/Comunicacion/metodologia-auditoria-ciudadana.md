# Metodología de Auditoría Ciudadana — Actas E-14
### Verificación Ciudadana | analizadore14.porciudad.com | Colombia 2026
**Versión:** 2026-07-05

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

- **Plataforma de escrutinio oficial (E-14C)** — actas digitalizadas y escrutadas por la comisión escrutadora.
- **Plataforma de transmisión (E-14T)** — actas dictadas por teléfono para el preconteo informativo en tiempo real.
- **Plataforma de delegados (E-14D)** — actas escaneadas en el puesto de votación la noche electoral.

El sistema cubre **ambas vueltas electorales**:

| Vuelta | Estado | Actas disponibles |
|--------|--------|-------------------|
| **Primera vuelta** | Pausada (análisis no terminado) — segunda vuelta es prioritaria | ~26,194 actas E14C (Claveros) |
| **Segunda vuelta** | Descarga completa — análisis transversal completo (34 departamentos, 115,741 mesas) | 118,543 actas únicas — E14C (Claveros) + E14T (Transmisión) + E14D (Delegados) |

El objetivo del sistema es detectar posibles irregularidades: tachones, enmiendas, errores aritméticos, y patrones anómalos en los datos de votación.

---

## 2. Fuentes de datos

Los jurados de votación diligencian a mano **tres copias físicas idénticas** del formulario E-14 el mismo día de la elección. Aunque las llena el mismo jurado en la misma mesa, son documentos independientes — pueden presentar diferencias por tachaduras o errores humanos distintos en cada copia.

Cuando esas diferencias son pequeñas y aisladas, corresponden a errores humanos normales del proceso. Cuando los números de una copia difieren sistemáticamente de las otras dos — especialmente en la copia E14C (Claveros), que es la base del escrutinio oficial — esa divergencia es una señal de posible irregularidad que merece revisión. Si la diferencia favorece a un candidato específico o se repite en varias mesas de la misma zona, el patrón adquiere mayor relevancia forense.

La comparación entre las tres versiones tiene precisamente ese valor: detectar cuándo una diferencia deja de ser un error y se convierte en una anomalía.

| Copia | Destino físico | Cuándo se digitaliza | Valor legal |
|---|---|---|---|
| **E14C** (Claveros) | Sobre sellado → arca triclave → sede comisión escrutadora | Días después, en la comisión | **Máximo** — base del escrutinio oficial |
| **E14D** (Delegados) | Se escanea en el puesto de votación la misma noche | Noche electoral | Referencia inmediata |
| **E14T** (Transmisión) | Se dicta por teléfono para el preconteo informativo | Noche electoral | Referencia inmediata |

El E14C (Claveros) es el documento con **máximo valor legal y probatorio** — es la referencia para resolver reclamaciones. El E14D (Delegados) y el E14T (Transmisión) sirven para transparencia inmediata y preconteo informativo.

### Organización de los archivos descargados

Los PDFs se organizan en carpetas locales siguiendo la jerarquía geográfica oficial:

```
{tu_carpeta}/
  {Nombre de Elecciones}/
    Votaciones 1/
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
    Votaciones 2/
      E14C/
        (misma jerarquía)
      E14T/
        (misma jerarquía)
      E14D/
        (misma jerarquía)
```

Cada acta pesa entre 1.8 y 2.2 MB. Para la segunda vuelta completa (118,543 actas) se requieren aproximadamente 250 GB de almacenamiento.

---

## 2.4 Observación: variabilidad en los datos del índice de la Registraduría

### El proceso de recolección

Para obtener las actas, el sistema consulta un archivo de índice publicado por la Registraduría en su servidor. Ese índice es un listado que mapea cada mesa de votación a la ubicación de su acta en formato PDF. A partir de ese listado, el sistema descarga automáticamente cada PDF y lo almacena localmente. No se accede a ningún sistema privado ni se requieren credenciales — toda la información proviene de URLs públicas del servidor oficial de la Registraduría.

### El índice de la Registraduría

El índice es un archivo JSON disponible en el servidor de escrutinio de la Registraduría. Contiene una entrada por cada mesa, con la ruta al PDF correspondiente. No es una página web ni un portal visual — es un archivo de datos que el sistema consulta directamente mediante una petición HTTP estándar, de la misma forma en que un navegador descarga cualquier recurso de internet.

Durante el proceso de recolección se realizaron dos consultas al mismo índice en fechas distintas. Los resultados obtenidos difieren de manera significativa:

| Métrica | 2026-06-21 | 2026-06-30 | Variación |
|---|---|---|---|
| Total mesas registradas | 1,400 | 122,019 | +8,614% |
| Actas de consulados | 684 | 3,670 | +437% |
| Actas sin subir | 23 | 0 | −100% |

El índice de la Registraduría es dinámico: no tiene un estado fijo. Su contenido refleja exactamente lo que ha sido subido al servidor en el momento de la consulta. La diferencia entre 1,400 y 122,019 mesas no describe necesariamente un cambio ocurrido durante los nueve días entre consultas — la misma diferencia podría haberse observado entre dos consultas realizadas el mismo 21 de junio con horas de diferencia, dependiendo del ritmo de subida. La ventana de tiempo entre las dos capturas no es el dato relevante.

Esta variación no permite afirmar que hubo fraude. La carga progresiva de actas es un comportamiento técnico esperado. Lo que sí permite afirmar es que el sistema **no es transparente en su estado actual**: no existe una forma pública de saber cuántas actas estaban disponibles en un momento dado, ni cuándo se subió cada una, ni si alguna fue modificada o reemplazada.

Lo que la variación evidencia es una ausencia de transparencia estructural:

1. El estado del índice en cualquier momento pasado es **irreconstruible desde la misma fuente**. No existe un historial accesible públicamente. La única forma de saber qué mostraba el servidor en un instante determinado es disponer de una captura realizada en ese instante.
2. No hay un acto formal que marque cuándo una mesa "entra" al sistema, ni un registro público de modificaciones posteriores.
3. Un sistema electoral transparente debería publicar un registro inmutable de cuándo se subió cada acta y si fue reemplazada. La ausencia de ese registro no es evidencia de fraude, pero sí es una falla de transparencia que impide descartarlo con certeza.

**Las capturas realizadas en este proyecto constituyen el único registro disponible del estado histórico de la plataforma en esas fechas.**

---

## 2.5 Verificación de integridad byte a byte — E14C

### Herramientas disponibles

La verificación de integridad se realiza con dos herramientas disponibles en el repositorio del proyecto:

| Herramienta | Descripción | Requiere internet |
|---|---|---|
| `verify_e14c.py` | Compara los PDFs locales contra el servidor de la Registraduría en tiempo real. Disponible en el repositorio del proyecto. | Sí |
| `verificador_e14c.py` / `verificador_e14c.exe` | Compara los PDFs locales contra un índice de huellas digitales pre-generado (snapshot del 2026-06-30). Funciona sin conexión. Disponible en la carpeta `Herramientas/verificador-hash-e14/` del repositorio. | No |

Ambas herramientas aplican dos pruebas de integridad en secuencia:

1. **Prueba de tamaño** — compara el tamaño en bytes del archivo local contra el tamaño reportado por el servidor a través de las cabeceras HTTP, sin descargar el contenido completo. Es la prueba rápida: si el tamaño difiere, hay divergencia confirmada sin necesidad de continuar.
2. **Prueba de contenido (SHA256)** — descarga el archivo del servidor y calcula la huella digital SHA256 de ambas copias. Es la prueba definitiva: dos archivos con el mismo tamaño pero SHA256 diferente tienen contenido distinto byte a byte.

### Metodología

Los PDFs E14C fueron descargados de la plataforma oficial de escrutinio aproximadamente el 22 de junio de 2026. Con el fin de detectar modificaciones posteriores a esa descarga, el sistema ejecuta ambas pruebas por cada archivo local.

El procedimiento es el siguiente:

1. Por cada PDF descargado, se construye la URL del servidor a partir del nombre del archivo.
2. Se consultan las cabeceras HTTP del servidor — si el tamaño difiere, se registra divergencia de tamaño sin descargar el contenido.
3. Si el tamaño coincide, se descarga el archivo completo y se calcula la huella SHA256 de ambas copias.
4. Si las huellas coinciden, el archivo no fue modificado.
5. Si las huellas difieren, se registra como divergencia confirmada. Ambas versiones se conservan como evidencia.

### Interpretación de resultados

| Estado | Significado |
|--------|-------------|
| **SIN CAMBIOS** | Huella local = huella servidor — archivo sin modificaciones |
| **DIVERGENCIA** | Huella local ≠ huella servidor — modificación confirmada entre la descarga original y la verificación |
| **INACCESIBLE** | El servidor no responde para esa URL — el acta puede haber sido retirada |

Los archivos con **DIVERGENCIA** constituyen evidencia de que el contenido del acta en la plataforma oficial fue alterado después de la descarga original.

### Hallazgo (2026-06-30): Re-subidas confirmadas por divergencia de tamaño

Durante la verificación se identificaron **5 PDFs con tamaño en el servidor significativamente distinto al archivo local**. La comparación confirmó que **son actas distintas**: el contenido de los PDFs del servidor difiere del contenido original descargado localmente.

| Acta | Dept / Mpio / Zona / Puesto / Mesa | Tamaño local | Tamaño servidor | Variación |
|---|---|---|---|---|
| Antioquia / Medellín | 01 / 001 / 90 / 02 / 049 | 626 KB | 5,538 KB | **+780%** |
| Córdoba | 13 / 031 / 99 / 11 / 002 | 1,389 KB | 8,323 KB | **+499%** |
| Santander | 27 / 071 / 00 / 00 / 006 | 1,018 KB | 3,115 KB | **+206%** |
| Santander / Bucaramanga | 27 / 001 / 04 / 01 / 014 | 1,251 KB | 1,281 KB | +2.4% |
| Guaviare | 54 / 007 / 00 / 00 / 003 | 1,206 KB | 1,223 KB | +1.4% |

Ambas versiones (la original descargada y la actual del servidor) se conservan en disco como evidencia.

### Resultado de verificación completa byte a byte (2026-06-30)

| Estado | Cantidad |
|--------|----------|
| Sin cambios — huella SHA256 idéntica | **83,498** |
| Divergencia — huella SHA256 diferente | **20** |
| Inaccesible — error irrecuperable | **10** |

**Total verificado: 83,538 actas.** Los 20 casos de divergencia son evidencia de modificación posterior a la descarga original.

### Verificación de seguimiento (2026-07-02)

Para las 20 mesas con divergencia confirmada, se realizó una segunda verificación el 2 de julio de 2026:

| Estado | Cantidad | Significado |
|--------|----------|-------------|
| **Sigue diferente** | **18** | El servidor continúa sirviendo una versión distinta. Modificación activa y persistente |
| **Ahora igual** | **2** | El servidor volvió a coincidir con la copia local. Fueron modificadas y revertidas sin trazabilidad pública |

Las 2 actas revertidas corresponden a:

| Mesa | Ubicación |
|------|-----------|
| Bogotá D.C. | zona 15 / puesto 06 / mesa 025 |
| Tolima / Ibagué | zona 11 / puesto 03 / mesa 020 |

Para estas dos actas, la modificación y la reversión ocurrieron sin registro público visible. La versión del servidor en el momento de la reversión se conserva como constancia de que el contenido fue distinto al menos en algún momento entre el 22 de junio y el 2 de julio.

---

## 2.6 Análisis de re-subidas por clase de acta (2026-06-30)

### Hallazgo: E14C — 1 acta con versión revertida

Comparando 90,838 actas E14C descargadas (~22 de junio) contra el índice del servidor (24 de junio):

| Métrica | Valor |
|---|---|
| Actas comparables | 90,838 |
| Sin cambio de versión | 90,837 |
| Versión revertida | **1** |

El único caso anómalo:

| Campo | Valor |
|---|---|
| Mesa | Córdoba / Lorica / zona 99 / puesto 05 / mesa 3 |
| Versión descargada (~22 jun) | Versión más reciente |
| Versión en servidor (24 jun) | **Versión anterior** |

El servidor muestra una versión más antigua que la descargada, lo que indica que el acta fue **revertida a una versión anterior** entre el momento de descarga y la verificación. El sufijo numérico en el nombre del archivo E14C identifica la versión — un número menor indica una versión más antigua.

---

## 3. Modelo de procesamiento

El sistema procesa los tres tipos de acta por mesa — E14C, E14T y E14D — aplicando el mismo modelo de análisis a cada una. A partir del PDF original, convierte cada página a imagen de alta resolución y localiza automáticamente la grilla del formulario para extraer celda por celda los valores registrados por los jurados. Sobre esos valores aplica reconocimiento óptico de caracteres, valida la aritmética interna del acta, y detecta alteraciones físicas como tachones o dobles escrituras. Cuando las tres copias de una misma mesa están disponibles, el sistema las compara entre sí para identificar discrepancias entre versiones del mismo documento. El resultado final es una clasificación por mesa: ⚪ sin alerta si todo cuadra, o 🔴 alerta si se detecta alguna irregularidad que requiere revisión humana.

---

## 4. Procesamiento de PDFs

### 4.1 Estructura del formulario E-14

Los formularios de primera y segunda vuelta tienen estructura distinta:

| Aspecto | Primera vuelta | Segunda vuelta |
|---------|---------------|----------------|
| Páginas | 3 (candidatos 1–7 · candidatos 8–13 + totales · firmas) | 1 (formulario único) |
| Candidatos | Múltiples | 2 (C1 Cepeda, C2 Abelardo) |
| Campos | Votos por candidato, blanco, nulos, no marcados, total | Votos por candidato, blanco, nulos, no marcados, incinerados, total |

### 4.2 Detección de la grilla

El formulario E-14 usa una grilla de líneas grises para separar filas y columnas. El sistema detecta automáticamente:

1. Las columnas de votos y totales.
2. Las filas de cada candidato y categoría.
3. Las celdas individuales en cada cruce.

**Resultado validado:** 9 de 9 filas detectadas correctamente en actas reales de segunda vuelta.

### 4.3 Lectura de dígitos (OCR)

Una vez detectada la grilla, el sistema lee los dígitos en cada celda mediante reconocimiento óptico de caracteres (OCR). El modelo principal tiene una precisión del **98.7%** en validación.

Para cada dígito se conservan los tres candidatos más probables — no solo el ganador — de forma que cuando el modelo duda entre dos dígitos, esa señal queda disponible para análisis forense posterior.

### 4.4 Validación aritmética

Por cada acta procesada se verifica la suma de votos contra el total declarado:

**Primera vuelta:**
```
C1 + C2 + ... + C13 + BLANCO + NULOS + NO_MARCADOS = TOTAL
```

**Segunda vuelta:**
```
C1_CEPEDA + C2_ABELARDO + BLANCO + NULOS + NO_MARCADOS = TOTAL
```

Los incinerados se registran como campo separado pero **no entran en la suma de validación** en ninguna vuelta.

#### Señales de irregularidad aritmética (diferencia pequeña — señal genuina)

Diferencia de 30 votos o menos con valores plausibles: señal de posible irregularidad real.

| Señal | Condición |
|------|-----------|
| Suma no cuadra | La suma declarada no coincide con la calculada; diferencia ≤ 30 |
| Urna vs. suma | El total de urna no coincide con la suma de votos; diferencia ≤ 30 |
| Votos exceden votantes | Total de urna supera el número de votantes habilitados; exceso ≤ 30 |
| Valor negativo | Cualquier candidato con valor negativo |

#### Señales de revisión de lectura (diferencia grande — probable error de lectura)

Diferencia mayor a 30, valores fuera de rango, o campo no diligenciado: probable error de lectura o acta incompleta.

| Señal | Condición |
|------|-----------|
| Suma dudosa | La suma difiere del calculado en más de 30 votos |
| Urna no diligenciada | Campo de total en urna en blanco (el sistema lo lee como 0) |
| Urna dudosa | El total de urna difiere de la suma en más de 30 votos |
| Votos dudosos | Total de urna supera votantes habilitados por más de 30 |

---

## 5. Detección de tachones y enmiendas

El sistema analiza cada celda del formulario para detectar alteraciones físicas. Se aplican cuatro métodos de detección:

| Método | Descripción |
|--------|-------------|
| Tachón clásico | Alta densidad de tinta horizontal — línea que cubre el dígito |
| Doble escritura | Superposición de dos dígitos distintos en la misma celda |
| Alta densidad | Concentración inusual de tinta en la zona del dígito |
| Zona sucia | Ruido generalizado en la celda — borrón o mancha |

Un puntaje combinado pondera los cuatro métodos para clasificar la celda.

### 5.1 Patrones de fraude físico documentados

#### Pre-marcación de tarjetas para anulación selectiva de votos

**Descripción:** Durante el traslado de las tarjetas electorales o durante la custodia previa a la jornada, actores con acceso físico a las tarjetas las pre-marcaban con señales pequeñas en la casilla de un candidato específico. Cuando un votante llegaba y marcaba ese mismo candidato, su tarjeta quedaba con dos marcas y era declarada nula. Los votantes que marcaban otros candidatos no sufrían este efecto.

**Efecto observable en el acta:**
- Incremento anómalo de votos nulos en mesas específicas sin causa aparente.
- La proporción de nulos sobre votantes habilitados es significativamente mayor que en mesas del mismo puesto o municipio.
- El patrón es asimétrico: afecta con mayor intensidad las mesas donde el candidato objetivo tenía más intención de voto.

**Señales de detección:**
- Ratio de nulos por mesa significativamente mayor que el promedio de mesas del mismo puesto.
- Dispersión inusual de nulos entre mesas del mismo puesto.
- Correlación entre nulos elevados y resultados bajos para un candidato específico en esa zona.

#### Alteración de la sección de notas y constancias

**Descripción:** La sección de notas del E-14 es un espacio de texto libre que los jurados pueden usar para registrar observaciones sobre la jornada. Si los jurados dejan áreas en blanco, terceros pueden añadir anotaciones posteriores que alteren el valor probatorio del acta.

**Buena práctica de los jurados:** Rayar completamente los espacios en blanco de la sección de notas al finalizar la jornada. Esto impide físicamente que alguien añada contenido posterior al cierre de mesa — igual que se rayan los espacios en blanco en documentos notariales y cheques.

**Efecto observable:**
- Actas con buena práctica: sección de notas con rayas diagonales cruzando el espacio no utilizado.
- Actas con práctica incorrecta o manipuladas: espacios en blanco intactos, o texto que visualmente no encaja con la caligrafía del resto del acta.

---

## 6. Portal de etiquetado ciudadano

El portal de etiquetado permite a ciudadanos voluntarios **confirmar o corregir** la lectura automática de los dígitos en las actas con alertas.

### ¿Cómo funciona?

1. El sistema identifica las mesas con posibles irregularidades y recorta las celdas con dígitos dudosos.
2. El voluntario accede al portal en [analizadore14.porciudad.com](https://analizadore14.porciudad.com).
3. Ve la imagen del dígito recortado del acta original y la sugerencia del sistema.
4. Confirma la lectura (Enter) o la corrige haciendo clic en el dígito correcto (0–9).

### Sistema de semáforo por mesa

| Estado | Significado |
|--------|-------------|
| ⚪ Sin alerta | El algoritmo no detectó irregularidades aritméticas |
| 🔴 Alerta | El algoritmo detectó una posible irregularidad — pendiente de revisión humana |
| 🟡 En revisión | Al menos un voluntario está revisando los dígitos de esta mesa |
| 🟢 Revisada | Los voluntarios confirmaron los datos — resultado disponible |

### Resultados posibles de una mesa revisada

- **Mesa Limpia:** La suma de votos por candidato cuadra con el total declarado en el acta.
- **Posible Fraude:** La suma de votos no cuadra con el total — discrepancia confirmada por revisión humana.

---

## 7. Modelo de reconocimiento óptico de caracteres (OCR)

El sistema usa dos capas de reconocimiento:

**Capa principal — red neuronal entrenada con actas colombianas:**
- Entrenada específicamente sobre recortes de dígitos de formularios E-14.
- Precisión en validación: **98.7%**
- Reconoce dígitos 0–9, celdas vacías y tachones.

**Capa de respaldo — motor OCR general:**
- Se activa cuando la red principal no converge en un resultado confiable.
- Proporciona una segunda opinión sobre la lectura.

### Umbral de confianza

Si el modelo principal tiene menos del 70% de confianza en su lectura, el dígito se marca como incierto y se preservan las tres opciones más probables para revisión humana. Esta señal de duda es información forense valiosa: indica que el dígito físico es ambiguo — ya sea por escritura dudosa o por una posible alteración.

---

## 8. Cobertura del análisis (al 2026-07-04)

| Métrica | Valor |
|---------|-------|
| Actas E14C — primera vuelta | ~26,194 |
| Actas E14C únicas — segunda vuelta | 118,543 |
| Departamentos con análisis transversal completo | 34 / 34 (100%) |
| Mesas analizadas en cross-validación | 115,741 |
| Actas con divergencia confirmada (modificadas en servidor) | 20 |
| Actas aún modificadas al 2 de julio | 18 |

---

## 9. Otras iniciativas ciudadanas

La verificación de actas E-14 no es exclusiva de este proyecto. Existen iniciativas independientes que abordan el mismo problema desde enfoques metodológicos distintos. La coexistencia de múltiples métodos sobre la misma fuente primaria fortalece la verificabilidad del proceso electoral.

### ConteoCol — comparación visual par a par por humanos

[ConteoCol](https://conteocol.lat/) es una plataforma ciudadana que propone un enfoque diferente al de este proyecto: en lugar de usar reconocimiento automático de caracteres, pone a ciudadanos voluntarios a comparar visualmente las tres versiones del acta de una misma mesa — E14C, E14T y E14D — lado a lado. El voluntario determina directamente si existen discrepancias entre las tres copias, sin intermediación algorítmica.

| Dimensión | Analizador E-14 | ConteoCol |
|---|---|---|
| Método principal | OCR automatizado + validación aritmética | Comparación visual humana par a par |
| Rol del voluntario | Confirmar o corregir dígitos dudosos marcados por el algoritmo | Comparar directamente las tres versiones del acta |
| Detección de discrepancias | Automática — el sistema identifica las mesas con alerta | Manual — el voluntario revisa y determina si hay diferencias |
| Escala | Procesamiento masivo (118,543 mesas) con revisión humana focalizada en alertas | Revisión humana directa de cada mesa comparada |

Ambas aproximaciones son complementarias: el análisis automatizado permite cubrir el universo completo de mesas e identificar prioridades; la revisión visual directa aporta una capa de verificación sin dependencia de modelos de machine learning.

### Colombia Elige — análisis estadístico y visualización electoral

[Colombia Elige](https://colombiaelige.co/) es una plataforma de análisis estadístico electoral enfocada en la visualización e interpretación de resultados electorales colombianos. A diferencia de este proyecto — que parte del documento físico (el PDF del acta) para detectar irregularidades en la transcripción — Colombia Elige trabaja sobre los resultados ya consolidados para identificar patrones estadísticos en la distribución de votos.

Las tres iniciativas son metodológicamente distintas y cubren ángulos complementarios del mismo problema:

| Iniciativa | Enfoque |
|---|---|
| **Analizador E-14** | Integridad del documento físico — OCR, aritmética, tachones, comparación entre versiones |
| **ConteoCol** | Comparación visual humana entre las tres copias del acta por mesa |
| **Colombia Elige** | Análisis estadístico de los resultados consolidados — patrones y visualización |

---

## 10. Análisis estadístico en desarrollo

### Detección de anulación selectiva por pre-marcación

La validación aritmética actual no detecta este fraude porque el acta puede ser aritméticamente correcta — los nulos simplemente son más altos de lo esperado. El análisis requiere comparar el ratio de nulos entre mesas del mismo puesto, municipio y zona.

**Señales a cruzar:**
- Ratio de nulos por mesa vs. mediana del puesto y zona.
- Dispersión inusual de nulos entre mesas del mismo puesto.
- Correlación entre nulos elevados y porcentaje de votos bajo para un candidato en esa mesa vs. mesas vecinas.

### Análisis geográfico de discrepancias por revisión

Con 34 departamentos analizados, es posible mapear la distribución geográfica de las mesas con discrepancias grandes. Si un departamento o municipio concentra una tasa desproporcionada de mesas con discrepancias, esa concentración misma es una señal que merece revisión humana prioritaria.
