"""Keep native HTTP Range downloads available when aria2 is not installed."""
from __future__ import annotations

import hashlib
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app import Service
from engine import AriaClient


DATA=hashlib.shake_256(b"native-range-no-aria2").digest(3*1024*1024)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith("/plain"):
            self.send_response(200)
            self.send_header("Content-Length",str(len(DATA)))
            self.end_headers()
            self.wfile.write(DATA)
            return
        raw=self.headers.get("Range","")
        if not raw.startswith("bytes="):
            return self.send_error(416)
        begin,end=map(int,raw[6:].split("-"))
        self.send_response(206)
        self.send_header("Content-Range",f"bytes {begin}-{end}/{len(DATA)}")
        self.send_header("Content-Length",str(end-begin+1))
        self.end_headers()
        try:self.wfile.write(DATA[begin:end+1])
        except (BrokenPipeError,ConnectionResetError):pass

    def log_message(self,*_):pass


server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
try:
    with tempfile.TemporaryDirectory(prefix="haitham_without_aria2_") as root:
        folder=Path(root)
        with patch.object(AriaClient,"start",side_effect=FileNotFoundError("aria2c is not installed")):
            app=Service(data_dir=folder/"appdata")
        try:
            state=app.snapshot()
            assert "aria2c" in state["error"]
            assert state["aria2_available"] is False
            url=f"http://127.0.0.1:{server.server_port}/file"
            # A user may choose the ordinary mode, yet the native engine
            # should still take the file when aria2 is absent.
            added=app.add(url,folder,"native.bin",mode="aria2")
            assert added["mode"]=="connections",added
            gid=added["gid"]
            deadline=time.monotonic()+12
            while app.range_engine.get(gid)["status"]=="active" and time.monotonic()<deadline:
                time.sleep(.025)
            assert app.range_engine.get(gid)["status"]=="complete",app.range_engine.get(gid)
            assert (folder/"native.bin").read_bytes()==DATA
            assert app.snapshot()["items"]
            print("PASS_NATIVE_RANGE_DOWNLOAD_WITHOUT_ARIA2_SHA256",flush=True)

            try:
                app.add(f"http://127.0.0.1:{server.server_port}/plain",folder,"plain.bin")
            except RuntimeError as error:
                assert "aria2" in str(error)
            else:
                raise AssertionError("A non-range link must report the missing fallback")
            assert not (folder/"plain.bin").exists()
            print("PASS_UNSUPPORTED_LINK_REPORTS_MISSING_FALLBACK",flush=True)
        finally:
            app.close()
finally:
    server.shutdown()
print("PASS_NO_ARIA2_DEGRADED_MODE",flush=True)
