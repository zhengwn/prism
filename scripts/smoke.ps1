<#
.SYNOPSIS
    End-to-end smoke test for the Prism sidecar (Windows PowerShell).

.DESCRIPTION
    Starts the Python sidecar in the background, waits for it to become
    healthy, pings a few endpoints, validates the JSON shape, and tears
    the process down. Exits 0 on success, 1 on any failure.

    Mirrors scripts/smoke.sh. Invoke directly:

        pwsh -File scripts/smoke.ps1

    Environment overrides:
        SIDECAR_HOST    default 127.0.0.1
        SIDECAR_PORT    default 8765
        SMOKE_RUNNER    default '' (auto: uv / python -m prism_sidecar)
        HEALTH_TIMEOUT  default 15 (seconds)
#>

[CmdletBinding()]
param(
    [string]$SidecarHost = $env:SIDECAR_HOST,
    [string]$SidecarPort = $env:SIDECAR_PORT,
    [string]$SmokeRunner = $env:SMOKE_RUNNER,
    [int]$HealthTimeout = 15
)

if ([string]::IsNullOrEmpty($SidecarHost)) { $SidecarHost = '127.0.0.1' }
if ([string]::IsNullOrEmpty($SidecarPort)) { $SidecarPort = '8765' }

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'   # faster Invoke-RestMethod

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir   = Split-Path -Parent $ScriptDir
$PyDir     = Join-Path $RootDir 'python'

$BaseUrl    = "http://${SidecarHost}:${SidecarPort}"
$HealthUrl  = "$BaseUrl/health"
$SourcesUrl = "$BaseUrl/api/sources"
$ItemsUrl   = "$BaseUrl/api/items"

$sidecarProc    = $null
$sidecarLogPath = $null
$started        = $false

function Write-Smoke   { param([string]$Msg) Write-Host "[smoke] $Msg" }
function Write-SmokeErr { param([string]$Msg) Write-Host "[smoke][fail] $Msg" -ForegroundColor Red }

function Stop-Sidecar {
    if ($null -ne $sidecarProc -and -not $sidecarProc.HasExited) {
        Write-Smoke "stopping sidecar (pid=$($sidecarProc.Id))"
        try { Stop-Process -Id $sidecarProc.Id -Force -ErrorAction SilentlyContinue } catch {}
    }
    # Belt-and-suspenders: anything still listening on the port.
    $listeners = Get-PortListeners -Port $SidecarPort
    foreach ($p in $listeners) {
        Write-Smoke "force-killing stubborn port listener pid=$p"
        try { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue } catch {}
    }
}

function Get-PortListeners {
    param([string]$Port)
    $pids = @()
    try {
        # Windows PowerShell 5.1+ and PowerShell 7+ on Windows.
        $conns = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop
        $pids  = $conns | ForEach-Object { $_.OwningProcess } | Sort-Object -Unique
    } catch {
        # Fallback: parse netstat output (works on all PS hosts).
        try {
            $lines = netstat -ano -p TCP | Select-String ":$Port\s.*LISTENING\s+(\d+)$"
            foreach ($l in $lines) {
                if ($l.Matches.Count -gt 0) { $pids += [int]$l.Matches[0].Groups[1].Value }
            }
            $pids = $pids | Sort-Object -Unique
        } catch {}
    }
    return $pids
}

