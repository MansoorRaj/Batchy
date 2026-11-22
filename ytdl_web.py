"""yt-dlp downloader with a local web UI, aria2c accelerated downloads and auto-installed tools.

Stdlib-only server: run it, and it opens http://127.0.0.1:<port> in your browser.
"""
import importlib
import importlib.util
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import threading
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

APP_HOME = Path.home() / ".ytdl_menu"
TOOLS = APP_HOME / "tools"
DEFAULT_DIR = Path.home() / "Downloads" / "yt-dlp"
EXE = ".exe" if os.name == "nt" else ""
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
ARIA2_WIN_URL = (
    "https://github.com/aria2/aria2/releases/download/release-1.37.0/"
    "aria2-1.37.0-win-64bit-build1.zip"
)
TOKEN = secrets.token_urlsafe(24)


# --------------------------------------------------------------------------- #
# Tool installers
# --------------------------------------------------------------------------- #
def run_quiet(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, creationflags=NOWIN, **kw)


def pip_install(*packages: str) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "-q", *packages]
    if run_quiet(cmd).returncode == 0:
        return True
    return run_quiet(cmd + ["--user", "--break-system-packages"]).returncode == 0


def ensure_ytdlp(say) -> bool:
    missing = [m for m in ("yt_dlp", "curl_cffi") if importlib.util.find_spec(m) is None]
    if not missing:
        return True
    say("Installing yt-dlp...")
    ok = pip_install("yt-dlp[default,curl-cffi]") or pip_install("yt-dlp[default]")
    importlib.invalidate_caches()
    return ok


