"""Reproducible localhost bandwidth benchmark (measured end-to-end, no internet)."""
from __future__ import annotations
import argparse, hashlib, tempfile, threading, time, statistics
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ranges import RangeManager

DATA = hashlib.shake_256(b"hdm-bandwidth-fixture-20261026").digest(96*1024*1024)
EXPECTED = hashlib.sha256(DATA).hexdigest()
BLOCK=65536
DELAY=0.016

class ServerHandler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    def log_message(self,*args):pass
    def do_GET(self):
        s=self.headers.get("Range","")
        if not s.startswith("bytes="):self.send_error(416);return
        start,end=s[6:].split("-",1)
        start=int(start);end=int(end) if end else len(DATA)-1
        if not (0<=start<=end<len(DATA)):self.send_error(416);return
        self.send_response(206)
        self.send_header("Content-Type","application/octet-stream")
        self.send_header("Content-Range",f"bytes {start}-{end}/{len(DATA)}")
        self.send_header("Content-Length",str(end-start+1))
        self.send_header("Connection","keep-alive")
        self.end_headers()
        try:
            for j in range(start,end+1,BLOCK):
                self.wfile.write(DATA[j:min(end+1,j+BLOCK)])
                if end>start:time.sleep(DELAY)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass

parser=argparse.ArgumentParser()
parser.add_argument("--runs",type=int,default=3)
parser.add_argument("--label",default="baseline")
args=parser.parse_args()
root=Path(r"D:\HaithamDownloader-SpeedTests")
server=ThreadingHTTPServer(("127.0.0.1",0),ServerHandler)
threading.Thread(target=server.serve_forever,daemon=True).start()
results=[]
try:
    for i in range(args.runs):
        with tempfile.TemporaryDirectory(prefix="hdmperf_",dir=root) as folder:
            dest=Path(folder)
            m=RangeManager(dest/"state")
            start=time.perf_counter()
            gid=m.add(f"http://127.0.0.1:{server.server_port}/test",dest,f"speed-{i}.bin",connections=12)
            deadline=start+50
            while time.perf_counter()<deadline:
                rec=m.get(gid)
                if rec["status"] in ("complete","error"):break
                time.sleep(.04)
            elapsed=time.perf_counter()-start
            assert rec["status"]=="complete",(i,rec["status"],rec["error"])
            file=dest/f"speed-{i}.bin"
            assert file.stat().st_size==len(DATA)
            assert hashlib.sha256(file.read_bytes()).hexdigest()==EXPECTED
            results.append(elapsed)
            print(f"RUN {args.label} #{i+1}: {elapsed:.3f}s, {len(DATA)*8/elapsed/1e6:.1f} Mbps, SHA256 OK",flush=True)
            m.close()
finally:
    server.shutdown()
print(f"BENCH {args.label}: median={statistics.median(results):.3f}s min={min(results):.3f}s max={max(results):.3f}s repetitions={len(results)}",flush=True)
