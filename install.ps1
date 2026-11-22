# yt-dlp GUI bootstrapper
# Run with:  irm https://raw.githubusercontent.com/YOUR_USER/YOUR_REPO/main/install.ps1 | iex

function Invoke-YtdlMenu {
    # >>> EDIT THESE TWO LINES <<<
    $RepoRaw = "https://raw.githubusercontent.com/YOUR_USER/YOUR_REPO/main"
    $ScriptName = "ytdl_gui.py"

    $ErrorActionPreference = "Stop"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = "SilentlyContinue"   # makes Invoke-WebRequest much faster on PS 5.1

    $Home_ = Join-Path $env:LOCALAPPDATA "ytdl_menu"
    $Venv = Join-Path $Home_ "venv"
    $Script = Join-Path $Home_ $ScriptName
    New-Item -ItemType Directory -Force -Path $Home_ | Out-Null

    function Refresh-Path {
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "User")
    }

    function Test-Python($exe) {
        try {
            & $exe -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
            return ($LASTEXITCODE -eq 0)
        } catch { return $false }
    }

    function Find-Python {
        $candidates = @("python", "python3")
        $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | ForEach-Object { $_.FullName }
        foreach ($c in $candidates) {
            if ((Get-Command $c -ErrorAction SilentlyContinue) -and (Test-Python $c)) { return $c }
        }
        return $null
    }

    # ---- 1. Python (with Tk) ------------------------------------------------
    $py = Find-Python
    if (-not $py) {
        Write-Host "Python 3.10+ not found - installing..." -ForegroundColor Cyan
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            winget install -e --id Python.Python.3.12 --scope user --silent `
                --accept-package-agreements --accept-source-agreements | Out-Null
        } else {
            $installer = Join-Path $env:TEMP "python-installer.exe"
            Invoke-WebRequest "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe" `
                -OutFile $installer -UseBasicParsing
            Start-Process $installer -Wait -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_tcltk=1"
            Remove-Item $installer -ErrorAction SilentlyContinue
        }
        Refresh-Path
        $py = Find-Python
        if (-not $py) { throw "Python install failed. Install Python 3.10+ manually from python.org and rerun." }
    }

    # ---- 2. JS runtime (YouTube needs Deno or Node) -------------------------
    $hasJs = (Get-Command deno -ErrorAction SilentlyContinue) -or (Get-Command node -ErrorAction SilentlyContinue)
    if (-not $hasJs -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "Installing Deno (needed for YouTube)..." -ForegroundColor Cyan
        try {
            winget install -e --id DenoLand.Deno --silent --accept-package-agreements --accept-source-agreements | Out-Null
            Refresh-Path
        } catch { Write-Host "Deno install skipped." -ForegroundColor Yellow }
    }

    # ---- 3. Virtual environment + yt-dlp ------------------------------------
    $venvPy = Join-Path $Venv "Scripts\python.exe"
    $venvPyw = Join-Path $Venv "Scripts\pythonw.exe"
    if (-not (Test-Path $venvPy)) {
        Write-Host "Creating environment..." -ForegroundColor Cyan
        & $py -m venv $Venv
    }
    Write-Host "Installing/updating yt-dlp (first run can take a minute)..." -ForegroundColor Cyan
    # Native stderr must not be treated as a terminating error in PS 5.1
    $ErrorActionPreference = "Continue"
    & $venvPy -m pip install --upgrade --disable-pip-version-check "yt-dlp[default,curl-cffi]" 2>&1 |
        ForEach-Object { "$_" } | Select-String -Pattern "ERROR|Successfully" | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Retrying without curl-cffi..." -ForegroundColor Yellow
        & $venvPy -m pip install --upgrade --disable-pip-version-check "yt-dlp[default]" 2>&1 |
            ForEach-Object { "$_" } | Select-String -Pattern "ERROR|Successfully" | ForEach-Object { Write-Host "  $_" }
        if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = "Stop"; throw "pip could not install yt-dlp." }
    }
    $ErrorActionPreference = "Stop"

    # ---- 4. Fetch the latest app script (fall back to cached copy) ----------
    try {
        Invoke-WebRequest "$RepoRaw/$ScriptName" -OutFile $Script -UseBasicParsing
    } catch {
        if (-not (Test-Path $Script)) { throw "Could not download $RepoRaw/$ScriptName : $_" }
        Write-Host "Offline - using cached copy." -ForegroundColor Yellow
    }

    # ---- 5. Launch the GUI (no console window) ------------------------------
    Write-Host "Launching..." -ForegroundColor Green
    $proc = Start-Process $venvPyw -ArgumentList "`"$Script`"" -PassThru
    Start-Sleep -Seconds 4
    if ($proc.HasExited) {
        Write-Host "The app closed right away - running it with a console to show the error:" -ForegroundColor Yellow
        $ErrorActionPreference = "Continue"
        & $venvPy $Script
        $ErrorActionPreference = "Stop"
    }
}

try { Invoke-YtdlMenu } catch { Write-Host "Error: $_" -ForegroundColor Red }