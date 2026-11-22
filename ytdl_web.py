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
                "noplaylist": False,
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
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>yt-dlp Downloader</title>
<style>
  :root{
    --bg:#0b0d12; --panel:#141821; --panel2:#1a1f2b; --line:#262c3a;
    --fg:#e8ebf2; --muted:#8b93a7; --accent:#6c8cff; --accent2:#9b6cff;
    --ok:#3ddc97; --bad:#ff6b7a; --warn:#ffc15e;
  }
  *{box-sizing:border-box}
  html,body{margin:0;background:var(--bg);color:var(--fg);
    font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
  body{background:radial-gradient(1200px 500px at 70% -10%,#1b2140 0%,transparent 60%),var(--bg);min-height:100vh}
  .wrap{max-width:900px;margin:0 auto;padding:28px 20px 60px}
  header{display:flex;align-items:center;gap:14px;margin-bottom:22px}
  .logo{width:40px;height:40px;border-radius:12px;display:grid;place-items:center;font-size:20px;
    background:linear-gradient(135deg,var(--accent),var(--accent2));box-shadow:0 6px 24px #6c8cff44}
  h1{font-size:20px;margin:0;font-weight:650;letter-spacing:.2px}
  .sub{color:var(--muted);font-size:13px}
  .spacer{flex:1}
  .chips{display:flex;gap:6px;flex-wrap:wrap}
  .chip{font-size:12px;padding:4px 10px;border-radius:99px;background:var(--panel);border:1px solid var(--line);color:var(--muted)}
  .chip.ok{color:var(--ok);border-color:#2a5a47}
  .chip.warn{color:var(--warn);border-color:#5a4a2a}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:16px}
  textarea,input[type=text],select{width:100%;background:var(--panel2);color:var(--fg);border:1px solid var(--line);
    border-radius:10px;padding:11px 13px;font:inherit;outline:none;transition:border-color .15s}
  textarea:focus,input:focus,select:focus{border-color:var(--accent)}
  textarea{resize:vertical;min-height:84px}
  .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  .row.grow>*:first-child{flex:1}
  button{font:inherit;color:var(--fg);background:var(--panel2);border:1px solid var(--line);border-radius:10px;
    padding:10px 16px;cursor:pointer;transition:transform .05s,background .15s,border-color .15s}
  button:hover:not(:disabled){background:#222838;border-color:#343c50}
  button:active:not(:disabled){transform:translateY(1px)}
  button:disabled{opacity:.45;cursor:not-allowed}
  button.primary{background:linear-gradient(135deg,var(--accent),var(--accent2));border:0;color:#fff;font-weight:600}
  button.primary:hover:not(:disabled){filter:brightness(1.1);background:linear-gradient(135deg,var(--accent),var(--accent2))}
  button.ghost{background:transparent}
  .label{font-size:12px;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:.6px}
  .settings{display:grid;grid-template-columns:1fr 1fr;gap:14px 18px}
  .settings .full{grid-column:1/-1}
  @media(max-width:640px){.settings{grid-template-columns:1fr}}
  .toggle{display:flex;align-items:center;gap:10px;cursor:pointer;user-select:none}
  .toggle input{width:18px;height:18px;accent-color:var(--accent)}
  input[type=range]{width:100%;accent-color:var(--accent)}
  .qhead{display:flex;align-items:center;gap:10px;margin:22px 2px 10px}
  .qhead h2{margin:0;font-size:15px;font-weight:600}
  .count{color:var(--muted);font-size:13px}
  .item{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:13px 14px;margin-bottom:10px}
  .item .top{display:flex;gap:10px;align-items:center}
  .item .title{flex:1;min-width:0;font-weight:550;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .item .url{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:2px}
  .badge{font-size:12px;padding:3px 10px;border-radius:99px;background:var(--panel2);color:var(--muted);white-space:nowrap}
  .item.running .badge{color:#9db4ff;background:#1d2646}
  .item.done .badge{color:var(--ok);background:#14301f}
  .item.failed .badge{color:var(--bad);background:#35161c;max-width:55%;overflow:hidden;text-overflow:ellipsis}
  .x{padding:2px 9px;line-height:1.2;font-size:16px;background:transparent;border:0;color:var(--muted)}
  .x:hover:not(:disabled){color:var(--bad);background:transparent!important}
  .bar{height:6px;border-radius:99px;background:var(--panel2);margin-top:11px;overflow:hidden;display:none}
  .item.running .bar,.item.done .bar{display:block}
  .fill{height:100%;width:0;border-radius:99px;background:linear-gradient(90deg,var(--accent),var(--accent2));transition:width .4s ease}
  .item.done .fill{background:var(--ok)}
  .fill.indet{width:40%!important;animation:slide 1.2s ease-in-out infinite}
  @keyframes slide{0%{margin-left:-40%}100%{margin-left:100%}}
  .empty{text-align:center;color:var(--muted);padding:34px 10px;border:1px dashed var(--line);border-radius:14px}
  .overall{height:8px;border-radius:99px;background:var(--panel2);overflow:hidden;margin-top:14px}
  .overall .fill{background:linear-gradient(90deg,var(--accent2),var(--accent))}
  .msg{color:var(--muted);font-size:13px;margin-top:10px;min-height:18px}
  .toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%) translateY(20px);opacity:0;
    background:#222838;border:1px solid var(--line);padding:10px 18px;border-radius:12px;transition:.25s;pointer-events:none}
  .toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
  .banner{background:#2a1a1f;border:1px solid #5a2a35;color:#ffb3bd;padding:12px 16px;border-radius:12px;margin-bottom:16px;display:none}
  .setup{color:var(--warn);font-size:13px;margin-bottom:14px;display:none}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">⬇</div>
    <div>
      <h1>yt-dlp Downloader</h1>
      <div class="sub">Best video + best audio, accelerated with aria2c</div>
    </div>
    <div class="spacer"></div>
    <button class="ghost" id="quit" title="Stop the local server">Quit</button>
  </header>

  <div class="banner" id="fatal"></div>
  <div class="setup" id="setup"></div>
  <div class="chips" id="chips" style="margin-bottom:16px"></div>

  <div class="card">
    <div class="label">Paste links (one or many)</div>
    <textarea id="links" placeholder="https://www.youtube.com/watch?v=...&#10;https://..."></textarea>
    <div class="row" style="margin-top:10px">
      <button class="primary" id="add">Add to queue</button>
      <button id="paste">Paste from clipboard</button>
      <span class="sub">Ctrl+Enter to add</span>
    </div>
  </div>

  <div class="card">
    <div class="settings">
      <div class="full">
        <div class="label">Save to</div>
        <div class="row grow">
          <input type="text" id="dir" spellcheck="false">
          <button id="browse">Browse</button>
          <button id="open">Open folder</button>
        </div>
      </div>
      <div>
        <div class="label">Container</div>
        <select id="container"><option value="mkv">mkv (keeps best streams)</option><option value="mp4">mp4 (more compatible)</option></select>
      </div>
      <div>
        <div class="label">aria2c</div>
        <label class="toggle"><input type="checkbox" id="aria"><span>Fast downloads</span></label>
        <div class="row" style="margin-top:8px"><input type="range" id="conn" min="1" max="16"><span id="connv" class="sub" style="min-width:90px">16 connections</span></div>
      </div>
    </div>
  </div>

  <div class="qhead">
    <h2>Queue</h2><span class="count" id="count"></span>
    <div class="spacer"></div>
    <button class="ghost" id="clear">Clear finished</button>
  </div>
  <div id="list"></div>
  <div class="empty" id="empty">No links yet. Paste some above.</div>

  <div class="row" style="margin-top:18px">
    <button class="primary" id="start" disabled>Download queue</button>
    <button id="stop" disabled>Stop</button>
  </div>
  <div class="overall"><div class="fill" id="overall"></div></div>
  <div class="msg" id="msg"></div>
</div>
<div class="toast" id="toast"></div>

<script>
const TOKEN = "__TOKEN__";
const $ = s => document.querySelector(s);
let toastTimer;
function toast(t){const e=$("#toast");e.textContent=t;e.classList.add("show");clearTimeout(toastTimer);toastTimer=setTimeout(()=>e.classList.remove("show"),2600);}
async function api(path, body){
  try{
    const r = await fetch(path,{method:body===undefined?"GET":"POST",
      headers:{"X-Token":TOKEN,"Content-Type":"application/json"},
      body:body===undefined?undefined:JSON.stringify(body)});
    return await r.json();
  }catch(e){return {error:"Server not reachable (was it closed?)"};}
}
async function act(path, body){const r=await api("/api/"+path, body||{}); if(r.error) toast(r.error); return r;}
function h(tag, cls, text){const e=document.createElement(tag); if(cls)e.className=cls; if(text!==undefined)e.textContent=text; return e;}

const els = new Map();
let cfgLoaded = false, last = null;

function makeItem(it){
  const root=h("div","item");
  const top=h("div","top"), title=h("div","title"), badge=h("span","badge"), x=h("button","x","×");
  x.title="Remove"; x.onclick=()=>act("remove",{ids:[it.id]});
  top.append(title,badge,x);
  const url=h("div","url",it.url);
  const bar=h("div","bar"), fill=h("div","fill"); bar.append(fill);
  root.append(top,url,bar);
  return {root,title,badge,x,fill};
}

function render(s){
  last = s;
  $("#fatal").style.display = s.fatal ? "block" : "none";
  $("#fatal").textContent = s.fatal || "";
  $("#setup").style.display = (!s.ready && !s.fatal) ? "block" : "none";
  $("#setup").textContent = s.setup_msg;

  const chips=$("#chips"); chips.textContent="";
  if(s.ready){
    const t=s.tools;
    [["yt-dlp "+t.ytdlp,true],["ffmpeg",t.ffmpeg],["aria2c",t.aria2],[t.js?t.js:"no JS runtime",!!t.js]].forEach(([n,ok])=>{
      const c=h("span","chip "+(ok?"ok":"warn"),(ok?"✓ ":"! ")+n);
      if(n==="no JS runtime") c.title="Install Deno for YouTube support";
      if(n==="aria2c"&&!ok) c.title="Not found - using the built-in downloader";
      chips.append(c);
    });
  }

  if(!cfgLoaded){
    $("#dir").value=s.cfg.dir; $("#container").value=s.cfg.container;
    $("#aria").checked=s.cfg.aria; $("#conn").value=s.cfg.conn;
    cfgLoaded=true;
  }
  $("#aria").disabled=!s.tools.aria2 && s.ready;
  if(s.ready && !s.tools.aria2) $("#aria").checked=false;
  $("#connv").textContent=$("#conn").value+" connections";
  $("#conn").disabled=!$("#aria").checked;

  const list=$("#list"), ids=new Set(s.items.map(i=>i.id));
  for(const [id,e] of els){ if(!ids.has(id)){e.root.remove(); els.delete(id);} }
  s.items.forEach((it,idx)=>{
    let e=els.get(it.id);
    if(!e){e=makeItem(it); els.set(it.id,e);}
    if(list.children[idx]!==e.root) list.insertBefore(e.root,list.children[idx]||null);
    e.root.className="item "+it.state;
    e.title.textContent=it.title||it.url;
    e.badge.textContent=it.text; e.badge.title=it.text;
    e.x.disabled=it.state==="running";
    const indet = it.state==="running" && it.pct===null;
    e.fill.classList.toggle("indet",indet);
    if(!indet) e.fill.style.width=(it.pct||0)+"%";
  });
  $("#empty").style.display=s.items.length?"none":"block";
  const q=s.items.filter(i=>i.state!=="done").length;
  $("#count").textContent=s.items.length?`${s.items.length} link${s.items.length>1?"s":""} · ${q} pending`:"";

  $("#start").disabled=!s.ready||s.running||!!s.fatal;
  $("#stop").disabled=!s.running;
  $("#overall").style.width=(s.total?Math.round(s.done*100/s.total):0)+"%";
  $("#msg").textContent=s.running?`Downloading ${Math.min(s.done+1,s.total)} of ${s.total}...`:s.message;
}

async function poll(){
  const s=await api("/api/state");
  if(!s.error) render(s);
  else if(s.error.startsWith("Server not")) {$("#msg").textContent=s.error;}
  setTimeout(poll,500);
}

async function addLinks(){
  const r=await act("add",{text:$("#links").value});
  if(r.error) return;
  if(r.found===0) toast("No links found in that text");
  else {toast(r.added?`Added ${r.added} link${r.added>1?"s":""}`:"Already in the queue"); $("#links").value="";}
}
$("#add").onclick=addLinks;
$("#links").addEventListener("keydown",e=>{if(e.key==="Enter"&&(e.ctrlKey||e.metaKey)) addLinks();});
$("#paste").onclick=async()=>{
  try{ const t=await navigator.clipboard.readText(); $("#links").value=t; addLinks(); }
  catch(e){ toast("Clipboard blocked - paste into the box with Ctrl+V"); $("#links").focus(); }
};
$("#clear").onclick=()=>act("clear_done");
$("#start").onclick=()=>act("start");
$("#stop").onclick=()=>act("stop");
$("#open").onclick=()=>act("open_folder");
$("#browse").onclick=async()=>{const r=await act("browse"); if(!r.error){cfgLoaded=false; const s=await api("/api/state"); render(s);}};
const pushCfg=()=>act("config",{dir:$("#dir").value,container:$("#container").value,aria:$("#aria").checked,conn:+$("#conn").value});
["#dir","#container"].forEach(s=>$(s).addEventListener("change",pushCfg));
$("#aria").addEventListener("change",()=>{pushCfg();if(last)render(last);});
$("#conn").addEventListener("input",()=>{$("#connv").textContent=$("#conn").value+" connections";});
$("#conn").addEventListener("change",pushCfg);
$("#quit").onclick=async()=>{
  if(!confirm("Stop the downloader server?")) return;
  await api("/api/quit",{});
  document.body.innerHTML='<div style="display:grid;place-items:center;height:100vh;color:#8b93a7;font:16px system-ui">Server stopped. You can close this tab.</div>';
};
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
    print(f"yt-dlp Downloader running at {url}")
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
