"""Deterministic local regression for stealing slow range tails safely.

No external network, no Zajil code, and no timing claim about Internet/IDM.
The first range is deliberately slow; all other connections are faster.
"""
from __future__ import annotations

import hashlib
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ranges import RangeManager


DATA = hashlib.shake_256(b"haitham-independent-dynamic-split-test").digest(24 * 1024 * 1024)
EXPECTED = hashlib.sha256(DATA).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        raw = self.headers.get("Range", "")
        if not raw.startswith("bytes="):
            return self.send_error(416)
        low, high = raw[6:].split("-", 1)
        low, high = int(low), int(high)
        if low < 0 or high >= len(DATA) or high < low:
            return self.send_error(416)
        self.send_response(206)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {low}-{high}/{len(DATA)}")
        self.send_header("Content-Length", str(high - low + 1))
        self.end_headers()
        if low == high == 0:
            self.wfile.write(DATA[:1])
            return
        try:
            for p in range(low, high + 1, 32768):
                self.wfile.write(DATA[p:min(high + 1, p + 32768)])
                self.wfile.flush()
                time.sleep(.025 if low == 0 else .0015)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def log_message(self, *_):
        pass


def wait_for(manager, gid, finish=("complete", "error"), timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = manager.get(gid)
        if detail["status"] in finish:
            return detail
        time.sleep(.02)
    raise AssertionError(f"Timed out: {manager.get(gid)}")


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with tempfile.TemporaryDirectory(prefix="haitham_dynamic_qa_") as tmp:
        root = Path(tmp)
        url = f"http://127.0.0.1:{server.server_port}/slow-initial-range.bin"
        timings = {}
        for label, enabled in (("fixed", False), ("dynamic", True)):
            folder = root / label
            folder.mkdir()
            mgr = RangeManager(folder / "state", enable_work_stealing=enabled)
            start = time.monotonic()
            gid = mgr.add(url, folder, "correct.bin", connections=4)
            detail = wait_for(mgr, gid)
            elapsed = time.monotonic() - start
            assert detail["status"] == "complete", detail
            assert detail["completed"] == len(DATA)
            assert hashlib.sha256((folder / "correct.bin").read_bytes()).hexdigest() == EXPECTED
            if not enabled:
                assert len(detail["ranges"]) == 4
            timings[label] = elapsed
            if enabled:
                assert len(detail["ranges"]) > 4, "No slow tail was reassigned"
                assert len(detail["ranges"]) <= 12, "Unbounded splitting"
            mgr.close()
            print(f"PASS_{label.upper()}_SHA256 {elapsed:.3f}s ranges={len(detail['ranges'])}", flush=True)

        assert timings["dynamic"] < timings["fixed"] * .92, timings
        print("PASS_DYNAMIC_FASTER_ON_THROTTLED_LOCAL_FIXTURE", timings, flush=True)

        # A second run deliberately pauses after a split; the saved range
        # map and existing .part files must survive a new manager instance.
        folder = root / "restart"
        folder.mkdir()
        mgr = RangeManager(folder / "state")
        gid = mgr.add(url, folder, "resumed.bin", connections=4)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = mgr.get(gid)
            if state["status"] == "active" and len(state["ranges"]) > 4:
                break
            time.sleep(.02)
        else:
            raise AssertionError("No live split to pause")
        mgr.pause(gid)
        before = mgr.get(gid)
        assert before["status"] == "paused" and len(before["ranges"]) > 4
        assert 0 < before["completed"] < len(DATA)
        mgr.close()
        mgr = RangeManager(folder / "state")
        assert mgr.get(gid)["completed"] == before["completed"]
        mgr.resume(gid)
        final = wait_for(mgr, gid)
        assert final["status"] == "complete", final
        assert final["completed"] == len(DATA)
        assert hashlib.sha256((folder / "resumed.bin").read_bytes()).hexdigest() == EXPECTED
        assert not (folder / ".HaithamDownloader-temp" / gid).exists()
        mgr.close()
        print("PASS_SPLIT_PAUSE_RESTART_RESUME_SHA256_AND_CLEANUP", flush=True)
finally:
    server.shutdown()
print("PASS_ALL_DYNAMIC_REBALANCING_QA", flush=True)
