"""Haitham Downloader V4 (preview): isolated Windows desktop app.

Backend: local-only authenticated API + aria2 session. UI: HTML/CSS/JS via WebView2.
No IDM source, binaries, registry or download history is modified.
"""
from __future__ import annotations
import argparse
import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from engine import AriaClient
from ranges import RangeManager, UnsupportedRange

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "HaithamDownloaderV41Classic"
DOWNLOADS = Path.home() / "Downloads"
INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
CATEGORIES = ("all", "active", "completed", "paused", "queued", "error",
              "archive", "document", "audio", "video", "software", "other")
TYPES = {
    "archive":{".zip",".7z",".rar",".tar",".gz",".bz2",".xz",".iso"},
    "document":{".pdf",".doc",".docx",".xls",".xlsx",".ppt",".pptx",".txt",".csv",".epub"},
    "video":{".mp4",".mkv",".avi",".mov",".webm",".wmv",".flv",".m4v"},
    "audio":{".mp3",".m4a",".wav",".flac",".aac",".ogg",".wma"},
    "software":{".exe",".msi",".msix",".apk",".deb",".rpm",".dmg",".pkg",".jar"}
}

def name_for(url):
    try:
        n=Path(urllib.parse.unquote(urlsplit(url).path)).name
    except Exception:
        n=""
    return safe_name(n or "download.bin")

def safe_name(name):
    name=Path(str(name).replace("\\","/")).name
    name=INVALID.sub("_",name).strip(" .")
    if not name or name.split(".")[0].upper() in {"CON","NUL","PRN","AUX",*(f"COM{i}" for i in range(1,10))}:
        name="download.bin"
    return name[:210]

def categorize(name):
    extension=Path(name.lower()).suffix
    return next((kind for kind,extensions in TYPES.items() if extension in extensions),"other")

def load_json(path,default):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError): return default

def save_json(path,value):
    tmp=path.with_name(path.name+".tmp")
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(path)

