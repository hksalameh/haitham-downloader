"""Deterministic adversarial QA for adaptive multipart HTTP downloader."""
from __future__ import annotations
import hashlib
import tempfile
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ranges import AdaptiveRequestGate, RangeManager

DATA = hashlib.shake_256(b"adaptive-retry-regression").digest(6 * 1024 * 1024)
SHA = hashlib.sha256(DATA).hexdigest()
lock = threading.RLock()
hits = {}
active = 0
peak_active = 0
rate_limit_seen = threading.Event()
slow_limit_seen = threading.Event()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global active, peak_active
        resource = self.path.strip("/")
        request_range = self.headers.get("Range", "")
        if not request_range.startswith("bytes="):
            return self.send_error(400)
        low_s, high_s = request_range[6:].split("-", 1)
        low = int(low_s)
        high = int(high_s) if high_s else len(DATA) - 1
        if high >= len(DATA) or low > high:
            return self.send_error(416)
        is_probe = low == 0 and high == 0
        with lock:
            hits[resource] = hits.get(resource, 0) + (not is_probe)
            count = hits[resource]
            if not is_probe:
                active += 1
                peak_active = max(peak_active, active)
        try:
            if not is_probe and resource in ("limited", "retry503", "always429", "pause429"):
                status = (429 if resource != "retry503" else 503)
                if resource == "always429" or resource == "pause429" or count <= 3:
                    self.send_response(status)
                    self.send_header("Retry-After", "8" if resource == "pause429" else "0.15")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    if status == 429:
                        rate_limit_seen.set()
                    if resource == "pause429":
                        slow_limit_seen.set()
                    return
            if not is_probe and resource == "notfound":
                self.send_error(404)
                return
            if not is_probe and resource == "wrongrange":
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {low+1}-{high}/{len(DATA)}")
                self.send_header("Content-Length", str(high-low+1))
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Length", str(high-low+1))
            self.send_header("Content-Range", f"bytes {low}-{high}/{len(DATA)}")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            if resource == "resume":
                for pos in range(low,high+1,32768):
                    self.wfile.write(DATA[pos:min(high+1,pos+32768)])
                    self.wfile.flush()
                    time.sleep(0.012)
            else:
                for pos in range(low,high+1,65536):
                    self.wfile.write(DATA[pos:min(high+1,pos+65536)])
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        finally:
            if not is_probe:
                with lock:
                    active -= 1

    def log_message(self, *_):
        pass


def until(manager, gid, states, timeout=12):
    expiry = time.monotonic() + timeout
    while time.monotonic() < expiry:
        state = manager.get(gid)
        if state["status"] in states:
            return state
        time.sleep(0.03)
    raise AssertionError(f"Timed out: {gid}: {manager.get(gid)}")


def test_case(mgr, root, port, scenario, expected="complete"):
    filename = scenario + ".bin"
    gid = mgr.add(f"http://127.0.0.1:{port}/{scenario}", root, filename, connections=6)
    state = until(mgr, gid, {"complete","error"}, timeout=20)
    assert state["status"] == expected, (scenario,state)
    if expected == "complete":
        path = root / filename
        assert path.stat().st_size == len(DATA)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == SHA
        assert state["completed"] == len(DATA)
        assert not (root / ".HaithamDownloader-temp" / gid).exists()
    else:
        assert not (root / filename).exists()
    return gid,state


server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
AdaptiveRequestGate.BASE_DELAY = 0.05
AdaptiveRequestGate.MAX_BACKOFF = 0.12
AdaptiveRequestGate.MAX_RETRIES = 2

try:
    with tempfile.TemporaryDirectory(prefix="hdm_rate_limit_") as d:
        root=Path(d)
        mgr=RangeManager(root/"state")
        port=server.server_port
        test_case(mgr,root,port,"limited")
        assert rate_limit_seen.is_set()
        assert peak_active <= 4, peak_active
        print("PASS_429_RECOVERY_SHARED_LIMIT_AND_SHA256",hits["limited"],peak_active,flush=True)
        test_case(mgr,root,port,"retry503")
        print("PASS_503_RETRY_AND_SHA256",flush=True)
        _,failure=test_case(mgr,root,port,"notfound","error")
        assert "404" in failure["error"]
        print("PASS_404_FAILS_FAST_NO_TARGET",flush=True)
        _,failure=test_case(mgr,root,port,"wrongrange","error")
        assert "inconsistent range" in failure["error"]
        print("PASS_INCORRECT_RANGE_REJECTED_NO_TARGET",flush=True)
        _,failure=test_case(mgr,root,port,"always429","error")
        assert "persisted" in failure["error"]
        print("PASS_BOUNDED_RETRIES_PRESERVE_NO_FALSE_COMPLETE",hits["always429"],flush=True)
        # Pause cancels a coordinated cooldown without waiting 8 seconds.
        gid=mgr.add(f"http://127.0.0.1:{port}/pause429",root,"pause429.bin",connections=4)
        assert slow_limit_seen.wait(4)
        start=time.monotonic()
        mgr.pause(gid)
        elapsed=time.monotonic()-start
        assert elapsed < 2.0,elapsed
        assert mgr.get(gid)["status"]=="paused"
        print("PASS_COOLDOWN_CANCELS_QUICKLY",round(elapsed,3),flush=True)
        # Existing behaviour: resume existing partial file after restart.
        gid=mgr.add(f"http://127.0.0.1:{port}/resume",root,"resume.bin",connections=4)
        expiry=time.monotonic()+4
        while mgr.get(gid)["completed"] < 300000 and time.monotonic()<expiry:
            time.sleep(0.05)
        assert mgr.get(gid)["completed"]>0,"Fixture too fast/slow"
        mgr.pause(gid)
        existing=mgr.get(gid)["completed"]
        assert existing>0 and existing<len(DATA),existing
        mgr.close()
        mgr=RangeManager(root/"state")
        assert mgr.get(gid)["completed"]>=existing
        mgr.resume(gid)
        state=until(mgr,gid,{"complete","error"},timeout=20)
        assert state["status"]=="complete",state
        assert hashlib.sha256((root/"resume.bin").read_bytes()).hexdigest()==SHA
        print("PASS_RESUME_AFTER_RESTART_SHA256",flush=True)
        mgr.close()
finally:
    server.shutdown()
print("PASS_ALL_ADVERSARIAL_QA",flush=True)
