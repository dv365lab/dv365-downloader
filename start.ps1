[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

try {
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        $pythonWorks = $false
        if ($null -ne $pythonCommand) {
            & $pythonCommand.Source -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
            $pythonWorks = ($LASTEXITCODE -eq 0)
        }
        Write-Host 'Creating a local Python environment...'
        if ($pythonWorks) {
            & $pythonCommand.Source -m venv (Join-Path $PSScriptRoot '.venv')
        } else {
            $pyCommand = Get-Command py -ErrorAction SilentlyContinue
            if ($null -eq $pyCommand) {
                throw 'Python 3.10+ is required. Install Python, enable Add Python to PATH, then reopen this launcher.'
            }
            & $pyCommand.Source -3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
            if ($LASTEXITCODE -ne 0) {
                throw 'Python 3.10+ is required. Install a supported Python version and try again.'
            }
            & $pyCommand.Source -3 -m venv (Join-Path $PSScriptRoot '.venv')
        }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
            throw 'Could not create .venv. Check that your Python installation includes venv and pip.'
        }
    }

    $requirementsPath = Join-Path $PSScriptRoot 'requirements.txt'
    $requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash
    $stampPath = Join-Path $PSScriptRoot '.venv\requirements.sha256'
    $savedHash = if (Test-Path -LiteralPath $stampPath) { (Get-Content -LiteralPath $stampPath -Raw).Trim() } else { '' }
    & $venvPython -c "import importlib.util,sys; sys.exit(0 if all(importlib.util.find_spec(name) for name in ['flask', 'waitress', 'yt_dlp']) else 1)"
    $dependenciesMissing = $LASTEXITCODE -ne 0
    if ($dependenciesMissing -or $savedHash -ne $requirementsHash) {
        Write-Host 'Installing dependencies (internet access required on first run)...'
        & $venvPython -m pip install -r $requirementsPath
        if ($LASTEXITCODE -ne 0) {
            throw 'Dependency installation failed. Check your internet connection and retry start.bat.'
        }
        Set-Content -LiteralPath $stampPath -Value $requirementsHash -Encoding ASCII
    }

    $appArgs = @((Join-Path $PSScriptRoot 'app.py'), '--port', $Port.ToString())
    if ($NoBrowser) { $appArgs += '--no-browser' }
    Write-Host "Starting VideoDown at http://127.0.0.1:$Port"
    Write-Host 'Keep this window open while downloading. Press Ctrl+C to stop.'
    & $venvPython @appArgs
    exit $LASTEXITCODE
} catch {
    Write-Host "VideoDown: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
