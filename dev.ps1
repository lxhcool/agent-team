# Team Agent - Windows dev script
# Usage: .\dev.ps1 [stop|status|restart|help]

$ErrorActionPreference = "Continue"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $ProjectDir "backend"
$FrontendDir = Join-Path $ProjectDir "frontend"
$RunDir = Join-Path $ProjectDir ".run"
$BackendPidFile = Join-Path $RunDir "backend.pid"
$FrontendPidFile = Join-Path $RunDir "frontend.pid"

$BackendDefaultPort = 8200
$FrontendDefaultPort = 3200

function Write-Info($text) { Write-Host $text -ForegroundColor Green }
function Write-Warn($text) { Write-Host $text -ForegroundColor Yellow }
function Write-Err($text)  { Write-Host $text -ForegroundColor Red }
function Write-Hl($text)   { Write-Host $text -ForegroundColor Cyan }

function Test-PortListening {
    param([int]$Port)
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect("127.0.0.1", $Port)
        $tcp.Close()
        return $true
    } catch { return $false }
}

function Wait-ForPort {
    param([int]$Port, [string]$Name, [int]$MaxWait = 60)
    $elapsed = 0
    while ($elapsed -lt $MaxWait) {
        if (Test-PortListening $Port) { return $true }
        Start-Sleep -Seconds 1
        $elapsed++
    }
    return $false
}

function Kill-Tree {
    param([int]$ProcId, [string]$Name)
    try {
        Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $ProcId } | ForEach-Object {
            Kill-Tree $_.ProcessId ""
        }
        Stop-Process -Id $ProcId -Force -ErrorAction SilentlyContinue
        if ($Name) { Write-Warn "   Stopped $Name (PID: $ProcId)" }
    } catch {}
}

function Stop-ByPidFile {
    param([string]$PidFile, [string]$Name)
    if (-not (Test-Path $PidFile)) { return }
    $pidVal = (Get-Content $PidFile -Encoding UTF8 -ErrorAction SilentlyContinue)
    if ($pidVal) { Kill-Tree ([int]$pidVal) $Name }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

function Stop-All {
    Stop-ByPidFile $FrontendPidFile "Frontend"
    Stop-ByPidFile $BackendPidFile "Backend"
    # Kill anything still on our ports
    foreach ($port in ($BackendDefaultPort, $FrontendDefaultPort)) {
        try {
            Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
                Kill-Tree $_.OwningProcess ""
            }
        } catch {}
    }
}

# ---- Setup ----

function Ensure-Env {
    $envFile = Join-Path $ProjectDir ".env"
    if (Test-Path $envFile) { return }
    $encKey = -join ((1..32) | ForEach-Object { [char](Get-Random -Min 33 -Max 126) })
    $content = @"
HOST=0.0.0.0
PORT=8200
DEBUG=true
DATABASE_URL=sqlite+aiosqlite:///./data/team_agent.db
ENCRYPTION_KEY=$encKey
CORS_ORIGINS=[""http://localhost:3200"",""http://127.0.0.1:3200"",""http://localhost:3000"",""http://127.0.0.1:3000""]
DEFAULT_LLM_PROVIDER=openai
DEFAULT_LLM_MODEL=gpt-4o-mini
DEFAULT_SESSION_BUDGET_USD=10.0
"@
    [System.IO.File]::WriteAllText($envFile, $content, [System.Text.Encoding]::UTF8)
}

function Ensure-BackendDeps {
    $venvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        Write-Warn "   Creating backend venv ..."
        Push-Location $BackendDir; python -m venv .venv; Pop-Location
    }
    $reqFile = Join-Path $BackendDir "requirements.txt"
    $stampFile = Join-Path $RunDir "deps.sha256"
    $hash = (Get-FileHash $reqFile -Algorithm SHA256 -ErrorAction SilentlyContinue).Hash
    $old = if (Test-Path $stampFile) { Get-Content $stampFile -Encoding UTF8 } else { "" }
    if ($hash -eq $old) { return }
    Write-Warn "   Installing backend deps ..."
    & $venvPython -m pip install -r $reqFile -q 2>$null
    Set-Content $stampFile $hash
}

function Ensure-FrontendDeps {
    $nextBin = Join-Path $FrontendDir "node_modules\.bin\next.cmd"
    if (Test-Path $nextBin) { return }
    Write-Warn "   Installing frontend deps ..."
    Push-Location $FrontendDir; npm install; Pop-Location
}

# ---- Start ----

