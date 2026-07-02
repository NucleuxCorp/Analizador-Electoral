# Verificador SSL - Registraduria 2026

Herramienta ciudadana para verificar el estado del certificado SSL de las plataformas electorales de la Registraduria Nacional durante las elecciones presidenciales colombianas 2026.

---

## Que hace

Se conecta directamente a los servidores de la Registraduria y verifica el certificado SSL: emisor, dominio, fechas de vigencia y validez de la cadena de confianza. Soporta verificacion puntual o en bucle para monitoreo continuo.

| Estado | Significado |
|--------|------------|
| **VALIDO** | Certificado activo, cadena de confianza correcta. |
| **INVALIDO** | Certificado vencido, no confiable o con errores de validacion. |
| **TIMEOUT** | No se pudo establecer conexion con el servidor en el tiempo limite. |

Al finalizar genera un informe `informe_ssl_YYYY-MM-DD_HH-mm.md` con el resumen y el detalle de cada plataforma verificada.

---

## Plataformas cubiertas

| Clave | Plataforma | Tipo |
|-------|-----------|------|
| 1 | `escrutinios2vueltapresidente2026.registraduria.gov.co` | E14C segunda vuelta |
| 2 | `escrutiniospresidente2026.registraduria.gov.co` | E14C primera vuelta |
| 3 | `e14segundavueltapresidente.registraduria.gov.co` | E14D segunda vuelta |
| 4 | `e14segundavueltapresidentet.registraduria.gov.co` | E14T segunda vuelta |
| 5 | `divulgacione14presidente.registraduria.gov.co` | E14D primera vuelta |
| 6 | `divulgacione14presidentet.registraduria.gov.co` | E14T primera vuelta |

---

## Como usar

1. Haz doble clic en `ejecutar.bat`
2. Selecciona una plataforma `[1-6]` o `[T]` para verificar todas
3. Indica cuantas veces quieres consultar (Enter = 1 vez; mas de 1 activa el bucle)
4. Si usas bucle, indica el tiempo de espera entre consultas en segundos
5. Abre el `informe_ssl_*.md` generado para ver los resultados

---

## Estructura de archivos

```
verificador-ssl-registraduria/
├── verificar_ssl.ps1    <- Script principal PowerShell
├── ejecutar.bat         <- Lanzador (doble clic)
└── README.md            <- Este archivo
```

---

## Requisitos

- Windows 10 v1803 o superior (incluye `curl.exe` y PowerShell 5.1 nativos)
- Sin instalacion adicional — usa unicamente componentes nativos de Windows

---

## Notas metodologicas

- La herramienta acepta cualquier certificado para poder inspeccionarlo, incluso si la cadena de confianza falla. El estado **INVALIDO** indica que el certificado existe pero no supera la validacion.
- Si se ejecuta detras de una VPN con inspeccion SSL, el certificado mostrado puede ser el del proxy de la VPN y no el del servidor real de la Registraduria.
- Los informes generados quedan en la misma carpeta del script con nombre `informe_ssl_YYYY-MM-DD_HH-mm.md`.

---

Esta herramienta es parte del **Proyecto Analizador Electoral** — auditoria ciudadana de actas electorales colombianas 2026.

Repositorio: https://github.com/NucleuxCorp/Analizador-Electoral/