def download_file(url: str, dest: Path, say, label: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(256 * 1024):
            f.write(chunk)
            done += len(chunk)
            if total:
                say(f"{label} {done * 100 // total}%")


def extract(archive: Path, target: Path) -> None:
    try:
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(target)
        else:
            with tarfile.open(archive) as t:
                try:
                    t.extractall(target, filter="data")
                except TypeError:
                    t.extractall(target)
    finally:
        archive.unlink(missing_ok=True)


def find_tool_dir(root: Path, name: str) -> Path | None:
    if root.exists():
        for p in root.rglob(name + EXE):
            if p.is_file():
                if os.name != "nt":
                    p.chmod(p.stat().st_mode | 0o111)
                return p.parent
    return None


def tool_works(directory: Path, name: str) -> bool:
    try:
        flag = "-version" if name == "ffmpeg" else "--version"
        return run_quiet([str(directory / (name + EXE)), flag], timeout=20).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def ensure_ffmpeg(say) -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return str(Path(found).parent)
    root = TOOLS / "ffmpeg"
    local = find_tool_dir(root, "ffmpeg")
    if local and tool_works(local, "ffmpeg"):
        return str(local)

    system, machine = platform.system(), platform.machine().lower()
    root.mkdir(parents=True, exist_ok=True)
    if system == "Windows":
        urls = [
            "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
            "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
        ]
        archive = root / "ffmpeg.zip"
    elif system == "Linux":
        arch = "arm64" if machine in ("aarch64", "arm64") else "amd64"
        urls = [f"https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-{arch}-static.tar.xz"]
        archive = root / "ffmpeg.tar.xz"
    elif system == "Darwin" and shutil.which("brew"):
        say("Running: brew install ffmpeg ...")
        run_quiet(["brew", "install", "ffmpeg"])
        found = shutil.which("ffmpeg")
        return str(Path(found).parent) if found else None
    else:
        return None

    for url in urls:
        try:
            download_file(url, archive, say, "Downloading ffmpeg")
            break
        except Exception:  # noqa: BLE001
            continue
    else:
        return None
    say("Extracting ffmpeg...")
    extract(archive, root)
    directory = find_tool_dir(root, "ffmpeg")
    return str(directory) if directory and tool_works(directory, "ffmpeg") else None


def ensure_aria2(say) -> str | None:
    found = shutil.which("aria2c")
    if found:
        return str(Path(found).parent)
    root = TOOLS / "aria2"
    local = find_tool_dir(root, "aria2c")
    if local and tool_works(local, "aria2c"):
        return str(local)
    if platform.system() != "Windows":
        return None  # Linux: apt install aria2 / macOS: brew install aria2
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "aria2.zip"
    try:
        download_file(ARIA2_WIN_URL, archive, say, "Downloading aria2c")
        say("Extracting aria2c...")
        extract(archive, root)
    except Exception:  # noqa: BLE001
        archive.unlink(missing_ok=True)
        return None
    directory = find_tool_dir(root, "aria2c")
    return str(directory) if directory and tool_works(directory, "aria2c") else None


def fmt_bytes(n: float | None) -> str:
    if not n:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return "?"


def extract_urls(text: str) -> list[str]:
    out = []
    for m in URL_RE.findall(text or ""):
        u = m.rstrip(".,;)")
        out.append("https://" + u if u.lower().startswith("www.") else u)
    return out


# --------------------------------------------------------------------------- #
# Core state + download worker
# --------------------------------------------------------------------------- #
class Core:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.items: list[dict] = []
        self.next_id = 1
        self.cfg = {"dir": str(DEFAULT_DIR), "container": "mkv", "aria": True, "conn": 16}
        self.ready = False
        self.setup_msg = "Setting up..."
        self.fatal: str | None = None
        self.tools = {"ytdlp": None, "ffmpeg": False, "aria2": False, "js": None}
        self.ffmpeg_dir: str | None = None
        self.aria2_dir: str | None = None
        self.cancel = threading.Event()
        self.running = False
        self.done = 0
        self.total = 0
        self.message = ""
        self.shutdown_cb = None

    # ---- setup ---------------------------------------------------------- #
    def say(self, text: str) -> None:
        with self.lock:
            self.setup_msg = text

    def setup(self) -> None:
        try:
            if not ensure_ytdlp(self.say):
                with self.lock:
                    self.fatal = "Could not install yt-dlp. Check your internet connection."
                return
            self.ffmpeg_dir = ensure_ffmpeg(self.say)
            self.aria2_dir = ensure_aria2(self.say)
            for d in (self.ffmpeg_dir, self.aria2_dir):
                if d:
                    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            import yt_dlp.version

            runtime = next((r for r in ("deno", "node", "bun", "qjs") if shutil.which(r)), None)
            with self.lock:
                self.tools = {
                    "ytdlp": yt_dlp.version.__version__,
                    "ffmpeg": bool(self.ffmpeg_dir),
                    "aria2": bool(self.aria2_dir),
                    "js": runtime,
                }
                if not self.aria2_dir:
                    self.cfg["aria"] = False
                self.ready = True
                self.setup_msg = "Ready"
        except Exception as e:  # noqa: BLE001
            with self.lock:
                self.fatal = f"Setup failed: {e}"

    # ---- state ---------------------------------------------------------- #
    def snapshot(self) -> dict:
        with self.lock:
            return {
                "ready": self.ready,
                "setup_msg": self.setup_msg,
                "fatal": self.fatal,
                "tools": self.tools,
                "cfg": self.cfg,
                "running": self.running,
                "done": self.done,
                "total": self.total,
                "message": self.message,
                "items": [dict(i) for i in self.items],
            }

    def _set(self, item: dict, **fields) -> None:
        with self.lock:
            item.update(fields)

    # ---- actions -------------------------------------------------------- #
    def add(self, text: str) -> dict:
        urls = extract_urls(text)
        added = 0
        with self.lock:
            known = {i["url"] for i in self.items}
            for u in urls:
                if u in known:
                    continue
                known.add(u)
                self.items.append({"id": self.next_id, "url": u, "title": "", "state": "queued",
                                   "text": "Queued", "pct": None})
                self.next_id += 1
                added += 1
        return {"added": added, "found": len(urls)}

    def remove(self, ids: list[int]) -> dict:
        with self.lock:
            self.items = [i for i in self.items if not (i["id"] in ids and i["state"] != "running")]
        return {}

    def clear_done(self) -> dict:
        with self.lock:
            self.items = [i for i in self.items if i["state"] != "done"]
        return {}

    def set_config(self, data: dict) -> dict:
        with self.lock:
            if isinstance(data.get("dir"), str) and data["dir"].strip():
                self.cfg["dir"] = data["dir"].strip()
            if data.get("container") in ("mkv", "mp4"):
                self.cfg["container"] = data["container"]
            if "aria" in data:
                self.cfg["aria"] = bool(data["aria"]) and bool(self.aria2_dir)
            if "conn" in data:
                try:
                    self.cfg["conn"] = max(1, min(16, int(data["conn"])))
                except (TypeError, ValueError):
                    pass
        return {}

    def open_folder(self) -> dict:
        path = Path(self.cfg["dir"]).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return {}

    def browse(self) -> dict:
        code = (
            "import tkinter as tk\nfrom tkinter import filedialog\n"
            "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
            f"print(filedialog.askdirectory(initialdir={self.cfg['dir']!r}) or '')"
        )
        try:
            out = run_quiet([sys.executable, "-c", code], timeout=300).stdout.strip()
        except Exception:  # noqa: BLE001
            return {"error": "Folder picker unavailable - type the path instead."}
        if out:
            with self.lock:
                self.cfg["dir"] = os.path.normpath(out)
        return {}

    def start(self) -> dict:
        with self.lock:
            if self.running:
                return {"error": "Already downloading."}
            if not self.ready:
                return {"error": "Still setting up..."}
            pending = [i for i in self.items if i["state"] in ("queued", "failed")]
            if not pending:
                return {"error": "Nothing to download - add some links first."}
            cfg = dict(self.cfg)
            cfg["aria"] = bool(cfg["aria"] and self.aria2_dir)
            for i in pending:
                i.update(state="waiting", text="Waiting", pct=None)
            self.running = True
            self.done, self.total, self.message = 0, len(pending), ""
            self.cancel.clear()
        threading.Thread(target=self._work, args=(pending, cfg), daemon=True).start()
        return {}

    def stop(self) -> dict:
        self.cancel.set()
        with self.lock:
            if self.running:
                self.message = "Stopping..."
        return {}

    def _work(self, pending: list[dict], cfg: dict) -> None:
        import yt_dlp
        from yt_dlp.utils import DownloadCancelled

        out_dir = Path(cfg["dir"]).expanduser()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            with self.lock:
                for i in pending:
                    i.update(state="failed", text=f"Cannot create folder: {e}")
                self.running = False
            return

        class Quiet:
            def debug(self, m): pass
            def info(self, m): pass
            def warning(self, m): pass
            def error(self, m): pass

        for item in pending:
            if self.cancel.is_set():
                break
            self._set(item, state="running", text="Starting...", pct=None)

            def hook(d: dict, item=item) -> None:
                if self.cancel.is_set():
                    raise DownloadCancelled()
                title = (d.get("info_dict") or {}).get("title")
                fields: dict = {}
                if title:
                    fields["title"] = title
                if d.get("status") == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate")
                    if total:
                        pct = (d.get("downloaded_bytes") or 0) * 100 / total
                        spd = d.get("speed")
                        fields.update(pct=round(pct, 1),
                                      text=f"{pct:.0f}%" + (f" · {fmt_bytes(spd)}/s" if spd else ""))
                    else:
                        fields.update(pct=None, text="Downloading...")
                elif d.get("status") == "finished":
                    fields.update(pct=100, text="Processing...")
                self._set(item, **fields)

            opts = {
                "format": "bestvideo*+bestaudio/best",
                "merge_output_format": cfg["container"],
                "outtmpl": str(out_dir / "%(title)s [%(id)s].%(ext)s"),
                "noplaylist": True,  # video+playlist links: only the video; pure playlist links still expand
                "retries": 10,
                "fragment_retries": 10,
                "concurrent_fragment_downloads": 4,
                "windowsfilenames": os.name == "nt",
                "logger": Quiet(),
                "noprogress": True,
                "progress_hooks": [hook],
            }
            if self.ffmpeg_dir:
                opts["ffmpeg_location"] = self.ffmpeg_dir
            if cfg["aria"]:
                n = cfg["conn"]
                opts["external_downloader"] = {"default": "aria2c"}
                opts["external_downloader_args"] = {
                    "aria2c": [f"-x{n}", f"-s{n}", "-k1M", "--file-allocation=none",
                               "--summary-interval=0", "--console-log-level=error"]
                }
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(item["url"], download=True)
                self._set(item, state="done", text="Done", pct=100,
                          title=(info or {}).get("title") or item["title"])
            except DownloadCancelled:
                self._set(item, state="queued", text="Cancelled", pct=None)
                break
            except Exception as e:  # noqa: BLE001
                msg = ANSI_RE.sub("", str(e)).replace("ERROR: ", "").strip().splitlines()
                self._set(item, state="failed", text=(msg[0] if msg else "Failed")[:200], pct=None)
            with self.lock:
                self.done += 1

        with self.lock:
            for i in pending:
                if i["state"] == "waiting":
                    i.update(state="queued", text="Queued", pct=None)
            failed = sum(1 for i in self.items if i["state"] == "failed")
            self.message = ("Stopped." if self.cancel.is_set() else "Finished.") + (
                f" {failed} failed - kept in the queue for retry." if failed else "")
            self.running = False


CORE = Core()


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "ytdl-web"

    def log_message(self, *args) -> None:  # silence request logging
        pass

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: dict, code: int = 200) -> None:
        self._send(code, json.dumps(data).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        path = urlparse(self.path).path
        if path == "/":
            return self._send(200, PAGE.replace("__TOKEN__", TOKEN).encode(), "text/html; charset=utf-8")
        if path == "/api/state":
            if self.headers.get("X-Token") != TOKEN:
                return self._json({"error": "bad token"}, 403)
            return self._json(CORE.snapshot())
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok() or self.headers.get("X-Token") != TOKEN:
            return self._json({"error": "forbidden"}, 403)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json({"error": "bad request"}, 400)
        action = urlparse(self.path).path.removeprefix("/api/")
        try:
            if action == "add":
                res = CORE.add(str(data.get("text", "")))
            elif action == "remove":
                res = CORE.remove([int(i) for i in data.get("ids", [])])
            elif action == "clear_done":
                res = CORE.clear_done()
            elif action == "config":
                res = CORE.set_config(data)
            elif action == "open_folder":
                res = CORE.open_folder()
            elif action == "browse":
                res = CORE.browse()
            elif action == "start":
                res = CORE.start()
            elif action == "stop":
                res = CORE.stop()
            elif action == "quit":
                self._json({})
                threading.Thread(target=CORE.shutdown_cb, daemon=True).start()
                return
            else:
                return self._json({"error": "unknown action"}, 404)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 500)
        self._json(res)