function Start-App {
    New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
    Stop-All
    Start-Sleep -Seconds 1

    Ensure-Env
    Ensure-BackendDeps
    Ensure-FrontendDeps

    $backendPort = $BackendDefaultPort
    $frontendPort = $FrontendDefaultPort

    Write-Info ">> Starting Team Agent ..."Write-Host ""

    # --- Backend ---
    Write-Info "   [1/2] Backend ..."
    $venvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
    $pythonCmd = if (Test-Path $venvPython) { $venvPython } else { "python" }
    New-Item -ItemType Directory -Path (Join-Path $BackendDir "data") -Force | Out-Null

    $beLog = Join-Path $RunDir "backend.log"
    try { [System.IO.File]::WriteAllText($beLog, "", [System.Text.Encoding]::UTF8) } catch {}

    $env:PORT = "$backendPort"
    $env:HOST = "0.0.0.0"
    $env:CORS_ORIGINS = "[`"http://localhost:$frontendPort`",`"http://127.0.0.1:$frontendPort`"]"

    $beProc = Start-Process -FilePath $pythonCmd `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "$backendPort" `
        -WorkingDirectory $BackendDir `
        -RedirectStandardOutput (Join-Path $RunDir "backend-stdout.log") `
        -RedirectStandardError $beLog `
        -WindowStyle Hidden -PassThru

    if ($beProc) { Set-Content $BackendPidFile $beProc.Id }

    if (Wait-ForPort $backendPort "Backend" 60) {
        Write-Info "         OK -> http://localhost:$backendPort"
    } else {
        Write-Err "         FAIL - check: $beLog"
        return
    }

    # --- Frontend ---
    Write-Info "   [2/2] Frontend ..."
    $envLocal = "NEXT_PUBLIC_API_URL=http://localhost:$backendPort`r`nNEXT_PUBLIC_BACKEND_PORT=$backendPort"
    [System.IO.File]::WriteAllText((Join-Path $FrontendDir ".env.local"), $envLocal, [System.Text.Encoding]::UTF8)

    # Use npm run dev instead of npx to avoid the "install?" prompt
    $feLog = Join-Path $RunDir "frontend.log"
    $nextEntry = Join-Path $FrontendDir "node_modules\next\dist\bin\next"
    if (-not (Test-Path $nextEntry)) {
        Write-Err "   next not found, running npm install ..."
        Push-Location $FrontendDir; npm install; Pop-Location
    }
    $env:NEXT_PUBLIC_BACKEND_PORT = "$backendPort"
    $feProc = Start-Process -FilePath "node" `
        -ArgumentList $nextEntry, "dev", "--hostname", "127.0.0.1", "--port", "$frontendPort" `
        -WorkingDirectory $FrontendDir `
        -RedirectStandardOutput $feLog `
        -RedirectStandardError (Join-Path $RunDir "frontend-err.log") `
        -WindowStyle Hidden -PassThru
    if ($feProc) { Set-Content $FrontendPidFile $feProc.Id }

    if (Wait-ForPort $frontendPort "Frontend" 60) {
        Write-Info "         OK -> http://localhost:$frontendPort"
    } else {
        Write-Err "         FAIL - check: $feLog"
        return
    }

    # --- Done ---
    Write-Host ""
    Write-Info "=========================================="
    Write-Host "   Frontend : " -NoNewline; Write-Hl "http://localhost:$frontendPort"
    Write-Host "   Backend  : " -NoNewline; Write-Hl "http://localhost:$backendPort"
    Write-Host "   API Docs : " -NoNewline; Write-Hl "http://localhost:${backendPort}/docs"
    Write-Host ""
    Write-Host "   Logs: $RunDir\"
    Write-Warn "   Ctrl+C to stop"
    Write-Info "=========================================="

    Start-Sleep -Seconds 3
    Start-Process "http://localhost:$frontendPort"

    try { while ($true) { Start-Sleep -Seconds 10 } }
    finally { Write-Host ""; Stop-All; Write-Info "Stopped." }
}

# ---- Main ----

$cmd = if ($args.Count -gt 0) { $args[0] } else { "" }
switch ($cmd) {
    "stop"    { Stop-All; Write-Info "Stopped." }
    "status"  {
        foreach ($e in @(@{F=$BackendPidFile;N="Backend"},@{F=$FrontendPidFile;N="Frontend"})) {
            if (Test-Path $e.F) {
                $p = Get-Content $e.F -Encoding UTF8
                try { $null = Get-Process -Id ([int]$p) -ErrorAction Stop; Write-Info "$($e.N): Running (PID $p)" }
                catch { Write-Err "$($e.N): Dead" }
            } else { Write-Warn "$($e.N): Not started" }
        }
    }
    "restart" { Stop-All; Start-Sleep 2; Start-App }
    "help"    {
        Write-Host "Usage: .\dev.ps1 [stop|status|restart|help]"
        Write-Host "Ports: Backend $BackendDefaultPort / Frontend $FrontendDefaultPort"
    }
    default { Start-App }
}
