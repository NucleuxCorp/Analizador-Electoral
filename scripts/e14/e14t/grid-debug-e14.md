# Skill: grid-debug-e14

## Activation Contract

Activar al depurar o calibrar detectores de grilla en formularios E14 (C, D, T).
Usar cuando las subceldas no se alinean con los dígitos de votación.

## Principio Fundamental

**NUNCA modificar código existente durante la calibración.**
Crear un script aislado en `scripts/` que:
1. Renderiza el PDF a imagen (fitz → numpy)
2. Superpone las celdas detectadas con `cv2.rectangle`
3. Guarda la imagen en `E:/e14_segunda/debug/` para revisión visual
4. Itera con parámetros ajustables hasta alineación correcta

## Flujo de trabajo

### Paso 1: Aislar con script de calibración propio
```
scripts/calibrar_e14{tipo}.py
```
NO tocar `grid_detector.py`, `grid_detector_corners.py`, ni ningún archivo existente.

### Paso 2: Detectar bloques
Los formularios E14D tienen estos bloques (de arriba abajo):
1. NIVELACION DE LA MESA — datos de mesa (votantes, urna, incinerados)
2. BARRA NEGRA — "CANDIDATO / AGRUPACION / VOTACION" (separador)
3. C1 — votos candidato 1
4. C2 — votos candidato 2
5. RESULTADOS — totales y firmas

Usar `cv2.findContours` + `cv2.boundingRect` para detectar cada bloque.
Filtrar por altura > 500px (excluye la barra negra fina).

### Paso 3: Definir estructura de celdas por bloque

| Bloque | Full cells (filas) | Header skip | Max altura |
|--------|-------------------|-------------|-----------|
| NIVELACION | 3 | 11% | — |
| C1 | 1 | — | 110% del ancho de subcelda |
| C2 | 1 | — | 110% del ancho de subcelda |
| RESULTADOS | 4 | — | — |

Cada full cell tiene 3 subceldas (posiciones de dígitos).

### Paso 4: Posicionar columnas (subceldas)
Las 3 subceldas ocupan el ~25% derecho del bloque:
- Inicio: `COL_LEFT_PCT = 72%` del ancho del bloque
- Ancho por subcelda: `COL_WIDTH_PCT = 9%`

### Paso 5: Debug visual iterativo
1. Dibujar cada bloque con `cv2.rectangle(verde)` 
2. Dibujar cada subcelda con colores: azul (col 0), rojo (col 1), amarillo (col 2)
3. Guardar en `E:/e14_segunda/debug/calib_*.png`
4. Pedir feedback al usuario y ajustar parámetros
5. Repetir hasta alineación perfecta

### Paso 6: Solo después de calibrado, integrar
Cuando el usuario confirma visualmente que la calibración es correcta,
trasladar los parámetros al módulo de producción correspondiente.

## Parámetros calibrados para E14D

```python
COL_LEFT_PCT = 72.0   # inicio de la primera subcelda (% desde izquierda del bloque)
COL_WIDTH_PCT = 9.0   # ancho de cada subcelda (% del ancho del bloque)
HEADER_PCT = 11.0     # skip del título en bloque NIVELACION
MAX_H_RATIO = 1.1     # alto máximo de celda de candidato (relativo a su ancho)
```

## Ejemplo de comando de calibración

```powershell
python scripts/calibrar_e14d.py --sample 3           # 3 PDFs aleatorios
python scripts/calibrar_e14d.py --pdf "ruta/pdf.pdf" # PDF específico
```

## Reglas

- NUNCA modificar código existente durante la calibración
- Siempre guardar imágenes en `E:/e14_segunda/debug/`
- El usuario es quien valida visualmente — no inferir sin revisión
- Iterar con un PDF de muestra conocido, luego validar con otros
- Si el usuario dice "asquerosamente mal", detenerse y preguntar qué falla