class Service:
    def __init__(self, data_dir=DATA_DIR):
        self.root=Path(data_dir);self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.path_catalog=self.root/"catalog.json"
        self.path_options=self.root/"options.json"
        self.catalog=load_json(self.path_catalog,{})
        self.options=load_json(self.path_options,{
            "language":"ar","theme":"dark","connections":12,"concurrent":3,
            "download_dir":str(DOWNLOADS),"temp_dir":"","remember_last_folder":False,"last_download_dir":"","schedule_enabled":False,
            "schedule_start":"02:00","schedule_stop":"07:00"})
        self.api_token=secrets.token_hex(24)
        self.engine=AriaClient(root=self.root/"engine",downloads=Path(self.options["download_dir"]))
        self.engine_error=""
        try:
            self.engine.start()
        except FileNotFoundError as exc:
            # Do not prevent independently supported HTTP Range downloads
            # because the optional aria2 fallback is not installed.
            self.engine=None
            self.engine_error=str(exc)
        self.range_engine=RangeManager(self.root/'range_engine')
        self.configure_engine()
        self.pending={}
        self.on_pending=None
        self.video_cancel={}
        self.last_write=time.monotonic()
        self.stop_event=threading.Event()
        self.scheduler=threading.Thread(target=self.scheduler_loop,daemon=True)
        self.scheduler.start()

    def configure_engine(self):
        if self.engine is None:
            return
        count=max(1,min(16,int(self.options.get("connections",12))))
        concurrent=max(1,min(10,int(self.options.get("concurrent",3))))
        self.engine.rpc("aria2.changeGlobalOption",[{
            "max-concurrent-downloads":str(concurrent),
            "max-connection-per-server":str(count),
            "split":str(count),"min-split-size":"1M"}])

    def persist(self):
        with self.lock:
            save_json(self.path_catalog,self.catalog)

    def snapshot(self):
        try:
            tasks=self.engine.list_all() if self.engine is not None else []
        except Exception as exc:
            tasks=[]
            error=str(exc)
        else:error=self.engine_error
        new={}
        now=datetime.now().strftime("%Y-%m-%d %H:%M")
        with self.lock:
            for t in tasks:
                gid=t.get("gid")
                if not gid:continue
                entry=self.catalog.get(gid,{})
                files=t.get("files") or []
                path=files[0].get("path","") if files else ""
                uris=files[0].get("uris",[]) if files else []
                url=entry.get("url") or (uris[0].get("uri","") if uris else "")
                name=entry.get("name") or (Path(path).name if path else name_for(url))
                total=int(t.get("totalLength","0") or 0)
                completed=int(t.get("completedLength","0") or 0)
                speed=int(t.get("downloadSpeed","0") or 0)
                entry.update(gid=gid,name=name,category=entry.get("category") or categorize(name),
                    url=url,filepath=path or entry.get("filepath",""),status=t.get("status","waiting"),
                    total=total,completed=completed,speed=speed,
                    date=entry.get("date") or now,
                    error=t.get("errorMessage",""))
                if self.catalog.get(gid)!=entry:self.catalog[gid]=entry
                new[gid]=entry
            for task in self.range_engine.list_all():
                task['category']=categorize(task['name'])
                gid=task['gid']
                # The range engine reports exact received bytes on every connection.
                new[gid]={k:v for k,v in task.items() if k!='ranges'}
            for gid,entry in self.catalog.items():
                if gid not in new:new[gid]=entry
            # Do not mark an old completed download as error merely because aria2 truncated history.
            pending=[{"id":pid,"url":v["url"],"filename":v["filename"],"created":v["created"]}
                     for pid,v in self.pending.items() if not v["event"].is_set()]
            rows=sorted(new.values(),key=lambda x:x.get("date",""),reverse=True)
            if time.monotonic()-self.last_write>8:
                self.persist();self.last_write=time.monotonic()
        speeds=sum(x.get("speed",0) for x in rows if x.get("status")=="active")
        return {"items":rows,"pending":pending,"speed":speeds,"error":error,
                "aria2_available":self.engine is not None,
                "options":self.options,"version":"4.1-classic-preview"}

    def add(self,url,directory=None,filename=None,kind="file",mode="aria2"):
        if not isinstance(url,str) or urlsplit(url).scheme not in ("http","https","ftp","magnet"):
            raise ValueError("Only http, https, ftp or magnet URLs are supported")
        target=Path(directory or self.options["download_dir"]).expanduser()
        if not target.is_absolute():raise ValueError("Save folder must be absolute")
        target.mkdir(parents=True,exist_ok=True)
        if not target.is_dir():raise ValueError("Invalid save directory")
        filename=safe_name(filename or name_for(url))
        # Choose the next unused name without overwriting existing or queued downloads.
        with self.lock:
            occupied = {str(Path(v.get("filepath", "")).resolve()).casefold() for v in self.catalog.values() if v.get("filepath")}
            occupied |= {str(Path(v.get("filepath", "")).resolve()).casefold() for v in self.range_engine.records.values() if v.get("filepath")}
            stem, extension = Path(filename).stem, Path(filename).suffix
            base = filename
            number = 1
            while (target / filename).exists() or str((target / filename).resolve()).casefold() in occupied:
                filename = safe_name(f"{stem} ({number}){extension}")
                number += 1
        if kind=="video":return self.start_video(url,target,filename)
        if (mode=="segmented" or self.engine is None) and urlsplit(url).scheme in ('http','https'):
            try:
                gid=self.range_engine.add(url,target,filename,self.options.get('connections',12),self.options.get("temp_dir") or None)
                return {'gid':gid,'name':filename,'mode':'connections'}
            except UnsupportedRange:
                pass # The robust aria2 fallback remains available for unsupported hosts.
        if self.engine is None:
            raise RuntimeError("This link requires the aria2 fallback, which is not installed. Install aria2c or use a direct HTTP Range file.")
        gid=self.engine.add(url,directory=target,filename=filename)
        now=datetime.now().strftime("%Y-%m-%d %H:%M")
        with self.lock:
            self.catalog[gid]={"gid":gid,"url":url,"name":filename,"category":categorize(filename),
                               "filepath":str(target/filename),"date":now,"status":"waiting",
                               "total":0,"completed":0,"speed":0}
            self.persist()
        return {"gid":gid,"name":filename}

    def start_video(self,url,target,filename):
        from yt_dlp import YoutubeDL
        gid="video-"+secrets.token_hex(8)
        stop=threading.Event()
        self.video_cancel[gid]=stop
        with self.lock:
            self.catalog[gid]={"gid":gid,"url":url,"name":filename,"category":"video",
                               "filepath":str(target),"date":datetime.now().strftime("%Y-%m-%d %H:%M"),
                               "status":"waiting","total":0,"completed":0,"speed":0}
            self.persist()
        def progress(info):
            if stop.is_set():raise RuntimeError("Video download was stopped")
            with self.lock:
                entry=self.catalog.get(gid)
                if entry:
                    entry.update(status="active",total=int(info.get("total_bytes") or info.get("total_bytes_estimate") or 0),
                                 completed=int(info.get("downloaded_bytes") or 0),
                                 speed=int(info.get("speed") or 0))
        def worker():
            try:
                options={"quiet":True,"noprogress":True,"noplaylist":True,
                    "outtmpl":str(target/"%(title).150B [%(id)s].%(ext)s"),
                    "format":"bv*+ba/b","progress_hooks":[progress],"retries":5,
                    "windowsfilenames":True}
                ffmpeg=shutil.which("ffmpeg")
                if ffmpeg:options["ffmpeg_location"]=str(Path(ffmpeg).parent)
                with YoutubeDL(options) as ydl:
                    info=ydl.extract_info(url,download=True)
                with self.lock:
                    self.catalog[gid].update(status="complete",
                         name=info.get("title") or filename,
                         completed=self.catalog[gid].get("total",0),speed=0)
                    self.persist()
            except Exception as exc:
                with self.lock:
                    self.catalog[gid].update(status="error",error=str(exc),speed=0)
                    self.persist()
        threading.Thread(target=worker,daemon=True).start()
        return {"gid":gid,"name":filename}

    def action(self, op, gids, delete_disk=False):
        if not isinstance(gids,list) or not all(isinstance(g,str) for g in gids):
            raise ValueError("Invalid selection")
        if op not in ("pause","resume","remove","open_folder"):raise ValueError("Unsupported action")
        results=[]
        for gid in gids[:200]:
            if gid.startswith('seg-'):
                task=self.range_engine.get(gid)
                if not task:continue
                if op=='pause':self.range_engine.pause(gid)
                elif op=='resume':self.range_engine.resume(gid)
                elif op=='open_folder':
                    p=Path(task['filepath'])
                    if p.parent.exists():os.startfile(str(p.parent))
                elif op=='remove':
                    self.range_engine.remove(gid)
                    if delete_disk:
                        p=Path(task['filepath'])
                        if p.is_file() and not p.is_symlink():
                            from send2trash import send2trash
                            send2trash(str(p))
                results.append(gid)
                continue
            entry=self.catalog.get(gid)
            if not entry:continue
            if op=="open_folder":
                folder=Path(entry.get("filepath") or self.options["download_dir"])
                folder=folder if folder.is_dir() else folder.parent
                if folder.exists():os.startfile(str(folder))
                results.append(gid);continue
            if gid.startswith("video-"):
                if op in ("pause","remove"):self.video_cancel.get(gid,threading.Event()).set()
                if op=="resume":continue
            elif self.engine is None:
                raise RuntimeError("aria2 is unavailable; this existing download cannot be controlled until it is installed")
            elif op=="pause":
                self.engine.rpc("aria2.pause",[gid])
            elif op=="resume":
                self.engine.rpc("aria2.unpause",[gid])
            elif op=="remove":
                try:self.engine.rpc("aria2.forceRemove",[gid])
                except Exception:
                    try:self.engine.rpc("aria2.removeDownloadResult",[gid])
                    except Exception:pass
            if op=="remove":
                p=Path(entry.get("filepath",""))
                if delete_disk and p.is_file() and not p.is_symlink():
                    # Safeguard against deleting a file while aria2 may still be writing it.
                    if gid.startswith("video-") and entry.get("status")=="active":
                        raise RuntimeError("Stop the video download before deleting files")
                    if not gid.startswith("video-"):
                        for i in range(30):
                            try:
                                status=self.engine.rpc("aria2.tellStatus",[gid])
                            except Exception:
                                # aria2 may forget a completed entry as soon as removed.
                                break
                            if status.get("status") not in ("active","waiting"):break
                            time.sleep(.1)
                        else:raise RuntimeError("Download still running; disk deletion cancelled")
                    try:
                        from send2trash import send2trash
                    except ImportError:
                        raise RuntimeError("send2trash is required for safe file deletion")
                    send2trash(str(p))
                with self.lock:
                    self.catalog.pop(gid,None)
                    self.persist()
            results.append(gid)
        return {"affected":results}

    def details(self,gid):
        if gid.startswith('seg-'):
            return self.range_engine.get(gid)
        with self.lock:
            item=self.catalog.get(gid)
        if item is None:return None
        detail=dict(item)
        try:
            task=self.engine.rpc('aria2.tellStatus',[gid])
            detail.update(total=int(task.get('totalLength') or 0),
                completed=int(task.get('completedLength') or 0),
                speed=int(task.get('downloadSpeed') or 0),
                connections=int(task.get('connections') or 0),
                bitfield=task.get('bitfield',''),
                numPieces=int(task.get('numPieces') or 0),
                pieceLength=int(task.get('pieceLength') or 0),
                status=task.get('status','waiting'))
        except Exception:pass
        detail['detail_type']='pieces' # Do not invent per-connection downloaded bytes.
        return detail

    def change_settings(self,values):
        safe={}
        for key in ("language","theme","download_dir","temp_dir","remember_last_folder","last_download_dir","schedule_start","schedule_stop","schedule_enabled",
                    "connections","concurrent"):
            if key in values:safe[key]=values[key]
        if "language" in safe and safe["language"] not in ("ar","en"):raise ValueError("Invalid language")
        if "theme" in safe and safe["theme"] not in ("dark","light"):raise ValueError("Invalid theme")
        if "connections" in safe:safe["connections"]=max(1,min(32,int(safe["connections"])))
        if "concurrent" in safe:safe["concurrent"]=max(1,min(10,int(safe["concurrent"])))
        if "download_dir" in safe:
            p=Path(safe["download_dir"]).expanduser()
            if not p.is_absolute():raise ValueError("Invalid path")
            p.mkdir(parents=True,exist_ok=True)
            safe["download_dir"]=str(p)
        if "remember_last_folder" in safe:
            if not isinstance(safe["remember_last_folder"],bool):
                raise ValueError("Invalid remember-folder setting")
        if "last_download_dir" in safe:
            last=str(safe["last_download_dir"]).strip()
            if last:
                folder=Path(last).expanduser()
                if not folder.is_absolute() or not folder.is_dir():
                    raise ValueError("Last save folder must exist")
                safe["last_download_dir"]=str(folder)
            else:
                safe["last_download_dir"]=""
        if "temp_dir" in safe:
            td=str(safe["temp_dir"]).strip()
            if td:
                p=Path(td).expanduser()
                if not p.is_absolute():raise ValueError("Temporary folder must be absolute")
                p.mkdir(parents=True,exist_ok=True)
                if not p.is_dir():raise ValueError("Invalid temporary folder")
                safe["temp_dir"]=str(p)
            else:
                safe["temp_dir"]=""
        for key in ("schedule_start","schedule_stop"):
            if key in safe:datetime.strptime(safe[key],"%H:%M")
        with self.lock:
            self.options.update(safe)
            save_json(self.path_options,self.options)
        self.configure_engine()
        return self.options

    def scheduler_loop(self):
        last=""
        while not self.stop_event.wait(10):
            if not self.options.get("schedule_enabled"):continue
            now=datetime.now()
            key=now.strftime("%Y-%m-%d %H:%M")
            if key==last:continue
            hm=now.strftime("%H:%M")
            if hm==self.options.get("schedule_start"):
                try:self.engine.rpc("aria2.unpauseAll")
                except Exception:pass
                last=key
            elif hm==self.options.get("schedule_stop"):
                try:self.engine.rpc("aria2.pauseAll")
                except Exception:pass
                last=key

    def new_pending(self,url,filename):
        pid=secrets.token_hex(10)
        with self.lock:
            self.pending[pid]={"url":url,"filename":safe_name(filename or name_for(url)),
                         "created":datetime.now().strftime("%H:%M:%S"),"event":threading.Event(),"result":False}
        if self.on_pending:self.on_pending()
        return pid

    def resolve_pending(self,pid,approved,directory=None,filename=None):
        with self.lock:
            pending=self.pending.get(pid)
            if not pending or pending["event"].is_set():raise ValueError("This request has expired")
        success=False
        added=None
        if approved:
            # A landing/share page is not the downloadable payload. Fail closed.
            candidate=pending["url"]
            try:
                check=Request(candidate,method="HEAD",headers={"User-Agent":"Mozilla/5.0"})
                with urlopen(check,timeout=10) as response:
                    mime=response.headers.get("Content-Type","").split(";")[0].strip().lower()
                    dispo=response.headers.get("Content-Disposition","")
                    final=response.geturl()
                if mime in ("text/html","application/xhtml+xml") or (mime.startswith("text/") and "attachment" not in dispo.lower()):
                    raise ValueError("This link opens a webpage, not a downloadable file. Open the final download link in Chrome.")
                if not urlsplit(final).scheme in ("http","https"):
                    raise ValueError("Invalid download redirect")
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError("Could not verify the real download URL: "+str(exc)[:100])
            added=self.add(candidate,directory,filename or pending["filename"],mode="segmented")
            success=True
        with self.lock:
            pending["result"]=success
            pending["event"].set()
        return {"accepted":success,"gid":added.get("gid") if added else None}

    def close(self):
        self.stop_event.set()
        for p in list(self.pending.values()):p["event"].set()
        self.persist()
        self.range_engine.close()
        if self.engine is not None:
            self.engine.close()

