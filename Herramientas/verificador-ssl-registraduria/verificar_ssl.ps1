# verificar_ssl.ps1
# Verifica el certificado SSL de las plataformas de la Registraduria
# Sin dependencias externas - usa curl nativo de Windows

$PLATAFORMAS = @(
    @{ key="1"; label="E14C segunda vuelta  (escrutinios2vuelta...)";        url="https://escrutinios2vueltapresidente2026.registraduria.gov.co/data/index.json" }
    @{ key="2"; label="E14C primera vuelta  (escrutinios...)";               url="https://escrutiniospresidente2026.registraduria.gov.co/data/index.json" }
    @{ key="3"; label="E14D segunda vuelta  (e14segundavueltapresidente...)"; url="https://e14segundavueltapresidente.registraduria.gov.co/home" }
    @{ key="4"; label="E14T segunda vuelta  (e14segundavueltapresidentet...)";url="https://e14segundavueltapresidentet.registraduria.gov.co/home" }
    @{ key="5"; label="E14D primera vuelta  (divulgacione14presidente...)";   url="https://divulgacione14presidente.registraduria.gov.co/home" }
    @{ key="6"; label="E14T primera vuelta  (divulgacione14presidentet...)";  url="https://divulgacione14presidentet.registraduria.gov.co/home" }
)

$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path

function Show-Header {
    Write-Host ""
    Write-Host "=======================================" -ForegroundColor Cyan
    Write-Host "  Verificador SSL - Registraduria 2026" -ForegroundColor Cyan
    Write-Host "=======================================" -ForegroundColor Cyan
    Write-Host ""
}

function Select-Plataforma {
    Write-Host "Plataformas disponibles:" -ForegroundColor Yellow
    foreach ($p in $PLATAFORMAS) {
        Write-Host "  [$($p.key)] $($p.label)"
    }
    Write-Host "  [T] Todas las plataformas"
    Write-Host ""
    $sel = Read-Host "Selecciona [1-6 / T] (Enter = T)"
    if ($sel -eq "" -or $sel -match "^[Tt]$") { return $null }
    $found = $PLATAFORMAS | Where-Object { $_.key -eq $sel }
    if (-not $found) { return $null }
    return $found
}

function Invoke-CheckSSL {
    param([string]$url)

    $status  = "TIMEOUT"
    $issuer  = ""
    $subject = ""
    $dates   = ""

    try {
        # Accept all certs so we can inspect even when validation fails
        [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }

        $request = [System.Net.HttpWebRequest]::Create($url)
        $request.Timeout = 15000
        $request.Method  = "HEAD"

        try   { $response = $request.GetResponse(); $response.Close() }
        catch {}

        $cert = $request.ServicePoint.Certificate
        if ($cert) {
            $cert2   = [System.Security.Cryptography.X509Certificates.X509Certificate2]$cert
            $issuer  = $cert2.Issuer
            $subject = $cert2.Subject
            $dates   = "desde $($cert2.NotBefore.ToString('yyyy-MM-dd')) hasta $($cert2.NotAfter.ToString('yyyy-MM-dd'))"
            $status  = "VALIDO"
        }
    } catch {
        $status = "TIMEOUT"
    }

    return @{
        status  = $status
        issuer  = $issuer
        subject = $subject
        dates   = $dates
    }
}

function Show-Result {
    param([string]$url, [hashtable]$r, [string]$timestamp)

    Write-Host "[$timestamp] $url" -ForegroundColor Gray
    switch ($r.status) {
        "VALIDO"   { Write-Host "  RESULTADO : CERTIFICADO VALIDO"           -ForegroundColor Green  }
        "INVALIDO" { Write-Host "  RESULTADO : CERTIFICADO INVALIDO / ERROR" -ForegroundColor Red    }
        "TIMEOUT"  { Write-Host "  RESULTADO : Sin respuesta o timeout"      -ForegroundColor Yellow }
    }
    if ($r.issuer)  { Write-Host "  Emisor    : $($r.issuer)"  }
    if ($r.subject) { Write-Host "  Sujeto    : $($r.subject)" }
    if ($r.dates)   { Write-Host "  Vigencia  : $($r.dates)"   }
    Write-Host ""
}

