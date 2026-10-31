"""Detailed multi-connection runtime test; inspect honest per-range counters."""
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from ranges import RangeManager
import threading,tempfile,time,hashlib
DATA=hashlib.shake_256(b"Haitham test realistic segments v4.1").digest(18*1024*1024)
calls=[]
class Handler(BaseHTTPRequestHandler):
  def do_GET(self):
    h=self.headers.get('Range','')
    if not h.startswith('bytes='):
      self.send_error(416);return
    beg,end=h[6:].split('-',1)
    lo=int(beg);hi=int(end) if end else len(DATA)-1
    calls.append((lo,hi))
    self.send_response(206)
    self.send_header('Accept-Ranges','bytes')
    self.send_header('Content-Length',str(hi-lo+1))
    self.send_header('Content-Range',f'bytes {lo}-{hi}/{len(DATA)}')
    self.end_headers()
    if lo==0 and hi==0:
      self.wfile.write(DATA[:1]);return
    try:
      for pos in range(lo,hi+1,16384):
        self.wfile.write(DATA[pos:min(pos+16384,hi+1)])
        self.wfile.flush()
        time.sleep(.007)
    except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
  def log_message(self,*args):pass
server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
try:
 with tempfile.TemporaryDirectory(prefix="hdm_detailed_") as tmp:
    p=Path(tmp)
    m=RangeManager(p/"engine")
    url=f"http://127.0.0.1:{server.server_port}/large.bin"
    gid=m.add(url,p,"test-resume.bin",connections=8)
    pause=False
    observations=[]
    for i in range(120):
      time.sleep(.06)
      detail=m.get(gid)
      assert detail["completed"]==sum(s["completed"] for s in detail["ranges"])
      if len(observations)<8:observations.append([(c["index"],c["completed"],c["speed"]) for c in detail["ranges"]])
      if detail["status"]=="active" and detail["completed"]>600000 and not pause:
        m.pause(gid)
        detail=m.get(gid)
        assert detail["status"]=="paused"
        partial=detail["completed"]
        assert partial>0
        pause=True
        m.close()
        m=RangeManager(p/"engine")
        assert m.get(gid)["status"]=="paused"
        assert m.get(gid)["completed"]>=partial
        m.resume(gid)
      if detail["status"]=="complete":break
      if detail["status"]=="error":raise RuntimeError(detail["error"])
    deadline=time.monotonic()+22
    while m.get(gid)["status"]!="complete" and time.monotonic()<deadline:
      time.sleep(.1)
    detail=m.get(gid)
    assert detail["status"]=="complete",detail
    assert detail["completed"]==len(DATA),detail
    assert all(s["completed"]==s["size"] for s in detail["ranges"])
    output=p/"test-resume.bin"
    assert output.read_bytes()==DATA
    # Dynamic splitting may append additional nonoverlapping pieces.
    assert 8<=len(detail["ranges"])<=24
    print("CONNECTIONS",len(detail["ranges"]),"HTTP_REQUESTS",len(calls))
    print("PROGRESS_OBSERVATIONS",observations[:3])
    print("PAUSE_AND_RESTART",pause,"COMPLETED",detail["completed"],"SHA256_MATCH",True)
    assert pause
    m.close()
finally:server.shutdown()
print("PASS_REAL_CONNECTION_COUNTERS_AND_RESUME")
