param(
    [switch]$TestOnly,
    [switch]$BackendOnly,
    [switch]$NoInstall,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

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

function Start-Backend {
    param([string]$PythonPath)

    Write-Step "Starting FastAPI backend"
    $command = "Set-Location '$Root'; & '$PythonPath' -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000"
    Start-Process powershell -ArgumentList @("-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command)
}

function Start-Frontend {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) {
        Write-Host ""
        Write-Host "Node.js/npm was not found on PATH." -ForegroundColor Yellow
        Write-Host "The backend is running, but the React browser UI needs Node.js."
        Write-Host "Install Node.js, then rerun this script."
        if (-not $NoBrowser) {
            Start-Process "http://127.0.0.1:8000/docs"
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

    Write-Step "Starting Vite frontend"
    $frontendRoot = Join-Path $Root "frontend"
    $command = "Set-Location '$frontendRoot'; npm run dev"
    Start-Process powershell -ArgumentList @("-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command)

    if (-not $NoBrowser) {
        Start-Sleep -Seconds 4
        Start-Process "http://127.0.0.1:5173"
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

Start-Backend -PythonPath $Python

if ($BackendOnly) {
    if (-not $NoBrowser) {
        Start-Sleep -Seconds 3
        Start-Process "http://127.0.0.1:8000/docs"
    }
    Write-Host ""
    Write-Host "Backend: http://127.0.0.1:8000"
    Write-Host "API docs: http://127.0.0.1:8000/docs"
    exit 0
}

Start-Frontend

Write-Host ""
Write-Host "Backend:  http://127.0.0.1:8000"
Write-Host "API docs: http://127.0.0.1:8000/docs"
Write-Host "Frontend: http://127.0.0.1:5173"
Write-Host ""
Write-Host "Useful options:"
Write-Host "  .\run_app.ps1 -TestOnly"
Write-Host "  .\run_app.ps1 -BackendOnly"
Write-Host "  .\run_app.ps1 -NoInstall"
