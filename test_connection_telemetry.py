"""Validate live HTTP request count, throttle status and inactive display snapshots."""
from __future__ import annotations
import tempfile
import threading
from pathlib import Path
from ranges import AdaptiveRequestGate, RangeManager

with tempfile.TemporaryDirectory(prefix="hdm_telemetry_") as folder:
    root=Path(folder)
    mgr=RangeManager(root/"state")
    gid="seg-telemetry-regression"
    requested=12
    total=requested*1024*1024
    mgr.records[gid]={
        "gid":gid, "url":"https://example.test/file.bin",
        "filepath":str(root/"file.bin"),"name":"file.bin",
        "date":"2026-10-26","status":"active","total":total,
        "error":"", "ranges":[
            {"index":i+1,"start":i*1024*1024,
             "end":(i+1)*1024*1024-1,"size":1024*1024}
            for i in range(requested)]
    }
    gate=AdaptiveRequestGate(requested)
    mgr.runtime[gid]={"gate":gate,"samples":{}}
    state=mgr.get(gid)
    assert state["connection_count"]==12
    assert state["active_requests"]==0
    assert state["connection_limit"]==4, state
    assert state["retry_wait_seconds"]==0
    print("PASS_NOT_CONFUSING_REQUESTED_PARTS_WITH_LIVE_REQUESTS")

    stop=threading.Event()
    for _ in range(4):
        assert gate.acquire(stop)
    state=mgr.get(gid)
    assert state["active_requests"]==4
    assert state["connection_limit"]==4
    print("PASS_FOUR_ACTIVE_REQUESTS_VISIBLE")

    gate.throttle(1,status=429,retry_after="2")
    state=mgr.get(gid)
    assert state["last_http_status"]==429, state
    assert state["retry_wait_seconds"]>=1, state
    assert state["connection_limit"]==1, state
    assert state["active_requests"]==4, state
    print("PASS_429_THROTTLE_AND_RETRY_WAIT_EXPOSED")

    for _ in range(4):
        gate.release()
    mgr.records[gid]["status"]="paused"
    state=mgr.get(gid)
    assert state["active_requests"]==0
    assert state["retry_wait_seconds"]==0
    assert state["last_http_status"] is None
    assert state["connection_count"]==12
    print("PASS_NO_STALE_ACTIVITY_AFTER_PAUSE")
print("PASS_ALL_CONNECTION_TELEMETRY")
