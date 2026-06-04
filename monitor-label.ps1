# monitor-label.ps1
# Live progress monitor for the labeling portal
# Run in a separate terminal while python main.py label is running

param(
    [int]$Target     = 1500,
    [int]$RefreshMs  = 1000
)

$manifestFile = Join-Path $PSScriptRoot "data\labels\manifest.jsonl"
$inv          = [System.Globalization.CultureInfo]::InvariantCulture
$lastCount    = 0
$lastTime     = [datetime]::Now
$rate         = 0.0

Write-Host "Watching: $manifestFile" -ForegroundColor DarkGray
Write-Host "Press Ctrl+C to stop"
Start-Sleep -Milliseconds 500

while ($true) {
    $count  = 0
    $dist   = @{ "0"=0;"1"=0;"2"=0;"3"=0;"4"=0;"5"=0;"6"=0;"7"=0;"8"=0;"9"=0 }
    $amend  = 0

    if (Test-Path $manifestFile) {
        $lines = @(Get-Content $manifestFile -ErrorAction SilentlyContinue)
        $count = $lines.Count

        foreach ($line in $lines) {
            if (-not $line) { continue }
            try {
                $row = $line | ConvertFrom-Json
                $lbl = $row.label_human
                if ($dist.ContainsKey($lbl)) { $dist[$lbl]++ }
                if ($row.amended) { $amend++ }
            } catch {}
        }
    }

    $now     = [datetime]::Now
    $elapsed = ($now - $lastTime).TotalSeconds
    if ($elapsed -gt 0 -and $count -gt $lastCount) {
        $rate = ($count - $lastCount) / $elapsed
    }
    $lastCount = $count
    $lastTime  = $now

    $pct    = if ($Target -gt 0) { [math]::Min($count * 100.0 / $Target, 100.0) } else { 0.0 }
    $barW   = 40
    $filled = [math]::Round($pct / 100.0 * $barW)
    $bar    = ("#" * $filled) + ("-" * ($barW - $filled))
    $pctStr = $pct.ToString("F1", $inv)
    $rateStr= $rate.ToString("F2", $inv)
    $etaStr = if ($rate -gt 0) { "$([math]::Round(($Target - $count) / $rate))s" } else { "---" }
    $color  = if ($pct -ge 100) { "Green" } elseif ($pct -ge 50) { "Yellow" } else { "Cyan" }

    Clear-Host
    Write-Host ""
    Write-Host "  Digit Labeling Progress" -ForegroundColor Cyan
    Write-Host "  =================================================" -ForegroundColor DarkGray
    Write-Host ("  [{0}] {1,5}%" -f $bar, $pctStr) -ForegroundColor $color
    Write-Host ""
    Write-Host ("  Labeled : {0,5} / {1}" -f $count, $Target)
    Write-Host ("  Amended : {0,5}  (enmiendas)" -f $amend)     -ForegroundColor DarkYellow
    Write-Host ("  Speed   : {0} labels/s" -f $rateStr)          -ForegroundColor DarkCyan
    Write-Host ("  ETA     : {0}" -f $etaStr)                    -ForegroundColor DarkCyan
    Write-Host ""
    Write-Host "  Class distribution:" -ForegroundColor DarkGray
    foreach ($d in 0..9) {
        $n      = $dist["$d"]
        $bFill  = [math]::Round($n * 20.0 / [math]::Max(($dist.Values | Measure-Object -Maximum).Maximum, 1))
        $bEmpty = 20 - $bFill
        $dbar   = ("#" * $bFill) + ("-" * $bEmpty)
        $dcolor = if ($n -ge 150) { "Green" } elseif ($n -ge 75) { "Yellow" } else { "Red" }
        Write-Host ("    {0}: [{1}] {2,4}" -f $d, $dbar, $n) -ForegroundColor $dcolor
    }
    Write-Host "  =================================================" -ForegroundColor DarkGray
    Write-Host ("  {0}" -f (Get-Date -Format "HH:mm:ss"))            -ForegroundColor DarkGray

    if ($count -ge $Target) {
        Write-Host ""
        Write-Host "  Done! $count labels collected. Ready to train." -ForegroundColor Green
        break
    }

    Start-Sleep -Milliseconds $RefreshMs
}
