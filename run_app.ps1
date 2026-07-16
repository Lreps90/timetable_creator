param(
    [switch]$TestOnly,
    [switch]$BackendOnly,
    [switch]$NoInstall,
    [switch]$NoBrowser,
    [int]$BackendPort = 0,
    [int]$FrontendPort = 0
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$script:BrowserUiUrl = $null
$script:BrowserUiLabel = $null

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Find-Python {
    $VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $VenvPython) {
        return $VenvPython
    }

    Write-Step "Creating local Python virtual environment"
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & py -3 -m venv .venv
    } else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) {
            throw "Python was not found. Install Python 3.11+ or restore the .venv folder."
        }
        & python -m venv .venv
    }

    if (-not (Test-Path $VenvPython)) {
        throw "Could not create .venv\Scripts\python.exe."
    }
    return $VenvPython
}

function Test-PortAvailable {
    param([int]$Port)

    $listener = $null
    try {
        $address = [System.Net.IPAddress]::Parse("127.0.0.1")
        $listener = [System.Net.Sockets.TcpListener]::new($address, $Port)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($listener) {
            $listener.Stop()
        }
    }
}

function Find-AvailablePort {
    param(
        [int]$PreferredPort,
        [int[]]$FallbackPorts
    )

    if ($PreferredPort -gt 0) {
        if (Test-PortAvailable -Port $PreferredPort) {
            return $PreferredPort
        }
        throw "Port $PreferredPort is not available. Choose another port."
    }

    foreach ($port in $FallbackPorts) {
        if (Test-PortAvailable -Port $port) {
            return $port
        }
    }

    throw "Could not find an available local port."
}

function Start-Backend {
    param(
        [string]$PythonPath,
        [int]$Port
    )

    Write-Step "Starting FastAPI backend on port $Port"
    $command = "Set-Location '$Root'; & '$PythonPath' -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port $Port"
    Start-Process powershell -ArgumentList @("-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command)
}

function Start-Frontend {
    param(
        [int]$Port,
        [int]$ApiPort
    )

    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) {
        Write-Host ""
        Write-Host "Node.js/npm was not found on PATH." -ForegroundColor Yellow
        Write-Host "Opening the built-in browser UI served by FastAPI instead."
        Write-Host "Install Node.js later if you want to run the React/Vite development UI."
        $script:BrowserUiUrl = "http://127.0.0.1:$ApiPort/"
        $script:BrowserUiLabel = "Built-in UI"
        if (-not $NoBrowser) {
            Start-Sleep -Seconds 3
            Start-Process $script:BrowserUiUrl
        }
        return
    }

    Write-Step "Preparing React frontend"
    Push-Location (Join-Path $Root "frontend")
    try {
        if (-not (Test-Path "node_modules")) {
            npm install
        }
    } finally {
        Pop-Location
    }

    Write-Step "Starting Vite frontend on port $Port"
    $frontendRoot = Join-Path $Root "frontend"
    $command = "Set-Location '$frontendRoot'; `$env:VITE_BACKEND_URL='http://127.0.0.1:$ApiPort'; npm run dev -- --host 127.0.0.1 --port $Port"
    Start-Process powershell -ArgumentList @("-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command)
    $script:BrowserUiUrl = "http://127.0.0.1:$Port"
    $script:BrowserUiLabel = "React/Vite UI"

    if (-not $NoBrowser) {
        Start-Sleep -Seconds 4
        Start-Process $script:BrowserUiUrl
    }
}

$Python = Find-Python

if (-not $NoInstall) {
    Write-Step "Installing backend dependencies"
    & $Python -m pip install -r requirements.txt
}

if ($TestOnly) {
    Write-Step "Running backend tests"
    & $Python -m pytest backend/tests -q
    exit $LASTEXITCODE
}

$BackendPort = Find-AvailablePort -PreferredPort $BackendPort -FallbackPorts @(8000, 8001, 8010, 8080, 8888, 9000, 5000, 5050)
$FrontendPort = Find-AvailablePort -PreferredPort $FrontendPort -FallbackPorts @(5173, 5174, 5175, 3000, 3001, 4173)

Start-Backend -PythonPath $Python -Port $BackendPort

if ($BackendOnly) {
    if (-not $NoBrowser) {
        Start-Sleep -Seconds 3
        Start-Process "http://127.0.0.1:$BackendPort/docs"
    }
    Write-Host ""
    Write-Host "Backend: http://127.0.0.1:$BackendPort"
    Write-Host "API docs: http://127.0.0.1:$BackendPort/docs"
    exit 0
}

Start-Frontend -Port $FrontendPort -ApiPort $BackendPort

Write-Host ""
Write-Host "Backend:  http://127.0.0.1:$BackendPort"
Write-Host "API docs: http://127.0.0.1:$BackendPort/docs"
if ($script:BrowserUiUrl) {
    Write-Host "$($script:BrowserUiLabel): $script:BrowserUiUrl"
}
Write-Host ""
Write-Host "Useful options:"
Write-Host "  .\run_app.ps1 -TestOnly"
Write-Host "  .\run_app.ps1 -BackendOnly"
Write-Host "  .\run_app.ps1 -NoInstall"
Write-Host "  .\run_app.ps1 -BackendPort 8010 -FrontendPort 5174"
