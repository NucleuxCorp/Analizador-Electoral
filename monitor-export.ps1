# monitor-export.ps1
# Live progress monitor for export-crops
# Run in a separate terminal while export-crops is running

param(
    [int]$TargetPdfs  = 300,
    [int]$CropsPerPdf = 38,
    [int]$RefreshMs   = 1000
)

$indexFile = Join-Path $PSScriptRoot "data\labels\crops\index.jsonl"
$target    = $TargetPdfs * $CropsPerPdf
$inv       = [System.Globalization.CultureInfo]::InvariantCulture
$lastCount = 0
$lastTime  = [datetime]::Now
$rate      = 0.0

Write-Host "Watching: $indexFile" -ForegroundColor DarkGray
Write-Host "Press Ctrl+C to stop"
Start-Sleep -Milliseconds 500

while ($true) {
    $count = 0
    $pdfs  = 0

    if (Test-Path $indexFile) {
        $lines = @(Get-Content $indexFile -ErrorAction SilentlyContinue)
        $count = $lines.Count

        $pdfs = ($lines |
            Where-Object { $_ } |
            ForEach-Object {
                try { ($_ | ConvertFrom-Json).pdf_path } catch { $null }
            } |
            Where-Object { $_ } |
            Sort-Object -Unique).Count
    }

    $now     = [datetime]::Now
    $elapsed = ($now - $lastTime).TotalSeconds
    if ($elapsed -gt 0 -and $count -gt $lastCount) {
        $rate = ($count - $lastCount) / $elapsed
    }
    $lastCount = $count
    $lastTime  = $now

    $pct    = if ($target -gt 0) { [math]::Min($count * 100.0 / $target, 100.0) } else { 0.0 }
    $barW   = 40
    $filled = [math]::Round($pct / 100.0 * $barW)
    $empty  = $barW - $filled
    $bar    = ("#" * $filled) + ("-" * $empty)
    $pctStr = $pct.ToString("F1", $inv)
    $rateStr= $rate.ToString("F1", $inv)

    if ($rate -gt 0) {
        $etaSec = [math]::Round(($target - $count) / $rate)
        $etaStr = "${etaSec}s"
    } else {
        $etaStr = "---"
    }

    $color = if ($pct -ge 100) { "Green" } elseif ($pct -ge 50) { "Yellow" } else { "Cyan" }

    Clear-Host
    Write-Host ""
    Write-Host "  E-14C Export Progress" -ForegroundColor Cyan
    Write-Host "  =================================================" -ForegroundColor DarkGray
    Write-Host ("  [{0}] {1,5}%" -f $bar, $pctStr) -ForegroundColor $color
    Write-Host ""
    Write-Host ("  Crops  : {0,6} / {1}" -f $count, $target)
    Write-Host ("  PDFs   : {0,6} / {1}" -f $pdfs,  $TargetPdfs)
    Write-Host ("  Speed  : {0} crops/s" -f $rateStr) -ForegroundColor DarkCyan
    Write-Host ("  ETA    : {0}"          -f $etaStr)  -ForegroundColor DarkCyan
    Write-Host "  =================================================" -ForegroundColor DarkGray
    Write-Host ("  {0}" -f (Get-Date -Format "HH:mm:ss")) -ForegroundColor DarkGray

    if ($count -ge $target) {
        Write-Host ""
        Write-Host ("  Done! {0} crops exported from {1} PDFs." -f $count, $pdfs) -ForegroundColor Green
        break
    }

    Start-Sleep -Milliseconds $RefreshMs
}
