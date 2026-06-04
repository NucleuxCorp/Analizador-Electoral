# validate-downloads.ps1
# Run from: d:\Nucleux\tools\Analizador de Elecciones

$root       = Split-Path -Parent $MyInvocation.MyCommand.Path
$urlsFile   = Join-Path $root "data\e14c_urls.jsonl"
$pdfsDir    = Join-Path $root "data\pdfs"
$failedFile = Join-Path $root "data\e14c_failed.jsonl"

Write-Host "`n=== E-14C Download Status ===" -ForegroundColor Cyan

# --- Totals ---
$totalUrls   = (Get-Content $urlsFile).Count
$totalPdfs   = (Get-ChildItem -Path $pdfsDir -Recurse -Filter "*.pdf" -ErrorAction SilentlyContinue).Count
$totalFailed = if (Test-Path $failedFile) { (Get-Content $failedFile).Count } else { 0 }
$missing     = $totalUrls - $totalPdfs

Write-Host "`n[General]"
Write-Host ("  URLs en indice  : {0,7}" -f $totalUrls)
Write-Host ("  PDFs descargados: {0,7}  ({1:P1})" -f $totalPdfs, ($totalPdfs / $totalUrls))
Write-Host ("  Faltantes       : {0,7}" -f $missing) -ForegroundColor $(if ($missing -gt 0) { "Yellow" } else { "Green" })
Write-Host ("  Entradas failed : {0,7}" -f $totalFailed) -ForegroundColor $(if ($totalFailed -gt 0) { "Red" } else { "Green" })

# --- Per-department breakdown ---
Write-Host "`n[Por Departamento]" -ForegroundColor Cyan
Write-Host ("{0,-30} {1,8} {2,8} {3,8}" -f "DEPARTAMENTO", "URLS", "PDFs", "FALT")
Write-Host ("-" * 58)

# Load URLs grouped by dept
$urlsByDept = @{}
Get-Content $urlsFile | ForEach-Object {
    $rec  = $_ | ConvertFrom-Json
    $dept = $rec.departamento
    if (-not $urlsByDept.ContainsKey($dept)) { $urlsByDept[$dept] = 0 }
    $urlsByDept[$dept]++
}

# Count PDFs per dept folder
Get-ChildItem -Path $pdfsDir -Directory -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object {
    $dept     = $_.Name
    $pdfCount = (Get-ChildItem -Path $_.FullName -Recurse -Filter "*.pdf").Count
    $urlCount = if ($urlsByDept.ContainsKey($dept)) { $urlsByDept[$dept] } else { "?" }
    $falt     = if ($urlCount -is [int]) { $urlCount - $pdfCount } else { "?" }
    $color    = if (($falt -is [int]) -and $falt -gt 0) { "Yellow" } else { "Green" }
    Write-Host ("{0,-30} {1,8} {2,8} {3,8}" -f $dept, $urlCount, $pdfCount, $falt) -ForegroundColor $color
}

# --- Unique failed URLs ---
if (Test-Path $failedFile) {
    $uniqueFailed = (Get-Content $failedFile |
        ForEach-Object { ($_ | ConvertFrom-Json).url } |
        Sort-Object -Unique).Count
    Write-Host ("`n  URLs unicas fallidas: {0} (de {1} intentos registrados)" -f $uniqueFailed, $totalFailed) -ForegroundColor Red
}

Write-Host ""
