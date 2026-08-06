Descargador E-14 — Herramientas/descargador-e14

Resumen (ES):
Esta utilidad permite descargar masivamente actas E-14 desde los índices públicos y monitorear cambios en los certificados TLS de los servidores de la Registraduría. Incluye dos herramientas:

- descargador_e14.py — descarga PDFs E-14 (E14C/E14D/E14T)
- cert_monitor.py — tripwire de certificados TLS

Limitaciones importantes:
- E14D/E14T están protegidos por Cloudflare y a menudo requieren un navegador real y cookies de sesión. The downloader attempts to use Playwright for those types; if Playwright is not present or a logged-in browser profile is not provided, downloads for E14D/E14T will likely fail. This is a platform limitation, not a bug.
- Filenames on the Registraduría servers are server-assigned identifiers, not SHA-256 of the content. Do NOT assume integrity from filename==hash. To verify content integrity use an independent third-party index that publishes SHA-256.

Cómo ejecutar (Windows)

1. Double-click launch-descargador.bat or from shell:
   python Herramientas\descargador-e14\descargador_e14.py --type e14t --limit 6

2. Run cert monitor once:
   python Herramientas\descargador-e14\cert_monitor.py --once

Requisitos opcionales

- Playwright Python (pip install playwright) and a browser profile if you need E14D/E14T downloads behind Cloudflare.

Informes

- descarga_resumen.md (Spanish) contains counts and failures after each run (created in the tool output dir).
- cert_monitor writes cert_fingerprints.json and cert_monitor_log.md (entries in Spanish) inside the tool directory.
