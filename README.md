# Batchy

**Download videos in batches. Simply, quickly, and locally.**

Batchy is a lightweight desktop-friendly web app that makes downloading videos easy. Paste your links, add them to the queue, and let Batchy handle the rest.

Powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp), Batchy automatically downloads the best available video and audio quality. It also supports [aria2c](https://aria2.github.io/) for faster, multi-connection downloads.

No complicated setup. No unnecessary clutter. Just paste, queue, and download.

---

## Get Started

<details>
<summary><strong>Windows — Quick Installation</strong></summary>

### 1. Open PowerShell

Open Windows PowerShell (not Command Prompt).

### 2. Run the installer

Copy and paste this command:

```powershell
irm https://raw.githubusercontent.com/MansoorRaj/Batchy/main/install.ps1 | iex
```

### 3. You're ready!

That's it! Batchy will automatically:

- Install any required dependencies.
- Set up its private Python environment.
- Download and configure yt-dlp.
- Prepare the application.
- Launch Batchy in your default browser.

Once everything is ready, open:

**http://127.0.0.1:8765/**

If that port is already occupied, Batchy automatically uses another available port and displays it in the PowerShell window.

</details>

---

## What Makes Batchy Useful?

- **Batch Downloads:** Add multiple video links and download them together.
- **Best Quality:** Automatically selects the best available video and audio streams.
- **Fast Downloads:** Supports aria2c for multi-connection downloading.
- **Simple Interface:** Manage everything from one clean web interface.
- **Queue Management:** Add, remove, stop, retry, and clear downloads.
- **Local & Private:** Runs on your own computer through a local web server.
- **Easy Setup:** The installer handles dependencies and configuration automatically.

---

## Using Batchy

Getting started is simple.

1. **Add links:** Paste one or more video URLs into the input box.
2. **Queue them:** Press `Enter` or click **Add**.
3. **Start downloading:** Click **Download queue**.
4. **Manage downloads:** Monitor your queue and manage individual items.

### Managing Your Queue

| Action | What it does |
|---|---|
| Download queue | Starts downloading queued items |
| Stop | Stops the current download process |
| × | Removes an individual item |
| Clear finished | Removes completed items from the queue |

**Tip:** Failed downloads remain in your queue, allowing you to retry them without adding the links again.

---

## Customize Your Experience

Open **Settings** using the sliders icon to configure how Batchy works.

| Setting | Description |
|---|---|
| **Save to** | Choose where your downloaded videos are stored. |
| **Container** | Choose between MKV and MP4 formats. |
| **aria2c Fast Mode** | Enable multi-connection downloads for potentially faster downloading. |

### Understanding the Settings

**Save Location**

By default, downloads are saved to:

```text
%USERPROFILE%\Downloads\yt-dlp
```

You can change this to any folder you prefer.

**Video Container**

- **MKV:** Preserves the best available streams without unnecessary conversion.
- **MP4:** Offers better compatibility with many devices and media players.

**aria2c Fast Mode**

Enable this option to use multiple connections (1–16) for downloading.

Keep in mind:
- Enabled: Uses aria2c for multi-connection downloads. Progress is shown using an animated bar.
- Disabled: Uses the built-in downloader and displays more accurate download percentages and speeds.

---

## Automatic Setup

Batchy takes care of its own dependencies.

On the first installation, the installer checks and prepares everything required:

| Component | Purpose |
|---|---|
| Python 3.10+ | Runs the Batchy application |
| Deno | Provides a JavaScript runtime needed for certain YouTube downloads |
| yt-dlp | Handles video and audio extraction |
| FFmpeg | Processes and merges audio/video streams |
| aria2c | Enables multi-connection downloads |

Dependencies are installed automatically where supported.

### What Happens on Future Launches?

You don't have to repeat the entire installation process.

On subsequent runs, the installer:
- Skips dependencies that are already installed.
- Updates yt-dlp.
- Downloads the latest application script.
- Starts Batchy.

This makes subsequent launches considerably faster.

---

## A Few Things to Know

**YouTube Mixes and Playlists**

- A link containing both an individual video and a playlist (such as a YouTube Mix) downloads only the selected video.
- A direct playlist link downloads the entire playlist.

**Download Progress**

When aria2c is enabled, exact download percentages and speeds aren't available through yt-dlp. Batchy displays an animated progress bar instead.

Disable aria2c if you prefer more detailed progress information.

**Internet Connection**

The interface loads Tailwind CSS, Lucide icons, and Google Fonts from online sources. An internet connection is required for these resources to load correctly.

---

## Stopping Batchy

You can shut down Batchy in either of these ways:

- Click the **Power** icon in the application.
- Press `Ctrl + C` in the PowerShell window running Batchy.

---

## Advanced: Running Batchy Manually

Already installed Batchy? You can launch it without running the installer again.

Open PowerShell and execute:

```powershell
& "$env:LOCALAPPDATA\ytdl_menu\venv\Scripts\python.exe" "$env:LOCALAPPDATA\ytdl_menu\ytdl_web.py"
```

### macOS & Linux

Install Python 3.10 or newer, then install yt-dlp:

```bash
pip install "yt-dlp[default,curl-cffi]"
```

Run the application:

```bash
python3 ytdl_web.py
```

**Additional dependencies:**

- FFmpeg is downloaded automatically on Linux and installed using Homebrew on macOS.
- aria2c must be installed separately.

For macOS:

```bash
brew install aria2
```

For Ubuntu/Debian:

```bash
sudo apt install aria2
```

Without aria2c, Batchy uses its built-in downloader.

---

## Troubleshooting

Having trouble? Here are some common fixes.

<details>
<summary><strong>PowerShell says 'irm' or 'iex' is not recognized</strong></summary>

You're probably using Command Prompt instead of PowerShell.

Either open PowerShell directly or run this command from Command Prompt:

```cmd
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/MansoorRaj/Batchy/main/install.ps1 | iex"
```

</details>

<details>
<summary><strong>Batchy still shows the old version</strong></summary>

An older instance might still be running.

1. Close the existing Batchy instance.
2. Open PowerShell.
3. Run:

```powershell
Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*ytdl_menu*" } | Stop-Process -Force

Remove-Item "$env:LOCALAPPDATA\ytdl_menu\ytdl_web.py" -ErrorAction SilentlyContinue

irm https://raw.githubusercontent.com/MansoorRaj/Batchy/main/install.ps1 | iex
```

Finally, refresh your browser using `Ctrl + F5`.

</details>

<details>
<summary><strong>YouTube downloads are failing</strong></summary>

Make sure you have a JavaScript runtime installed (Deno or Node.js
