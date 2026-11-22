"""yt-dlp downloader with a Tk GUI, aria2c accelerated downloads and auto-installed tools."""
import importlib
import importlib.util
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import tkinter as tk
import urllib.request
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_HOME = Path.home() / ".ytdl_menu"
TOOLS = APP_HOME / "tools"
DEFAULT_DIR = Path.home() / "Downloads" / "yt-dlp"
EXE = ".exe" if os.name == "nt" else ""
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no flashing consoles under pythonw

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

ARIA2_WIN_URL = (
    "https://github.com/aria2/aria2/releases/download/release-1.37.0/"
    "aria2-1.37.0-win-64bit-build1.zip"
)

BG, PANEL, FG, MUTED, ACCENT = "#14161a", "#1c1f26", "#e6e6e6", "#8a8f98", "#4f8cff"


# --------------------------------------------------------------------------- #
# Setup helpers (run in a background thread; `say` updates the status bar)
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
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return "?"


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("yt-dlp Downloader")
        self.geometry("920x580")
        self.minsize(740, 440)
        self.configure(bg=BG)

        self.events: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.ready = False
        self.ffmpeg_dir: str | None = None
        self.aria2_dir: str | None = None
        self.items: dict[str, dict] = {}
        self.counter = 0

        self.dir_var = tk.StringVar(value=str(DEFAULT_DIR))
        self.container_var = tk.StringVar(value="mkv")
        self.aria_var = tk.BooleanVar(value=True)
        self.conn_var = tk.IntVar(value=16)
        self.url_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Setting up...")

        self._style()
        self._build()
        self.after(100, self._pump)
        threading.Thread(target=self._setup, daemon=True).start()

    # ---- layout ---------------------------------------------------------- #
    def _style(self) -> None:
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, font=("Segoe UI", 10))
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Muted.TLabel", foreground=MUTED)
        s.configure("TButton", background=PANEL, foreground=FG, padding=(12, 6), borderwidth=0)
        s.map("TButton", background=[("active", "#272b35"), ("disabled", BG)],
              foreground=[("disabled", MUTED)])
        s.configure("Accent.TButton", background=ACCENT, foreground="white", font=("Segoe UI", 10, "bold"))
        s.map("Accent.TButton", background=[("active", "#6aa0ff"), ("disabled", "#2b3a5c")])
        s.configure("TEntry", fieldbackground=PANEL, foreground=FG, insertcolor=FG, borderwidth=0, padding=6)
        s.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=FG, arrowcolor=FG)
        s.configure("TCheckbutton", background=BG, foreground=FG)
        s.configure("TSpinbox", fieldbackground=PANEL, foreground=FG, arrowcolor=FG)
        s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                    rowheight=28, borderwidth=0)
        s.configure("Treeview.Heading", background=BG, foreground=MUTED, borderwidth=0,
                    font=("Segoe UI", 9, "bold"))
        s.map("Treeview", background=[("selected", "#2a3550")])
        s.configure("Horizontal.TProgressbar", troughcolor=PANEL, background=ACCENT, borderwidth=0)

    def _build(self) -> None:
        pad = {"padx": 14}

        top = ttk.Frame(self)
        top.pack(fill="x", pady=(14, 8), **pad)
        entry = ttk.Entry(top, textvariable=self.url_var)
        entry.pack(side="left", fill="x", expand=True, ipady=3)
        entry.bind("<Return>", lambda _e: self.add_from_entry())
        entry.focus_set()
        ttk.Button(top, text="Add", command=self.add_from_entry).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Paste", command=self.paste_clipboard).pack(side="left", padx=(8, 0))

        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, **pad)
        cols = ("n", "title", "status")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("n", text="#")
        self.tree.heading("title", text="Title / Link")
        self.tree.heading("status", text="Status")
        self.tree.column("n", width=40, anchor="e", stretch=False)
        self.tree.column("title", width=520)
        self.tree.column("status", width=220, stretch=False)
        self.tree.tag_configure("done", foreground="#5fd08a")
        self.tree.tag_configure("fail", foreground="#ff6b6b")
        self.tree.tag_configure("run", foreground="#7fb0ff")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Delete>", lambda _e: self.remove_selected())

        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=(10, 4), **pad)
        ttk.Label(opts, text="Save to", style="Muted.TLabel").pack(side="left")
        ttk.Entry(opts, textvariable=self.dir_var).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(opts, text="Browse", command=self.browse).pack(side="left")
        ttk.Button(opts, text="Open", command=self.open_folder).pack(side="left", padx=(6, 0))

        opts2 = ttk.Frame(self)
        opts2.pack(fill="x", pady=4, **pad)
        ttk.Label(opts2, text="Container", style="Muted.TLabel").pack(side="left")
        ttk.Combobox(opts2, textvariable=self.container_var, values=("mkv", "mp4"),
                     width=5, state="readonly").pack(side="left", padx=(8, 18))
        self.aria_chk = ttk.Checkbutton(opts2, text="aria2c fast downloads", variable=self.aria_var)
        self.aria_chk.pack(side="left")
        ttk.Label(opts2, text="connections", style="Muted.TLabel").pack(side="left", padx=(12, 6))
        ttk.Spinbox(opts2, from_=1, to=16, textvariable=self.conn_var, width=4).pack(side="left")

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=(8, 6), **pad)
        self.dl_btn = ttk.Button(btns, text="Download queue", style="Accent.TButton",
                                 command=self.start, state="disabled")
        self.dl_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="Remove selected", command=self.remove_selected).pack(side="left")
        ttk.Button(btns, text="Clear finished", command=self.clear_done).pack(side="left", padx=6)

        self.bar = ttk.Progressbar(self, mode="determinate")
        self.bar.pack(fill="x", pady=(4, 0), **pad)
        ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel").pack(
            anchor="w", padx=14, pady=(4, 10))

    # ---- queue actions ---------------------------------------------------- #
    def add_urls(self, urls: list[str]) -> None:
        known = {it["url"] for it in self.items.values()}
        for url in urls:
            if url in known:
                continue
            self.counter += 1
            iid = str(self.counter)
            self.items[iid] = {"url": url, "state": "queued"}
            self.tree.insert("", "end", iid=iid, values=(len(self.items), url, "Queued"))
            known.add(url)
        self._renumber()

    @staticmethod
    def _extract(text: str) -> list[str]:
        out = []
        for m in URL_RE.findall(text):
            u = m.rstrip(".,;)")
            out.append("https://" + u if u.lower().startswith("www.") else u)
        return out

    def add_from_entry(self) -> None:
        urls = self._extract(self.url_var.get())
        if urls:
            self.add_urls(urls)
            self.url_var.set("")
        else:
            self.status_var.set("No link found in the box.")

    def paste_clipboard(self) -> None:
        try:
            urls = self._extract(self.clipboard_get())
        except tk.TclError:
            urls = []
        if urls:
            self.add_urls(urls)
            self.status_var.set(f"Added {len(urls)} link(s) from clipboard.")
        else:
            self.status_var.set("No links in the clipboard.")

    def _renumber(self) -> None:
        for n, iid in enumerate(self.tree.get_children(), 1):
            self.tree.set(iid, "n", n)

    def remove_selected(self) -> None:
        for iid in self.tree.selection():
            if self.items.get(iid, {}).get("state") == "running":
                continue
            self.items.pop(iid, None)
            self.tree.delete(iid)
        self._renumber()

    def clear_done(self) -> None:
        for iid in list(self.items):
            if self.items[iid]["state"] == "done":
                self.items.pop(iid)
                self.tree.delete(iid)
        self._renumber()

    def browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.dir_var.get() or str(Path.home()))
        if d:
            self.dir_var.set(d)

    def open_folder(self) -> None:
        path = Path(self.dir_var.get())
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    # ---- setup thread ----------------------------------------------------- #
    def _setup(self) -> None:
        say = lambda t: self.events.put(("say", t))  # noqa: E731
        if not ensure_ytdlp(say):
            self.events.put(("fatal", "Could not install yt-dlp. Check your internet connection."))
            return
        self.ffmpeg_dir = ensure_ffmpeg(say)
        self.aria2_dir = ensure_aria2(say)
        for d in (self.ffmpeg_dir, self.aria2_dir):
            if d:
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        import yt_dlp.version

        runtime = next((r for r in ("deno", "node", "bun", "qjs") if shutil.which(r)), None)
        parts = [f"yt-dlp {yt_dlp.version.__version__}",
                 "ffmpeg ✓" if self.ffmpeg_dir else "ffmpeg MISSING",
                 "aria2c ✓" if self.aria2_dir else "aria2c missing (using built-in downloader)",
                 f"{runtime} ✓" if runtime else "no JS runtime (install Deno for YouTube)"]
        self.events.put(("ready", "  ·  ".join(parts)))

    # ---- download thread -------------------------------------------------- #
    def start(self) -> None:
        pending = [i for i in self.tree.get_children()
                   if self.items[i]["state"] in ("queued", "failed")]
        if not pending:
            self.status_var.set("Nothing to download - add some links first.")
            return
        cfg = {
            "dir": Path(self.dir_var.get()).expanduser(),
            "container": self.container_var.get(),
            "aria": bool(self.aria_var.get() and self.aria2_dir),
            "conn": max(1, min(16, int(self.conn_var.get() or 16))),
        }
        self.cancel.clear()
        self.dl_btn.state(["disabled"])
        self.stop_btn.state(["!disabled"])
        self.bar.configure(maximum=len(pending), value=0)
        self.worker = threading.Thread(target=self._work, args=(pending, cfg), daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.cancel.set()
        self.status_var.set("Stopping after the current step...")

    def _work(self, pending: list[str], cfg: dict) -> None:
        import yt_dlp
        from yt_dlp.utils import DownloadCancelled

        put = self.events.put
        cfg["dir"].mkdir(parents=True, exist_ok=True)

        class Quiet:
            def debug(self, m): pass
            def info(self, m): pass
            def warning(self, m): pass
            def error(self, m): pass

        done = 0
        for iid in pending:
            if self.cancel.is_set():
                break
            url = self.items[iid]["url"]
            put(("item", iid, "running", "Starting...", None))

            def hook(d: dict, iid=iid) -> None:
                if self.cancel.is_set():
                    raise DownloadCancelled()
                title = (d.get("info_dict") or {}).get("title")
                if d.get("status") == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate")
                    if total:
                        pct = (d.get("downloaded_bytes") or 0) * 100 / total
                        spd = d.get("speed")
                        text = f"{pct:.0f}%  {fmt_bytes(spd)}/s" if spd else f"{pct:.0f}%"
                    else:
                        text = "Downloading..."
                    put(("item", iid, "running", text, title))
                elif d.get("status") == "finished":
                    put(("item", iid, "running", "Processing...", title))

            opts = {
                "format": "bestvideo*+bestaudio/best",
                "merge_output_format": cfg["container"],
                "outtmpl": str(cfg["dir"] / "%(title)s [%(id)s].%(ext)s"),
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
                    info = ydl.extract_info(url, download=True)
                put(("item", iid, "done", "✓ Done", (info or {}).get("title")))
            except DownloadCancelled:
                put(("item", iid, "queued", "Cancelled", None))
                break
            except Exception as e:  # noqa: BLE001
                msg = ANSI_RE.sub("", str(e)).replace("ERROR: ", "").splitlines()[0][:120]
                put(("item", iid, "failed", f"✗ {msg}", None))
            done += 1
            put(("overall", done))
        put(("finished",))

    # ---- event pump (main thread) ---------------------------------------- #
    def _pump(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "say":
                    self.status_var.set(ev[1])
                elif kind == "ready":
                    self.ready = True
                    self.status_var.set(ev[1])
                    self.dl_btn.state(["!disabled"])
                    if not self.aria2_dir:
                        self.aria_var.set(False)
                        self.aria_chk.state(["disabled"])
                elif kind == "fatal":
                    messagebox.showerror("Setup failed", ev[1])
                    self.status_var.set(ev[1])
                elif kind == "item":
                    _, iid, state, text, title = ev
                    if iid in self.items:
                        self.items[iid]["state"] = state
                        self.tree.set(iid, "status", text)
                        if title:
                            self.tree.set(iid, "title", title)
                        tag = {"done": "done", "failed": "fail", "running": "run"}.get(state, "")
                        self.tree.item(iid, tags=(tag,) if tag else ())
                elif kind == "overall":
                    self.bar.configure(value=ev[1])
                elif kind == "finished":
                    self.dl_btn.state(["!disabled"])
                    self.stop_btn.state(["disabled"])
                    failed = sum(1 for i in self.items.values() if i["state"] == "failed")
                    self.status_var.set(
                        f"Finished. Saved to {self.dir_var.get()}"
                        + (f"  ·  {failed} failed (kept in queue for retry)" if failed else ""))
        except queue.Empty:
            pass
        self.after(100, self._pump)


if __name__ == "__main__":
    App().mainloop()
