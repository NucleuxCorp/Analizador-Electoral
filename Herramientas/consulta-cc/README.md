# Consulta CC — Cédulas Colombianas

Herramienta integrada para consultas de cédulas colombianas. Combina dos funcionalidades:

1. **Vigencia de Cédula** — ¿La persona está fallecida según la Registraduría?
2. **Datos Personales** — Nombres, apellidos desde procuraduría

---

## Módulos

### `consulta_defunciones.py` (original)
Consulta vigencia de cédula contra API pública de defunciones. Sin cambios desde versión anterior.

```bash
python consulta_defunciones.py              # Menú interactivo
python consulta_defunciones.py --file X    # Batch mode
```

### `modulos/datos_personales.py` (nuevo)
Cliente HTTP para procuraduría con CAPTCHA solver (preguntas predefinidas, sin OCR).

**Características:**
- Consulta vía HTTP directo (sin Puppeteer)
- CAPTCHA solver para preguntas de seguridad:
  - Capitales departamentales (barranquilla, cali, bogota, medellin)
  - Operaciones aritméticas (5 + 3 = 8)
  - Tres primeros dígitos de la cédula
- Extrae: nombres, segundo nombre, primer apellido, segundo apellido
- Manejo de sesiones y cookies

### `consulta_cc.py` (nuevo)
CLI integrado con menú principal para elegir qué consultar.

```bash
python consulta_cc.py    # Menú interactivo
```

---

## Requisitos

Python 3.8+ (stdlib: urllib, ssl, re, csv, json, logging, etc.)

### Optional (para vigencia)
```bash
pip install tqdm
```

---

## Uso rápido

### Opción 1: Consultar datos personales (nombres, apellidos)
```bash
python consulta_cc.py
# Selecciona [2]
# Ingresa cédula: 73135439
```

### Opción 2: Menú integrado (vigencia + datos)
```bash
python consulta_cc.py
# Selecciona [1], [2] o [3] según necesidad
```

### Opción 3: Vigencia (defunciones)
```bash
python consulta_defunciones.py
# Menú interactivo con opciones de lote/individual
```

---

## Estructura

```
consulta-cc/
├── consulta_cc.py                 # CLI integrado (nuevo)
├── consulta_defunciones.py        # Vigencia API (original)
├── test_datos_personales.py       # Unit tests para CAPTCHA solver
├── modulos/
│   ├── __init__.py
│   ├── datos_personales.py        # Cliente procuraduría + CAPTCHA
│   └── vigencia.py                # (placeholder)
├── ejecutar.bat                   # Lanzador Windows
├── requirements.txt               # tqdm
├── README.md                      # Este archivo
├── cedulas.txt                    # Tu lista (entrada vigencia)
├── cedulas.txt.example
└── reportes/                      # Salidas vigencia
    ├── resultados.csv
    ├── fallecidos.csv
    ├── informe.md
    └── consulta.log
```

---

## Próximos pasos

- [ ] Integrar vigencia en `consulta_cc.py` (delegación a `consulta_defunciones.py`)
- [ ] Integrar puesto de votación (requiere Puppeteer desde `searchPeople/`)
- [ ] Batch mode para datos personales (leer desde CSV, guardar en CSV)
- [ ] CLI mejorado con opciones `--file`, `--mode`, etc.

---

## Privacidad y uso responsable

- Cédulas son datos personales sensibles (Ley 1581 de 2012)
- Consulta solo listas autorizadas
- No publiques archivos con NUIPs reales
- Elimina outputs cuando termines

---

## Troubleshooting

### SSL Certificate Error
Si falla: `CERTIFICATE_VERIFY_FAILED`

Ejecuta diagnóstico SSL:
```bash
python consulta_defunciones.py --diag-ssl
```

Esto indica si hay antivirus/proxy local inspeccionando HTTPS.

### CAPTCHA no resuelto
Si falla: `Pregunta no mapeada`

1. Abre una sesión manual en procuraduría
2. Captura la pregunta exacta (incluyendo espacios/puntuación)
3. Agrega a `CAPTCHA_ANSWERS` en `modulos/datos_personales.py`

---

## Desarrollo

Test CAPTCHA solver:
```bash
python test_datos_personales.py
```

Importar módulo:
```python
from modulos.datos_personales import ProcuradoriaClient

client = ProcuradoriaClient()
datos = client.consultar_cedula("73135439")
print(datos.get("primerApellido"))  # CUETO
```

---

Parte del **Proyecto Analizador Electoral** — auditoría ciudadana 2026.