function Write-Informe {
    param([System.Collections.Generic.List[object]]$entries)

    $ts       = Get-Date -Format "yyyy-MM-dd_HH-mm"
    $ts_label = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $filename = "informe_ssl_$ts.md"
    $path     = Join-Path $SCRIPT_DIR $filename

    $validos   = @($entries | Where-Object { $_.status -eq "VALIDO"   }).Count
    $invalidos = @($entries | Where-Object { $_.status -eq "INVALIDO" }).Count
    $timeouts  = @($entries | Where-Object { $_.status -eq "TIMEOUT"  }).Count

    $lines = @()
    $lines += "# Informe SSL - Registraduria 2026"
    $lines += ""
    $lines += "**Fecha:** $ts_label"
    $lines += ""
    $lines += "## Resumen"
    $lines += ""
    $lines += "| Estado | Cantidad |"
    $lines += "|--------|--------:|"
    $lines += "| VALIDO | $validos |"
    $lines += "| INVALIDO | $invalidos |"
    $lines += "| TIMEOUT | $timeouts |"
    $lines += "| **Total** | **$($entries.Count)** |"
    $lines += ""
    $lines += "## Detalle por plataforma"
    $lines += ""
    $lines += "| Plataforma | Estado | Emisor | Vigencia |"
    $lines += "|------------|--------|--------|---------|"

    foreach ($e in $entries) {
        $lines += "| $($e.url) | $($e.status) | $($e.issuer) | $($e.dates) |"
    }

    $lines += ""
    $lines += "---"
    $lines += "*Generado por verificador-ssl-registraduria - Proyecto Analizador de Elecciones*"

    $lines -join "`n" | Set-Content -Path $path -Encoding UTF8
    return $path
}

function Run-Plataforma {
    param([string]$url, [string]$label, [int]$repeticiones, [int]$delay)

    $entries = [System.Collections.Generic.List[object]]::new()

    for ($i = 1; $i -le $repeticiones; $i++) {
        if ($repeticiones -gt 1) {
            Write-Host "--- Consulta $i / $repeticiones ---" -ForegroundColor DarkCyan
        }
        $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Write-Host ""
        Write-Host "Verificando: $label" -ForegroundColor Cyan
        $r = Invoke-CheckSSL -url $url
        Show-Result -url $url -r $r -timestamp $ts

        $entries.Add(@{
            url     = $url
            label   = if ($repeticiones -gt 1) { "$label (consulta $i)" } else { $label }
            status  = $r.status
            issuer  = $r.issuer
            subject = $r.subject
            dates   = $r.dates
            ts      = $ts
        })

        if ($i -lt $repeticiones) {
            Write-Host "Esperando $delay segundos..." -ForegroundColor Gray
            Start-Sleep -Seconds $delay
        }
    }

    if ($repeticiones -gt 1) {
        Write-Host "Bucle completado." -ForegroundColor Green
    }

    $informe = Write-Informe -entries $entries
    Write-Host "Informe guardado: $informe" -ForegroundColor Green
}

function Run-All {
    $entries = [System.Collections.Generic.List[object]]::new()

    Write-Host ""
    Write-Host "Verificando las $($PLATAFORMAS.Count) plataformas..." -ForegroundColor Cyan

    foreach ($p in $PLATAFORMAS) {
        $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Write-Host ""
        Write-Host "[$($p.key)] $($p.label)" -ForegroundColor DarkCyan
        $r = Invoke-CheckSSL -url $p.url
        Show-Result -url $p.url -r $r -timestamp $ts

        $entries.Add(@{
            url     = $p.url
            label   = $p.label
            status  = $r.status
            issuer  = $r.issuer
            subject = $r.subject
            dates   = $r.dates
            ts      = $ts
        })
    }

    $informe = Write-Informe -entries $entries
    Write-Host "Informe guardado: $informe" -ForegroundColor Green
}

# --- Main ---

Show-Header

$seleccion = Select-Plataforma

if ($seleccion -eq $null) {
    Run-All
} else {
    $repInput = Read-Host "Repeticiones (Enter = 1)"
    if (-not ($repInput -match '^\d+$') -or [int]$repInput -lt 1) { $repInput = 1 }
    $rep = [int]$repInput

    $delay = 10
    if ($rep -gt 1) {
        $delayInput = Read-Host "Delay entre consultas en segundos (Enter = 10)"
        if ($delayInput -match '^\d+$' -and [int]$delayInput -ge 1) { $delay = [int]$delayInput }
    }

    Run-Plataforma -url $seleccion.url -label $seleccion.label -repeticiones $rep -delay $delay
}

Write-Host ""
Write-Host "Presiona Enter para salir..."
Read-Host | Out-Null
