"""Haitham Downloader — aria2 RPC core. MIT licensed original glue code."""
from __future__ import annotations
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import URLError

ROOT = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "HaithamDownloader"
DOWNLOADS = Path.home() / "Downloads"

def find_aria2():
    choices = [
        shutil.which("aria2c"),
        str(Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "aria2c.exe"),
        str(Path(sys.executable).parent / "aria2c.exe"),
        str(Path(__file__).resolve().parent / "aria2c.exe"),
    ]
    for candidate in choices:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    base = Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if base.exists():
        try:
            return str(next(base.glob("aria2.aria2_*/*/aria2c.exe")))
        except StopIteration:
            pass
    raise FileNotFoundError("لم أجد aria2c. تأكد من تثبيت aria2 أولًا.")

class AriaClient:
    def __init__(self, root=ROOT, downloads=DOWNLOADS):
        self.root = Path(root)
        self.downloads = Path(downloads)
        self.root.mkdir(parents=True, exist_ok=True)
        self.downloads.mkdir(parents=True, exist_ok=True)
        self.secret = secrets.token_hex(24)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.proc = None

    def start(self):
        aria2 = find_aria2()
        session = self.root / "session.txt"
        args = [
            aria2, "--enable-rpc=true", "--rpc-listen-all=false",
            f"--rpc-listen-port={self.port}", f"--rpc-secret={self.secret}",
            "--max-concurrent-downloads=4", "--max-connection-per-server=8",
            "--split=8", "--min-split-size=1M", "--continue=true",
            "--file-allocation=none", "--auto-file-renaming=true",
            "--summary-interval=0", "--enable-color=false",
            f"--dir={self.downloads}", f"--save-session={session}",
            "--save-session-interval=30",
        ]
        if session.exists() and session.stat().st_size:
            args.append(f"--input-file={session}")
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, **kw)
        for _ in range(70):
            if self.proc.poll() is not None:
                raise RuntimeError(f"aria2 exited early: {self.proc.returncode}")
            try:
                self.rpc("aria2.getVersion", timeout=1)
                return
            except (URLError, OSError, ValueError):
                time.sleep(.12)
        raise RuntimeError("تعذر الاتصال بمحرك aria2")

    def rpc(self, method, params=None, timeout=4):
        req = Request(
            f"http://127.0.0.1:{self.port}/jsonrpc",
            data=json.dumps({"jsonrpc": "2.0", "id": "haitham",
                 "method": method, "params": [f"token:{self.secret}"] + list(params or [])}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=timeout) as r:
            result = json.load(r)
        if "error" in result:
            raise RuntimeError(result["error"].get("message", str(result["error"])))
        return result["result"]

    def add(self, url, directory=None, filename=None):
        opts = {"dir": str(Path(directory or self.downloads))}
        if filename:
            opts["out"] = filename
        return self.rpc("aria2.addUri", [[url], opts])

    def list_all(self):
        keys = ["gid","status","totalLength","completedLength","downloadSpeed",
                "files","errorMessage","bittorrent"]
        active = self.rpc("aria2.tellActive", [keys])
        waiting = self.rpc("aria2.tellWaiting", [0, 100, keys])
        stopped = self.rpc("aria2.tellStopped", [0, 100, keys])
        return active + waiting + stopped

    def pause(self, gid):
        return self.rpc("aria2.pause", [gid])

    def resume(self, gid):
        return self.rpc("aria2.unpause", [gid])

    def remove(self, gid):
        return self.rpc("aria2.remove", [gid])

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.rpc("aria2.saveSession")
                self.rpc("aria2.shutdown")
            except Exception:
                pass
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