try {
    # ----- Pre-flight checks --------------------------------------------
    if (-not (Test-Path $PyDir)) {
        throw "python/ not found at $PyDir — run from the repo root"
    }
    if (-not (Test-Path (Join-Path $PyDir 'pyproject.toml'))) {
        throw "$PyDir\pyproject.toml missing — is this the Prism repo?"
    }

    # Port conflict detection.
    $existing = Get-PortListeners -Port $SidecarPort
    if ($existing.Count -gt 0) {
        throw "port $SidecarPort already in use by pid(s): $($existing -join ', ') — stop them first"
    }

    # ----- Pick runner --------------------------------------------------
    if (-not [string]::IsNullOrEmpty($SmokeRunner)) {
        $runnerParts = $SmokeRunner -split '\s+'
        $runnerLabel = "SMOKE_RUNNER override: $SmokeRunner"
    } elseif (Get-Command 'uv' -ErrorAction SilentlyContinue) {
        $runnerParts = @('uv', 'run', 'prism-sidecar', '--host', $SidecarHost, '--port', $SidecarPort)
        $runnerLabel = 'uv run prism-sidecar'
    } elseif (Get-Command 'python' -ErrorAction SilentlyContinue) {
        $runnerParts = @('python', '-m', 'prism_sidecar', '--host', $SidecarHost, '--port', $SidecarPort)
        $runnerLabel = 'python -m prism_sidecar'
    } elseif (Get-Command 'python3' -ErrorAction SilentlyContinue) {
        $runnerParts = @('python3', '-m', 'prism_sidecar', '--host', $SidecarHost, '--port', $SidecarPort)
        $runnerLabel = 'python3 -m prism_sidecar'
    } else {
        throw "neither 'uv' nor 'python' is on PATH — install one to run the smoke test"
    }

    # ----- Start the sidecar --------------------------------------------
    Write-Smoke "starting sidecar via $runnerLabel on $BaseUrl"
    $sidecarLogPath = [System.IO.Path]::GetTempFileName()
    $sidecarProc = Start-Process -FilePath $runnerParts[0] `
        -ArgumentList ($runnerParts[1..($runnerParts.Count - 1)] -join ' ') `
        -WorkingDirectory $PyDir `
        -RedirectStandardOutput $sidecarLogPath `
        -RedirectStandardError  $sidecarLogPath `
        -PassThru -NoNewWindow
    Write-Smoke "sidecar pid=$($sidecarProc.Id), log=$sidecarLogPath"
    $started = $true

    # ----- Wait for /health ---------------------------------------------
    Write-Smoke "waiting for $HealthUrl (timeout=${HealthTimeout}s)"
    $elapsed = 0
    $healthy = $false
    while ($elapsed -lt $HealthTimeout) {
        if ($sidecarProc.HasExited) {
            Write-Smoke "sidecar exited prematurely. Log tail:"
            if (Test-Path $sidecarLogPath) { Get-Content $sidecarLogPath -Tail 40 -ErrorAction SilentlyContinue | Write-Host }
            $started = $false   # already dead, nothing to clean
            throw "sidecar process died before becoming healthy (try: cd $PyDir && uv sync)"
        }
        try {
            $null = Invoke-RestMethod -Uri $HealthUrl -Method Get -TimeoutSec 2 -ErrorAction Stop
            Write-Smoke "sidecar is healthy after ${elapsed}s"
            $healthy = $true
            break
        } catch {
            Start-Sleep -Seconds 1
            $elapsed += 1
        }
    }
    if (-not $healthy) {
        Write-Smoke "sidecar did not become healthy within ${HealthTimeout}s. Log tail:"
        if (Test-Path $sidecarLogPath) { Get-Content $sidecarLogPath -Tail 40 -ErrorAction SilentlyContinue | Write-Host }
        throw "health check timed out"
    }

    # ----- /health ------------------------------------------------------
    Write-Smoke "GET $HealthUrl"
    $health = Invoke-RestMethod -Uri $HealthUrl -Method Get -TimeoutSec 5
    if ($health.ok -ne $true) { throw "/health .ok expected true, got $($health.ok)" }
    if ([string]::IsNullOrEmpty($health.version)) { throw "/health .version missing" }
    if ($health.sourcesCount -lt 1) { throw "/health .sourcesCount not a positive int (got $($health.sourcesCount))" }
    if ($health.itemsCount   -lt 1) { throw "/health .itemsCount not a positive int (got $($health.itemsCount))" }
    Write-Smoke "/health OK  version=$($health.version)  sources=$($health.sourcesCount)  items=$($health.itemsCount)"

    # ----- /api/sources -------------------------------------------------
    Write-Smoke "GET $SourcesUrl"
    $sources = Invoke-RestMethod -Uri $SourcesUrl -Method Get -TimeoutSec 5
    if ($sources.Count -lt 1) { throw "/api/sources expected >=1 item, got $($sources.Count)" }
    $first = $sources[0]
    if ([string]::IsNullOrEmpty($first.id))   { throw "/api/sources[0].id missing" }
    if ([string]::IsNullOrEmpty($first.name)) { throw "/api/sources[0].name missing" }
    if ([string]::IsNullOrEmpty($first.kind)) { throw "/api/sources[0].kind missing" }
    Write-Smoke "/api/sources OK  count=$($sources.Count)  first=$($first.id) ($($first.name), $($first.kind))"

    # ----- /api/items ---------------------------------------------------
    Write-Smoke "GET $ItemsUrl"
    $items = Invoke-RestMethod -Uri $ItemsUrl -Method Get -TimeoutSec 5
    if ($items.Count -lt 1) { throw "/api/items expected >=1 item, got $($items.Count)" }
    $firstItem = $items[0]
    if ([string]::IsNullOrEmpty($firstItem.id))       { throw "/api/items[0].id missing" }
    if ([string]::IsNullOrEmpty($firstItem.title))    { throw "/api/items[0].title missing" }
    if ([string]::IsNullOrEmpty($firstItem.sourceId)) { throw "/api/items[0].sourceId missing" }
    Write-Smoke "/api/items OK  count=$($items.Count)  first=$($firstItem.id)"

    # ----- Deep check: /api/sources/{id} --------------------------------
    Write-Smoke "GET $BaseUrl/api/sources/$($first.id)"
    $deep = Invoke-RestMethod -Uri "$BaseUrl/api/sources/$($first.id)" -Method Get -TimeoutSec 5
    if ($deep.kind -ne $first.kind) { throw "/api/sources/$($first.id) .kind='$($deep.kind)', expected '$($first.kind)'" }
    if ($deep.id   -ne $first.id)   { throw "/api/sources/$($first.id) .id='$($deep.id)', expected '$($first.id)'" }
    Write-Smoke "/api/sources/$($first.id) OK"

    Write-Smoke "all smoke checks passed"
    exit 0
}
catch {
    Write-SmokeErr $_.Exception.Message
    exit 1
}
finally {
    if ($started) { Stop-Sidecar }
}