PAGE = r"""<!doctype html>
<html lang="en" class="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Batchy</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com/3.4.17"></script>
<script>
  tailwind.config = {
    theme: { extend: { fontFamily: {
      sans: ['Inter', 'ui-sans-serif', 'system-ui', 'Segoe UI', 'sans-serif'],
      mono: ['JetBrains Mono', 'ui-monospace', 'Consolas', 'monospace']
    } } }
  };
</script>
<script src="https://unpkg.com/lucide@0.460.0/dist/umd/lucide.min.js"></script>
<style>
  html { color-scheme: dark; font-size: 17px; -webkit-text-size-adjust: 100%; }
  @media (min-width: 1024px) { html { font-size: 18px; } }
  @keyframes slide { 0% { margin-left: -35%; } 100% { margin-left: 100%; } }
  .indet { width: 35% !important; animation: slide 1.1s ease-in-out infinite; }
  textarea { field-sizing: content; }
  ::selection { background: #6366f155; }
</style>
</head>
<body class="min-h-screen bg-zinc-950 font-sans text-zinc-200 antialiased">
<div class="fixed inset-x-0 top-0 z-20 h-1 bg-transparent">
  <div id="overall" class="h-full w-0 bg-indigo-400 transition-all duration-500"></div>
</div>

<main class="mx-auto w-full max-w-3xl px-4 pb-28 pt-8 sm:px-8 sm:pt-14">

  <!-- header -->
  <header class="flex items-center gap-3 sm:gap-4">
    <div class="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-zinc-900 ring-1 ring-zinc-800 sm:h-12 sm:w-12">
      <i data-lucide="arrow-down-to-line" class="h-5 w-5 text-indigo-400"></i>
    </div>
    <div class="min-w-0 flex-1">
      <h1 class="truncate text-xl font-semibold tracking-tight text-zinc-100 sm:text-2xl">Batchy</h1>
    </div>
    <button id="settingsBtn" title="Settings" class="rounded-xl p-2.5 text-zinc-500 transition hover:bg-zinc-900 hover:text-zinc-200">
      <i data-lucide="sliders-horizontal" class="h-5 w-5"></i>
    </button>
    <button id="quit" title="Quit" class="rounded-xl p-2.5 text-zinc-500 transition hover:bg-zinc-900 hover:text-red-400">
      <i data-lucide="power" class="h-5 w-5"></i>
    </button>
  </header>

  <div id="setup" class="mt-5 hidden items-center gap-2 text-sm text-amber-300/90">
    <span id="setupIcon"></span><span id="setupText" class="min-w-0 truncate"></span>
  </div>
  <div id="fatal" class="mt-5 hidden rounded-2xl border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-300"></div>

  <!-- input -->
  <section class="mt-7 sm:mt-9">
    <div class="rounded-3xl bg-zinc-900 p-2.5 ring-1 ring-zinc-800 transition focus-within:ring-zinc-600">
      <textarea id="links" rows="2" spellcheck="false"
        placeholder="Paste one or more links…"
        class="max-h-56 min-h-[72px] w-full resize-none bg-transparent px-3.5 py-3 font-mono text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none sm:text-base"></textarea>
      <div class="flex items-center justify-between gap-2 px-1 pb-1">
        <span class="hidden px-2 text-xs text-zinc-600 sm:inline">Ctrl + Enter to add</span>
        <div class="flex w-full gap-2 sm:w-auto">
          <button id="paste" class="flex flex-1 items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium text-zinc-400 transition hover:bg-zinc-800 hover:text-zinc-100 sm:flex-none">
            <i data-lucide="clipboard-paste" class="h-4 w-4"></i>Paste
          </button>
          <button id="add" class="flex flex-1 items-center justify-center gap-2 rounded-xl bg-zinc-100 px-5 py-2.5 text-sm font-semibold text-zinc-900 transition hover:bg-white sm:flex-none">
            <i data-lucide="plus" class="h-4 w-4"></i>Add
          </button>
        </div>
      </div>
    </div>
  </section>

  <!-- settings -->
  <section id="settings" class="mt-3 hidden rounded-3xl bg-zinc-900/60 p-4 ring-1 ring-zinc-800 sm:p-5">
    <div class="space-y-5">
      <div>
        <label class="mb-2 block text-xs font-medium uppercase tracking-wider text-zinc-500">Save to</label>
        <div class="flex gap-2">
          <input id="dir" type="text" spellcheck="false"
            class="min-w-0 flex-1 rounded-xl bg-zinc-950 px-3.5 py-2.5 font-mono text-sm text-zinc-200 ring-1 ring-zinc-800 focus:outline-none focus:ring-zinc-600">
          <button id="browse" title="Browse" class="rounded-xl bg-zinc-800 px-3.5 text-zinc-300 transition hover:bg-zinc-700"><i data-lucide="folder" class="h-5 w-5"></i></button>
          <button id="open" title="Open folder" class="rounded-xl bg-zinc-800 px-3.5 text-zinc-300 transition hover:bg-zinc-700"><i data-lucide="folder-open" class="h-5 w-5"></i></button>
        </div>
      </div>

      <div class="grid grid-cols-1 gap-5 sm:grid-cols-2 sm:gap-8">
        <div>
          <label class="mb-2 block text-xs font-medium uppercase tracking-wider text-zinc-500">Container</label>
          <div class="flex rounded-xl bg-zinc-950 p-1 ring-1 ring-zinc-800" id="seg">
            <button data-v="mkv" class="flex-1 rounded-lg px-4 py-2 font-mono text-sm transition">mkv</button>
            <button data-v="mp4" class="flex-1 rounded-lg px-4 py-2 font-mono text-sm transition">mp4</button>
          </div>
        </div>

        <div>
          <div class="mb-2 flex items-center justify-between">
            <label class="text-xs font-medium uppercase tracking-wider text-zinc-500">aria2c fast mode</label>
            <label class="relative inline-flex cursor-pointer items-center">
              <input id="aria" type="checkbox" class="peer sr-only">
              <div class="h-6 w-11 rounded-full bg-zinc-700 transition after:absolute after:left-0.5 after:top-0.5 after:h-5 after:w-5 after:rounded-full after:bg-white after:transition peer-checked:bg-indigo-500 peer-checked:after:translate-x-5 peer-disabled:opacity-40"></div>
            </label>
          </div>
          <div class="flex items-center gap-3 py-2">
            <input id="conn" type="range" min="1" max="16" class="h-1.5 flex-1 cursor-pointer accent-indigo-500 disabled:opacity-40">
            <span id="connv" class="w-10 text-right font-mono text-sm text-zinc-400">16×</span>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- queue -->
  <section class="mt-9 sm:mt-11">
    <div class="mb-3 flex items-center px-1">
      <h2 class="text-sm font-medium uppercase tracking-wider text-zinc-500">Queue</h2>
      <span id="count" class="ml-3 font-mono text-xs text-zinc-600"></span>
      <button id="clear" class="ml-auto flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-zinc-500 transition hover:bg-zinc-900 hover:text-zinc-200">
        <i data-lucide="trash-2" class="h-3.5 w-3.5"></i><span class="hidden sm:inline">Clear finished</span><span class="sm:hidden">Clear</span>
      </button>
    </div>

    <div id="list" class="divide-y divide-zinc-800/70 overflow-hidden rounded-3xl bg-zinc-900/50 ring-1 ring-zinc-800 empty:hidden"></div>

    <div id="empty" class="flex flex-col items-center gap-3 rounded-3xl border border-dashed border-zinc-800 py-14 text-zinc-600 sm:py-16">
      <i data-lucide="link" class="h-6 w-6"></i>
      <span class="text-base">Nothing queued</span>
    </div>
  </section>

  <!-- actions -->
  <section class="mt-7">
    <div class="flex flex-col gap-3 sm:flex-row sm:items-center">
      <div class="flex gap-3">
        <button id="start" disabled class="flex flex-1 items-center justify-center gap-2.5 rounded-2xl bg-indigo-500 px-6 py-3.5 text-base font-semibold text-white shadow-lg shadow-indigo-500/10 transition hover:bg-indigo-400 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-500 disabled:shadow-none sm:flex-none">
          <i data-lucide="download" class="h-5 w-5"></i>Download queue
        </button>
        <button id="stop" disabled class="flex items-center justify-center gap-2 rounded-2xl px-5 py-3.5 text-base font-medium text-zinc-300 ring-1 ring-zinc-800 transition hover:bg-zinc-900 disabled:cursor-not-allowed disabled:text-zinc-600">
          <i data-lucide="square" class="h-4 w-4"></i>Stop
        </button>
      </div>
      <span id="msg" class="min-w-0 text-sm text-zinc-500 sm:ml-2 sm:truncate"></span>
    </div>
  </section>
</main>

<div id="toast" class="pointer-events-none fixed bottom-6 left-1/2 z-30 w-max max-w-[90vw] -translate-x-1/2 translate-y-3 rounded-2xl bg-zinc-800 px-5 py-3 text-sm text-zinc-100 opacity-0 shadow-xl ring-1 ring-zinc-700 transition duration-200"></div>

<script>
const TOKEN = "__TOKEN__";
const $ = s => document.querySelector(s);
const ICON = {queued:"clock", waiting:"clock", running:"loader-circle", done:"check", failed:"circle-alert"};
const TONE = {queued:"text-zinc-500", waiting:"text-zinc-500", running:"text-indigo-400", done:"text-emerald-400", failed:"text-red-400"};

function icon(el, name, cls, size){ el.innerHTML = '<i data-lucide="'+name+'" class="'+(size||"h-5 w-5")+' '+(cls||"")+'"></i>'; lucide.createIcons(); }
function h(tag, cls, text){ const e=document.createElement(tag); if(cls) e.className=cls; if(text!==undefined) e.textContent=text; return e; }

let toastTimer;
function toast(t){
  const e=$("#toast"); e.textContent=t;
  e.classList.remove("opacity-0","translate-y-3"); clearTimeout(toastTimer);
  toastTimer=setTimeout(()=>e.classList.add("opacity-0","translate-y-3"),2600);
}
async function api(path, body){
  try{
    const r = await fetch(path,{method:body===undefined?"GET":"POST",
      headers:{"X-Token":TOKEN,"Content-Type":"application/json"},
      body:body===undefined?undefined:JSON.stringify(body)});
    return await r.json();
  }catch(e){ return {error:"Server not reachable (was it closed?)"}; }
}
async function act(path, body){ const r=await api("/api/"+path, body||{}); if(r.error) toast(r.error); return r; }

const els = new Map();
let cfg = null, last = null;

function makeItem(it){
  const root = h("div","group relative px-4 py-4 sm:px-5");
  const row = h("div","flex items-center gap-3 sm:gap-4");
  const ic = h("span","shrink-0");
  const mid = h("div","min-w-0 flex-1");
  const title = h("div","truncate text-base font-medium text-zinc-100");
  const meta = h("div","mt-0.5 flex min-w-0 items-center gap-2 text-xs");
  const status = h("span","shrink-0 font-mono");
  const url = h("span","min-w-0 truncate font-mono text-zinc-600");
  meta.append(status, url);
  mid.append(title, meta);
  const x = h("button","shrink-0 rounded-lg p-2 text-zinc-600 transition hover:text-red-400 disabled:hidden sm:opacity-0 sm:group-hover:opacity-100");
  x.title = "Remove"; icon(x,"x","","h-5 w-5");
  x.onclick = () => act("remove",{ids:[it.id]});
  row.append(ic, mid, x);
  const bar = h("div","mt-3 hidden h-1 overflow-hidden rounded-full bg-zinc-800");
  const fill = h("div","h-full w-0 rounded-full bg-indigo-400 transition-all duration-500");
  bar.append(fill);
  root.append(row, bar);
  return {root, ic, title, status, url, x, bar, fill, state:null};
}

function render(s){
  last = s;
  const fatal=$("#fatal"); fatal.classList.toggle("hidden",!s.fatal); fatal.textContent=s.fatal||"";
  const setup=$("#setup"); const settingUp=!s.ready&&!s.fatal;
  setup.classList.toggle("hidden",!settingUp); setup.classList.toggle("flex",settingUp);
  if(settingUp){ if(!$("#setupIcon").firstChild) icon($("#setupIcon"),"loader-circle","animate-spin","h-4 w-4"); $("#setupText").textContent=s.setup_msg; }

  if(!cfg){
    cfg={...s.cfg};
    $("#dir").value=cfg.dir; $("#aria").checked=cfg.aria; $("#conn").value=cfg.conn;
  }
  $("#aria").disabled = s.ready && !s.tools.aria2;
  if(s.ready && !s.tools.aria2){ $("#aria").checked=false; }
  $("#conn").disabled = !$("#aria").checked;
  $("#connv").textContent = $("#conn").value+"×";
  document.querySelectorAll("#seg button").forEach(b=>{
    const on=b.dataset.v===cfg.container;
    b.className="flex-1 rounded-lg px-4 py-2 font-mono text-sm transition "+(on?"bg-zinc-800 text-zinc-100":"text-zinc-500 hover:text-zinc-300");
  });

  const list=$("#list"), ids=new Set(s.items.map(i=>i.id));
  for(const [id,e] of els){ if(!ids.has(id)){ e.root.remove(); els.delete(id); } }
  s.items.forEach((it,idx)=>{
    let e=els.get(it.id);
    if(!e){ e=makeItem(it); els.set(it.id,e); }
    if(list.children[idx]!==e.root) list.insertBefore(e.root,list.children[idx]||null);
    const st=it.state;
    if(e.state!==st){
      e.state=st;
      icon(e.ic, ICON[st], st==="running"?"animate-spin":"");
      e.ic.className="shrink-0 "+TONE[st];
    }
    e.title.textContent = it.title || it.url;
    const statusText = st==="queued" ? "Queued" : it.text;
    e.status.className = "shrink-0 font-mono "+TONE[st];
    e.status.textContent = it.title ? statusText : statusText;
    e.status.title = it.text;
    e.status.classList.toggle("max-w-[60%]", true);
    e.status.classList.add("truncate");
    e.url.textContent = it.title ? "· "+it.url : "";
    e.x.disabled = st==="running";
    const showBar = st==="running";
    e.bar.classList.toggle("hidden", !showBar);
    const indet = showBar && it.pct===null;
    e.fill.classList.toggle("indet", indet);
    if(!indet) e.fill.style.width = (it.pct||0)+"%";
  });
  $("#empty").classList.toggle("hidden", s.items.length>0);
  const pending=s.items.filter(i=>i.state!=="done").length;
  $("#count").textContent = s.items.length ? s.items.length+" · "+pending+" pending" : "";

  $("#start").disabled = !s.ready || s.running || !!s.fatal;
  $("#stop").disabled = !s.running;
  $("#overall").style.width = (s.total ? Math.round(s.done*100/s.total) : 0)+"%";
  $("#msg").textContent = s.running ? "Downloading "+Math.min(s.done+1,s.total)+" of "+s.total+"…" : s.message;
}

async function poll(){
  const s=await api("/api/state");
  if(!s.error) render(s);
  else if(s.error.startsWith("Server not")) $("#msg").textContent=s.error;
  setTimeout(poll,500);
}

async function addLinks(){
  const r=await act("add",{text:$("#links").value});
  if(r.error) return;
  if(r.found===0) toast("No links found in that text");
  else { toast(r.added ? "Added "+r.added+" link"+(r.added>1?"s":"") : "Already in the queue"); $("#links").value=""; }
}
$("#add").onclick=addLinks;
$("#links").addEventListener("keydown",e=>{ if(e.key==="Enter"&&(e.ctrlKey||e.metaKey)) addLinks(); });
$("#paste").onclick=async()=>{
  try{ $("#links").value=await navigator.clipboard.readText(); addLinks(); }
  catch(e){ toast("Clipboard blocked - paste with Ctrl+V"); $("#links").focus(); }
};
$("#clear").onclick=()=>act("clear_done");
$("#start").onclick=()=>act("start");
$("#stop").onclick=()=>act("stop");
$("#open").onclick=()=>act("open_folder");
$("#settingsBtn").onclick=()=>$("#settings").classList.toggle("hidden");
$("#browse").onclick=async()=>{
  const r=await act("browse");
  if(!r.error){ const s=await api("/api/state"); if(cfg&&!s.error){ cfg.dir=s.cfg.dir; $("#dir").value=cfg.dir; } }
};
function pushCfg(){ if(cfg) act("config",cfg); }
$("#dir").addEventListener("change",()=>{ cfg.dir=$("#dir").value; pushCfg(); });
$("#aria").addEventListener("change",()=>{ cfg.aria=$("#aria").checked; pushCfg(); if(last) render(last); });
$("#conn").addEventListener("input",()=>{ $("#connv").textContent=$("#conn").value+"×"; });
$("#conn").addEventListener("change",()=>{ cfg.conn=+$("#conn").value; pushCfg(); });
document.querySelectorAll("#seg button").forEach(b=>b.onclick=()=>{ cfg.container=b.dataset.v; pushCfg(); if(last) render(last); });
$("#quit").onclick=async()=>{
  if(!confirm("Stop the downloader server?")) return;
  await api("/api/quit",{});
  document.body.innerHTML='<div class="grid h-screen place-items-center text-base text-zinc-500">Server stopped. You can close this tab.</div>';
};

lucide.createIcons();
poll();
</script>
</body>
</html>
"""


def main() -> None:
    server = None
    for port in list(range(8765, 8785)) + [0]:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if server is None:
        sys.exit("Could not open a local port.")
    CORE.shutdown_cb = server.shutdown
    url = f"http://127.0.0.1:{server.server_address[1]}/"

    threading.Thread(target=CORE.setup, daemon=True).start()
    print(f"Batchy running at {url}")
    print("Close this window or press Ctrl+C to stop.")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        CORE.cancel.set()
        server.server_close()
        print("Bye!")


if __name__ == "__main__":
    main()