class Handler(BaseHTTPRequestHandler):
    def _json(self,status,data):
        raw=json.dumps(data,ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(raw)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.end_headers()
        try:self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):pass

    def do_GET(self):
        endpoint=urllib.parse.urlsplit(self.path).path
        app=self.server.app
        if endpoint=="/health":
            return self._json(200,{"ok":True,"version":"4.1-classic-preview"})
        if endpoint in ("/api/state","/api/details"):
            if self.headers.get("X-Haitham-Token")!=app.api_token:return self._json(403,{"error":"Forbidden"})
            if endpoint=="/api/state":return self._json(200,app.snapshot())
            gid=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("gid",[""])[0]
            if not gid or len(gid)>90:return self._json(400,{"error":"Invalid download ID"})
            result=app.details(gid)
            if result is None:return self._json(404,{"error":"Not found"})
            return self._json(200,result)
        if endpoint in ("/","/index.html"):
            content=(APP_DIR/"web"/"index.html").read_text(encoding="utf-8")
            content=content.replace("__TOKEN__",app.api_token)
            raw=content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        elif endpoint in ("/ui.js","/ui.css"):
            file=APP_DIR/"web"/endpoint[1:]
            raw=file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type","text/javascript; charset=utf-8" if endpoint.endswith(".js") else "text/css; charset=utf-8")
        else:
            return self._json(404,{"error":"Not found"})
        self.send_header("Content-Length",str(len(raw)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.end_headers()
        try:self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):pass

    def do_POST(self):
        endpoint=urllib.parse.urlsplit(self.path).path
        app=self.server.app
        if endpoint=="/wake":
            if self.headers.get("X-Haitham-Downloader")!="1":return self._json(403,{"error":"Forbidden"})
            if app.on_pending:app.on_pending()
            return self._json(200,{"ok":True})
        if endpoint.startswith("/api/"):
            if self.headers.get("X-Haitham-Token")!=app.api_token:
                return self._json(403,{"error":"Forbidden"})
        elif endpoint=="/browser/add":
            origin=self.headers.get("Origin","")
            if self.headers.get("X-Haitham-Downloader")!="1" or (origin and not origin.startswith(("chrome-extension://","moz-extension://"))):
                return self._json(403,{"error":"Forbidden"})
        else:return self._json(404,{"error":"Not found"})
        try:
            count=int(self.headers.get("Content-Length","0"))
            if count<=0 or count>8192:raise ValueError("Invalid payload length")
            payload=json.loads(self.rfile.read(count))
            if endpoint=="/api/add":
                data=app.add(payload.get("url",""),payload.get("directory"),payload.get("filename"),
                    payload.get("kind","file"),payload.get("mode","aria2"))
            elif endpoint=="/api/action":
                data=app.action(payload["action"],payload.get("gids",[]),payload.get("delete_disk",False))
            elif endpoint=="/api/settings":
                data=app.change_settings(payload)
            elif endpoint=="/api/resolve":
                data=app.resolve_pending(payload["id"],payload.get("accept",False),
                                         payload.get("directory"),payload.get("filename"))
            elif endpoint=="/browser/add":
                url=payload.get("url","")
                if urlsplit(url).scheme not in ("http","https","ftp"):raise ValueError("Invalid URL")
                pid=app.new_pending(url,payload.get("filename",""))
                req=app.pending[pid]
                answered=req["event"].wait(85)
                accepted=bool(answered and req["result"])
                with app.lock:app.pending.pop(pid,None)
                return self._json(200 if accepted else 503,{"ok":accepted,"reason":"accepted" if accepted else "not accepted"})
            else:return self._json(404,{"error":"Unknown API route"})
            return self._json(200,{"ok":True,"data":data})
        except (ValueError,FileExistsError,KeyError) as exc:
            return self._json(400,{"error":str(exc)})
        except Exception as exc:
            return self._json(500,{"error":str(exc)})

    def log_message(self,*args):pass

def run(headless=False,port=18767,data_dir=DATA_DIR):
    # Re-opening the desktop shortcut must reveal the existing tray instance.
    try:
        with urlopen(f"http://127.0.0.1:{port}/health",timeout=0.7) as health:
            instance=json.load(health)
        if instance.get("ok") and str(instance.get("version","")).startswith("4.1-"):
            req=Request(f"http://127.0.0.1:{port}/wake",data=b"{}",
                        headers={"Content-Type":"application/json","X-Haitham-Downloader":"1"},method="POST")
            with urlopen(req,timeout=2):pass
            print("ALREADY_RUNNING_RESTORED",flush=True)
            return
    except Exception:pass
    service=Service(data_dir)
    server=ThreadingHTTPServer(("127.0.0.1",port),Handler)
    server.daemon_threads=True
    server.app=service
    threading.Thread(target=server.serve_forever,daemon=True).start()
    print(f"Haitham Downloader V4: http://127.0.0.1:{port} [headless={headless}]",flush=True)
    def activate():
        window=globals().get("_webview_window")
        if window:
            try:
                window.restore()
                window.show()
                window.on_top=True
                time.sleep(.12)
                window.on_top=False
            except Exception:pass
    service.on_pending=activate
    try:
        if headless:
            while True:time.sleep(.2)
        else:
            try:
                import webview
            except ImportError:
                print("pywebview missing; falling back to browser",flush=True)
                webbrowser.open(f"http://127.0.0.1:{port}")
                while True:time.sleep(.2)
            else:
                from desktop_shell import FolderApi, TrayController
                folder_api=FolderApi()
                window=webview.create_window("Haitham Downloader Classic 4.1",f"http://127.0.0.1:{port}",
                    width=1250,height=800,min_size=(850,560),background_color="#10151d",
                    js_api=folder_api)
                folder_api._window=window
                globals()["_webview_window"]=window
                tray=TrayController(window)
                tray.start()
                wake_lock=threading.Lock()
                wake_last=[0.0]
                def wake_window_async():
                    # Coalesce bursts of browser downloads: one UI restore at a time.
                    with wake_lock:
                        now=time.monotonic()
                        if now-wake_last[0] < 1.5:
                            return
                        wake_last[0]=now
                    threading.Thread(target=tray.show,daemon=True,name='HaithamTrayRestore').start()
                service.on_pending=wake_window_async
                def verify_webview():
                    for attempt in range(15):
                        try:
                            title=window.evaluate_js("document.title")
                            text=window.evaluate_js("document.body ? document.body.innerText.slice(0,120) : ''")
                            if title and text:
                                print("V4_WEBVIEW_RENDER_OK",str(title).encode("ascii","backslashreplace").decode(),str(text[:80]).encode("ascii","backslashreplace").decode(),flush=True)
                                return
                        except Exception as exc:
                            if attempt==14:print("V4_WEBVIEW_CHECK_ERROR",str(exc),flush=True)
                        time.sleep(.4)
                    print("V4_WEBVIEW_RENDER_NOT_VERIFIED",flush=True)
                try:
                    webview.start(verify_webview,debug=False,private_mode=True)
                finally:
                    tray.stop()
    except KeyboardInterrupt:pass
    finally:
        service.close()
        server.shutdown()
        server.server_close()

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--headless",action="store_true")
    parser.add_argument("--port",type=int,default=18767)
    parser.add_argument("--data-dir",default=str(DATA_DIR))
    args=parser.parse_args()
    run(args.headless,args.port,args.data_dir)

