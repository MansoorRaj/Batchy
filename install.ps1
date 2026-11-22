# yt-dlp web UI bootstrapper
# Run with:  irm https://raw.githubusercontent.com/MansoorRaj/Batchy/main/install.ps1 | iex

function Invoke-YtdlMenu {
    $RepoRaw = "https://raw.githubusercontent.com/MansoorRaj/Batchy/main"
    $ScriptName = "ytdl_web.py"

    $ErrorActionPreference = "Stop"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = "SilentlyContinue"   # makes Invoke-WebRequest much faster on PS 5.1

    $Home_ = Join-Path $env:LOCALAPPDATA "ytdl_menu"
    $Venv = Join-Path $Home_ "venv"
    $Script = Join-Path $Home_ $ScriptName
    New-Item -ItemType Directory -Force -Path $Home_ | Out-Null

    # Runs a native command without PS 5.1 turning its stderr output into a terminating error.
    # Returns the exit code.
    function Run-Native {
        param([string]$Exe, [string[]]$ArgList)
        $old = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try { & $Exe @ArgList 2>&1 | Out-Null } catch { }
        finally { $ErrorActionPreference = $old }
        return $LASTEXITCODE
    }

    function Refresh-Path {
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "User")
    }

    function Test-Python($exe) {
        try {
            $code = Run-Native $exe @("-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)")
            return ($code -eq 0)
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

    # ---- 1. Python ----------------------------------------------------------
    $py = Find-Python
    if (-not $py) {
        Write-Host "Python 3.10+ not found - installing..." -ForegroundColor Cyan
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Run-Native "winget" @("install", "-e", "--id", "Python.Python.3.12", "--scope", "user",
                "--silent", "--accept-package-agreements", "--accept-source-agreements") | Out-Null
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
        Run-Native "winget" @("install", "-e", "--id", "DenoLand.Deno", "--silent",
            "--accept-package-agreements", "--accept-source-agreements") | Out-Null
        Refresh-Path
    }

    # ---- 3. Virtual environment + yt-dlp ------------------------------------
    $venvPy = Join-Path $Venv "Scripts\python.exe"
    if (-not (Test-Path $venvPy)) {
        Write-Host "Creating environment..." -ForegroundColor Cyan
        $code = Run-Native $py @("-m", "venv", $Venv)
        if ($code -ne 0 -or -not (Test-Path $venvPy)) { throw "Could not create the virtual environment." }
    }

    Write-Host "Installing/updating yt-dlp (first run can take a minute)..." -ForegroundColor Cyan
    $code = Run-Native $venvPy @("-m", "pip", "install", "--upgrade", "--disable-pip-version-check", "yt-dlp[default,curl-cffi]")
    if ($code -ne 0) {
        Write-Host "Retrying without curl-cffi..." -ForegroundColor Yellow
        $code = Run-Native $venvPy @("-m", "pip", "install", "--upgrade", "--disable-pip-version-check", "yt-dlp[default]")
        if ($code -ne 0) { throw "pip could not install yt-dlp." }
    }

    # ---- 4. Fetch the latest app script (fall back to cached copy) ----------
    try {
        $stamp = [DateTime]::UtcNow.Ticks
        $url = "${RepoRaw}/${ScriptName}?t=${stamp}"   # braces matter: PowerShell treats $Name? as a variable name
        Invoke-WebRequest $url -OutFile $Script -UseBasicParsing
    } catch {
        if (-not (Test-Path $Script)) { throw "Could not download ${RepoRaw}/${ScriptName} : $($_.Exception.Message)" }
        Write-Host "Could not fetch the latest script ($($_.Exception.Message)) - using cached copy." -ForegroundColor Yellow
    }

    # ---- 5. Run the web UI (opens in your browser; Ctrl+C or the Quit button stops it)
    Write-Host "Starting the web UI..." -ForegroundColor Green
    $ErrorActionPreference = "Continue"
    & $venvPy $Script
}

try { Invoke-YtdlMenu } catch { Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red }
