# Scripts de descarga E14T y E14D

Tres métodos funcionales. No modificar estos archivos directamente.
Si necesitas depurar o modificar, copia el archivo a otro lado y trabaja sobre esa copia.

## Métodos

| Script | Método | Velocidad | Uso |
|--------|--------|-----------|-----|
| `turbo_e14.py` | Route interception + batch | ~7-8 PDFs/s | El principal, recomendado |
| `stealth_e14.py` | Fetch desde Chrome con stealth | ~2-3 PDFs/s | Cuando Cloudflare está agresivo |
| `batch_e14.py` | Lotes de fetch con Playwright | ~1-2 PDFs/s | El más simple |

## Turbo (recomendado)

```powershell
# E14T
python scripts/e14/e14t/turbo_e14.py --source e14t --batch 6 --stagger 80 --cooldown 30

# E14D  
python scripts/e14/e14t/turbo_e14.py --source e14d --batch 6 --stagger 80 --cooldown 30

# Prueba rápida
python scripts/e14/e14t/turbo_e14.py --source e14t --limit 20
```

### Parámetros

| Parámetro | Default | Descripción |
|-----------|---------|-------------|
| `--source` | `e14t` | `e14t` o `e14d` |
| `--limit` | `0` | Máx descargas (0 = todas) |
| `--batch` | `6` | Fetch simultáneos por lote |
| `--stagger` | `80` | ms entre cada fetch (más bajo = más rápido) |
| `--refresh` | `0` | Pausa cada N descargas (0 = sin pausa) |
| `--cooldown` | `30` | Segundos de pausa si Cloudflare bloquea |

## Stealth

Usa `debug_sv/allTransmissionCodes.json` (códigos de primera vuelta).
Para usarlo con segunda vuelta, hay que adaptar la fuente de datos.

## Batch

Usa `debug_sv/allTransmissionCodes.json` (códigos de primera vuelta).
Idem, requiere adaptación para segunda vuelta.

## Notas

- Todos necesitan Chrome con perfil de usuario (iniciaste sesión en la Registraduría)
- Cloudflare detecta requests directos sin browser — estos scripts siempre pasan por Chrome
- Si el turbo bloquea, probar con `--stagger 200` o `--batch 4`
- Para velocidad máxima, usar el turbo con VPN rotando IPs
