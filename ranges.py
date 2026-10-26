"""HTTP Range multi-connection downloader with *measured* bytes per connection.

Limited to publicly reachable direct HTTP(S) files supporting Content-Length
and bytes ranges. aria2 is the fallback for everything else.
The independent .part files support pausing and continuing after restarting app.
"""
from __future__ import annotations
import json, os, re, shutil, threading, time, uuid
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlsplit
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

class UnsupportedRange(Exception):
    pass

def probe(url, timeout=8):
    if urlsplit(url).scheme not in ("http","https"):
        raise UnsupportedRange("HTTP/HTTPS only")
    request=Request(url,headers={"User-Agent":"HaithamDownloader/4.1","Range":"bytes=0-0"})
    with urlopen(request,timeout=timeout) as response:
        content_range=response.headers.get("Content-Range","")
        match=re.match(r"^bytes\s+0-0/(\d+)$",content_range)
        if response.status!=206 or not match:
            raise UnsupportedRange("Server does not support verified HTTP byte ranges")
        size=int(match.group(1))
        if size<1024*1024 or size>1024**4:
            raise UnsupportedRange("Content size outside detailed downloader limits")
        response.read(1)
    return size

class AdaptiveRequestGate:
    """Coordinated per-download concurrency and Retry-After-aware backoff.

    In-flight ranges finish safely; new requests pause when a host starts
    rate-limiting. Saved part files are never discarded by a retry.
    """
    MAX_RETRIES = 8
    BASE_DELAY = 1.5
    MAX_BACKOFF = 30.0

    def __init__(self, requested):
        self.ceiling = max(1, int(requested))
        self.limit = min(4, self.ceiling)
        self.inflight = 0
        self.cooldown_until = 0.0
        self.successes = 0
        self.last_http_status = None
        self.lock = threading.RLock()

    def acquire(self, cancel):
        while not cancel.is_set():
            with self.lock:
                cooldown = max(0, self.cooldown_until - time.monotonic())
                if cooldown <= 0 and self.inflight < self.limit:
                    self.inflight += 1
                    return True
            cancel.wait(min(0.2, max(0.05, cooldown)) if cooldown else 0.1)
        return False

    def release(self):
        with self.lock:
            self.inflight -= 1

    @staticmethod
    def retry_after_seconds(header):
        if not header:
            return 0.0
        try:
            return max(0.0, float(header))
        except (ValueError, TypeError):
            try:
                date = parsedate_to_datetime(header)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                return 0.0

    def throttle(self, retry_number, status=None, retry_after=None):
        backoff = min(self.MAX_BACKOFF, self.BASE_DELAY * 2 ** min(retry_number - 1, 6))
        delay = max(backoff, min(180.0, self.retry_after_seconds(retry_after)))
        with self.lock:
            if status == 429:
                self.limit = 1
            elif status in (500, 502, 503, 504):
                self.limit = max(1, self.limit // 2)
            self.successes = 0
            self.last_http_status = status
            self.cooldown_until = max(self.cooldown_until, time.monotonic() + delay)

    def successful_range(self):
        with self.lock:
            self.successes += 1
            if self.successes >= 3 and self.limit < self.ceiling:
                self.limit += 1
                self.successes = 0

class RangeManager:
    def __init__(self, root):
        self.root=Path(root)
        self.root.mkdir(parents=True,exist_ok=True)
        self.parts_dir=self.root/"parts"
        self.parts_dir.mkdir(exist_ok=True)
        self.path=self.root/"tasks.json"
        self.lock=threading.RLock()
        self.records={}
        self.runtime={}
        self._load()

    def _load(self):
        try:
            records=json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError,OSError):
            records={}
        for gid,record in records.items():
            if not isinstance(record,dict) or not gid.startswith("seg-"):continue
            if record.get("status") in ("active","waiting"):record["status"]="paused"
            record["gid"]=gid
            self.records[gid]=record

    def _save(self):
        snap={}
        for gid,v in self.records.items():
            snap[gid]={k:val for k,val in v.items() if k not in ("speed",)}
        temp=self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(snap,ensure_ascii=False,indent=2),encoding="utf8")
        temp.replace(self.path)

    def add(self,url,directory,filename,connections=8,temp_dir=None):
        size=probe(url)
        count=min(max(1,int(connections)),max(1,size//(1024*1024)))
        step=size//count
        ranges=[]
        for idx in range(count):
            start=idx*step
            end=(idx+1)*step-1 if idx<count-1 else size-1
            ranges.append({"index":idx+1,"start":start,"end":end,"size":end-start+1})
        gid="seg-"+uuid.uuid4().hex[:16]
        # Default: parts are stored on the destination volume, never the Windows C: drive.
        staging_root=Path(temp_dir) if temp_dir else Path(directory)/".HaithamDownloader-temp"
        staging_root.mkdir(parents=True,exist_ok=True)
        record={"gid":gid,"url":url,"name":filename,"directory":str(directory),
                "filepath":str(Path(directory)/filename),"parts_root":str(staging_root),"total":size,
                "date":time.strftime("%Y-%m-%d %H:%M"),"status":"waiting",
                "ranges":ranges,"error":""}
        with self.lock:
            self.records[gid]=record
            (staging_root/gid).mkdir(exist_ok=False)
            self._save()
        self.resume(gid)
        return gid

    def _part(self,gid,index):
        return Path(self.records[gid].get("parts_root",str(self.parts_dir)))/gid/(f"part-{index:02d}.bin")

    def _completed(self,record):
        return [min(r["size"],self._part(record["gid"],r["index"]).stat().st_size
                    if self._part(record["gid"],r["index"]).exists() else 0)
                for r in record["ranges"]]

    def resume(self,gid):
        with self.lock:
            rec=self.records[gid]
            worker=self.runtime.get(gid,{}).get("worker")
            if worker and worker.is_alive():
                if rec["status"]=="active":return
                raise RuntimeError("Previous connections are stopping; retry")
            if rec["status"]=="complete":return
            cancel=threading.Event()
            runtime={"cancel":cancel,"samples":{},"worker":None,
                     "gate":AdaptiveRequestGate(len(rec["ranges"]))}
            self.runtime[gid]=runtime
            rec["status"]="active";rec["error"]=""
            thread=threading.Thread(target=self._download,args=(gid,runtime),daemon=True,name=gid)
            runtime["worker"]=thread
            self._save()
            thread.start()

    def _download_one(self,rec,part,cancel,samples,gate):
        gid=rec["gid"]
        r=part
        local=self._part(gid,r["index"])
        size=r["size"]
        written=local.stat().st_size if local.exists() else 0
        if written>size:raise RuntimeError("Existing partial file exceeds its range")
        retries=0
        while written<size and not cancel.is_set():
            # The gate coordinates new requests across ALL ranges in this download.
            if not gate.acquire(cancel):
                break
            begin=r["start"]+written
            request=Request(rec["url"],headers={
                "Range":f"bytes={begin}-{r['end']}",
                "User-Agent":"HaithamDownloader/4.1"})
            try:
                with urlopen(request,timeout=15) as reply:
                    actual=reply.headers.get("Content-Range","")
                    m=re.match(r"^bytes\s+(\d+)-(\d+)/(\d+)$",actual)
                    if reply.status!=206 or not m or tuple(map(int,m.groups()))!=(begin,r["end"],rec["total"]):
                        raise RuntimeError(f"Server returned inconsistent range: {actual}")
                    with local.open("ab") as output:
                        while not cancel.is_set() and written<size:
                            block=reply.read(min(65536,size-written))
                            if not block:raise OSError("Connection ended early")
                            output.write(block)
                            written+=len(block)
                            with self.lock:
                                samples[r["index"]]=(time.monotonic(),written)
                retries=0
                if written>=size:
                    gate.successful_range()
            except HTTPError as exc:
                if cancel.is_set():break
                if exc.code not in (429,500,502,503,504):
                    raise  # 401, 403, 404, 416: do not blindly retry.
                retries+=1
                if retries>gate.MAX_RETRIES:
                    raise RuntimeError(f"HTTP {exc.code} persisted after {gate.MAX_RETRIES} retries; partial data saved") from exc
                gate.throttle(retries,exc.code,exc.headers.get("Retry-After") if exc.headers else None)
            except (OSError, TimeoutError) as exc:
                if cancel.is_set():break
                retries+=1
                if retries>gate.MAX_RETRIES:
                    raise RuntimeError(f"Connection failed after {gate.MAX_RETRIES} retries; partial data saved") from exc
                gate.throttle(retries)
            finally:
                gate.release()

    def _download(self,gid,runtime):
        from concurrent.futures import ThreadPoolExecutor,as_completed
        rec=self.records[gid]
        cancel=runtime["cancel"]
        try:
            with ThreadPoolExecutor(max_workers=len(rec["ranges"]),thread_name_prefix="HDM") as pool:
                futures=[pool.submit(self._download_one,rec,r,cancel,runtime["samples"],runtime["gate"]) for r in rec["ranges"]]
                for f in as_completed(futures):
                    try:f.result()
                    except Exception:
                        cancel.set()
                        raise
            if cancel.is_set():return
            parts=self._completed(rec)
            if any(parts[i]!=r["size"] for i,r in enumerate(rec["ranges"])):
                raise RuntimeError("One or more segments ended without all bytes")
            target=Path(rec["filepath"])
            merge=target.with_name(target.name+".hdm-merging")
            if target.exists():raise FileExistsError("Target appeared while downloading")
            try:
                with merge.open("wb") as out:
                    for r in rec["ranges"]:
                        with self._part(gid,r["index"]).open("rb") as source:
                            shutil.copyfileobj(source,out,1024*1024)
                if merge.stat().st_size!=rec["total"]:raise RuntimeError("Merged file size mismatch")
                os.replace(merge,target)
            finally:
                if merge.exists():merge.unlink()
            shutil.rmtree(Path(rec.get("parts_root",str(self.parts_dir)))/gid,ignore_errors=True)
            with self.lock:
                if gid in self.records:
                    rec["status"]="complete"
                    self._save()
        except Exception as ex:
            with self.lock:
                if gid in self.records:
                    rec["status"]="paused" if cancel.is_set() and str(ex)=="" else "error"
                    rec["error"]=str(ex)
                    self._save()
        finally:
            with self.lock:
                if gid in self.records and rec["status"]=="active":
                    rec["status"]="paused" if cancel.is_set() else "error"
                    self._save()

    def pause(self,gid):
        with self.lock:
            runtime=self.runtime.get(gid)
            rec=self.records[gid]
            if runtime and runtime["worker"].is_alive():
                runtime["cancel"].set()
                worker=runtime["worker"]
            else:worker=None
        if worker:worker.join(timeout=12)
        with self.lock:
            if worker and worker.is_alive():
                raise RuntimeError("Timeout while stopping connections")
            if rec["status"] not in ("complete","error"):
                rec["status"]="paused"
            self._save()

    def remove(self,gid):
        if gid not in self.records:return
        rec=self.records[gid]
        if rec["status"]=="active":self.pause(gid)
        with self.lock:
            self.records.pop(gid,None)
            self.runtime.pop(gid,None)
            self._save()
        shutil.rmtree(Path(rec.get("parts_root",str(self.parts_dir)))/gid,ignore_errors=True)

    def get(self,gid):
        with self.lock:
            rec=self.records.get(gid)
            if rec is None:return None
            # Completed tasks have their temporary segments merged and removed.
            # Preserve full counters from the verified completed file.
            target=Path(rec["filepath"])
            file_ok=target.is_file() and target.stat().st_size==rec["total"]
            # Historical completion is not proof that the file still exists.
            # Show a missing-file state without rewriting the saved download history.
            display_status="missing" if rec["status"]=="complete" and not file_ok else rec["status"]
            if rec["status"]=="complete" and file_ok:
                sizes=[r["size"] for r in rec["ranges"]]
            else:
                sizes=self._completed(rec)
            now=time.monotonic()
            runtime=self.runtime.get(gid,{})
            previous=runtime.setdefault("previous",{})
            cached=runtime.setdefault("speeds",{})
            speeds=[]
            for idx,done in enumerate(sizes):
                recent=previous.get(idx)
                # Status and the progress dialog poll independently. Avoid
                # resetting speeds to zero on back-to-back requests.
                if rec["status"]!="active":
                    speed=0
                    cached[idx]=0
                elif recent and now-recent[0]>=0.65:
                    dt=now-recent[0]
                    speed=max(0,int((done-recent[1])/dt))
                    cached[idx]=speed
                    previous[idx]=(now,done)
                elif recent:
                    speed=cached.get(idx,0)
                else:
                    speed=0
                    previous[idx]=(now,done)
                speeds.append(speed)
            completed=sum(sizes)
            active_requests=0
            connection_limit=0
            retry_wait_seconds=0
            last_http_status=None
            gate=runtime.get("gate") if rec["status"]=="active" else None
            if gate is not None:
                with gate.lock:
                    active_requests=gate.inflight
                    connection_limit=gate.limit
                    retry_wait_seconds=max(0,int(gate.cooldown_until-now+0.999))
                    last_http_status=gate.last_http_status
            entries=[{"index":r["index"],"start":r["start"],"end":r["end"],
                     "size":r["size"],"completed":sizes[i], "speed":speeds[i],
                     "status":"complete" if sizes[i]>=r["size"] else rec["status"]}
                     for i,r in enumerate(rec["ranges"])]
            return {"gid":gid,"url":rec["url"],"name":rec["name"],"filepath":rec["filepath"],
                    "category":"other","date":rec["date"],"status":display_status,
                    "total":rec["total"],"completed":completed,"speed":sum(speeds),
                    "error":"Completed file is missing or has a different size" if display_status=="missing" else rec.get("error",""),"ranges":entries,
                    "connection_count":len(entries),"detail_type":"connections",
                    "active_requests":active_requests,"connection_limit":connection_limit,
                    "retry_wait_seconds":retry_wait_seconds,"last_http_status":last_http_status}

    def list_all(self):
        with self.lock:keys=list(self.records)
        return [x for key in keys if (x:=self.get(key))]

    def close(self):
        with self.lock:
            ids=[gid for gid,v in self.records.items() if v["status"]=="active"]
        for gid in ids:
            try:self.pause(gid)
            except Exception:pass
        with self.lock:self._save()
