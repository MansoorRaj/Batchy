# Batchy

A small local web app for downloading videos with [yt-dlp](https://github.com/yt-dlp/yt-dlp). Paste links, queue them, and download the best video + best audio. [aria2c](https://aria2.github.io/) is used for fast multi-connection downloads.

## Run it (Windows)

Open **PowerShell** (not Command Prompt) and run:

```powershell
irm https://raw.githubusercontent.com/MansoorRaj/Batchy/main/install.ps1 | iex
```

That's it. Your browser opens to `http://127.0.0.1:8765/` (or the next free port, printed in the console).

### What the installer does

On first run it sets everything up automatically:

1. Installs Python 3.10+ if missing (via winget, or python.org as a fallback)
2. Installs Deno if no JS runtime is found (needed for YouTube)
3. Creates a private environment in `%LOCALAPPDATA%\ytdl_menu` and installs/updates yt-dlp
4. Downloads the latest `ytdl_web.py` from this repo
5. Starts the app. ffmpeg and aria2c are downloaded in the background on first launch

Later runs skip what's already installed and only update yt-dlp and the app script, so they start much faster.

## Using it

1. Paste one or more links into the box and press **Enter** (or click **Add**). Shift+Enter adds a new line.
2. Click **Download queue**.
3. Use **Stop** to cancel, the **x** on a row to remove it, and **Clear finished** to tidy up done items.

Failed items stay in the queue so you can retry them.

### Settings (sliders icon)

| Setting | What it does |
| --- | --- |
| Save to | Download folder. Default: `Downloads\yt-dlp` in your user folder. |
| Container | `mkv` keeps the best streams untouched. `mp4` is more compatible. |
| aria2c fast mode | Multi-connection downloads (1 to 16). Turn off for exact live progress. |

### Good to know

- A link that contains both a video and a playlist (for example a YouTube Mix) downloads **only that video**. A link that is purely a playlist downloads the whole playlist.
- With aria2c on, yt-dlp can't report live percent and speed, so active items show an animated bar. Turn aria2c off if you want exact progress.
- The page loads Tailwind, Lucide icons and Google Fonts from the internet, so it needs a connection to look right.

## Stopping it

Click the **power** icon in the page, or press **Ctrl+C** in the PowerShell window.

## Run it manually

If you've already run the installer once, you can start it directly:

```powershell
& "$env:LOCALAPPDATA\ytdl_menu\venv\Scripts\python.exe" "$env:LOCALAPPDATA\ytdl_menu\ytdl_web.py"
```

On macOS or Linux, install Python 3.10+, then:

```bash
pip install "yt-dlp[default,curl-cffi]"
python3 ytdl_web.py
```

ffmpeg is downloaded automatically on Linux and installed with Homebrew on macOS. For aria2c, install it yourself (`brew install aria2` or `sudo apt install aria2`); without it the app uses the built-in downloader.

## Files

| File | Purpose |
| --- | --- |
| `install.ps1` | Bootstrapper for the `irm \| iex` one-liner |
| `ytdl_web.py` | The app: a local server plus the web UI |

## Troubleshooting

- **`irm` or `iex` is not recognized:** you're in Command Prompt. Type `powershell` first, or use:
  ```
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/MansoorRaj/Batchy/main/install.ps1 | iex"
  ```
- **Old page or old behavior after an update:** an earlier copy may still be running. Close it, then clear the cached script and re-run the installer:
  ```powershell
  Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*ytdl_menu*" } | Stop-Process -Force
  Remove-Item "$env:LOCALAPPDATA\ytdl_menu\ytdl_web.py" -ErrorAction SilentlyContinue
  irm https://raw.githubusercontent.com/MansoorRaj/Batchy/main/install.ps1 | iex
  ```
  Then hard-refresh the browser with Ctrl+F5.
- **YouTube downloads fail:** make sure a JS runtime (Deno or Node) is installed, and that yt-dlp is up to date. Re-running the installer updates it.
- **The installer says "using cached copy":** it couldn't reach GitHub. Check your connection and that `ytdl_web.py` is in the repo root.
- **Reset everything:** delete the `%LOCALAPPDATA%\ytdl_menu` and `%USERPROFILE%\.ytdl_menu` folders and run the installer again.

## Notes

Only download content you have the right to download, and follow the terms of the sites you use.
